from rest_framework import serializers
from .models import QRToken, CheckInRecord
from shifts.models import Shift


class QRTokenSerializer(serializers.ModelSerializer):
    shift_info = serializers.SerializerMethodField()

    class Meta:
        model = QRToken
        fields = ["id", "shift", "shift_info", "token", "generated_at", "expires_at", "is_used"]
        read_only_fields = ["token", "generated_at", "is_used"]

    def get_shift_info(self, obj):
        shift = obj.shift
        return {
            "id": shift.id,
            "date": shift.date,
            "start_time": shift.start_time,
            "end_time": shift.end_time,
            "store": shift.store.name,
        }


class CheckInSerializer(serializers.Serializer):
    """Serializer để nhân viên gửi yêu cầu check-in"""
    token = serializers.CharField(max_length=255, help_text="QR token được quét từ mã QR của ca")
    latitude = serializers.DecimalField(
        max_digits=10, decimal_places=8,
        help_text="Vĩ độ GPS của nhân viên (bắt buộc)"
    )
    longitude = serializers.DecimalField(
        max_digits=11, decimal_places=8,
        help_text="Kinh độ GPS của nhân viên (bắt buộc)"
    )


class CheckOutSerializer(serializers.Serializer):
    """Serializer để nhân viên gửi yêu cầu check-out"""
    shift_id = serializers.IntegerField(help_text="ID ca làm việc cần check-out")
    note = serializers.CharField(max_length=500, required=False, allow_blank=True)


class CheckInRecordSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.full_name", read_only=True)
    user_phone = serializers.CharField(source="user.phone", read_only=True)
    shift_date = serializers.DateField(source="shift.date", read_only=True)
    shift_start = serializers.TimeField(source="shift.start_time", read_only=True)
    shift_end = serializers.TimeField(source="shift.end_time", read_only=True)
    store_name = serializers.CharField(source="shift.store.name", read_only=True)

    is_late = serializers.BooleanField(source="is_late", read_only=True)

    class Meta:
        model = CheckInRecord
        fields = [
            "id",
            "shift",
            "shift_date",
            "shift_start",
            "shift_end",
            "store_name",
            "user",
            "user_name",
            "user_phone",
            "checkin_at",
            "checkout_at",
            "checkin_lat",
            "checkin_lng",
            "checkin_distance_meters",
            "checkin_method",
            "geo_valid",
            "late_minutes",
            "is_late",
            "worked_minutes",
            "note",
        ]
        read_only_fields = fields
