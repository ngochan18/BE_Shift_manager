import csv
from datetime import date

from django.db.models import Count, Sum, Value, DecimalField
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdminRole
from stores.models import Store
from .models import PayrollPeriod, PayrollDetail
from .serializers import (
    EstimatedSalarySerializer,
    PayrollPreviewItemSerializer,
    PayrollPeriodSerializer,
    PayrollPeriodDetailSerializer,
    PayrollDetailSerializer,
    PayrollDetailUpdateSerializer,
    CreatePayrollPeriodSerializer,
)
from .services import (
    tinh_luong_tong_hop,
    tinh_luong_ca_nhan,
    create_or_update_payroll_period,
    update_payroll_detail,
    confirm_payroll_period,
    pay_payroll_period,
    delete_payroll_period,
    get_user_payslips,
)


class PayrollPreviewView(APIView):
    """GET /api/payroll/preview/?store=<id>&month=<m>&year=<y>"""

    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        store_id = request.query_params.get("store")
        today = date.today()
        try:
            month = int(request.query_params.get("month", today.month))
            year = int(request.query_params.get("year", today.year))
        except ValueError:
            return Response({"error": "month và year phải là số nguyên."}, status=status.HTTP_400_BAD_REQUEST)

        if not store_id:
            return Response({"error": "Thiếu tham số store."}, status=status.HTTP_400_BAD_REQUEST)

        store = get_object_or_404(Store, id=store_id)
        preview = tinh_luong_tong_hop(store, month, year)
        serializer = PayrollPreviewItemSerializer(preview, many=True)

        total_gross = sum(item["thanh_tien"] for item in preview)
        return Response({
            "store": store.name,
            "month": month,
            "year": year,
            "total_employees": len(preview),
            "total_gross": total_gross,
            "employees": serializer.data,
        })


