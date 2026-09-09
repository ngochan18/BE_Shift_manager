from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import ShiftTemplate, Shift, ShiftAssignment, ShiftSwapRequest

User = get_user_model()


class ShiftTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShiftTemplate
        fields = '__all__'


class ShiftAssignmentSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.full_name', read_only=True)
    user_phone = serializers.CharField(source='user.phone', read_only=True)

    class Meta:
        model = ShiftAssignment
        fields = ['id', 'user', 'user_name', 'user_phone', 'assigned_by', 'assigned_at', 'status']
        read_only_fields = ['id', 'assigned_by', 'assigned_at']


class ShiftSerializer(serializers.ModelSerializer):
    assignments = ShiftAssignmentSerializer(many=True, read_only=True)
    created_by_name = serializers.CharField(source='created_by.full_name', read_only=True)

    class Meta:
        model = Shift
        fields = [
            'id', 'store', 'template', 'date', 'start_time', 'end_time',
            'is_open', 'is_emergency', 'salary_multiplier', 'note', 
            'created_by', 'created_by_name', 'created_at', 'assignments'
        ]
        read_only_fields = ['id', 'created_by', 'created_at']


class AssignSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()


class ShiftSwapRequestSerializer(serializers.ModelSerializer):
    requester_name = serializers.CharField(source='requester.full_name', read_only=True)
    partner_name = serializers.CharField(source='partner.full_name', read_only=True)

    class Meta:
        model = ShiftSwapRequest
        fields = [
            'id', 'swap_type', 'requester', 'requester_name', 'partner', 'partner_name',
            'target_shift', 'offered_shift', 'status',
            'message', 'expires_at', 'created_at', 'updated_at',
            'resolved_by', 'resolved_at'
        ]
        read_only_fields = ['id', 'requester', 'status', 'created_at', 'updated_at', 'resolved_by', 'resolved_at']
    def validate(self, data):
        swap_type = data.get('swap_type')
        partner = data.get('partner')
        target_shift = data.get('target_shift')
        offered_shift = data.get('offered_shift')
        request = self.context.get('request')
        requester = request.user if request else None
    
        if swap_type == ShiftSwapRequest.SwapType.EXCHANGE:
            if not partner or not offered_shift:
                raise serializers.ValidationError("Đổi ca cần có người đổi và ca đổi.")
        if swap_type == ShiftSwapRequest.SwapType.CLAIM and partner:
            raise serializers.ValidationError("Tranh ca không cần chỉ định đối tác.")
        if partner and requester and partner == requester:
            raise serializers.ValidationError("Không thể tạo yêu cầu với chính mình.")
        if offered_shift and target_shift and offered_shift == target_shift:
            raise serializers.ValidationError("Ca đề xuất và ca mục tiêu không được giống nhau.")
        if target_shift and target_shift.date < date.today():
            raise serializers.ValidationError("Không thể tạo yêu cầu cho ca đã diễn ra.")
