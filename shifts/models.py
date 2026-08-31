from django.db import models

class ShiftTemplate(models.Model):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="shift_templates")
    name = models.CharField(max_length=100)           # Ca sáng, Ca chiều...
    start_time = models.TimeField()
    end_time = models.TimeField()
    min_workers = models.PositiveIntegerField(default=1)
    max_workers = models.PositiveIntegerField(default=3)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ["store", "name"]
        ordering = ["start_time"]

    def __str__(self):
        return f"{self.name} ({self.start_time} - {self.end_time})"

class Shift(models.Model):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="shifts")
    template = models.ForeignKey(ShiftTemplate, on_delete=models.SET_NULL, null=True, blank=True)
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_open = models.BooleanField(default=False)  # ca trống chờ tranh
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="created_shifts")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(end_time__gt=models.F("start_time")),
                name="shift_no_time_paradox"
            )
        ]
        indexes = [
            models.Index(fields=["date"]),
            models.Index(fields=["store", "date"]),
            models.Index(fields=["is_open"], condition=models.Q(is_open=True), name="idx_open_shifts"),
        ]

    def __str__(self):
        return f"{self.store} - {self.date} {self.start_time}-{self.end_time}"
class ShiftAssignment(models.Model):
    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="assignments")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="shift_assignments")
    assigned_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="assigned_by")
    assigned_at = models.DateTimeField(auto_now_add=True)
    
    class Status(models.TextChoices):
        CONFIRMED = "confirmed", "Đã xác nhận"
        CANCELLED = "cancelled", "Đã hủy"
        PENDING   = "pending",   "Chờ duyệt"

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CONFIRMED)

    class Meta:
        unique_together = ["shift", "user"]
        indexes = [
            models.Index(
                fields=["status"],
                condition=models.Q(status="pending"),
                name="idx_pending_assignments"
            ) 
        ]

    def __str__(self):
        return f"{self.user} → {self.shift}"


class ShiftSwapRequest(models.Model):
    class SwapType(models.TextChoices):
        CLAIM    = "claim",    "Tranh ca"
        EXCHANGE = "exchange", "Đổi ca"

    class Status(models.TextChoices):
        PENDING   = "pending",   "Chờ duyệt"
        ACCEPTED  = "accepted",  "Đã chấp nhận"
        REJECTED  = "rejected",  "Từ chối"
        CANCELLED = "cancelled", "Đã hủy"
        EXPIRED   = "expired",   "Hết hạn"

    swap_type = models.CharField(max_length=10, choices=SwapType.choices)
    requester = models.ForeignKey(User, on_delete=models.CASCADE, related_name="swap_requests")
    target_shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="incoming_swap_requests")
    offered_shift = models.ForeignKey(Shift, on_delete=models.SET_NULL, null=True, blank=True, related_name="offered_swap_requests")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    message = models.TextField(blank=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="resolved_swaps")
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(swap_type="claim") | models.Q(offered_shift__isnull=False),
                name="exchange_needs_offered_shift"
            )
        ]

    def __str__(self):
        return f"{self.requester} - {self.swap_type} - {self.target_shift}"