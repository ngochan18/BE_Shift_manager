from rest_framework import serializers
from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = [
            'id', 'notification_type', 'title', 'message',
            'is_read', 'related_shift', 'related_swap', 'created_at',
        ]
        read_only_fields = ['id', 'notification_type', 'title', 'message', 'related_shift', 'related_swap', 'created_at']
