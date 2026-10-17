from django.urls import path

from .views import PayrollMyEstimateView, PayrollPreviewView

urlpatterns = [
    path("preview/", PayrollPreviewView.as_view(), name="payroll-preview"),
    path("my-estimate/", PayrollMyEstimateView.as_view(), name="payroll-my-estimate"),
]
