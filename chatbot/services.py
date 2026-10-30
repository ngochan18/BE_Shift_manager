import json
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

from google import genai
from google.genai import types

from attendance.models import CheckInRecord
from payroll.services import tinh_luong_ca_nhan
from shifts.models import Shift, ShiftAssignment, ShiftSwapRequest

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

SYSTEM_PROMPT = """Bạn là trợ lý ShiftMate AI, chỉ hỗ trợ các vấn đề liên quan đến công việc tại ShiftMate.

=== PHẠM VI HỖ TRỢ ===
Bạn CHỈ được phép trả lời các câu hỏi thuộc các chủ đề sau:
1. Lịch làm việc — xem ca, đăng ký ca trống
2. Chấm công — lịch sử check-in/check-out, đi muộn, về sớm
3. Bảng lương — số giờ làm, lương thực nhận theo kỳ
4. Thông tin cá nhân của chính nhân viên đang đăng nhập

=== TỪ CHỐI ===
Với bất kỳ câu hỏi nào KHÔNG thuộc phạm vi trên (ví dụ: thời tiết, nấu ăn, lập trình, tin tức, tư vấn cá nhân, v.v.), hãy trả lời đúng một câu:
"Tôi chỉ hỗ trợ các vấn đề liên quan đến ca làm việc, chấm công và lương. Bạn cần tra cứu điều gì về công việc không?"
Không giải thích thêm, không xin lỗi dài dòng.

=== CÔNG CỤ (chỉ trả về dữ liệu của người đang đăng nhập) ===
- getMySchedule: Xem lịch làm việc theo khoảng thời gian (mặc định: tuần này)
- getOpenShifts: Xem danh sách ca trống có thể đăng ký (mặc định: từ hôm nay)

- getAttendanceRecord: Tổng hợp lịch sử chấm công (mặc định: tháng này)
- getAttendanceLateDetail: Chi tiết đi muộn/về sớm từng ca (mặc định: tháng này)
- getMySwapRequests: Xem yêu cầu đổi/nhượng ca
- getEstimatedSalary: Xem thu nhập ước tính theo tháng/năm (mặc định: tháng hiện tại)
- getMyProfile: Xem thông tin cá nhân

=== QUY TẮC ===
- Trả lời ngắn gọn, thân thiện bằng tiếng Việt
- Hiển thị ngày theo định dạng dd/MM/yyyy
- Trình bày dữ liệu dạng danh sách có cấu trúc, KHÔNG hiện JSON thô
- KHÔNG hiển thị ID (id, shift_id) của ca làm trong câu trả lời — ID chỉ dùng nội bộ để gọi tool
- TUYỆT ĐỐI không cung cấp thông tin của nhân viên khác"""

def _s(t, **kw):
    return types.Schema(type=t, **kw)

