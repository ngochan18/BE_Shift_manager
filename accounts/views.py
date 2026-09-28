from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework import viewsets, status, generics
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.contrib.auth import get_user_model
from .serializers import (
    CustomTokenObtainPairSerializer,
    UserSerializer,
    EmployeeProfileUpdateSerializer,
    ChangePasswordSerializer
)
from .permissions import IsAdminRole
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.decorators import action
from .firebase_utils import verify_id_token
User = get_user_model()


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer


class EmployeeViewSet(viewsets.ModelViewSet):
    queryset = User.objects.exclude(role=User.Role.ADMIN).exclude(is_superuser=True).order_by('-date_joined')
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated, IsAdminRole]
    authentication_classes = [JWTAuthentication]

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.is_active = False
        instance.save()
        return Response({"message": "Vô hiệu hóa tài khoản thành công"}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def activate(self, request, pk=None):
        instance = self.get_object()
        instance.is_active = True
        instance.save()
        return Response({"message": "Mở khóa tài khoản thành công"}, status=status.HTTP_200_OK)


class UserProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = EmployeeProfileUpdateSerializer
    permission_classes = [IsAuthenticated]
    authentication_classes = [JWTAuthentication]

    def get_object(self):
        return self.request.user


class ChangePasswordView(generics.UpdateAPIView):
    serializer_class = ChangePasswordSerializer
    permission_classes = [IsAuthenticated]
    authentication_classes = [JWTAuthentication]

    def get_object(self):
        return self.request.user

    def update(self, request, *args, **kwargs):
        self.object = self.get_object()
        serializer = self.get_serializer(data=request.data)

        if serializer.is_valid():
            self.object.set_password(serializer.validated_data.get("new_password"))
            self.object.save()
            return Response({"detail": "Mật khẩu đã được cập nhật thành công."}, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ResetPasswordWithFirebaseView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        id_token = request.data.get('id_token', '').strip()
        new_password = request.data.get('new_password', '')

        if not id_token or not new_password:
            return Response({'error': 'Thiếu thông tin.'}, status=status.HTTP_400_BAD_REQUEST)

        if len(new_password) < 6:
            return Response({'error': 'Mật khẩu phải có ít nhất 6 ký tự.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            decoded = verify_id_token(id_token)
        except Exception:
            return Response({'error': 'Token Firebase không hợp lệ hoặc đã hết hạn.'}, status=status.HTTP_401_UNAUTHORIZED)

        # Firebase trả số dạng +84912345678 → chuẩn hoá về 0912345678
        firebase_phone = decoded.get('phone_number', '')
        if firebase_phone.startswith('+84'):
            phone = '0' + firebase_phone[3:]
        else:
            phone = firebase_phone

        try:
            user = User.objects.get(phone=phone, is_active=True)
        except User.DoesNotExist:
            return Response({'error': 'Không tìm thấy tài khoản với số điện thoại này.'}, status=status.HTTP_404_NOT_FOUND)

        user.set_password(new_password)
        user.save()

        return Response({'message': 'Đặt lại mật khẩu thành công. Vui lòng đăng nhập lại.'})
