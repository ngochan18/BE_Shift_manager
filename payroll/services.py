from decimal import Decimal
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.timezone import make_aware

from attendance.models import CheckInRecord
from accounts.models import EmployeeProfile
from payroll.models import PayrollPeriod, PayrollDetail
from notifications.services import NotificationService

PHUT_CHAM_CHUOC = 5  # Châm chước đi muộn hoặc về sớm tối đa 5 phút vẫn tính trọn giờ


def _tinh_phut_huong_luong(cham_cong) -> int:
    """
    Tính số phút được hưởng lương của một bản ghi chấm công.
    """
    ca = cham_cong.shift
    if not cham_cong.checkin_at or not cham_cong.checkout_at:
        return cham_cong.worked_minutes or 0

    dau_ca = make_aware(datetime.combine(ca.date, ca.start_time))
    cuoi_ca = make_aware(datetime.combine(ca.date, ca.end_time))
    # Nếu ca làm việc xuyên đêm (ví dụ 22:00 - 06:00), cuối ca thuộc ngày hôm sau
    if ca.end_time < ca.start_time:
        cuoi_ca = make_aware(datetime.combine(ca.date, ca.end_time)) + timedelta(days=1)

    do_dai_ca = int((cuoi_ca - dau_ca).total_seconds() // 60)

    # Giờ bắt đầu tính lương: Nếu đến muộn <= 5 phút, vẫn tính từ đầu ca
    muon_giay = (cham_cong.checkin_at - dau_ca).total_seconds()
    bat_dau_tinh = dau_ca if muon_giay <= PHUT_CHAM_CHUOC * 60 else cham_cong.checkin_at

    # Giờ kết thúc tính lương: Nếu về sớm <= 5 phút, vẫn tính đủ đến cuối ca.
    # Không tính lương cho thời gian tự ý nán lại sau khi hết ca (checkout_at > cuoi_ca)
    if cham_cong.checkout_at > cuoi_ca:
        ket_thuc_tinh = cuoi_ca
    else:
        som_giay = (cuoi_ca - cham_cong.checkout_at).total_seconds()
        ket_thuc_tinh = cuoi_ca if 0 <= som_giay <= PHUT_CHAM_CHUOC * 60 else cham_cong.checkout_at

    phut = max(0, int((ket_thuc_tinh - bat_dau_tinh).total_seconds() // 60))
    return min(phut, do_dai_ca)


def tinh_luong_tong_hop(store, thang, nam):
    """Tính lương tạm tính cho tất cả nhân viên tại store trong tháng/năm."""
    ds_cham_cong = (
        CheckInRecord.objects
        .filter(
            shift__store=store,
            shift__date__year=nam,
            shift__date__month=thang,
            checkin_at__isnull=False,
        )
        .select_related("user", "user__profile", "shift")
    )

    nhan_vien = {}
    for cc in ds_cham_cong:
        uid = cc.user_id
        if uid not in nhan_vien:
            profile = getattr(cc.user, "profile", None)
            nhan_vien[uid] = {
                "user": cc.user,
                "luong_gio": profile.hourly_rate if profile else Decimal("0"),
                "so_ca": 0,
                "tong_phut": 0,
                "thanh_tien": Decimal("0"),
                "di_muon_count": 0,
                "ve_som_count": 0,
                "quen_checkout_count": 0,
            }
        phut = _tinh_phut_huong_luong(cc)
        he_so = Decimal(str(cc.shift.salary_multiplier or 1))
        luong_gio = nhan_vien[uid]["luong_gio"]
        nhan_vien[uid]["so_ca"] += 1
        nhan_vien[uid]["tong_phut"] += phut
        nhan_vien[uid]["thanh_tien"] += Decimal(phut) / Decimal("60") * luong_gio * he_so

        if cc.late_minutes > PHUT_CHAM_CHUOC:
            nhan_vien[uid]["di_muon_count"] += 1

        if not cc.checkout_at or cc.auto_checkout:
            nhan_vien[uid]["quen_checkout_count"] += 1
        else:
            ca = cc.shift
            cuoi_ca = make_aware(datetime.combine(ca.date, ca.end_time))
            if ca.end_time < ca.start_time:
                cuoi_ca += timedelta(days=1)
            som_giay = (cuoi_ca - cc.checkout_at).total_seconds()
            if som_giay > PHUT_CHAM_CHUOC * 60:
                nhan_vien[uid]["ve_som_count"] += 1

    ket_qua = []
    for nv in nhan_vien.values():
        nv["tong_gio"] = round(Decimal(nv["tong_phut"]) / Decimal("60"), 2)
        nv["thanh_tien"] = round(nv["thanh_tien"], 2)
        ket_qua.append(nv)

    ket_qua.sort(key=lambda x: x["user"].full_name)
    return ket_qua


def tinh_luong_ca_nhan(nhan_vien, thang, nam):
    """Tính thu nhập ước tính cho một nhân viên."""
    ds_cham_cong = (
        CheckInRecord.objects
        .filter(
            user=nhan_vien,
            shift__date__year=nam,
            shift__date__month=thang,
            checkin_at__isnull=False,
        )
        .select_related("shift")
    )

    profile = EmployeeProfile.objects.filter(user=nhan_vien).first()
    luong_gio = profile.hourly_rate if profile else Decimal("0")

    so_ca = 0
    tong_phut = 0
    thanh_tien = Decimal("0")
    late_count = 0
    early_leave_count = 0
    missing_checkout_count = 0

    for cc in ds_cham_cong:
        phut = _tinh_phut_huong_luong(cc)
        he_so = Decimal(str(cc.shift.salary_multiplier or 1))
        so_ca += 1
        tong_phut += phut
        thanh_tien += Decimal(phut) / Decimal("60") * luong_gio * he_so

        if cc.late_minutes > PHUT_CHAM_CHUOC:
            late_count += 1

        if not cc.checkout_at or cc.auto_checkout:
            missing_checkout_count += 1
        else:
            ca = cc.shift
            cuoi_ca = make_aware(datetime.combine(ca.date, ca.end_time))
            if ca.end_time < ca.start_time:
                cuoi_ca += timedelta(days=1)
            som_giay = (cuoi_ca - cc.checkout_at).total_seconds()
            if som_giay > PHUT_CHAM_CHUOC * 60:
                early_leave_count += 1

    return {
        "thang": thang,
        "nam": nam,
        "so_ca": so_ca,
        "tong_gio": round(Decimal(tong_phut) / Decimal("60"), 2),
        "luong_gio": luong_gio,
        "thu_nhap_uoc_tinh": round(thanh_tien, 2),
        "di_muon_count": late_count,
        "ve_som_count": early_leave_count,
        "quen_checkout_count": missing_checkout_count,
    }



def create_or_update_payroll_period(store, thang, nam, admin_user):
    """
    Tạo mới hoặc tính toán lại kỳ lương cho cửa hàng trong tháng/năm.
    Nếu kỳ lương đã ở trạng thái Đã chốt hoặc Đã thanh toán thì không cho phép tính lại.
    """
    with transaction.atomic():
        period = PayrollPeriod.objects.filter(store=store, month=thang, year=nam).first()
        if period:
            if period.status != PayrollPeriod.Status.DRAFT:
                return None, f"Bảng lương tháng {thang}/{nam} đã chốt hoặc thanh toán, không thể tính toán lại."
            period.created_by = admin_user
            period.save(update_fields=["created_by"])
        else:
            period = PayrollPeriod.objects.create(
                store=store,
                month=thang,
                year=nam,
                created_by=admin_user,
                status=PayrollPeriod.Status.DRAFT
            )

        # Lấy danh sách chấm công
        ds_cham_cong = (
            CheckInRecord.objects
            .filter(
                shift__store=store,
                shift__date__year=nam,
                shift__date__month=thang,
                checkin_at__isnull=False,
            )
            .select_related("user", "user__profile", "shift")
        )

        nhan_vien_data = {}
        for cc in ds_cham_cong:
            uid = cc.user_id
            if uid not in nhan_vien_data:
                profile = getattr(cc.user, "profile", None)
                nhan_vien_data[uid] = {
                    "user": cc.user,
                    "luong_gio": profile.hourly_rate if profile else Decimal("0"),
                    "so_ca": 0,
                    "tong_phut": 0,
                    "gross_amount": Decimal("0"),
                    "late_count": 0,
                    "early_leave_count": 0,
                    "missing_checkout_count": 0,
                }
            phut = _tinh_phut_huong_luong(cc)
            he_so = Decimal(str(cc.shift.salary_multiplier or 1))
            luong_gio = nhan_vien_data[uid]["luong_gio"]
            nhan_vien_data[uid]["so_ca"] += 1
            nhan_vien_data[uid]["tong_phut"] += phut
            nhan_vien_data[uid]["gross_amount"] += Decimal(phut) / Decimal("60") * luong_gio * he_so

            if cc.late_minutes > PHUT_CHAM_CHUOC:
                nhan_vien_data[uid]["late_count"] += 1

            if not cc.checkout_at or cc.auto_checkout:
                nhan_vien_data[uid]["missing_checkout_count"] += 1
            else:
                ca = cc.shift
                cuoi_ca = make_aware(datetime.combine(ca.date, ca.end_time))
                if ca.end_time < ca.start_time:
                    cuoi_ca += timedelta(days=1)
                som_giay = (cuoi_ca - cc.checkout_at).total_seconds()
                if som_giay > PHUT_CHAM_CHUOC * 60:
                    nhan_vien_data[uid]["early_leave_count"] += 1

        # Lấy danh sách chi tiết hiện có để duy trì thưởng, phạt, ghi chú
        existing_details = {d.user_id: d for d in period.details.all()}
        current_user_ids = set(nhan_vien_data.keys())

        # Xóa các chi tiết của nhân viên không còn bản ghi chấm công
        for uid, detail in list(existing_details.items()):
            if uid not in current_user_ids:
                detail.delete()
                del existing_details[uid]

        for uid, data in nhan_vien_data.items():
            tong_gio = round(Decimal(data["tong_phut"]) / Decimal("60"), 2)
            gross = round(data["gross_amount"], 2)

            if uid in existing_details:
                detail = existing_details[uid]
                detail.total_shifts = data["so_ca"]
                detail.total_hours = tong_gio
                detail.hourly_rate = data["luong_gio"]
                detail.gross_amount = gross
                detail.late_count = data["late_count"]
                detail.early_leave_count = data["early_leave_count"]
                detail.missing_checkout_count = data["missing_checkout_count"]
                detail.net_amount = gross + detail.bonus - detail.deduction
                detail.save()
            else:
                PayrollDetail.objects.create(
                    period=period,
                    user=data["user"],
                    total_shifts=data["so_ca"],
                    total_hours=tong_gio,
                    hourly_rate=data["luong_gio"],
                    gross_amount=gross,
                    bonus=Decimal("0"),
                    deduction=Decimal("0"),
                    late_count=data["late_count"],
                    early_leave_count=data["early_leave_count"],
                    missing_checkout_count=data["missing_checkout_count"],
                    net_amount=gross,
                )

        return period, None


def update_payroll_detail(detail_id, bonus=None, deduction=None, note=None):
    """Cập nhật các khoản thưởng, phạt và ghi chú cho chi tiết bảng lương khi còn ở trạng thái Nháp."""
    with transaction.atomic():
        try:
            detail = PayrollDetail.objects.select_related("period").select_for_update().get(pk=detail_id)
        except PayrollDetail.DoesNotExist:
            return None, "Không tìm thấy chi tiết bảng lương."

        if detail.period.status != PayrollPeriod.Status.DRAFT:
            return None, "Chỉ được phép chỉnh sửa chi tiết khi bảng lương ở trạng thái Nháp."

        if bonus is not None:
            detail.bonus = Decimal(str(bonus))
        if deduction is not None:
            detail.deduction = Decimal(str(deduction))
        if note is not None:
            detail.note = note

        detail.net_amount = detail.gross_amount + detail.bonus - detail.deduction
        detail.save()
        return detail, None


def confirm_payroll_period(period_id, admin_user):
    """Chốt bảng lương và gửi thông báo cho nhân viên."""
    with transaction.atomic():
        try:
            period = PayrollPeriod.objects.select_related("store").select_for_update().get(pk=period_id)
        except PayrollPeriod.DoesNotExist:
            return None, "Không tìm thấy kỳ lương."

        if period.status != PayrollPeriod.Status.DRAFT:
            return None, f"Kỳ lương hiện tại không ở trạng thái Nháp (Trạng thái: {period.get_status_display()})."

        period.status = PayrollPeriod.Status.CONFIRMED
        period.confirmed_at = timezone.now()
        period.save(update_fields=["status", "confirmed_at"])

        # Gửi thông báo
        details = period.details.select_related("user").all()
        for d in details:
            NotificationService.notify_payroll_confirmed(d.user, period, d.net_amount)

        return period, None


def pay_payroll_period(period_id, admin_user):
    """Thanh toán bảng lương và thông báo chuyển khoản thành công."""
    with transaction.atomic():
        try:
            period = PayrollPeriod.objects.select_related("store").select_for_update().get(pk=period_id)
        except PayrollPeriod.DoesNotExist:
            return None, "Không tìm thấy kỳ lương."

        if period.status != PayrollPeriod.Status.CONFIRMED:
            return None, f"Chỉ có thể thanh toán kỳ lương đã chốt (Trạng thái: {period.get_status_display()})."

        period.status = PayrollPeriod.Status.PAID
        period.paid_at = timezone.now()
        period.save(update_fields=["status", "paid_at"])

        # Gửi thông báo
        details = period.details.select_related("user").all()
        for d in details:
            NotificationService.notify_payroll_paid(d.user, period, d.net_amount)

        return period, None


def delete_payroll_period(period_id):
    """Xóa kỳ lương nháp."""
    with transaction.atomic():
        try:
            period = PayrollPeriod.objects.select_for_update().get(pk=period_id)
        except PayrollPeriod.DoesNotExist:
            return False, "Không tìm thấy kỳ lương."

        if period.status != PayrollPeriod.Status.DRAFT:
            return False, "Chỉ được phép xóa kỳ lương ở trạng thái Nháp."

        period.delete()
        return True, None


def get_user_payslips(user, year=None):
    """Lấy danh sách phiếu lương của nhân viên (đã chốt hoặc thanh toán)."""
    qs = PayrollDetail.objects.filter(
        user=user,
        period__status__in=[PayrollPeriod.Status.CONFIRMED, PayrollPeriod.Status.PAID]
    ).select_related("period__store").order_by("-period__year", "-period__month")
    if year:
        qs = qs.filter(period__year=year)
    return qs

