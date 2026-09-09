from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import date

from accounts.permissions import IsAdminRole
from .models import ShiftTemplate, Shift, ShiftAssignment, ShiftSwapRequest
from .serializers import (
    ShiftTemplateSerializer, ShiftSerializer,
    ShiftAssignmentSerializer, AssignSerializer,
    ShiftSwapRequestSerializer,
)
from django.db import transaction
User = get_user_model()

def claim_open_shift(user, shift, from_user=None, assigned_by=None):
    # 1. Kiểm tra trùng lịch (loại trừ chính ca đang nhượng nếu có)
    overlapping_qs = ShiftAssignment.objects.filter(
        user=user,
        status__in=[ShiftAssignment.Status.PENDING, ShiftAssignment.Status.CONFIRMED],
        shift__date=shift.date,
        shift__start_time__lt=shift.end_time,
        shift__end_time__gt=shift.start_time,
    )
    if from_user:
        overlapping_qs = overlapping_qs.exclude(shift=shift)
        
    if overlapping_qs.exists():
        return None, "Bạn đã có ca khác trùng khung giờ này."

    try:
        with transaction.atomic():
            # 2. Xử lý "Nhượng ca" nếu có người nhượng cụ thể
            if from_user:
                target_assignment = ShiftAssignment.objects.filter(
                    shift=shift, 
                    user=from_user, 
                    status=ShiftAssignment.Status.CONFIRMED
                ).first()
                
                if not target_assignment:
                    return None, "Người nhượng không còn giữ ca này hoặc thông tin không hợp lệ."
                
                target_assignment.status = ShiftAssignment.Status.CANCELLED 
                target_assignment.save()

            # 3. Đăng ký ca
            assignment, created = ShiftAssignment.objects.get_or_create(
                shift=shift,
                user=user,
                defaults={
                    'assigned_by': assigned_by,
                    'status': ShiftAssignment.Status.CONFIRMED if assigned_by else ShiftAssignment.Status.PENDING
                }
            )
            
            if not created:
                if assignment.status == ShiftAssignment.Status.CANCELLED:
                    assignment.status = ShiftAssignment.Status.PENDING
                    assignment.assigned_by = assigned_by
                    assignment.save()
                else:
                    return None, "Bạn đã đăng ký hoặc đang chờ duyệt ca này rồi."

            return assignment, None

    except Exception as e:
        return None, f"Lỗi hệ thống: {str(e)}"


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
        if self.action in ['list', 'retrieve', 'my_shifts', 'claim']:
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsAdminRole()]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=['post'])
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

        assignment, error = claim_open_shift(user=user, shift=shift, assigned_by=request.user)
        if not assignment:
            return Response({"detail": error}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            "message": "Phân công nhân viên thành công.",
            "assignment": ShiftAssignmentSerializer(assignment).data
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['delete'])
    def unassign(self, request, pk=None):
        """Admin hủy phân công nhân viên"""
        shift = self.get_object()

        if shift.date < date.today():
            return Response({"detail": "Không thể hủy phân công của ca đã diễn ra."}, status=status.HTTP_400_BAD_REQUEST)

        user_id = request.query_params.get('user_id')
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
        confirmed_count = ShiftAssignment.objects.filter(shift=shift, status=ShiftAssignment.Status.CONFIRMED).count()
        if shift.template and confirmed_count < shift.template.min_workers:
            shift.is_open = True
            shift.save()

        return Response({"message": "Hủy phân công thành công."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def claim(self, request, pk=None):
        """Nhân viên đăng ký nguyện vọng nhận ca trống"""
        shift = self.get_object()

        # 1. Không cho đăng ký ca đã qua
        if shift.date < date.today():
            return Response({"detail": "Không thể đăng ký ca đã diễn ra."}, status=status.HTTP_400_BAD_REQUEST)

        # 2. Kiểm tra ca có đang mở không
        if not shift.is_open:
            return Response({"detail": "Ca này không phải ca trống, không thể đăng ký."}, status=status.HTTP_400_BAD_REQUEST)

        assignment, error = claim_open_shift(user=request.user, shift=shift, assigned_by=None)
        if error:
            return Response({"detail": error}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            "message": "Đăng ký nguyện vọng thành công. Vui lòng chờ Admin duyệt.",
            "assignment": ShiftAssignmentSerializer(assignment).data
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='approve-claim/(?P<assignment_id>[^/.]+)')
    def approve_claim(self, request, pk=None, assignment_id=None):
        """Admin duyệt nguyện vọng"""
        shift = self.get_object()
        try:
            assignment = ShiftAssignment.objects.get(id=assignment_id, shift=shift, status=ShiftAssignment.Status.PENDING)
        except ShiftAssignment.DoesNotExist:
            return Response({"detail": "Không tìm thấy nguyện vọng chờ duyệt."}, status=status.HTTP_404_NOT_FOUND)

        # Kiểm tra nhân viên có bị trùng ca cùng khung giờ không 
        overlapping = ShiftAssignment.objects.filter(
            user=assignment.user,
            status=ShiftAssignment.Status.CONFIRMED,
            shift__date=shift.date,
            shift__start_time__lt=shift.end_time,
            shift__end_time__gt=shift.start_time,
        ).exists()
        if overlapping:
            return Response({"detail": f"{assignment.user.full_name} đã có ca trùng khung giờ trong ngày này."}, status=status.HTTP_400_BAD_REQUEST)

        # Duyệt nguyện vọng này
        assignment.status = ShiftAssignment.Status.CONFIRMED
        assignment.assigned_by = request.user
        assignment.save()

        # Kiểm tra đã đủ max_workers chưa
        confirmed_count = ShiftAssignment.objects.filter(shift=shift, status=ShiftAssignment.Status.CONFIRMED).count()
        if shift.template and confirmed_count >= shift.template.max_workers:
            shift.is_open = False
            shift.save()
            ShiftAssignment.objects.filter(
                shift=shift,
                status=ShiftAssignment.Status.PENDING
            ).update(status=ShiftAssignment.Status.CANCELLED)

        return Response({"message": f"Đã duyệt nguyện vọng của {assignment.user.full_name}."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='reject-claim/(?P<assignment_id>[^/.]+)')
    def reject_claim(self, request, pk=None, assignment_id=None):
        """Admin từ chối nguyện vọng"""
        shift = self.get_object()
        try:
            assignment = ShiftAssignment.objects.get(id=assignment_id, shift=shift, status=ShiftAssignment.Status.PENDING)
        except ShiftAssignment.DoesNotExist:
            return Response({"detail": "Không tìm thấy nguyện vọng chờ duyệt."}, status=status.HTTP_404_NOT_FOUND)

        assignment.status = ShiftAssignment.Status.CANCELLED
        assignment.save()
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
        return ShiftSwapRequest.objects.filter(requester=user).order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(requester=self.request.user)

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated, IsAdminRole])
    def approve(self, request, pk=None):
        """Admin duyệt yêu cầu đổi/tranh/nhượng ca"""
        swap = self.get_object()
        if swap.status != ShiftSwapRequest.Status.PENDING:
            return Response({"detail": "Yêu cầu này không còn ở trạng thái chờ duyệt."}, status=status.HTTP_400_BAD_REQUEST)
        # TH1: Đổi ca
        if swap.swap_type == ShiftSwapRequest.SwapType.EXCHANGE and swap.offered_shift:
            if not swap.partner:
                return Response({"detail": "Yêu cầu đổi ca phải có người chấp nhận."}, status=status.HTTP_400_BAD_REQUEST)

            target_shift = swap.target_shift
            offered_shift = swap.offered_shift

            try:
                with transaction.atomic():
                    # Tìm assignment của NV A
                    assignment_a = ShiftAssignment.objects.select_for_update().get(
                        shift=offered_shift, user=swap.requester, status=ShiftAssignment.Status.CONFIRMED
                    )
                    # Tìm assignment của NV B (partner)
                    assignment_b = ShiftAssignment.objects.select_for_update().get(
                        shift=target_shift, user=swap.partner, status=ShiftAssignment.Status.CONFIRMED
                    )

                    # Kiểm tra NV A có trùng ca với target_shift không
                    overlap_a = ShiftAssignment.objects.filter(
                        user=swap.requester, status=ShiftAssignment.Status.CONFIRMED,
                        shift__date=target_shift.date,
                        shift__start_time__lt=target_shift.end_time,
                        shift__end_time__gt=target_shift.start_time,
                    ).exclude(id=assignment_a.id).exists()
                    if overlap_a:
                        raise ValueError(f"{swap.requester.full_name} bị trùng khung giờ với ca muốn đổi.")

                    # Kiểm tra NV B có trùng ca với offered_shift không
                    overlap_b = ShiftAssignment.objects.filter(
                        user=swap.partner, status=ShiftAssignment.Status.CONFIRMED,
                        shift__date=offered_shift.date,
                        shift__start_time__lt=offered_shift.end_time,
                        shift__end_time__gt=offered_shift.start_time,
                    ).exclude(id=assignment_b.id).exists()
                    if overlap_b:
                        raise ValueError(f"{swap.partner.full_name} bị trùng khung giờ với ca được đổi sang.")

                    assignment_a.user = swap.partner
                    assignment_b.user = swap.requester
                    assignment_a.save()
                    assignment_b.save()

            except ShiftAssignment.DoesNotExist:
                return Response({"detail": "Một trong hai nhân viên không còn phân công tại ca này."}, status=status.HTTP_400_BAD_REQUEST)
            except ValueError as e:
                return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        # TH2: Nhượng ca
        elif swap.swap_type == ShiftSwapRequest.SwapType.TRANSFER and swap.offered_shift:
            if not swap.partner:
                return Response({"detail": "Yêu cầu nhượng ca phải có người nhận (partner)."}, 
                               status=status.HTTP_400_BAD_REQUEST)

            offered_shift = swap.offered_shift
            try:
                with transaction.atomic():
                    try:
                        assignment_requester = ShiftAssignment.objects.select_for_update().get(
                            shift=offered_shift,
                            user=swap.requester,
                            status=ShiftAssignment.Status.CONFIRMED
                        )
                    except ShiftAssignment.DoesNotExist:
                        return Response({"detail": "Người nhượng không còn giữ ca này nữa."}, 
                                    status=status.HTTP_400_BAD_REQUEST)

                    if ShiftAssignment.objects.filter(
                        shift=offered_shift,
                        user=swap.partner,
                        status__in=[ShiftAssignment.Status.PENDING, ShiftAssignment.Status.CONFIRMED]
                    ).exists():
                        return Response({"detail": f"{swap.partner.full_name} đã đăng ký hoặc đang giữ ca này rồi."}, 
                                    status=status.HTTP_400_BAD_REQUEST)
                    overlapping = ShiftAssignment.objects.filter(
                        user=swap.partner,
                        status__in=[ShiftAssignment.Status.PENDING, ShiftAssignment.Status.CONFIRMED],
                        shift__date=offered_shift.date,
                        shift__start_time__lt=offered_shift.end_time,
                        shift__end_time__gt=offered_shift.start_time,
                    ).exists()

                    if overlapping:
                        return Response({
                            "detail": f"{swap.partner.full_name} đã có ca làm trùng khung giờ với ca được nhượng."
                        }, status=status.HTTP_400_BAD_REQUEST)

                    assignment_requester.status = ShiftAssignment.Status.CANCELLED
                    assignment_requester.save()
                    new_assignment = ShiftAssignment.objects.create(
                        shift=offered_shift,
                        user=swap.partner,
                        assigned_by=request.user,
                        status=ShiftAssignment.Status.CONFIRMED
                    )

                    confirmed_count = ShiftAssignment.objects.filter(
                        shift=offered_shift,
                        status=ShiftAssignment.Status.CONFIRMED
                    ).count()

                    if offered_shift.template and confirmed_count >= offered_shift.template.max_workers:
                        offered_shift.is_open = False
                        offered_shift.save()

            except Exception as e:
                return Response({"detail": f"Lỗi hệ thống khi xử lý nhượng ca: {str(e)}"}, 
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            swap.status = ShiftSwapRequest.Status.ACCEPTED
            swap.resolved_by = request.user
            swap.resolved_at = timezone.now()
            swap.save()

            return Response({
                "message": f"Đã duyệt nhượng ca thành công. {swap.partner.full_name} đã nhận thêm ca."
            }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated, IsAdminRole])
    def reject(self, request, pk=None):
        """Admin từ chối yêu cầu đổi ca"""
        swap = self.get_object()
        if swap.status != ShiftSwapRequest.Status.PENDING:
            return Response({"detail": "Yêu cầu này không còn ở trạng thái chờ duyệt."}, status=status.HTTP_400_BAD_REQUEST)

        swap.status = ShiftSwapRequest.Status.REJECTED
        swap.resolved_by = request.user
        swap.resolved_at = timezone.now()
        swap.save()

        return Response({"message": "Yêu cầu đổi ca đã bị từ chối."}, status=status.HTTP_200_OK)