TOOLS = [types.Tool(function_declarations=[
    types.FunctionDeclaration(
        name="getMySchedule",
        description="Lấy lịch làm việc của nhân viên hiện tại.",
        parameters=_s(types.Type.OBJECT, properties={
            "from_date": _s(types.Type.STRING, description="Ngày bắt đầu YYYY-MM-DD"),
            "to_date":   _s(types.Type.STRING, description="Ngày kết thúc YYYY-MM-DD"),
        }),
    ),
    types.FunctionDeclaration(
        name="getOpenShifts",
        description="Lấy danh sách ca trống mà nhân viên có thể đăng ký.",
        parameters=_s(types.Type.OBJECT, properties={
            "from_date": _s(types.Type.STRING, description="Ngày bắt đầu YYYY-MM-DD"),
            "to_date":   _s(types.Type.STRING, description="Ngày kết thúc YYYY-MM-DD"),
        }),
    ),
    types.FunctionDeclaration(
        name="getAttendanceRecord",
        description="Lấy lịch sử chấm công (check-in/check-out) của nhân viên.",
        parameters=_s(types.Type.OBJECT, properties={
            "from_date": _s(types.Type.STRING, description="Ngày bắt đầu YYYY-MM-DD"),
            "to_date":   _s(types.Type.STRING, description="Ngày kết thúc YYYY-MM-DD"),
        }),
    ),
    types.FunctionDeclaration(
        name="getAttendanceLateDetail",
        description=(
            "Liệt kê chi tiết từng ca: giờ ca quy định, giờ check-in thực tế, "
            "số phút đi muộn, giờ check-out, số phút về sớm. "
            "Mặc định chỉ hiện các ca có đi muộn hoặc về sớm."
        ),
        parameters=_s(types.Type.OBJECT, properties={
            "from_date": _s(types.Type.STRING, description="Ngày bắt đầu YYYY-MM-DD"),
            "to_date":   _s(types.Type.STRING, description="Ngày kết thúc YYYY-MM-DD"),
            "only_late": _s(types.Type.BOOLEAN, description="true = chỉ hiện ca đi muộn/về sớm (mặc định). false = hiện tất cả ca."),
        }),
    ),
    types.FunctionDeclaration(
        name="getMySwapRequests",
        description="Lấy danh sách yêu cầu đổi/nhượng ca của nhân viên (bao gồm cả yêu cầu người khác gửi cho mình).",
        parameters=_s(types.Type.OBJECT, properties={
            "status": _s(types.Type.STRING, description="Lọc theo trạng thái: pending, partner_accepted, partner_rejected, accepted, rejected, cancelled, expired. Để trống để lấy tất cả."),
        }),
    ),
    types.FunctionDeclaration(
        name="getEstimatedSalary",
        description="Xem thu nhập ước tính theo tháng/năm: số ca, tổng giờ, lương theo giờ, thành tiền. Mặc định: tháng hiện tại.",
        parameters=_s(types.Type.OBJECT, properties={
            "month": _s(types.Type.INTEGER, description="Tháng (1-12). Mặc định: tháng hiện tại."),
            "year":  _s(types.Type.INTEGER, description="Năm. Mặc định: năm hiện tại."),
        }),
    ),
    types.FunctionDeclaration(
        name="getMyProfile",
        description="Lấy thông tin cá nhân của nhân viên đang đăng nhập.",
        parameters=_s(types.Type.OBJECT, properties={}),
    ),
])]

# ─── Date helpers ─────────────────────────────────────────────────────────────

def _today_vn() -> date:
    return datetime.now(tz=_VN_TZ).date()