class PayrollMyEstimateView(APIView):
    """GET /api/payroll/my-estimate/?month=<m>&year=<y>"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        today = date.today()
        try:
            month = int(request.query_params.get("month", today.month))
            year = int(request.query_params.get("year", today.year))
        except ValueError:
            return Response({"error": "month và year phải là số nguyên."}, status=status.HTTP_400_BAD_REQUEST)

        data = tinh_luong_ca_nhan(request.user, month, year)
        return Response(EstimatedSalarySerializer(data).data)


class PayrollPeriodViewSet(viewsets.ModelViewSet):
    """
    Quản lý các kỳ lương (Dành cho Admin).
    """
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get_queryset(self):
        qs = PayrollPeriod.objects.select_related("store", "created_by").annotate(
            total_employees=Count("details", distinct=True),
            total_gross=Coalesce(Sum("details__gross_amount"), Value(0), output_field=DecimalField()),
            total_deduction=Coalesce(Sum("details__deduction"), Value(0), output_field=DecimalField()),
            total_bonus=Coalesce(Sum("details__bonus"), Value(0), output_field=DecimalField()),
            total_net=Coalesce(Sum("details__net_amount"), Value(0), output_field=DecimalField()),
        )
        store_id = self.request.query_params.get("store_id")
        if store_id:
            qs = qs.filter(store_id=store_id)
        month = self.request.query_params.get("month")
        if month:
            qs = qs.filter(month=month)
        year = self.request.query_params.get("year")
        if year:
            qs = qs.filter(year=year)
        status_val = self.request.query_params.get("status")
        if status_val:
            qs = qs.filter(status=status_val)
        return qs

    def get_serializer_class(self):
        if self.action == "retrieve":
            return PayrollPeriodDetailSerializer
        return PayrollPeriodSerializer

    def create(self, request, *args, **kwargs):
        serializer = CreatePayrollPeriodSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        store = get_object_or_404(Store, id=serializer.validated_data["store_id"])
        month = serializer.validated_data["month"]
        year = serializer.validated_data["year"]

        period, err = create_or_update_payroll_period(store, month, year, admin_user=request.user)
        if err:
            return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
        qs = self.get_queryset()
        annotated_period = qs.get(id=period.id)
        resp_serializer = self.get_serializer(annotated_period)
        return Response(resp_serializer.data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        period = self.get_object()
        success, err = delete_payroll_period(period.id)
        if err:
            return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        period = self.get_object()
        confirmed_period, err = confirm_payroll_period(period.id, admin_user=request.user)
        if err:
            return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
        qs = self.get_queryset()
        annotated_period = qs.get(id=confirmed_period.id)
        return Response(self.get_serializer(annotated_period).data)

    @action(detail=True, methods=["post"])
    def pay(self, request, pk=None):
        period = self.get_object()
        paid_period, err = pay_payroll_period(period.id, admin_user=request.user)
        if err:
            return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
        qs = self.get_queryset()
        annotated_period = qs.get(id=paid_period.id)
        return Response(self.get_serializer(annotated_period).data)

    @action(detail=True, methods=["post"])
    def recalculate(self, request, pk=None):
        period = self.get_object()
        recalculated_period, err = create_or_update_payroll_period(
            period.store, period.month, period.year, admin_user=request.user
        )
        if err:
            return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
        qs = self.get_queryset()
        annotated_period = qs.get(id=recalculated_period.id)
        return Response(self.get_serializer(annotated_period).data)

    @action(detail=True, methods=["get"], url_path="export-csv")
    def export_csv(self, request, pk=None):
        period = self.get_object()
        details = period.details.select_related("user__profile").all()

        response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
        response["Content-Disposition"] = f'attachment; filename="payroll_{period.store.id}_{period.month}_{period.year}.csv"'

        writer = csv.writer(response)
        writer.writerow([
            "Mã NV", "Họ và tên", "Số điện thoại", "Ngân hàng", "Số tài khoản",
            "Số ca", "Tổng số giờ", "Lương cơ bản/giờ", "Lương chính",
            "Thưởng", "Khấu trừ", "Thực lĩnh", "Ghi chú",
            "Đi muộn (lần)", "Về sớm (lần)", "Quên check-out (lần)"
        ])

        for d in details:
            bank_name = d.user.profile.bank_name if hasattr(d.user, "profile") else ""
            bank_account = d.user.profile.bank_account if hasattr(d.user, "profile") else ""
            writer.writerow([
                d.user.id, d.user.full_name, d.user.phone, bank_name, bank_account,
                d.total_shifts, d.total_hours, d.hourly_rate, d.gross_amount,
                d.bonus, d.deduction, d.net_amount, d.note,
                d.late_count, d.early_leave_count, d.missing_checkout_count
            ])

        return response


class PayrollDetailViewSet(viewsets.GenericViewSet):
    """
    Quản lý cập nhật chi tiết phiếu lương (thưởng, phạt, ghi chú) (Dành cho Admin).
    """
    permission_classes = [IsAuthenticated, IsAdminRole]
    queryset = PayrollDetail.objects.select_related("period", "user").all()
    serializer_class = PayrollDetailUpdateSerializer

    def update(self, request, *args, **kwargs):
        return self.partial_update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        detail = self.get_object()
        serializer = self.get_serializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        bonus = serializer.validated_data.get("bonus", detail.bonus)
        deduction = serializer.validated_data.get("deduction", detail.deduction)
        note = serializer.validated_data.get("note", detail.note)

        updated_detail, err = update_payroll_detail(detail.id, bonus=bonus, deduction=deduction, note=note)
        if err:
            return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
        return Response(PayrollDetailSerializer(updated_detail).data)


class MyPayslipsView(APIView):
    """
    Danh sách các phiếu lương của nhân viên đang đăng nhập.
    GET /api/payroll/my-payslips/
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        payslips = get_user_payslips(request.user)
        return Response(PayrollDetailSerializer(payslips, many=True).data)
