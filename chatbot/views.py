from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import ChatbotMessage, ChatbotSession
from .services import run_chat


from django.db import transaction
from django.utils import timezone

class ChatView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        message = request.data.get("message", "").strip()
        session_id = request.data.get("session_id")

        if not message:
            return Response({"error": "Tin nhắn không được để trống."}, status=400)

        session = None
        if session_id:
            session = ChatbotSession.objects.filter(id=session_id, user=user).first()
        
        if not session:
            session = ChatbotSession.objects.create(user=user, title=message[:50])
        history = list(session.messages.order_by("created_at")[:20])

        try:
            reply = run_chat(user, message, history)
            with transaction.atomic():
                ChatbotMessage.objects.create(session=session, role="user", content=message)
                ChatbotMessage.objects.create(session=session, role="assistant", content=reply)
                session.last_active_at = timezone.now()
                session.save(update_fields=['last_active_at'])

            return Response({"reply": reply, "session_id": session.id})

        except Exception as exc:
            err = str(exc).upper()
            if any(code in err for code in ["429", "RESOURCE_EXHAUSTED", "QUOTA"]):
                return Response(
                    {"error": "Hệ thống AI đang quá tải lượt dùng miễn phí, bạn vui lòng đợi 1 phút nhé."},
                    status=503
                )
            return Response({"error": "Đã có lỗi xảy ra khi kết nối AI."}, status=500)

class HistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        session_id = request.query_params.get("session_id")
        user = request.user

        session = None
        if session_id:
            session = ChatbotSession.objects.filter(id=session_id, user=user).first()
        if not session:
            session = ChatbotSession.objects.filter(user=user).order_by("-last_active_at").first()

        if not session:
            return Response({"session_id": None, "messages": []})

        messages = session.messages.order_by("created_at").values("role", "content", "created_at")
        return Response({
            "session_id": session.id,
            "messages": [
                {
                    "role": m["role"],
                    "content": m["content"],
                    "created_at": m["created_at"].isoformat(),
                }
                for m in messages
            ],
        })


class ResetSessionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        session_id = request.data.get("session_id")
        if session_id:
            ChatbotSession.objects.filter(id=session_id, user=request.user).delete()
        return Response({"message": "Đã xóa phiên hội thoại."})
