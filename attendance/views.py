from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from django.contrib.auth import get_user_model
from shifts.models import Shift
from .models import CheckInRecord
from .serializers import (
    QRTokenSerializer,
    CheckInSerializer,
    CheckOutSerializer,
    CheckInRecordSerializer,
)
from .services import AttendanceService

User = get_user_model()


def _is_admin_or_manager(user):
    return user.role in ("admin", "manager")


class QRTokenViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['post'], url_path='shift/(?P<shift_id>\d+)/generate')
    def generate_qr(self, request, shift_id=None):
        if not _is_admin_or_manager(request.user):
            return Response({"detail": "Chỉ Admin/Manager mới được tạo QR."}, status=status.HTTP_403_FORBIDDEN)

        try:
            shift = Shift.objects.select_related("store").get(pk=shift_id)
        except Shift.DoesNotExist:
            return Response({"detail": "Không tìm thấy ca làm việc."}, status=status.HTTP_404_NOT_FOUND)

        qr, error = AttendanceService.generate_qr_token(shift, request.user)
        if error:
            return Response({"detail": error}, status=status.HTTP_400_BAD_REQUEST)

        return Response(QRTokenSerializer(qr).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['get'], url_path='shift/(?P<shift_id>\d+)/list')
    def list_qr(self, request, shift_id=None):
        if not _is_admin_or_manager(request.user):
            return Response({"detail": "Chỉ Admin/Manager mới được xem QR."}, status=status.HTTP_403_FORBIDDEN)

        try:
            shift = Shift.objects.get(pk=shift_id)
        except Shift.DoesNotExist:
            return Response({"detail": "Không tìm thấy ca làm việc."}, status=status.HTTP_404_NOT_FOUND)

        tokens = AttendanceService.list_qr_tokens(shift)
        return Response(QRTokenSerializer(tokens, many=True).data)


class AttendanceRecordViewSet(viewsets.GenericViewSet):
    """
    ViewSet xử lý Check-in, Check-out và tra cứu lịch sử chấm công.
    """
    permission_classes = [IsAuthenticated]
    queryset = CheckInRecord.objects.all()
    serializer_class = CheckInRecordSerializer

    @action(detail=False, methods=['post'])
    def checkin(self, request):
        serializer = CheckInSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        record, message = AttendanceService.checkin(
            user=request.user,
            token_str=data["token"],
            latitude=data.get("latitude"),
            longitude=data.get("longitude"),
        )

        if record is None:
            return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)

        response_data = self.get_serializer(record).data
        if message:
            response_data["warning"] = message

        return Response(response_data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'])
    def checkout(self, request):
        serializer = CheckOutSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        record, error = AttendanceService.checkout(
            user=request.user,
            shift_id=data["shift_id"],
            note=data.get("note", ""),
        )

        if error:
            return Response({"detail": error}, status=status.HTTP_400_BAD_REQUEST)

        return Response(self.get_serializer(record).data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'])
    def me(self, request):
        """Lịch sử check-in cá nhân"""
        date_from = request.query_params.get("from")
        date_to = request.query_params.get("to")

        records = AttendanceService.get_records_for_user(
            user=request.user,
            shift_date_from=date_from,
            shift_date_to=date_to,
        )
        return Response(self.get_serializer(records, many=True).data)

    @action(detail=False, methods=['get'], url_path='shift/(?P<shift_id>\d+)')
    def shift_records(self, request, shift_id=None):
        """Danh sách đã check-in trong một ca làm việc (Admin)"""
        if not _is_admin_or_manager(request.user):
            return Response({"detail": "Không có quyền truy cập."}, status=status.HTTP_403_FORBIDDEN)

        try:
            shift = Shift.objects.get(pk=shift_id)
        except Shift.DoesNotExist:
            return Response({"detail": "Không tìm thấy ca làm việc."}, status=status.HTTP_404_NOT_FOUND)

        records = AttendanceService.get_records_for_shift(shift)
        
        return Response(self.get_serializer(records, many=True).data)

    @action(detail=False, methods=['get'], url_path=r'user/(?P<user_id>\d+)')
    def user_records(self, request, user_id=None):
        """Lịch sử đi làm của một nhân viên (Admin)"""
        if not _is_admin_or_manager(request.user):
            return Response({"detail": "Không có quyền truy cập."}, status=status.HTTP_403_FORBIDDEN)

        try:
            target_user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return Response({"detail": "Không tìm thấy nhân viên."}, status=status.HTTP_404_NOT_FOUND)

        date_from = request.query_params.get("from")
        date_to = request.query_params.get("to")

        records = AttendanceService.get_records_for_user(
            user=target_user,
            shift_date_from=date_from,
            shift_date_to=date_to,
        )
        return Response(self.get_serializer(records, many=True).data)

    @action(detail=False, methods=['post'], url_path='force-checkout-all')
    def force_checkout_all(self, request):
        """Ép checkout toàn bộ các ca làm việc đã kết thúc (Admin/Manager)"""
        if not _is_admin_or_manager(request.user):
            return Response({"detail": "Không có quyền truy cập."}, status=status.HTTP_403_FORBIDDEN)

        count = AttendanceService.force_checkout_all(request.user)
        return Response({
            "detail": f"Đã chốt thành công {count} ca làm việc chưa check-out.",
            "count": count
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='today')
    def today_records(self, request):
        """Danh sách check-in, check-out của toàn bộ nhân viên trong ngày hôm nay (Admin/Manager)"""
        if not _is_admin_or_manager(request.user):
            return Response({"detail": "Không có quyền truy cập."}, status=status.HTTP_403_FORBIDDEN)

        from zoneinfo import ZoneInfo
        from django.utils import timezone
        _VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
        now = timezone.now().astimezone(_VN_TZ)
        today = now.date()

        records = CheckInRecord.objects.filter(
            shift__date=today
        ).select_related('shift', 'user')

        return Response(self.get_serializer(records, many=True).data)

    @action(detail=False, methods=['get'], url_path='admin-stats')
    def admin_stats(self, request):
        """Thống kê báo cáo chấm công của nhân viên dành cho admin/manager"""
        if not _is_admin_or_manager(request.user):
            return Response({"detail": "Không có quyền truy cập."}, status=status.HTTP_403_FORBIDDEN)

        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        store_id = request.query_params.get("store_id")
        user_id = request.query_params.get("user_id")

        if store_id:
            try:
                store_id = int(store_id)
            except ValueError:
                store_id = None
        if user_id:
            try:
                user_id = int(user_id)
            except ValueError:
                user_id = None

        stats = AttendanceService.get_admin_stats(
            start_date=start_date,
            end_date=end_date,
            store_id=store_id,
            user_id=user_id
        )
        return Response(stats, status=status.HTTP_200_OK)

