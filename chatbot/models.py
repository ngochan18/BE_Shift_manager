from django.db import models
from django.conf import settings

User = settings.AUTH_USER_MODEL

class ChatbotSession(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="chat_sessions")
    title = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_active_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "chatbot_sessions"


class ChatbotMessage(models.Model):
    session = models.ForeignKey(ChatbotSession, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=10)  # user / assistant
    content = models.TextField()
    sql_query = models.TextField(blank=True, null=True)
    query_result = models.JSONField(null=True, blank=True)
    tokens_used = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "chatbot_messages"
        ordering = ["created_at"]
