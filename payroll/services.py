from decimal import Decimal
from datetime import datetime

from django.utils.timezone import make_aware

from attendance.models import CheckInRecord
from accounts.models import EmployeeProfile

PHUT_CHEKOUT_SOM = 5


def _tinh_phut_huong_luong(cham_cong) -> int:
    """
    Tính số phút được hưởng lương của một bản ghi chấm công.
    """
    ca = cham_cong.shift
    if not cham_cong.checkin_at or not cham_cong.checkout_at:
        return cham_cong.worked_minutes or 0

    dau_ca  = make_aware(datetime.combine(ca.date, ca.start_time))
    cuoi_ca = make_aware(datetime.combine(ca.date, ca.end_time))
    do_dai_ca = int((cuoi_ca - dau_ca).total_seconds() // 60)

    # Giờ bắt đầu tính lương
    muon_giay = (cham_cong.checkin_at - dau_ca).total_seconds()
    bat_dau_tinh = dau_ca if muon_giay <= PHUT_CHEKOUT_SOM * 60 else cham_cong.checkin_at

    # Giờ kết thúc tính lương
    som_giay = (cuoi_ca - cham_cong.checkout_at).total_seconds()
    ket_thuc_tinh = cuoi_ca if 0 <= som_giay <= PHUT_CHEKOUT_SOM * 60 else cham_cong.checkout_at

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
            checkout_at__isnull=False,
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
            }
        phut = _tinh_phut_huong_luong(cc)
        he_so = Decimal(str(cc.shift.salary_multiplier or 1))
        luong_gio = nhan_vien[uid]["luong_gio"]
        nhan_vien[uid]["so_ca"] += 1
        nhan_vien[uid]["tong_phut"] += phut
        nhan_vien[uid]["thanh_tien"] += Decimal(phut) / Decimal("60") * luong_gio * he_so

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
            checkout_at__isnull=False,
        )
        .select_related("shift")
    )

    profile = EmployeeProfile.objects.filter(user=nhan_vien).first()
    luong_gio = profile.hourly_rate if profile else Decimal("0")

    so_ca = 0
    tong_phut = 0
    thanh_tien = Decimal("0")

    for cc in ds_cham_cong:
        phut = _tinh_phut_huong_luong(cc)
        he_so = Decimal(str(cc.shift.salary_multiplier or 1))
        so_ca += 1
        tong_phut += phut
        thanh_tien += Decimal(phut) / Decimal("60") * luong_gio * he_so

    return {
        "thang": thang,
        "nam": nam,
        "so_ca": so_ca,
        "tong_gio": round(Decimal(tong_phut) / Decimal("60"), 2),
        "luong_gio": luong_gio,
        "thu_nhap_uoc_tinh": round(thanh_tien, 2),
    }
