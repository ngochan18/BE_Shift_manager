from django.db import models
from django.conf import settings


class Notification(models.Model):
    class Type(models.TextChoices):
        SHIFT_ASSIGNED       = "shift_assigned",       "Được phân công ca"
        SHIFT_UNASSIGNED     = "shift_unassigned",     "Bị hủy phân công"
        CLAIM_PENDING        = "claim_pending",        "Có nguyện vọng mới chờ duyệt"
        CLAIM_APPROVED       = "claim_approved",       "Nguyện vọng được duyệt"
        CLAIM_REJECTED       = "claim_rejected",       "Nguyện vọng bị từ chối"
        EMERGENCY_SHIFT      = "emergency_shift",      "Ca khẩn cấp mới"
        EMERGENCY_CLAIMED    = "emergency_claimed",    "Đã nhận ca khẩn cấp"
        SWAP_REQUESTED       = "swap_requested",       "Có yêu cầu đổi/nhượng ca"
        SWAP_PARTNER_ACCEPTED = "swap_partner_accepted", "Đối tác đã đồng ý"
        SWAP_PARTNER_REJECTED = "swap_partner_rejected", "Đối tác đã từ chối"
        SWAP_APPROVED        = "swap_approved",        "Admin đã duyệt yêu cầu"
        SWAP_REJECTED        = "swap_rejected",        "Admin đã từ chối yêu cầu"
        SWAP_CANCELLED       = "swap_cancelled",       "Yêu cầu đã bị hủy"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="notifications"
    )
    notification_type = models.CharField(max_length=30, choices=Type.choices)
    title = models.CharField(max_length=200)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    related_shift = models.ForeignKey(
        'shifts.Shift', on_delete=models.SET_NULL,
        null=True, blank=True, related_name="notifications"
    )
    related_swap = models.ForeignKey(
        'shifts.ShiftSwapRequest', on_delete=models.SET_NULL,
        null=True, blank=True, related_name="notifications"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "notifications"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(
                fields=["is_read"],
                condition=models.Q(is_read=False),
                name="idx_unread_notifications"
            ),
        ]

    def __str__(self):
        return f"[{self.notification_type}] {self.user} - {self.title}"
