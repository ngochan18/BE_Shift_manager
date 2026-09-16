from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from .models import Notification
from .serializers import NotificationSerializer


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    """API thông báo cho nhân viên"""
    serializer_class = NotificationSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user).order_by('-created_at')

    @action(detail=True, methods=['post'], url_path='read')
    def mark_read(self, request, pk=None):
        """Đánh dấu một thông báo đã đọc"""
        notification = self.get_object()
        notification.is_read = True
        notification.save(update_fields=['is_read'])
        return Response({"message": "Đã đánh dấu đã đọc."}, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='read-all')
    def mark_all_read(self, request):
        """Đánh dấu tất cả thông báo đã đọc"""
        updated = Notification.objects.filter(
            user=request.user, is_read=False
        ).update(is_read=True)
        return Response({"message": f"Đã đánh dấu {updated} thông báo đã đọc."}, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='unread-count')
    def unread_count(self, request):
        """Đếm số thông báo chưa đọc"""
        count = Notification.objects.filter(
            user=request.user, is_read=False
        ).count()
        return Response({"unread_count": count}, status=status.HTTP_200_OK)
