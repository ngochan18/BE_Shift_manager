from rest_framework import serializers


class PayrollPreviewItemSerializer(serializers.Serializer):
    user_id = serializers.IntegerField(source="user.id")
    user_name = serializers.CharField(source="user.full_name")
    user_phone = serializers.CharField(source="user.phone")
    total_shifts = serializers.IntegerField(source="so_ca")
    total_hours = serializers.DecimalField(source="tong_gio", max_digits=6, decimal_places=2)
    hourly_rate = serializers.DecimalField(source="luong_gio", max_digits=10, decimal_places=2)
    gross_amount = serializers.DecimalField(source="thanh_tien", max_digits=12, decimal_places=2)
    late_count = serializers.IntegerField(source="di_muon_count")
    early_leave_count = serializers.IntegerField(source="ve_som_count")
    missing_checkout_count = serializers.IntegerField(source="quen_checkout_count")


class EstimatedSalarySerializer(serializers.Serializer):
    month = serializers.IntegerField(source="thang")
    year = serializers.IntegerField(source="nam")
    total_shifts = serializers.IntegerField(source="so_ca")
    total_hours = serializers.DecimalField(source="tong_gio", max_digits=6, decimal_places=2)
    hourly_rate = serializers.DecimalField(source="luong_gio", max_digits=10, decimal_places=2)
    gross_amount = serializers.DecimalField(source="thu_nhap_uoc_tinh", max_digits=12, decimal_places=2)
    late_count = serializers.IntegerField(source="di_muon_count")
    early_leave_count = serializers.IntegerField(source="ve_som_count")
    missing_checkout_count = serializers.IntegerField(source="quen_checkout_count")



from payroll.models import PayrollPeriod, PayrollDetail


class PayrollDetailSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(source="user.id", read_only=True)
    user_name = serializers.CharField(source="user.full_name", read_only=True)
    user_phone = serializers.CharField(source="user.phone", read_only=True)
    bank_account = serializers.CharField(source="user.profile.bank_account", read_only=True, default="")
    bank_name = serializers.CharField(source="user.profile.bank_name", read_only=True, default="")
    store_name = serializers.CharField(source="period.store.name", read_only=True)
    month = serializers.IntegerField(source="period.month", read_only=True)
    year = serializers.IntegerField(source="period.year", read_only=True)
    status_display = serializers.CharField(source="period.get_status_display", read_only=True)

    class Meta:
        model = PayrollDetail
        fields = [
            "id", "user_id", "user_name", "user_phone", "bank_account", "bank_name",
            "store_name", "month", "year", "status_display",
            "total_shifts", "total_hours", "hourly_rate", "gross_amount",
            "deduction", "bonus", "net_amount", "note",
            "late_count", "early_leave_count", "missing_checkout_count"
        ]


class PayrollPeriodSerializer(serializers.ModelSerializer):
    store_name = serializers.CharField(source="store.name", read_only=True)
    created_by_name = serializers.CharField(source="created_by.full_name", read_only=True, default="")
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    total_employees = serializers.IntegerField(read_only=True)
    total_gross = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    total_deduction = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    total_bonus = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    total_net = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)

    class Meta:
        model = PayrollPeriod
        fields = [
            "id", "store", "store_name", "month", "year", "status", "status_display",
            "created_by", "created_by_name", "confirmed_at", "paid_at", "note",
            "total_employees", "total_gross", "total_deduction", "total_bonus", "total_net"
        ]


class PayrollPeriodDetailSerializer(PayrollPeriodSerializer):
    details = PayrollDetailSerializer(many=True, read_only=True)

    class Meta(PayrollPeriodSerializer.Meta):
        fields = PayrollPeriodSerializer.Meta.fields + ["details"]


class PayrollDetailUpdateSerializer(serializers.Serializer):
    bonus = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    deduction = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    note = serializers.CharField(required=False, allow_blank=True)


class CreatePayrollPeriodSerializer(serializers.Serializer):
    store_id = serializers.IntegerField()
    month = serializers.IntegerField(min_value=1, max_value=12)
    year = serializers.IntegerField(min_value=2000, max_value=2100)
