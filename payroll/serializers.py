from rest_framework import serializers


class PayrollPreviewItemSerializer(serializers.Serializer):
    user_id = serializers.IntegerField(source="user.id")
    user_name = serializers.CharField(source="user.full_name")
    user_phone = serializers.CharField(source="user.phone")
    total_shifts = serializers.IntegerField(source="so_ca")
    total_hours = serializers.DecimalField(source="tong_gio", max_digits=6, decimal_places=2)
    hourly_rate = serializers.DecimalField(source="luong_gio", max_digits=10, decimal_places=2)
    gross_amount = serializers.DecimalField(source="thanh_tien", max_digits=12, decimal_places=2)


class EstimatedSalarySerializer(serializers.Serializer):
    month = serializers.IntegerField(source="thang")
    year = serializers.IntegerField(source="nam")
    total_shifts = serializers.IntegerField(source="so_ca")
    total_hours = serializers.DecimalField(source="tong_gio", max_digits=6, decimal_places=2)
    hourly_rate = serializers.DecimalField(source="luong_gio", max_digits=10, decimal_places=2)
    gross_amount = serializers.DecimalField(source="thu_nhap_uoc_tinh", max_digits=12, decimal_places=2)
