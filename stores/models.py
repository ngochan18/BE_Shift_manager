from django.db import models

class Store(models.Model):
    name = models.CharField(max_length=200)
    address = models.TextField()
    geo_latitude = models.DecimalField(max_digits=10, decimal_places=8)
    geo_longitude = models.DecimalField(max_digits=11, decimal_places=8)
    geo_radius_meters = models.PositiveIntegerField(default=100)
    phone = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Cửa hàng"
        verbose_name_plural = "Cửa hàng"

    def __str__(self):
        return self.name
class AuditLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=100)
    target_type = models.CharField(max_length=50, blank=True)
    target_id = models.PositiveIntegerField(null=True, blank=True)
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user"]),
            models.Index(fields=["target_type", "target_id"]),
        ]
        ordering = ["-created_at"]