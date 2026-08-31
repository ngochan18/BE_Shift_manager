from django.db import models

class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    type = models.CharField(max_length=50)
    title = models.CharField(max_length=200)
    body = models.TextField()
    related_id = models.PositiveIntegerField(null=True, blank=True)
    related_type = models.CharField(max_length=50, blank=True)
    is_read = models.BooleanField(default=False)
    sent_via_telegram = models.BooleanField(default=False)
    telegram_msg_id = models.CharField(max_length=50, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "is_read"], condition=models.Q(is_read=False)),
        ]
        ordering = ["-created_at"]
