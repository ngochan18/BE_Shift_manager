from datetime import date

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdminRole
from stores.models import Store

from .serializers import EstimatedSalarySerializer, PayrollPreviewItemSerializer
from .services import tinh_luong_tong_hop, tinh_luong_ca_nhan


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
