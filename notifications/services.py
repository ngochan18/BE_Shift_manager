import logging
from .models import Notification
from .telegram import TelegramService

logger = logging.getLogger(__name__)


class NotificationService:
    @staticmethod
    def _send_telegram_safe(user, text):
        """Gửi Telegram cho user nếu có telegram_chat_id."""
        try:
            chat_id = getattr(user, 'telegram_chat_id', None)
            if chat_id:
                TelegramService.send_message(chat_id, text)
        except Exception as e:
            logger.error(f"Telegram error for user {user.id}: {str(e)}")

    @staticmethod
    def send(user, notification_type, title, message, related_shift=None, related_swap=None):
        """Tạo thông báo DB + gửi Telegram."""
        notification = Notification.objects.create(
            user=user,
            notification_type=notification_type,
            title=title,
            message=message,
            related_shift=related_shift,
            related_swap=related_swap,
        )
        # Gửi Telegram
        telegram_text = f"<b>{title}</b>\n{message}"
        NotificationService._send_telegram_safe(user, telegram_text)
        return notification

    @staticmethod
    def send_to_many(users, notification_type, title, message, related_shift=None, related_swap=None):
        """Tạo thông báo DB hàng loạt + gửi Telegram."""
        notifications = [
            Notification(
                user=u,
                notification_type=notification_type,
                title=title,
                message=message,
                related_shift=related_shift,
                related_swap=related_swap,
            )
            for u in users
        ]
        created = Notification.objects.bulk_create(notifications)
        # Gửi Telegram cho từng người
        telegram_text = f"<b>{title}</b>\n{message}"
        for u in users:
            NotificationService._send_telegram_safe(u, telegram_text)
        return created

    # ── Shift Assignment ──
    @staticmethod
    def notify_shift_assigned(user, shift, assigned_by):
        """Thông báo khi nhân viên được phân công vào ca."""
        msg = f"Bạn đã được phân công vào ca {shift.start_time.strftime('%H:%M')}-{shift.end_time.strftime('%H:%M')} ngày {shift.date.strftime('%d/%m/%Y')}."
        NotificationService.send(
            user=user,
            notification_type=Notification.Type.SHIFT_ASSIGNED,
            title="📋 Bạn được phân công ca mới",
            message=msg,
            related_shift=shift,
        )

    @staticmethod
    def notify_shift_unassigned(user, shift):
        """Thông báo khi nhân viên bị hủy phân công."""
        msg = f"Phân công ca {shift.start_time.strftime('%H:%M')}-{shift.end_time.strftime('%H:%M')} ngày {shift.date.strftime('%d/%m/%Y')} đã bị hủy."
        NotificationService.send(
            user=user,
            notification_type=Notification.Type.SHIFT_UNASSIGNED,
            title="❌ Bạn đã bị hủy phân công",
            message=msg,
            related_shift=shift,
        )

    @staticmethod
    def notify_shift_self_cancelled(user, shift):
        """Thông báo nhân viên tự hủy ca + thông báo admin/manager """
        from django.contrib.auth import get_user_model

        time_range = f"{shift.start_time.strftime('%H:%M')}-{shift.end_time.strftime('%H:%M')}"
        date_str = shift.date.strftime('%d/%m/%Y')

        # Thông báo cho chính nhân viên
        NotificationService.send(
            user=user,
            notification_type=Notification.Type.SHIFT_UNASSIGNED,
            title="✅ Hủy đăng ký ca thành công",
            message=f"Bạn đã tự hủy đăng ký ca {time_range} ngày {date_str}. Ca đã được mở lại để nhân viên khác đăng ký.",
            related_shift=shift,
        )
        User = get_user_model()
        admins = list(User.objects.filter(role__in=['admin', 'manager'], is_active=True))
        if admins:
            NotificationService.send_to_many(
                users=admins,
                notification_type=Notification.Type.SHIFT_UNASSIGNED,
                title="⚠️ Nhân viên hủy đăng ký ca",
                message=(
                    f"{getattr(user, 'full_name', user.email)} đã hủy đăng ký ca {time_range} "
                    f"ngày {date_str}. Ca đã được mở lại."
                ),
                related_shift=shift,
            )

    # ── Emergency Shift ──
    @staticmethod
    def notify_emergency_shift(users, shift, is_assigned=True):
        """Thông báo ca khẩn cấp """
        if is_assigned:
            title = "🚨 Ca khẩn cấp mới"
            msg = f"Bạn đã được phân công vào ca khẩn cấp {shift.start_time.strftime('%H:%M')}-{shift.end_time.strftime('%H:%M')} ngày {shift.date.strftime('%d/%m/%Y')}."
        else:
            title = "🚨 Có ca khẩn cấp đang tìm người"
            msg = f"Hệ thống đang cần gấp nhân sự cho ca khẩn cấp {shift.start_time.strftime('%H:%M')}-{shift.end_time.strftime('%H:%M')} ngày {shift.date.strftime('%d/%m/%Y')}. Hãy vào đăng ký nhận ca ngay!"

        NotificationService.send_to_many(
            users=users,
            notification_type=Notification.Type.EMERGENCY_SHIFT,
            title=title,
            message=msg,
            related_shift=shift,
        )

    @staticmethod
    def notify_emergency_claimed(user, shift):
        """Thông báo nhân viên đã nhận ca khẩn cấp thành công."""
        msg = f"Bạn đã nhận ca khẩn cấp {shift.start_time.strftime('%H:%M')}-{shift.end_time.strftime('%H:%M')} ngày {shift.date.strftime('%d/%m/%Y')} thành công."
        NotificationService.send(
            user=user,
            notification_type=Notification.Type.EMERGENCY_CLAIMED,
            title="✅ Nhận ca khẩn cấp thành công",
            message=msg,
            related_shift=shift,
        )

    # ── Claim (nguyện vọng) ──
    @staticmethod
    def notify_claim_pending(user, shift):
        """Thông báo NV đã đăng ký nguyện vọng, chờ duyệt."""
        msg = f"Nguyện vọng nhận ca {shift.start_time.strftime('%H:%M')}-{shift.end_time.strftime('%H:%M')} ngày {shift.date.strftime('%d/%m/%Y')} đang chờ Admin duyệt."
        NotificationService.send(
            user=user,
            notification_type=Notification.Type.CLAIM_PENDING,
            title="⏳ Đăng ký nguyện vọng thành công",
            message=msg,
            related_shift=shift,
        )

    @staticmethod
    def notify_claim_approved(assignment):
        """Thông báo nguyện vọng đã được Admin duyệt."""
        shift = assignment.shift
        msg = f"Nguyện vọng nhận ca {shift.start_time.strftime('%H:%M')}-{shift.end_time.strftime('%H:%M')} ngày {shift.date.strftime('%d/%m/%Y')} đã được duyệt."
        NotificationService.send(
            user=assignment.user,
            notification_type=Notification.Type.CLAIM_APPROVED,
            title="✅ Nguyện vọng được duyệt",
            message=msg,
            related_shift=shift,
        )

    @staticmethod
    def notify_claim_rejected(assignment):
        """Thông báo nguyện vọng bị từ chối."""
        shift = assignment.shift
        msg = f"Nguyện vọng nhận ca {shift.start_time.strftime('%H:%M')}-{shift.end_time.strftime('%H:%M')} ngày {shift.date.strftime('%d/%m/%Y')} đã bị từ chối."
        NotificationService.send(
            user=assignment.user,
            notification_type=Notification.Type.CLAIM_REJECTED,
            title="❌ Nguyện vọng bị từ chối",
            message=msg,
            related_shift=shift,
        )

    # ── Swap Request ──
    @staticmethod
    def notify_swap_requested(swap):
        """Thông báo NV B có yêu cầu đổi/nhượng ca từ NV A."""
        if not swap.partner:
            return
        type_label = dict(swap.SwapType.choices).get(swap.swap_type, "đổi ca")
        NotificationService.send(
            user=swap.partner,
            notification_type=Notification.Type.SWAP_REQUESTED,
            title=f"📩 Yêu cầu {type_label} mới",
            message=f"{swap.requester.full_name} đã gửi yêu cầu {type_label} cho bạn.",
            related_swap=swap,
        )

    @staticmethod
    def notify_swap_partner_accepted(swap):
        """Thông báo NV A rằng NV B đã đồng ý."""
        NotificationService.send(
            user=swap.requester,
            notification_type=Notification.Type.SWAP_PARTNER_ACCEPTED,
            title="👍 Đối tác đã đồng ý",
            message=f"{swap.partner.full_name} đã đồng ý yêu cầu của bạn. Đang chờ Admin duyệt.",
            related_swap=swap,
        )

    @staticmethod
    def notify_swap_partner_rejected(swap):
        """Thông báo NV A rằng NV B đã từ chối."""
        NotificationService.send(
            user=swap.requester,
            notification_type=Notification.Type.SWAP_PARTNER_REJECTED,
            title="👎 Đối tác đã từ chối",
            message=f"{swap.partner.full_name} đã từ chối yêu cầu của bạn.",
            related_swap=swap,
        )

    @staticmethod
    def notify_swap_approved(swap):
        """Thông báo cả hai bên khi Admin duyệt swap."""
        type_label = dict(swap.SwapType.choices).get(swap.swap_type, "đổi ca")
        users = [swap.requester]
        if swap.partner:
            users.append(swap.partner)
        NotificationService.send_to_many(
            users=users,
            notification_type=Notification.Type.SWAP_APPROVED,
            title=f"✅ Yêu cầu {type_label} đã được duyệt",
            message=f"Admin đã duyệt yêu cầu {type_label} của bạn.",
            related_swap=swap,
        )

    @staticmethod
    def notify_swap_rejected(swap):
        """Thông báo cả hai bên khi Admin từ chối swap."""
        type_label = dict(swap.SwapType.choices).get(swap.swap_type, "đổi ca")
        users = [swap.requester]
        if swap.partner:
            users.append(swap.partner)
        NotificationService.send_to_many(
            users=users,
            notification_type=Notification.Type.SWAP_REJECTED,
            title=f"❌ Yêu cầu {type_label} bị từ chối",
            message=f"Admin đã từ chối yêu cầu {type_label} của bạn.",
            related_swap=swap,
        )

    @staticmethod
    def notify_checkin_success(user, shift, late_minutes=0):
        """Thông báo check-in thành công, kèm cảnh báo nếu đi trễ."""
        time_range = f"{shift.start_time.strftime('%H:%M')}-{shift.end_time.strftime('%H:%M')}"
        if late_minutes > 0:
            msg = f"Bạn đã check-in ca {time_range} ngày {shift.date.strftime('%d/%m/%Y')} (trễ {late_minutes} phút)."
        else:
            msg = f"Bạn đã check-in ca {time_range} ngày {shift.date.strftime('%d/%m/%Y')} đúng giờ."
        NotificationService.send(
            user=user,
            notification_type=Notification.Type.CHECKIN_SUCCESS,
            title="✅ Check-in thành công",
            message=msg,
            related_shift=shift,
        )

    @staticmethod
    def notify_swap_cancelled(swap):
        """Thông báo đối tác khi NV A hủy yêu cầu."""
        if not swap.partner:
            return
        type_label = dict(swap.SwapType.choices).get(swap.swap_type, "đổi ca")
        NotificationService.send(
            user=swap.partner,
            notification_type=Notification.Type.SWAP_CANCELLED,
            title=f"🚫 Yêu cầu {type_label} đã bị hủy",
            message=f"{swap.requester.full_name} đã hủy yêu cầu {type_label}.",
            related_swap=swap,
        )

    # ── Payroll (Tính lương) ──
    @staticmethod
    def notify_payroll_confirmed(user, period, net_amount):
        """Thông báo khi bảng lương được chốt."""
        msg = f"Bảng lương tháng {period.month}/{period.year} tại {period.store.name} đã được chốt. Tổng thu nhập thực lĩnh: {int(net_amount):,d} VND. Vui lòng kiểm tra chi tiết trong mục Phiếu Lương."
        NotificationService.send(
            user=user,
            notification_type=Notification.Type.PAYROLL_CONFIRMED,
            title="💰 Bảng lương đã được chốt",
            message=msg,
        )

    @staticmethod
    def notify_payroll_paid(user, period, net_amount):
        """Thông báo khi lương đã được thanh toán."""
        msg = f"Lương tháng {period.month}/{period.year} tại {period.store.name} (Số tiền: {int(net_amount):,d} VND) đã được chuyển khoản thành công. Vui lòng kiểm tra tài khoản ngân hàng."
        NotificationService.send(
            user=user,
            notification_type=Notification.Type.PAYROLL_PAID,
            title="💸 Đã thanh toán lương",
            message=msg,
        )

