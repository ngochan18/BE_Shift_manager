import uuid
import math
from datetime import datetime, timedelta

from django.utils import timezone
from django.db import transaction, IntegrityError

from shifts.models import Shift, ShiftAssignment
from .models import QRToken, CheckInRecord
from notifications.services import NotificationService


QR_VALID_MINUTES = 2           # Mỗi QR chỉ có hiệu lực trong 2 phút
CHECKIN_EARLY_MINUTES = 15     # Cho phép check-in sớm tối đa 15 phút
CHECKOUT_MAX_HOURS_AFTER_END = 2  # Cho phép check-out muộn tối đa 2 giờ sau khi ca kết thúc


def _haversine_meters(lat1, lon1, lat2, lon2):
    """Tính khoảng cách (mét) giữa 2 toạ độ GPS bằng công thức Haversine."""
    R = 6_371_000  # bán kính Trái Đất (m)
    phi1, phi2 = math.radians(float(lat1)), math.radians(float(lat2))
    dphi = math.radians(float(lat2) - float(lat1))
    dlambda = math.radians(float(lon2) - float(lon1))
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return int(2 * R * math.asin(math.sqrt(a)))


class AttendanceService:

    @staticmethod
    def generate_qr_token(shift: Shift, admin_user):
        now = timezone.now()
        expires_at = now + timedelta(minutes=QR_VALID_MINUTES)
        token_str = str(uuid.uuid4())

        try:
            qr = QRToken.objects.create(
                shift=shift,
                token=token_str,
                expires_at=expires_at,
            )
            return qr, None
        except Exception as e:
            return None, f"Lỗi hệ thống khi tạo QR: {str(e)}"

    @staticmethod
    def list_qr_tokens(shift: Shift):
        return QRToken.objects.filter(shift=shift).order_by("-generated_at")

    @staticmethod
    def checkin(user, token_str: str, latitude, longitude):
        try:
            with transaction.atomic():
                try:
                    qr = QRToken.objects.select_related("shift__store").get(token=token_str)
                except QRToken.DoesNotExist:
                    return None, "QR không hợp lệ."

                if qr.expires_at < timezone.now():
                    return None, "QR đã hết hạn. Vui lòng yêu cầu Admin tạo QR mới."

                shift = qr.shift
                store = shift.store

                # Kiểm tra phân công
                has_assignment = ShiftAssignment.objects.filter(
                    shift=shift,
                    user=user,
                    status=ShiftAssignment.Status.CONFIRMED,
                ).exists()
                if not has_assignment:
                    return None, "Bạn không được phân công vào ca này."

                now = timezone.now()
                shift_start_dt = timezone.make_aware(
                    datetime.combine(shift.date, shift.start_time)
                )
                earliest_checkin = shift_start_dt - timedelta(minutes=CHECKIN_EARLY_MINUTES)
                if now < earliest_checkin:
                    minutes_left = int((earliest_checkin - now).total_seconds() // 60)
                    return None, f"Chưa đến giờ check-in (còn {minutes_left} phút nữa mới được check-in)."

                shift_end_dt = timezone.make_aware(
                    datetime.combine(shift.date, shift.end_time)
                )
                if now > shift_end_dt:
                    return None, "Ca làm việc đã kết thúc, không thể check-in."

                try:
                    record = CheckInRecord.objects.select_for_update().get(shift=shift, user=user)
                    if record.checkin_at:
                        return None, "Bạn đã check-in ca này rồi."
                except CheckInRecord.DoesNotExist:
                    record = None

                #  Tính số phút đi trễ
                late_minutes = max(0, int((now - shift_start_dt).total_seconds() // 60))

                #  GPS
                distance = _haversine_meters(
                    latitude, longitude,
                    store.geo_latitude, store.geo_longitude,
                )
                geo_valid = distance <= store.geo_radius_meters

                if not geo_valid:
                    return None, (
                        f"Vị trí của bạn quá xa cửa hàng ({distance}m, giới hạn {store.geo_radius_meters}m). "
                        f"Vui lòng đến gần cửa hàng để check-in."
                    )

                if record is None:
                    try:
                        record = CheckInRecord.objects.create(
                            shift=shift,
                            user=user,
                            checkin_at=now,
                            checkin_lat=latitude,
                            checkin_lng=longitude,
                            checkin_distance_meters=distance,
                            qr_token_used=token_str,
                            checkin_method="qr_gps",
                            geo_valid=geo_valid,
                            late_minutes=late_minutes,
                        )
                    except IntegrityError:
                        return None, "Bạn đã check-in ca này rồi."

                NotificationService.notify_checkin_success(user, shift, late_minutes)
                return record, None

        except Exception as e:
            return None, f"Lỗi hệ thống: {str(e)}"

    @staticmethod
    def checkout(user, shift_id: int, note: str = ""):
        try:
            with transaction.atomic():
                try:
                    record = CheckInRecord.objects.select_related("shift").select_for_update().get(
                        shift_id=shift_id, user=user
                    )
                except CheckInRecord.DoesNotExist:
                    if not Shift.objects.filter(pk=shift_id).exists():
                        return None, "Không tìm thấy ca làm việc."
                    return None, "Bạn chưa check-in ca này."

                if not record.checkin_at:
                    return None, "Bạn chưa check-in ca này."

                if record.checkout_at:
                    return None, "Bạn đã check-out ca này rồi."

                now = timezone.now()

                shift = record.shift
                shift_end_dt = timezone.make_aware(
                    datetime.combine(shift.date, shift.end_time)
                )
                max_checkout_dt = shift_end_dt + timedelta(hours=CHECKOUT_MAX_HOURS_AFTER_END)
                if now > max_checkout_dt:
                    return None, (
                        f"Đã quá thời gian cho phép check-out "
                        f"(tối đa {CHECKOUT_MAX_HOURS_AFTER_END} giờ sau khi ca kết thúc)."
                    )

                worked_minutes = max(0, int((now - record.checkin_at).total_seconds() // 60))

                record.checkout_at = now
                record.worked_minutes = worked_minutes

                update_fields = ["checkout_at", "worked_minutes"]
                if note:
                    record.note = note
                    update_fields.append("note")
                record.save(update_fields=update_fields)

                return record, None

        except Exception as e:
            return None, f"Lỗi hệ thống: {str(e)}"

    @staticmethod
    def get_records_for_shift(shift: Shift):
        return CheckInRecord.objects.filter(shift=shift).select_related("user", "shift__store")

    @staticmethod
    def get_records_for_user(user, shift_date_from=None, shift_date_to=None):
        """Lấy lịch sử check-in của một nhân viên (có thể lọc theo khoảng ngày)."""
        qs = CheckInRecord.objects.filter(user=user).select_related("shift__store")
        if shift_date_from:
            qs = qs.filter(shift__date__gte=shift_date_from)
        if shift_date_to:
            qs = qs.filter(shift__date__lte=shift_date_to)
        return qs.order_by("-shift__date", "-checkin_at")
