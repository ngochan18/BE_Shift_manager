from django.db import models

class PayrollPeriod(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Nháp"
        CONFIRMED = "confirmed", "Đã chốt"
        PAID = "paid", "Đã thanh toán"

    store = models.ForeignKey(Store, on_delete=models.CASCADE)
    month = models.PositiveSmallIntegerField()
    year = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        unique_together = ["store", "month", "year"]
        ordering = ["-year", "-month"]

    def __str__(self):
        return f"{self.store} - {self.month}/{self.year}"


class PayrollDetail(models.Model):
    period = models.ForeignKey(PayrollPeriod, on_delete=models.CASCADE, related_name="details")
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    total_shifts = models.PositiveIntegerField(default=0)
    total_hours = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    hourly_rate = models.DecimalField(max_digits=10, decimal_places=2)  # snapshot
    gross_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    deduction = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    bonus = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    net_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    note = models.TextField(blank=True)

    class Meta:
        unique_together = ["period", "user"]

    def __str__(self):
        return f"{self.user} - {self.period}"
