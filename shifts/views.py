from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import date, datetime
from accounts.permissions import IsAdminRole
from .models import ShiftTemplate, Shift, ShiftAssignment, ShiftSwapRequest
from .serializers import (
    ShiftTemplateSerializer, ShiftSerializer,
    ShiftAssignmentSerializer, AssignSerializer,
    ShiftSwapRequestSerializer,
)
from .services import ShiftService
from notifications.services import NotificationService
from django.db import transaction
from django.db.models import Q
User = get_user_model()


class ShiftTemplateViewSet(viewsets.ModelViewSet):
    """Quản lý Ca mẫu (Ca sáng, Ca chiều...) - chỉ Admin CRUD"""
    queryset = ShiftTemplate.objects.all().order_by('start_time')
    serializer_class = ShiftTemplateSerializer
    authentication_classes = [JWTAuthentication]

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsAdminRole()]


class ShiftViewSet(viewsets.ModelViewSet):
    """Quản lý Ca thực tế theo ngày"""
    queryset = Shift.objects.select_related('store', 'template', 'created_by').prefetch_related('assignments__user').order_by('-date', 'start_time')
    serializer_class = ShiftSerializer
    authentication_classes = [JWTAuthentication]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user

        if self.action == 'list' and hasattr(user, 'role') and user.role != 'admin':
            now = timezone.now()
            today = now.date()
            current_time = now.time()
            
            qs = qs.filter(
                is_open=True
            ).filter(
                Q(is_emergency=True, date__gte=today) | 
                Q(is_emergency=False, date__gt=today) | 
                Q(is_emergency=False, date=today, start_time__gt=current_time)
            )
        else:
            is_open = self.request.query_params.get('is_open')
            if is_open and is_open.lower() == 'true':
                qs = qs.filter(is_open=True, date__gte=timezone.now().date())

        store_id = self.request.query_params.get('store')
        if store_id:
            qs = qs.filter(store_id=store_id)
            
        from_date = self.request.query_params.get('from')
        if from_date:
            qs = qs.filter(date__gte=from_date)
            
        to_date = self.request.query_params.get('to')
        if to_date:
            qs = qs.filter(date__lte=to_date)

        return qs

    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'my_shifts', 'claim', 'swap_candidates']:
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsAdminRole()]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=False, methods=['post'], url_path='create-emergency')
    def create_emergency(self, request):
        """Admin tạo ca khẩn cấp"""
        data = request.data.copy()
        
        data['is_emergency'] = True
        
        if 'is_open' not in data:
            data['is_open'] = True
            
        if 'salary_multiplier' not in data or float(data.get('salary_multiplier', 1.0)) < 1.0:
            data['salary_multiplier'] = 1.5  
            
        serializer = self.get_serializer(data=data)
        if serializer.is_valid():
            phones = request.data.get('phones', [])
            
            shift, error = ShiftService.create_emergency_shift(
                validated_data=serializer.validated_data,
                phones=phones,
                creator=request.user
            )
            if error:
                status_code = status.HTTP_500_INTERNAL_SERVER_ERROR if error.startswith("Lỗi hệ thống") else status.HTTP_400_BAD_REQUEST
                return Response({"detail": error}, status=status_code)

            return Response({
                "message": "Tạo ca khẩn cấp thành công.",
                "shift": self.get_serializer(shift).data
            }, status=status.HTTP_201_CREATED)
            
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='assignments')
    def assign(self, request, pk=None):
        """Admin phân công nhân viên vào ca"""
        shift = self.get_object()
        serializer = AssignSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user_id = serializer.validated_data['user_id']
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({"detail": "Nhân viên không tồn tại."}, status=status.HTTP_404_NOT_FOUND)

        assignment, error = ShiftService.claim_open_shift(user=user, shift=shift, assigned_by=request.user)
        if not assignment:
            return Response({"detail": error}, status=status.HTTP_400_BAD_REQUEST)

        NotificationService.notify_shift_assigned(user, shift, request.user)

        return Response({
            "message": "Phân công nhân viên thành công.",
            "assignment": ShiftAssignmentSerializer(assignment).data
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['delete'], url_path=r'assignments/(?P<user_id>\d+)')
    def unassign(self, request, pk=None, user_id=None):
        """Admin hủy phân công nhân viên"""
        shift = self.get_object()

        if ShiftService.is_shift_past(shift):
            return Response({"detail": "Không thể hủy phân công của ca đã diễn ra."}, status=status.HTTP_400_BAD_REQUEST)

        if not user_id:
            return Response({"detail": "Vui lòng cung cấp user_id."}, status=status.HTTP_400_BAD_REQUEST)

        updated = ShiftAssignment.objects.filter(
            shift=shift,
            user_id=user_id,
            status=ShiftAssignment.Status.CONFIRMED
        ).update(status=ShiftAssignment.Status.CANCELLED)
        if not updated:
            return Response({"detail": "Không tìm thấy phân công này."}, status=status.HTTP_404_NOT_FOUND)

        # Mở lại ca trống nếu số người confirmed giảm dưới min_workers
        ShiftService.auto_update_shift_status(shift)

        try:
            unassigned_user = User.objects.get(id=user_id)
            NotificationService.notify_shift_unassigned(unassigned_user, shift)
        except User.DoesNotExist:
            pass

        return Response({"message": "Hủy phân công thành công."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def claim(self, request, pk=None):
        """Nhân viên đăng ký nguyện vọng nhận ca trống"""
        shift = self.get_object()
        
        assignment, error, is_auto_confirmed = ShiftService.employee_claim_shift(shift, request.user)
        if error:
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR if error.startswith("Lỗi hệ thống") else status.HTTP_400_BAD_REQUEST
            return Response({"detail": error}, status=status_code)

        if is_auto_confirmed:
            return Response({
                "message": "Bạn đã nhận ca khẩn cấp thành công. Ca đã được tự động xác nhận.",
                "assignment": ShiftAssignmentSerializer(assignment).data
            }, status=status.HTTP_201_CREATED)

        return Response({
            "message": "Đăng ký nguyện vọng thành công. Vui lòng chờ Admin duyệt.",
            "assignment": ShiftAssignmentSerializer(assignment).data
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path=r'assignments/(?P<assignment_id>\d+)/approve')
    def approve_claim(self, request, pk=None, assignment_id=None):
        """Admin duyệt nguyện vọng"""
        shift = self.get_object()
        
        assignment, error = ShiftService.approve_shift_claim(shift, assignment_id, request.user)
        if error:
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR if error.startswith("Lỗi hệ thống") else status.HTTP_400_BAD_REQUEST
            if error == "Không tìm thấy nguyện vọng chờ duyệt.":
                status_code = status.HTTP_404_NOT_FOUND
            return Response({"detail": error}, status=status_code)

        return Response({"message": f"Đã duyệt nguyện vọng của {assignment.user.full_name}."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path=r'assignments/(?P<assignment_id>\d+)/reject')
    def reject_claim(self, request, pk=None, assignment_id=None):
        """Admin từ chối nguyện vọng"""
        shift = self.get_object()
        try:
            assignment = ShiftAssignment.objects.get(id=assignment_id, shift=shift, status=ShiftAssignment.Status.PENDING)
        except ShiftAssignment.DoesNotExist:
            return Response({"detail": "Không tìm thấy nguyện vọng chờ duyệt."}, status=status.HTTP_404_NOT_FOUND)

        assignment.status = ShiftAssignment.Status.CANCELLED
        assignment.save()

        NotificationService.notify_claim_rejected(assignment)

        return Response({"message": f"Đã từ chối nguyện vọng của {assignment.user.full_name}."}, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='my-shifts')
    def my_shifts(self, request):
        """Nhân viên xem lịch ca của bản thân       """
        qs = Shift.objects.filter(
            assignments__user=request.user,
            assignments__status=ShiftAssignment.Status.CONFIRMED
        ).order_by('date', 'start_time')

        from_date = request.query_params.get('from')
        to_date = request.query_params.get('to')
        if from_date:
            qs = qs.filter(date__gte=from_date)
        if to_date:
            qs = qs.filter(date__lte=to_date)

        serializer = ShiftSerializer(qs, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'], url_path='swap-candidates')
    def swap_candidates(self, request):
        """Danh sách ca có nhân viên khác được phân công — để tìm đối tác đổi ca"""
        from_date = request.query_params.get('from', str(timezone.now().date()))
        to_date = request.query_params.get('to')
        employee_id = request.query_params.get('employee')

        qs = Shift.objects.prefetch_related('assignments__user').filter(
            date__gte=from_date,
            assignments__status=ShiftAssignment.Status.CONFIRMED,
        ).distinct().order_by('date', 'start_time')

        if to_date:
            qs = qs.filter(date__lte=to_date)
        if employee_id:
            qs = qs.filter(assignments__user_id=employee_id, assignments__status=ShiftAssignment.Status.CONFIRMED)

        data = []
        for shift in qs:
            confirmed = [
                a for a in shift.assignments.all()
                if a.status == ShiftAssignment.Status.CONFIRMED and str(a.user_id) != str(request.user.id)
            ]
            if employee_id:
                confirmed = [a for a in confirmed if str(a.user_id) == str(employee_id)]
            if confirmed:
                data.append({
                    'id': shift.id,
                    'date': str(shift.date),
                    'start_time': str(shift.start_time)[:5],
                    'end_time': str(shift.end_time)[:5],
                    'employees': [
                        {'id': a.user.id, 'full_name': a.user.full_name, 'phone': a.user.phone}
                        for a in confirmed
                    ],
                })

        return Response(data)


class ShiftSwapRequestViewSet(viewsets.ModelViewSet):
    """Quản lý yêu cầu đổi / tranh ca"""
    queryset = ShiftSwapRequest.objects.all().order_by('-created_at')
    serializer_class = ShiftSwapRequestSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if hasattr(user, 'role') and user.role == 'admin':
            return ShiftSwapRequest.objects.all().order_by('-created_at')
        return ShiftSwapRequest.objects.filter(Q(requester=user) | Q(partner=user)).order_by('-created_at')
    def get_permissions(self):
        if self.action in ['update', 'partial_update', 'destroy']:
            return [IsAuthenticated(), IsAdminRole()]
        return [IsAuthenticated()]
    def perform_create(self, serializer):
        user = self.request.user
        partner = serializer.validated_data.get('partner')
        swap_type = serializer.validated_data.get('swap_type')

        if not partner or swap_type == ShiftSwapRequest.SwapType.CLAIM:
            swap = serializer.save(requester=user, status=ShiftSwapRequest.Status.PARTNER_ACCEPTED)
        else:
            swap = serializer.save(requester=user, status=ShiftSwapRequest.Status.PENDING)
            NotificationService.notify_swap_requested(swap)

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated], url_path='partner-accept')
    def partner_accept(self, request, pk=None):
        """NV B (partner) xác nhận đồng ý yêu cầu đổi/nhượng ca"""
        try:
            with transaction.atomic():
                swap = ShiftSwapRequest.objects.select_for_update().get(pk=pk)
                self.check_object_permissions(request, swap)

                if request.user != swap.partner:
                    return Response({"detail": "Bạn không có quyền xác nhận yêu cầu này."}, status=status.HTTP_403_FORBIDDEN)
                
                if swap.status != ShiftSwapRequest.Status.PENDING:
                    return Response({"detail": "Yêu cầu này không ở trạng thái chờ bạn xác nhận."}, status=status.HTTP_400_BAD_REQUEST)

                swap.status = ShiftSwapRequest.Status.PARTNER_ACCEPTED
                swap.save()
                NotificationService.notify_swap_partner_accepted(swap)
                return Response({"message": "Bạn đã xác nhận đồng ý yêu cầu đổi/nhượng ca. Vui lòng chờ Admin duyệt."}, status=status.HTTP_200_OK)
        except ShiftSwapRequest.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated], url_path='partner-reject')
    def partner_reject(self, request, pk=None):
        """NV B (partner) từ chối yêu cầu đổi/nhượng ca"""
        try:
            with transaction.atomic():
                swap = ShiftSwapRequest.objects.select_for_update().get(pk=pk)
                self.check_object_permissions(request, swap)

                if request.user != swap.partner:
                    return Response({"detail": "Bạn không có quyền từ chối yêu cầu này."}, status=status.HTTP_403_FORBIDDEN)
                
                if swap.status != ShiftSwapRequest.Status.PENDING:
                    return Response({"detail": "Yêu cầu này không ở trạng thái chờ bạn xác nhận."}, status=status.HTTP_400_BAD_REQUEST)

                swap.status = ShiftSwapRequest.Status.PARTNER_REJECTED
                swap.resolved_by = request.user
                swap.resolved_at = timezone.now()
                swap.save()
                NotificationService.notify_swap_partner_rejected(swap)
                return Response({"message": "Bạn đã từ chối yêu cầu đổi/nhượng ca."}, status=status.HTTP_200_OK)
        except ShiftSwapRequest.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)


    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated, IsAdminRole])
    def approve(self, request, pk=None):
        """Admin duyệt yêu cầu đổi/tranh/nhượng ca"""
        swap = self.get_object()

        message, error = ShiftService.approve_swap_request(swap, request.user)
        if error:
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR if error.startswith("Lỗi hệ thống") else status.HTTP_400_BAD_REQUEST
            return Response({"detail": error}, status=status_code)

        return Response({"message": message}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated, IsAdminRole])
    def reject(self, request, pk=None):
        """Admin từ chối yêu cầu đổi ca"""
        try:
            with transaction.atomic():
                swap = ShiftSwapRequest.objects.select_for_update().get(pk=pk)
                self.check_object_permissions(request, swap)

                if swap.status not in [ShiftSwapRequest.Status.PENDING, ShiftSwapRequest.Status.PARTNER_ACCEPTED]:
                    return Response({"detail": "Yêu cầu này không còn ở trạng thái chờ duyệt."}, status=status.HTTP_400_BAD_REQUEST)

                swap.status = ShiftSwapRequest.Status.REJECTED
                swap.resolved_by = request.user
                swap.resolved_at = timezone.now()
                swap.save()

                NotificationService.notify_swap_rejected(swap)

                return Response({"message": "Yêu cầu đổi ca đã bị từ chối."}, status=status.HTTP_200_OK)
        except ShiftSwapRequest.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated])
    def cancel(self, request, pk=None):
        """Người tạo hủy yêu cầu đổi / nhượng / tranh ca"""
        try:
            with transaction.atomic():
                swap = ShiftSwapRequest.objects.select_for_update().get(pk=pk)
                self.check_object_permissions(request, swap)

                if request.user != swap.requester:
                    return Response({"detail": "Bạn không có quyền hủy yêu cầu này."}, status=status.HTTP_403_FORBIDDEN)
                
                if swap.status not in [ShiftSwapRequest.Status.PENDING, ShiftSwapRequest.Status.PARTNER_ACCEPTED]:
                    return Response({"detail": "Chỉ có thể hủy yêu cầu khi đang chờ xác nhận hoặc chờ duyệt."}, status=status.HTTP_400_BAD_REQUEST)

                swap.status = ShiftSwapRequest.Status.CANCELLED
                swap.resolved_by = request.user
                swap.resolved_at = timezone.now()
                swap.save()

                NotificationService.notify_swap_cancelled(swap)

                return Response({"message": "Đã hủy yêu cầu thành công."}, status=status.HTTP_200_OK)
        except ShiftSwapRequest.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
