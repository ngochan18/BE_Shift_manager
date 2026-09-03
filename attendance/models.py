from django.db import models
from django.conf import settings
from shifts.models import Shift

User = settings.AUTH_USER_MODEL

class QRToken(models.Model):
    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="qr_tokens")
    token = models.CharField(max_length=255, unique=True)
    generated_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    class Meta:
        db_table = "qr_tokens"
        indexes = [
            models.Index(fields=["token"]),
            models.Index(fields=["expires_at", "is_used"]),
        ]


class CheckInRecord(models.Model):
    shift = models.ForeignKey(Shift, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    checkin_at = models.DateTimeField(null=True, blank=True)
    checkout_at = models.DateTimeField(null=True, blank=True)
    
    # Geofencing
    checkin_lat = models.DecimalField(max_digits=10, decimal_places=8, null=True)
    checkin_lng = models.DecimalField(max_digits=11, decimal_places=8, null=True)
    checkin_distance_meters = models.PositiveIntegerField(null=True)

    # QR
    qr_token_used = models.CharField(max_length=255, blank=True)
    checkin_method = models.CharField(max_length=20, default="qr_gps")
    is_valid = models.BooleanField(default=True)
    worked_minutes = models.PositiveIntegerField(null=True, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        db_table = "check_in_records"
        unique_together = ["shift", "user"]
        indexes = [models.Index(fields=["user", "shift"])]

    def __str__(self):
        return f"Check-in {self.user} @ {self.shift}"
