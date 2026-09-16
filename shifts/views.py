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
        if self.action in ['list', 'retrieve', 'my_shifts', 'claim']:
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
            users_to_assign = []
            
            #  Kiểm tra số điện thoại 
            if isinstance(phones, list) and phones:
                users_to_assign = list(User.objects.filter(phone__in=phones))
                found_phones = set(u.phone for u in users_to_assign)
                missing_phones = set(phones) - found_phones
                
                if missing_phones:
                    return Response({
                        "detail": f"Không tìm thấy nhân viên với số điện thoại: {', '.join(missing_phones)}"
                    }, status=status.HTTP_400_BAD_REQUEST)    
            try:
                with transaction.atomic():
                    shift = serializer.save(created_by=request.user)
                    
                    # Phân công trực tiếp cho nhân viên theo số điện thoại
                    if users_to_assign:
                        for user in users_to_assign:
                            ShiftAssignment.objects.get_or_create(
                                shift=shift,
                                user=user,
                                defaults={
                                    'assigned_by': request.user,
                                    'status': ShiftAssignment.Status.CONFIRMED
                                }
                            )
                            
                        # Tự động đóng ca nếu đã phân công đủ người
                        ShiftService.auto_update_shift_status(shift)

                    return Response({
                        "message": "Tạo ca khẩn cấp thành công.",
                        "shift": self.get_serializer(shift).data
                    }, status=status.HTTP_201_CREATED)
            except Exception as e:
                return Response({"detail": f"Lỗi hệ thống: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
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

        return Response({"message": "Hủy phân công thành công."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def claim(self, request, pk=None):
        """Nhân viên đăng ký nguyện vọng nhận ca trống"""
        try:
            with transaction.atomic():

                shift = Shift.objects.select_for_update().get(pk=pk)
                self.check_object_permissions(request, shift)

                # 1. Không cho đăng ký ca đã qua hoặc đã kết thúc
                if ShiftService.is_shift_ended(shift):
                    return Response({
                        "detail": "Ca làm việc này đã kết thúc, không thể đăng ký."
                    }, status=status.HTTP_400_BAD_REQUEST)

                # 2. Kiểm tra ca có đang mở không
                if not shift.is_open:
                    return Response({"detail": "Ca này không phải ca trống hoặc đã đủ người, không thể đăng ký."}, status=status.HTTP_400_BAD_REQUEST)

                assignment, error = ShiftService.claim_open_shift(user=request.user, shift=shift, assigned_by=None)
                if error:
                    return Response({"detail": error}, status=status.HTTP_400_BAD_REQUEST)

                # 3. Phê duyệt tự động nếu là ca khẩn cấp
                if shift.is_emergency:
                    assignment.status = ShiftAssignment.Status.CONFIRMED
                    assignment.save()
                    
                    # Khóa ca nếu đã đủ người
                    ShiftService.auto_update_shift_status(shift)
                        
                    return Response({
                        "message": "Bạn đã nhận ca khẩn cấp thành công. Ca đã được tự động xác nhận.",
                        "assignment": ShiftAssignmentSerializer(assignment).data
                    }, status=status.HTTP_201_CREATED)

                return Response({
                    "message": "Đăng ký nguyện vọng thành công. Vui lòng chờ Admin duyệt.",
                    "assignment": ShiftAssignmentSerializer(assignment).data
                }, status=status.HTTP_201_CREATED)

        except Shift.DoesNotExist:
            return Response({"detail": "Không tìm thấy ca này."}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=True, methods=['post'], url_path=r'assignments/(?P<assignment_id>\d+)/approve')
    def approve_claim(self, request, pk=None, assignment_id=None):
        """Admin duyệt nguyện vọng"""
        try:
            with transaction.atomic():
                shift = Shift.objects.select_for_update().get(pk=pk)
                self.check_object_permissions(request, shift)
                
                try:
                    assignment = ShiftAssignment.objects.get(id=assignment_id, shift=shift, status=ShiftAssignment.Status.PENDING)
                except ShiftAssignment.DoesNotExist:
                    return Response({"detail": "Không tìm thấy nguyện vọng chờ duyệt."}, status=status.HTTP_404_NOT_FOUND)

                # Kiểm tra nhân viên có bị trùng ca cùng khung giờ không 
                if ShiftService.has_overlapping_shift(assignment.user, shift, exclude_assignment_ids=[assignment.id]):
                    return Response({"detail": f"{assignment.user.full_name} đã có ca trùng khung giờ trong ngày này."}, status=status.HTTP_400_BAD_REQUEST)

                # Duyệt nguyện vọng này
                assignment.status = ShiftAssignment.Status.CONFIRMED
                assignment.assigned_by = request.user
                assignment.save()

                # Kiểm tra đã đủ max_workers chưa
                is_open = ShiftService.auto_update_shift_status(shift)
                if not is_open:
                    ShiftAssignment.objects.filter(
                        shift=shift,
                        status=ShiftAssignment.Status.PENDING
                    ).update(status=ShiftAssignment.Status.CANCELLED)

                return Response({"message": f"Đã duyệt nguyện vọng của {assignment.user.full_name}."}, status=status.HTTP_200_OK)
        except Shift.DoesNotExist:
            return Response({"detail": "Không tìm thấy ca này."}, status=status.HTTP_404_NOT_FOUND)

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
            serializer.save(requester=user, status=ShiftSwapRequest.Status.PARTNER_ACCEPTED)
        else:
            serializer.save(requester=user, status=ShiftSwapRequest.Status.PENDING)

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
                return Response({"message": "Bạn đã từ chối yêu cầu đổi/nhượng ca."}, status=status.HTTP_200_OK)
        except ShiftSwapRequest.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)


    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated, IsAdminRole])
    def approve(self, request, pk=None):
        """Admin duyệt yêu cầu đổi/tranh/nhượng ca"""
        try:
            with transaction.atomic():
                swap = ShiftSwapRequest.objects.select_for_update().get(pk=pk)
                self.check_object_permissions(request, swap)

                if swap.status != ShiftSwapRequest.Status.PARTNER_ACCEPTED:
                    return Response({"detail": "Yêu cầu này không ở trạng thái chờ Admin duyệt."}, status=status.HTTP_400_BAD_REQUEST)

                # TH1: Đổi ca
                if swap.swap_type == ShiftSwapRequest.SwapType.EXCHANGE and swap.offered_shift:
                    if not swap.partner:
                        return Response({"detail": "Yêu cầu đổi ca phải có người chấp nhận."}, status=status.HTTP_400_BAD_REQUEST)

                    target_shift = swap.target_shift
                    offered_shift = swap.offered_shift

                    # Tìm assignment của NV A
                    assignment_a = ShiftAssignment.objects.select_for_update().get(
                        shift=offered_shift, user=swap.requester, status=ShiftAssignment.Status.CONFIRMED
                    )
                    # Tìm assignment của NV B (partner)
                    assignment_b = ShiftAssignment.objects.select_for_update().get(
                        shift=target_shift, user=swap.partner, status=ShiftAssignment.Status.CONFIRMED
                    )

                    # Kiểm tra NV A có trùng ca với target_shift không
                    if ShiftService.has_overlapping_shift(swap.requester, target_shift, exclude_assignment_ids=[assignment_a.id]):
                        raise ValueError(f"{swap.requester.full_name} bị trùng khung giờ với ca muốn đổi.")

                    # Kiểm tra NV B có trùng ca với offered_shift không
                    if ShiftService.has_overlapping_shift(swap.partner, offered_shift, exclude_assignment_ids=[assignment_b.id]):
                        raise ValueError(f"{swap.partner.full_name} bị trùng khung giờ với ca được đổi sang.")

                    assignment_a.user = swap.partner
                    assignment_b.user = swap.requester
                    assignment_a.save()
                    assignment_b.save()

                # TH2: Nhượng ca
                elif swap.swap_type == ShiftSwapRequest.SwapType.TRANSFER and swap.offered_shift:
                    if not swap.partner:
                        return Response({"detail": "Yêu cầu nhượng ca phải có người nhận (partner)."}, 
                                       status=status.HTTP_400_BAD_REQUEST)

                    offered_shift = swap.offered_shift

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

                    if ShiftService.has_overlapping_shift(swap.partner, offered_shift, exclude_shift=offered_shift):
                        return Response({
                            "detail": f"{swap.partner.full_name} đã có ca làm trùng khung giờ với ca được nhượng."
                        }, status=status.HTTP_400_BAD_REQUEST)

                    assignment_requester.status = ShiftAssignment.Status.CANCELLED
                    assignment_requester.save()
                    ShiftAssignment.objects.create(
                        shift=offered_shift,
                        user=swap.partner,
                        assigned_by=request.user,
                        status=ShiftAssignment.Status.CONFIRMED
                    )

                    ShiftService.auto_update_shift_status(offered_shift)

                # TH3: Tranh ca
                elif swap.swap_type == ShiftSwapRequest.SwapType.CLAIM:
                    assignment, error = ShiftService.claim_open_shift(user=swap.requester, shift=swap.target_shift, assigned_by=request.user)
                    if not assignment:
                        return Response({"detail": error}, status=status.HTTP_400_BAD_REQUEST)

                swap.status = ShiftSwapRequest.Status.ACCEPTED
                swap.resolved_by = request.user
                swap.resolved_at = timezone.now()
                swap.save()

                # Thông báo phản hồi chung
                msg_map = {
                    ShiftSwapRequest.SwapType.EXCHANGE: f"Đã duyệt đổi ca thành công giữa {swap.requester.full_name} và {swap.partner.full_name}.",
                    ShiftSwapRequest.SwapType.TRANSFER: f"Đã duyệt nhượng ca thành công. {swap.partner.full_name} đã nhận thêm ca.",
                    ShiftSwapRequest.SwapType.CLAIM: f"Đã duyệt tranh ca thành công cho {swap.requester.full_name}."
                }
                return Response({"message": msg_map.get(swap.swap_type, "Đã duyệt yêu cầu thành công.")}, status=status.HTTP_200_OK)

        except ShiftSwapRequest.DoesNotExist:
            return Response({"detail": "Không tìm thấy yêu cầu."}, status=status.HTTP_404_NOT_FOUND)
        except ShiftAssignment.DoesNotExist:
            return Response({"detail": "Một trong hai nhân viên không còn phân công tại ca này."}, status=status.HTTP_400_BAD_REQUEST)
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"detail": f"Lỗi hệ thống: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

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

                return Response({"message": "Đã hủy yêu cầu thành công."}, status=status.HTTP_200_OK)
        except ShiftSwapRequest.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
