import uuid
import math
from datetime import datetime, timedelta

from django.utils import timezone
from django.db import transaction, IntegrityError

from shifts.models import Shift, ShiftAssignment
from shifts.services import ShiftService
from .models import QRToken, CheckInRecord
from notifications.services import NotificationService
from zoneinfo import ZoneInfo
from datetime import date

QR_VALID_SECONDS = 30            # Mỗi QR chỉ có hiệu lực trong 30 giây
CHECKIN_EARLY_MINUTES = 15     # Cho phép check-in sớm tối đa 15 phút
CHECKOUT_MAX_MINUTES_AFTER_END = 30  # Cho phép check-out muộn tối đa 30 phút sau khi ca kết thúc


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
    def get_user_monthly_metrics(user):
        if not user:
            return None
        _VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
        now = timezone.now().astimezone(_VN_TZ)
        start_of_month = now.date().replace(day=1)
        if now.month == 12:
            end_of_month = date(now.year + 1, 1, 1) - timedelta(days=1)
        else:
            end_of_month = date(now.year, now.month + 1, 1) - timedelta(days=1)

        
        late_count = CheckInRecord.objects.filter(
            user=user,
            shift__date__range=[start_of_month, end_of_month],
            late_minutes__gt=5
        ).count()

        records = CheckInRecord.objects.filter(
            user=user,
            shift__date__range=[start_of_month, end_of_month],
            checkout_at__isnull=False
        ).select_related('shift')
        
        early_checkout_count = 0
        for r in records:
            shift = r.shift
            shift_end_dt = datetime.combine(shift.date, shift.end_time)
            if shift.end_time < shift.start_time:
                shift_end_dt += timedelta(days=1)
            shift_end_dt = timezone.make_aware(shift_end_dt, timezone=_VN_TZ)
            if r.checkout_at < shift_end_dt:
                if (shift_end_dt - r.checkout_at).total_seconds() > 300:
                    early_checkout_count += 1

        confirmed_assignments = ShiftAssignment.objects.filter(
            user=user,
            status=ShiftAssignment.Status.CONFIRMED,
            shift__date__range=[start_of_month, end_of_month]
        ).select_related('shift')

        absent_count = 0
        shift_ids = [a.shift.id for a in confirmed_assignments]
        checkins = {c.shift_id: c for c in CheckInRecord.objects.filter(user=user, shift_id__in=shift_ids)}

        for a in confirmed_assignments:
            shift = a.shift
            shift_end_dt = datetime.combine(shift.date, shift.end_time)
            if shift.end_time < shift.start_time:
                shift_end_dt += timedelta(days=1)
            shift_end_dt = timezone.make_aware(shift_end_dt, timezone=_VN_TZ)

            if shift_end_dt <= now:
                record = checkins.get(shift.id)
                if not record or not record.checkin_at:
                    absent_count += 1

        return {
            "late_count": late_count,
            "early_checkout_count": early_checkout_count,
            "absent_count": absent_count
        }

    @staticmethod
    def generate_qr_token(shift: Shift, admin_user):
        if ShiftService.is_shift_ended(shift):
            return None, "Ca làm việc này đã kết thúc, không thể tạo mã QR check-in."

        now = timezone.now()
        expires_at = now + timedelta(seconds=QR_VALID_SECONDS)
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

                record, created = CheckInRecord.objects.update_or_create(
                    shift=shift,
                    user=user,
                    defaults={
                        "checkin_at": now,
                        "checkin_lat": latitude,
                        "checkin_lng": longitude,
                        "checkin_distance_meters": distance,
                        "qr_token_used": token_str,
                        "checkin_method": "qr_gps",
                        "geo_valid": geo_valid,
                        "late_minutes": late_minutes,
                    },
                )

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
                if shift.end_time < shift.start_time:
                    shift_end_dt += timedelta(days=1)

                max_checkout_dt = shift_end_dt + timedelta(minutes=CHECKOUT_MAX_MINUTES_AFTER_END)
                if now > max_checkout_dt:
                    return None, (
                        f"Đã quá thời gian cho phép check-out "
                        f"(tối đa {CHECKOUT_MAX_MINUTES_AFTER_END} phút sau khi ca kết thúc)."
                    )

                # Tính công tối đa theo ca làm, không tính giờ dôi dư lúc check-out muộn
                effective_checkout_dt = min(now, shift_end_dt)
                worked_minutes = max(0, int((effective_checkout_dt - record.checkin_at).total_seconds() // 60))

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

    @staticmethod
    def force_checkout_all(admin_user):
        """
        Ép checkout toàn bộ các record chưa checkout có ca làm việc đã kết thúc.
        """
        now = timezone.now()
        pending_records = CheckInRecord.objects.filter(
            checkin_at__isnull=False,
            checkout_at__isnull=True
        ).select_related("shift")

        checked_out_count = 0
        with transaction.atomic():
            for record in pending_records:
                shift = record.shift
                shift_end_dt = timezone.make_aware(
                    datetime.combine(shift.date, shift.end_time)
                )
                if shift.end_time < shift.start_time:
                    shift_end_dt += timedelta(days=1)

                if now > shift_end_dt:
                    worked_minutes = max(0, int((shift_end_dt - record.checkin_at).total_seconds() // 60))
                    
                    record.checkout_at = shift_end_dt
                    record.worked_minutes = worked_minutes
                    record.auto_checkout = True
                    record.save(update_fields=["checkout_at", "worked_minutes", "auto_checkout"])
                    checked_out_count += 1
        
        return checked_out_count

    @staticmethod
    def get_admin_stats(start_date=None, end_date=None, store_id=None, user_id=None):
        """
        Tổng hợp thống kê công, đi muộn, về sớm, vắng mặt của toàn bộ nhân viên
        phục vụ giao diện Thống kê báo cáo của Admin.
        """
        from django.contrib.auth import get_user_model
        User = get_user_model()
        _VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
        now = timezone.now().astimezone(_VN_TZ)

        if start_date:
            try:
                start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
            except ValueError:
                start_dt = now.date().replace(day=1)
        else:
            start_dt = now.date().replace(day=1)

        if end_date:
            try:
                end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
            except ValueError:
                if now.month == 12:
                    end_dt = date(now.year + 1, 1, 1) - timedelta(days=1)
                else:
                    end_dt = date(now.year, now.month + 1, 1) - timedelta(days=1)
        else:
            if now.month == 12:
                end_dt = date(now.year + 1, 1, 1) - timedelta(days=1)
            else:
                end_dt = date(now.year, now.month + 1, 1) - timedelta(days=1)

        records_qs = CheckInRecord.objects.filter(
            shift__date__range=[start_dt, end_dt]
        ).select_related("shift", "user")

        if store_id:
            records_qs = records_qs.filter(shift__store_id=store_id)
        if user_id:
            records_qs = records_qs.filter(user_id=user_id)

        records = list(records_qs)

        assignments_qs = ShiftAssignment.objects.filter(
            status=ShiftAssignment.Status.CONFIRMED,
            shift__date__range=[start_dt, end_dt]
        ).select_related("shift")

        if store_id:
            assignments_qs = assignments_qs.filter(shift__store_id=store_id)
        if user_id:
            assignments_qs = assignments_qs.filter(user_id=user_id)

        assignments = list(assignments_qs)

        record_map = {(r.shift_id, r.user_id): r for r in records}

        total_shifts = len(records)
        total_worked_minutes = sum(r.worked_minutes for r in records if r.worked_minutes)
        total_worked_hours = round(total_worked_minutes / 60.0, 2)

        late_count = sum(1 for r in records if r.late_minutes > 5)

        early_checkout_count = 0
        for r in records:
            if not r.checkout_at:
                continue
            shift = r.shift
            shift_end_dt = datetime.combine(shift.date, shift.end_time)
            if shift.end_time < shift.start_time:
                shift_end_dt += timedelta(days=1)
            shift_end_dt = timezone.make_aware(shift_end_dt, timezone=_VN_TZ)
            if r.checkout_at < shift_end_dt:
                if (shift_end_dt - r.checkout_at).total_seconds() > 300:
                    early_checkout_count += 1

        # Vắng mặt
        absent_count = 0
        total_confirmed_ended = 0
        
        # Theo dõi vắng mặt theo ngày và theo nhân viên
        absent_assignments = []
        for a in assignments:
            shift = a.shift
            shift_end_dt = datetime.combine(shift.date, shift.end_time)
            if shift.end_time < shift.start_time:
                shift_end_dt += timedelta(days=1)
            shift_end_dt = timezone.make_aware(shift_end_dt, timezone=_VN_TZ)

            if shift_end_dt <= now:
                total_confirmed_ended += 1
                rec = record_map.get((shift.id, a.user_id))
                if not rec or not rec.checkin_at:
                    absent_count += 1
                    absent_assignments.append(a)

        # Tính toán tỷ lệ
        late_rate = round((late_count / total_shifts * 100.0) if total_shifts > 0 else 0.0, 1)
        early_checkout_rate = round((early_checkout_count / total_shifts * 100.0) if total_shifts > 0 else 0.0, 1)
        absent_rate = round((absent_count / total_confirmed_ended * 100.0) if total_confirmed_ended > 0 else 0.0, 1)

        # Tổng hợp
        summary = {
            "total_shifts": total_shifts,
            "total_worked_hours": total_worked_hours,
            "late_count": late_count,
            "early_checkout_count": early_checkout_count,
            "absent_count": absent_count,
            "late_rate": late_rate,
            "early_checkout_rate": early_checkout_rate,
            "absent_rate": absent_rate
        }

        daily_data = {}
        curr = start_dt
        while curr <= end_dt:
            daily_data[curr] = {
                "date": curr.strftime("%Y-%m-%d"),
                "worked_hours": 0.0,
                "late_count": 0,
                "early_checkout_count": 0,
                "absent_count": 0
            }
            curr += timedelta(days=1)

        # Thêm bản ghi vào thống kê ngày
        for r in records:
            d = r.shift.date
            if d in daily_data:
                daily_data[d]["worked_hours"] += (r.worked_minutes or 0) / 60.0
                if r.late_minutes > 5:
                    daily_data[d]["late_count"] += 1
                
                if r.checkout_at:
                    shift = r.shift
                    shift_end_dt = datetime.combine(shift.date, shift.end_time)
                    if shift.end_time < shift.start_time:
                        shift_end_dt += timedelta(days=1)
                    shift_end_dt = timezone.make_aware(shift_end_dt, timezone=_VN_TZ)
                    if r.checkout_at < shift_end_dt:
                        if (shift_end_dt - r.checkout_at).total_seconds() > 300:
                            daily_data[d]["early_checkout_count"] += 1

        # Thêm vắng mặt vào thống kê ngày
        for a in absent_assignments:
            d = a.shift.date
            if d in daily_data:
                daily_data[d]["absent_count"] += 1

        # Làm tròn giờ làm việc theo ngày
        for d in daily_data:
            daily_data[d]["worked_hours"] = round(daily_data[d]["worked_hours"], 2)

        sorted_daily = [daily_data[k] for k in sorted(daily_data.keys())]

        # 4. Thống kê theo nhân viên
        users_qs = User.objects.filter(role="employee")
        if user_id:
            users_qs = users_qs.filter(id=user_id)
        
        users_list = list(users_qs)
        employee_data = {}
        for u in users_list:
            employee_data[u.id] = {
                "id": u.id,
                "full_name": u.full_name,
                "phone": u.phone,
                "avatar_url": u.avatar_url,
                "total_shifts": 0,
                "total_worked_hours": 0.0,
                "late_count": 0,
                "early_checkout_count": 0,
                "absent_count": 0
            }

        # Thêm bản ghi vào thống kê nhân viên
        for r in records:
            uid = r.user_id
            if uid in employee_data:
                employee_data[uid]["total_shifts"] += 1
                employee_data[uid]["total_worked_hours"] += (r.worked_minutes or 0) / 60.0
                if r.late_minutes > 5:
                    employee_data[uid]["late_count"] += 1
                
                if r.checkout_at:
                    shift = r.shift
                    shift_end_dt = datetime.combine(shift.date, shift.end_time)
                    if shift.end_time < shift.start_time:
                        shift_end_dt += timedelta(days=1)
                    shift_end_dt = timezone.make_aware(shift_end_dt, timezone=_VN_TZ)
                    if r.checkout_at < shift_end_dt:
                        if (shift_end_dt - r.checkout_at).total_seconds() > 300:
                            employee_data[uid]["early_checkout_count"] += 1

        # Thêm vắng mặt vào thống kê nhân viên
        for a in absent_assignments:
            uid = a.user_id
            if uid in employee_data:
                employee_data[uid]["absent_count"] += 1

        # Làm tròn và dọn dẹp dữ liệu nhân viên
        for uid in employee_data:
            employee_data[uid]["total_worked_hours"] = round(employee_data[uid]["total_worked_hours"], 2)

        sorted_employees = sorted(employee_data.values(), key=lambda x: x["total_worked_hours"], reverse=True)

        return {
            "summary": summary,
            "daily_stats": sorted_daily,
            "employee_stats": sorted_employees
        }

