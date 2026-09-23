from django.contrib import admin
from .models import QRToken, CheckInRecord


@admin.register(QRToken)
class QRTokenAdmin(admin.ModelAdmin):
    list_display = ["id", "shift", "token", "generated_at", "expires_at", "is_used"]
    list_filter = ["is_used"]
    search_fields = ["token", "shift__store__name"]
    readonly_fields = ["token", "generated_at"]
    ordering = ["-generated_at"]


@admin.register(CheckInRecord)
class CheckInRecordAdmin(admin.ModelAdmin):
    list_display = [
        "id", "user", "shift", "checkin_at", "checkout_at",
        "late_minutes", "worked_minutes", "checkin_distance_meters", "geo_valid",
    ]
    list_filter = ["geo_valid", "checkin_method", "shift__store"]
    search_fields = ["user__full_name", "user__phone", "shift__store__name"]
    readonly_fields = [
        "checkin_at", "checkout_at", "checkin_lat", "checkin_lng",
        "checkin_distance_meters", "qr_token_used", "worked_minutes",
    ]
    ordering = ["-checkin_at"]
