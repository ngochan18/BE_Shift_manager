from django.urls import path
from .views import ChatView, HistoryView, ResetSessionView

urlpatterns = [
    path("chat/",    ChatView.as_view(),    name="chatbot-chat"),
    path("history/", HistoryView.as_view(), name="chatbot-history"),
    path("reset/",   ResetSessionView.as_view(), name="chatbot-reset"),
]
