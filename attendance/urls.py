from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import QRTokenViewSet, AttendanceRecordViewSet

router = DefaultRouter()

# /api/attendance/qr/
router.register(r'qr', QRTokenViewSet, basename='attendance-qr')

# /api/attendance/records/
router.register(r'records', AttendanceRecordViewSet, basename='attendance-records')

urlpatterns = [
    path('', include(router.urls)),
]
