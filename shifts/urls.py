from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ShiftTemplateViewSet, ShiftViewSet, ShiftSwapRequestViewSet

router = DefaultRouter()
router.register(r'templates', ShiftTemplateViewSet, basename='shift-template')
router.register(r'swap-requests', ShiftSwapRequestViewSet, basename='shift-swap-request')
router.register(r'', ShiftViewSet, basename='shift')

urlpatterns = [
    path('', include(router.urls)),
]
