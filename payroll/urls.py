from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    PayrollMyEstimateView,
    PayrollPreviewView,
    PayrollPeriodViewSet,
    PayrollDetailViewSet,
    MyPayslipsView,
)

router = DefaultRouter()
router.register("periods", PayrollPeriodViewSet, basename="payroll-period")
router.register("details", PayrollDetailViewSet, basename="payroll-detail")

urlpatterns = [
    path("preview/", PayrollPreviewView.as_view(), name="payroll-preview"),
    path("my-estimate/", PayrollMyEstimateView.as_view(), name="payroll-my-estimate"),
    path("my-payslips/", MyPayslipsView.as_view(), name="payroll-my-payslips"),
    path("", include(router.urls)),
]