def _to_vn(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = datetime.combine(dt.date(), dt.time(), tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(_VN_TZ)

def _fmt_date(d):
    if not d:
        return ""
    days = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
    try:
        weekday_str = days[d.weekday()]
        return f"{d.strftime('%d/%m/%Y')} ({weekday_str})"
    except AttributeError:
        return d.strftime("%d/%m/%Y")

def _fmt_time(t):
    return t.strftime("%H:%M") if t else ""

def _fmt_dt(dt):
    """Format datetime sang giờ Việt Nam."""
    vn = _to_vn(dt)
    return vn.strftime("%H:%M") if vn else ""

def _fmt_dt_full(dt):
    """Format datetime đầy đủ sang giờ Việt Nam."""
    vn = _to_vn(dt)
    return vn.strftime("%d/%m/%Y %H:%M") if vn else ""

def _week_range():
    today = _today_vn()
    monday = today - timedelta(days=today.weekday())
    return monday, monday + timedelta(days=6)

def _month_range():
    today = _today_vn()
    return today.replace(day=1), today

def _parse_date(s, fallback):
    if not s:
        return fallback
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return fallback

# ─── Tool ─────────────────────────────────────────────────────

def get_my_schedule(user, args):
    monday, sunday = _week_range()
    from_date = _parse_date(args.get("from_date"), monday)
    to_date   = _parse_date(args.get("to_date"),   sunday)

    assignments = (
        ShiftAssignment.objects
        .filter(user=user, status="confirmed", shift__date__range=[from_date, to_date])
        .select_related("shift", "shift__store")
        .order_by("shift__date", "shift__start_time")
    )

    if not assignments.exists():
        return {"message": "Không có ca làm nào trong khoảng thời gian này.", "ca_lam": []}

    ca_lam = [
        {
            "id": a.shift.id,
            "ngay": _fmt_date(a.shift.date),
            "ca": f"{_fmt_time(a.shift.start_time)} - {_fmt_time(a.shift.end_time)}",
            "cua_hang": a.shift.store.name if a.shift.store else "",
            "dia_chi": getattr(a.shift.store, "address", ""),
            "ghi_chu": a.shift.note or "",
        }
        for a in assignments
    ]
    return {"tong_so": len(ca_lam), "ca_lam": ca_lam}


def get_open_shifts(args):
    today = _today_vn()
    from_date = _parse_date(args.get("from_date"), today)
    to_date   = _parse_date(args.get("to_date"),   None)

    qs = Shift.objects.filter(is_open=True, date__gte=from_date).select_related("store")
    if to_date:
        qs = qs.filter(date__lte=to_date)
    qs = qs.order_by("date", "start_time")

    if not qs.exists():
        return {"message": "Hiện không có ca trống nào.", "ca_trong": []}

    ca_trong = []
    for s in qs:
        da_dk = s.assignments.filter(status__in=["confirmed", "pending"]).count()
        ca_trong.append({
            "id": s.id,
            "ngay": _fmt_date(s.date),
            "ca": f"{_fmt_time(s.start_time)} - {_fmt_time(s.end_time)}",
            "cua_hang": s.store.name if s.store else "",
            "so_nguoi_toi_da": s.max_workers,
            "da_dang_ky": da_dk,
            "con_cho": max(0, s.max_workers - da_dk),
            "khan_cap": "Có" if s.is_emergency else "Không",
            "he_so_luong": str(s.salary_multiplier),
            "ghi_chu": s.note or "",
        })
    return {"tong_so": len(ca_trong), "ca_trong": ca_trong}

def get_attendance_record(user, args):
    first, today = _month_range()
    from_date = _parse_date(args.get("from_date"), first)
    to_date   = _parse_date(args.get("to_date"),   today)

    records = (
        CheckInRecord.objects
        .filter(user=user, shift__date__range=[from_date, to_date])
        .select_related("shift", "shift__store")
        .order_by("shift__date")
    )

    if not records.exists():
        return {"message": "Không có dữ liệu chấm công trong khoảng thời gian này.", "chi_tiet": []}

    chi_tiet = []
    tong_phut = 0
    so_lan_muon = 0
    for r in records:
        phut = r.worked_minutes or 0
        tong_phut += phut
        muon = r.late_minutes > 0
        if muon:
            so_lan_muon += 1
        chi_tiet.append({
            "ngay": _fmt_date(r.shift.date),
            "ca": f"{_fmt_time(r.shift.start_time)} - {_fmt_time(r.shift.end_time)}",
            "cua_hang": r.shift.store.name if r.shift.store else "",
            "check_in":  _fmt_dt(r.checkin_at)  if r.checkin_at  else "Chưa check-in",
            "check_out": _fmt_dt(r.checkout_at) if r.checkout_at else "Chưa check-out",
            "so_gio_lam": f"{phut // 60}g{phut % 60:02d}p",
            "di_muon": f"Có ({r.late_minutes} phút)" if muon else "Không",
            "ghi_chu": r.note or "",
        })

    return {
        "tong_so": len(chi_tiet),
        "tong_gio_lam": f"{tong_phut // 60} giờ {tong_phut % 60} phút",
        "so_lan_di_muon": so_lan_muon,
        "chi_tiet": chi_tiet,
    }


_SWAP_STATUS_LABEL = {
    "pending":          "Chờ đối tác xác nhận",
    "partner_accepted": "Đối tác đã đồng ý, chờ Admin duyệt",
    "partner_rejected": "Đối tác từ chối",
    "accepted":         "Admin đã duyệt",
    "rejected":         "Admin từ chối",
    "cancelled":        "Đã hủy",
    "expired":          "Hết hạn",
}

_SWAP_TYPE_LABEL = {
    "claim":    "Tranh ca",
    "exchange": "Đổi ca",
    "transfer": "Nhượng ca",
}


def get_my_swap_requests(user, args):
    status_filter = args.get("status", "").strip()

    qs = (
        ShiftSwapRequest.objects
        .filter(requester=user) | ShiftSwapRequest.objects.filter(partner=user)
    ).select_related(
        "requester", "partner",
        "target_shift", "target_shift__store",
        "offered_shift", "offered_shift__store",
    ).order_by("-created_at")

    if status_filter:
        qs = qs.filter(status=status_filter)

    if not qs.exists():
        return {"message": "Không có yêu cầu đổi ca nào.", "yeu_cau": []}

    yeu_cau = []
    for r in qs:
        ts = r.target_shift
        os_ = r.offered_shift
        yeu_cau.append({
            "id": r.id,
            "loai": _SWAP_TYPE_LABEL.get(r.swap_type, r.swap_type),
            "vai_tro": "Người gửi" if r.requester_id == user.id else "Đối tác",
            "nguoi_gui": r.requester.full_name,
            "doi_tac": r.partner.full_name if r.partner else "—",
            "ca_muon": f"{_fmt_date(ts.date)} {_fmt_time(ts.start_time)}–{_fmt_time(ts.end_time)}" if ts else "—",
            "ca_doi": f"{_fmt_date(os_.date)} {_fmt_time(os_.start_time)}–{_fmt_time(os_.end_time)}" if os_ else "—",
            "trang_thai": _SWAP_STATUS_LABEL.get(r.status, r.status),
            "tin_nhan": r.message or "",
            "het_han": _fmt_dt_full(r.expires_at) if r.expires_at else "",
        })
    return {"tong_so": len(yeu_cau), "yeu_cau": yeu_cau}



def get_estimated_salary(user, args):
    today = _today_vn()
    user_specified = args.get("month") or args.get("year")
    month = int(args.get("month") or today.month)
    year  = int(args.get("year")  or today.year)

    data = tinh_luong_ca_nhan(user, month, year)

    # Nếu tháng hiện tại chưa có dữ liệu và người dùng không chỉ định tháng,
    # tự động thử tháng trước.
    fallback_note = None
    if data["so_ca"] == 0 and not user_specified:
        prev = date(year, month, 1) - timedelta(days=1)
        data = tinh_luong_ca_nhan(user, prev.month, prev.year)
        if data["so_ca"] > 0:
            month, year = prev.month, prev.year
            fallback_note = f"Tháng {today.month}/{today.year} chưa có ca hoàn thành, hiển thị dữ liệu tháng {month}/{year}."
        else:
            return {"message": f"Tháng {today.month}/{today.year} và tháng {prev.month}/{prev.year} đều chưa có ca hoàn thành. Bạn muốn xem tháng nào?"}

    if data["so_ca"] == 0:
        return {"message": f"Chưa có ca hoàn thành nào trong tháng {month}/{year}."}

    hourly = int(data["luong_gio"])
    gross  = int(data["thu_nhap_uoc_tinh"])
    hours  = float(data["tong_gio"])

    result = {
        "ky_tinh": f"Tháng {month}/{year}",
        "so_ca_hoan_thanh": data["so_ca"],
        "tong_gio_lam": f"{hours:.1f} giờ",
        "luong_theo_gio": f"{hourly:,} đ/giờ",
        "thu_nhap_tam_tinh": f"{gross:,} đ",
        "ghi_chu": "Đây là số tạm tính dựa trên giờ thực làm. Lương chính thức do Admin chốt sau kỳ.",
    }
    if fallback_note:
        result["luu_y"] = fallback_note
    return result


def get_my_profile(user):
    profile = getattr(user, "profile", None)
    data = {
        "ho_ten": user.full_name,
        "so_dien_thoai": user.phone,
        "email": user.email or "",
        "vai_tro": {"admin": "Admin", "manager": "Quản lý", "employee": "Nhân viên"}.get(user.role, user.role),
        "ngay_tham_gia": _fmt_date(_to_vn(user.date_joined).date()) if user.date_joined else "",
    }
    if profile:
        data.update({
            "luong_theo_gio": str(profile.hourly_rate),
            "ngan_hang": f"{profile.bank_name} — {profile.bank_account}".strip(" —") if profile.bank_name or profile.bank_account else "",
            "dia_chi": profile.address or "",
            "lien_he_khan_cap": profile.emergency_contact or "",
            "ngay_bat_dau_lam": _fmt_date(profile.start_date) if profile.start_date else "",
        })
    return data


def get_attendance_late_detail(user, args):
    only_late = args.get("only_late", True)
    if isinstance(only_late, str):
        only_late = only_late.lower() != "false"

    first, today = _month_range()
    from_date = _parse_date(args.get("from_date"), first)
    to_date   = _parse_date(args.get("to_date"),   today)

    qs = (
        CheckInRecord.objects
        .filter(user=user, shift__date__range=[from_date, to_date])
        .select_related("shift", "shift__store")
        .order_by("shift__date", "shift__start_time")
    )
    if only_late:
        qs = qs.filter(late_minutes__gt=0)

    if not qs.exists():
        msg = "Không có ca đi muộn nào trong khoảng thời gian này." if only_late \
              else "Không có dữ liệu chấm công trong khoảng thời gian này."
        return {"message": msg, "chi_tiet": []}

    chi_tiet = []
    tong_phut_muon = 0
    for r in qs:
        s = r.shift
        som_phut = 0
        if r.checkout_at and s.end_time:
            end_dt = datetime.combine(s.date, s.end_time).replace(tzinfo=_VN_TZ)
            checkout_vn = _to_vn(r.checkout_at)
            diff = int((end_dt - checkout_vn).total_seconds() // 60)
            som_phut = max(0, diff)

        tong_phut_muon += r.late_minutes
        chi_tiet.append({
            "ngay": _fmt_date(s.date),
            "cua_hang": s.store.name if s.store else "",
            "gio_bat_dau_ca": _fmt_time(s.start_time),
            "gio_ket_thuc_ca": _fmt_time(s.end_time),
            "check_in_thuc_te": _fmt_dt(r.checkin_at) if r.checkin_at else "Chưa check-in",
            "di_muon_phut": r.late_minutes,
            "check_out_thuc_te": _fmt_dt(r.checkout_at) if r.checkout_at else "Chưa check-out",
            "ve_som_phut": som_phut,
            "tong_gio_lam": f"{(r.worked_minutes or 0) // 60}g{(r.worked_minutes or 0) % 60:02d}p",
            "chinh_sua_thu_cong": "Có" if r.is_manual_override else "Không",
            "ghi_chu": r.note or "",
        })

    return {
        "tong_ca": len(chi_tiet),
        "tong_phut_di_muon": tong_phut_muon,
        "chi_tiet": chi_tiet,
    }


def execute_tool(name, args, user):
    tools = {
        "getMySchedule":           lambda: get_my_schedule(user, args),
        "getOpenShifts":           lambda: get_open_shifts(args),
        "getAttendanceRecord":     lambda: get_attendance_record(user, args),
        "getAttendanceLateDetail": lambda: get_attendance_late_detail(user, args),
        "getMySwapRequests":       lambda: get_my_swap_requests(user, args),
        "getEstimatedSalary":      lambda: get_estimated_salary(user, args),
        "getMyProfile":            lambda: get_my_profile(user),
    }
    tool = tools.get(name)
    if tool:
        return tool()
    return {"error": f"Tool '{name}' không được hỗ trợ."}

MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]


def run_chat(user, message, history):
    client = genai.Client(api_key=GEMINI_API_KEY)

    base_contents = [
        types.Content(
            role="user" if msg.role == "user" else "model",
            parts=[types.Part(text=msg.content)],
        )
        for msg in history
    ]
    base_contents.append(types.Content(role="user", parts=[types.Part(text=message)]))

    today_vn = datetime.now(tz=_VN_TZ)
    current_date = today_vn.date()
    current_date_str = today_vn.strftime("%d/%m/%Y")
    current_day_name = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"][today_vn.weekday()]

    monday_this_week = current_date - timedelta(days=current_date.weekday())
    sunday_this_week = monday_this_week + timedelta(days=6)
    monday_next_week = monday_this_week + timedelta(days=7)
    sunday_next_week = monday_next_week + timedelta(days=6)

    dynamic_system_prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"=== THÔNG TIN HỆ THỐNG HIỆN TẠI ===\n"
        f"- Ngày hôm nay: {current_day_name}, {current_date_str}\n"
        f"- Giờ hiện tại: {today_vn.strftime('%H:%M')}\n"
        f"- Tuần này bắt đầu từ Thứ Hai {monday_this_week.strftime('%d/%m/%Y')} đến Chủ Nhật {sunday_this_week.strftime('%d/%m/%Y')}.\n"
        f"- Tuần sau bắt đầu từ Thứ Hai {monday_next_week.strftime('%d/%m/%Y')} đến Chủ Nhật {sunday_next_week.strftime('%d/%m/%Y')}.\n"
        f"Sử dụng thông tin thời gian thực này để trả lời chính xác các câu hỏi liên quan đến thời gian, "
        f"và khi gọi tool getMySchedule/getOpenShifts, hãy truyền tham số from_date và to_date phù hợp nếu cần thiết."
    )

    config = types.GenerateContentConfig(
        system_instruction=dynamic_system_prompt,
        tools=TOOLS,
    )

    last_exc = None
    for model in MODELS:
        contents = list(base_contents)
        reply = "Xin lỗi, tôi không thể xử lý yêu cầu này ngay bây giờ."
        try:
            for _ in range(5):
                response = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config,
                )

                function_calls = response.function_calls
                if not function_calls:
                    reply = response.text or reply
                    break

                contents.append(response.candidates[0].content)

                response_parts = [
                    types.Part.from_function_response(
                        name=fc.name,
                        response={"result": json.dumps(execute_tool(fc.name, dict(fc.args), user), ensure_ascii=False)},
                    )
                    for fc in function_calls
                ]
                contents.append(types.Content(role="user", parts=response_parts))

            return reply
        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e).upper():
                last_exc = e
                continue
            raise

    raise last_exc
