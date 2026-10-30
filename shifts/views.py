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

        if self.action == 'list' and hasattr(user, 'role') and user.role not in ('admin', 'manager'):
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
        if self.action in ['list', 'retrieve', 'my_shifts', 'claim', 'cancel_my_assignment', 'swap_candidates']:
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsAdminRole()]

    @action(detail=False, methods=['post'], url_path='bulk-from-template')
    def bulk_from_template(self, request):
        """Tạo nhiều ca từ template theo danh sách ngày"""
        from datetime import date as date_type
        template_id = request.data.get('template_id')
        dates = request.data.get('dates', [])
        max_workers = request.data.get('max_workers')
        salary_multiplier = request.data.get('salary_multiplier', '1.00')
        is_open = request.data.get('is_open', True)
        note = request.data.get('note', '')

        if not template_id or not dates:
            return Response({'error': 'Cần cung cấp template_id và danh sách dates.'}, status=400)

        try:
            template = ShiftTemplate.objects.get(pk=template_id)
        except ShiftTemplate.DoesNotExist:
            return Response({'error': 'Template không tồn tại.'}, status=404)

        created = []
        skipped = []
        for d in dates:
            try:
                shift, was_created = Shift.objects.get_or_create(
                    template=template,
                    date=d,
                    store=template.store,
                    defaults=dict(
                        start_time=template.start_time,
                        end_time=template.end_time,
                        max_workers=max_workers if max_workers is not None else template.max_workers,
                        salary_multiplier=salary_multiplier,
                        is_open=is_open,
                        note=note or template.name,
                        created_by=request.user,
                    )
                )
                if was_created:
                    created.append(str(d))
                else:
                    skipped.append(str(d))
            except Exception as e:
                skipped.append(str(d))

        return Response({
            'created': created,
            'skipped': skipped,
            'message': f'Đã tạo {len(created)} ca, bỏ qua {len(skipped)} ca đã tồn tại.'
        }, status=201)

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

    @action(detail=True, methods=['get'], url_path='available-employees')
    def available_employees(self, request, pk=None):
        """Lấy danh sách nhân viên khả dụng và bị trùng ca cho ca này"""
        shift = self.get_object()

        all_employees = User.objects.filter(is_active=True).exclude(
            role=User.Role.ADMIN
        ).order_by('full_name')

        # Lấy danh sách user_id đã được phân công (confirmed/pending) vào ca này
        already_assigned_ids = set(
            ShiftAssignment.objects.filter(
                shift=shift,
                status__in=[ShiftAssignment.Status.CONFIRMED, ShiftAssignment.Status.PENDING]
            ).values_list('user_id', flat=True)
        )

        # Lấy danh sách user_id có ca trùng khung giờ
        conflicting_user_ids = set(
            ShiftService.get_overlapping_assignments_for_shift(shift).values_list('user_id', flat=True)
        )

        available = []
        conflicting = []
        for emp in all_employees:
            if emp.id in already_assigned_ids:
                continue
            entry = {
                'id': emp.id,
                'full_name': emp.full_name,
                'phone': emp.phone or '',
                'role': emp.role,
            }
            if emp.id in conflicting_user_ids:
                # Lấy chi tiết ca trùng để hiển thị
                overlap_assignments = ShiftService.get_overlapping_assignments(
                    user=emp, shift=shift, exclude_shift=shift
                ).select_related('shift')[:3]
                entry['overlapping_shifts'] = [
                    {
                        'id': a.shift.id,
                        'start_time': str(a.shift.start_time)[:5],
                        'end_time': str(a.shift.end_time)[:5],
                    }
                    for a in overlap_assignments
                ]
                conflicting.append(entry)
            else:
                available.append(entry)

        return Response({
            'available': available,
            'conflicting': conflicting,
        })

    @action(detail=True, methods=['post'], url_path='assignments')
    def assign(self, request, pk=None):
        """Admin phân công nhân viên vào ca"""
        shift = self.get_object()
        if ShiftService.is_shift_ended(shift):
            return Response({"detail": "Ca làm việc này đã kết thúc, không thể thêm nhân viên."}, status=status.HTTP_400_BAD_REQUEST)

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

        ShiftService.auto_update_shift_status(shift)
        NotificationService.notify_shift_assigned(user, shift, request.user)

        return Response({
            "message": "Phân công nhân viên thành công.",
            "assignment": ShiftAssignmentSerializer(assignment).data
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['delete'], url_path=r'assignments/(?P<user_id>\d+)')
    def unassign(self, request, pk=None, user_id=None):
        """Admin hủy phân công nhân viên"""
        shift = self.get_object()

        if ShiftService.is_shift_ended(shift):
            return Response({"detail": "Không thể hủy phân công của ca đã kết thúc."}, status=status.HTTP_400_BAD_REQUEST)

        if not user_id:
            return Response({"detail": "Vui lòng cung cấp user_id."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            locked_shift = Shift.objects.select_for_update().get(pk=shift.pk)
            updated = ShiftAssignment.objects.filter(
                shift=locked_shift,
                user_id=user_id,
                status=ShiftAssignment.Status.CONFIRMED
            ).update(status=ShiftAssignment.Status.CANCELLED)
            if not updated:
                return Response({"detail": "Không tìm thấy phân công này."}, status=status.HTTP_404_NOT_FOUND)

            ShiftService.auto_update_shift_status(locked_shift)

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

    @action(detail=True, methods=['delete'], url_path='assignments/me')
    def cancel_my_assignment(self, request, pk=None):
        """Nhân viên tự hủy đăng ký ca — chỉ cho phép trước 24 giờ khi ca bắt đầu"""
        shift = self.get_object()
        assignment, error = ShiftService.employee_unclaim_shift(shift, request.user)
        if error:
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR if error.startswith("Lỗi hệ thống") else status.HTTP_400_BAD_REQUEST
            return Response({"detail": error}, status=status_code)
        return Response(status=status.HTTP_204_NO_CONTENT)

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

        # Exclude employees currently requesting to send or swap shifts
        active_requester_ids = ShiftSwapRequest.objects.filter(
            status__in=[ShiftSwapRequest.Status.PENDING, ShiftSwapRequest.Status.PARTNER_ACCEPTED]
        ).values_list('requester_id', flat=True)

        data = []
        for shift in qs:
            confirmed = [
                a for a in shift.assignments.all()
                if a.status == ShiftAssignment.Status.CONFIRMED 
                and str(a.user_id) != str(request.user.id)
                and a.user_id not in active_requester_ids
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
        if hasattr(user, 'role') and user.role in ('admin', 'manager'):
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

                from .services import ShiftService
                if swap.swap_type == ShiftSwapRequest.SwapType.TRANSFER:
                    #  Kiểm tra xem người nhượng đã có yêu cầu nhượng ca này được đồng ý bởi người khác chưa
                    conflict_give = ShiftSwapRequest.objects.filter(
                        requester=swap.requester,
                        offered_shift_id=swap.offered_shift_id,
                        status=ShiftSwapRequest.Status.PARTNER_ACCEPTED
                    ).exclude(id=swap.id).exists()
                    if conflict_give:
                        return Response({"detail": "Người nhượng đã có yêu cầu nhượng ca này được người khác chấp nhận."}, status=status.HTTP_400_BAD_REQUEST)

                    #  Kiểm tra xem bạn (partner) đã đồng ý nhận ca này từ yêu cầu khác chưa
                    conflict_receive = ShiftSwapRequest.objects.filter(
                        Q(partner=swap.partner, offered_shift_id=swap.offered_shift_id) |
                        Q(requester=swap.partner, target_shift_id=swap.offered_shift_id),
                        status=ShiftSwapRequest.Status.PARTNER_ACCEPTED
                    ).exclude(id=swap.id).exists()
                    if conflict_receive:
                        return Response({"detail": "Bạn đã đồng ý nhận ca làm việc này từ một yêu cầu khác."}, status=status.HTTP_400_BAD_REQUEST)

                elif swap.swap_type == ShiftSwapRequest.SwapType.EXCHANGE:
                    #  Kiểm tra người yêu cầu (requester) đã có yêu cầu cho đi ca offered_shift được đồng ý bởi người khác chưa
                    conflict_req_give = ShiftSwapRequest.objects.filter(
                        requester=swap.requester,
                        offered_shift_id=swap.offered_shift_id,
                        status=ShiftSwapRequest.Status.PARTNER_ACCEPTED
                    ).exclude(id=swap.id).exists()
                    if conflict_req_give:
                        return Response({"detail": "Người yêu cầu đã có yêu cầu đổi ca này được người khác chấp nhận."}, status=status.HTTP_400_BAD_REQUEST)

                    # 2. Kiểm tra bạn (partner) đã có yêu cầu cho đi ca target_shift được đồng ý bởi người khác chưa
                    conflict_partner_give = ShiftSwapRequest.objects.filter(
                        Q(requester=swap.partner, offered_shift_id=swap.target_shift_id) |
                        Q(partner=swap.partner, target_shift_id=swap.target_shift_id),
                        status=ShiftSwapRequest.Status.PARTNER_ACCEPTED
                    ).exclude(id=swap.id).exists()
                    if conflict_partner_give:
                        return Response({"detail": "Bạn đã đồng ý đổi ca làm việc này của bạn trong một yêu cầu khác."}, status=status.HTTP_400_BAD_REQUEST)

                    # 3. Kiểm tra bạn (partner) đã đồng ý nhận ca offered_shift từ yêu cầu khác chưa
                    conflict_partner_receive = ShiftSwapRequest.objects.filter(
                        Q(partner=swap.partner, offered_shift_id=swap.offered_shift_id) |
                        Q(requester=swap.partner, target_shift_id=swap.offered_shift_id),
                        status=ShiftSwapRequest.Status.PARTNER_ACCEPTED
                    ).exclude(id=swap.id).exists()
                    if conflict_partner_receive:
                        return Response({"detail": "Bạn đã đồng ý nhận ca làm việc này từ một yêu cầu khác."}, status=status.HTTP_400_BAD_REQUEST)

                    # 4. Kiểm tra người yêu cầu (requester) đã đồng ý nhận ca target_shift từ yêu cầu khác chưa
                    conflict_req_receive = ShiftSwapRequest.objects.filter(
                        Q(partner=swap.requester, offered_shift_id=swap.target_shift_id) |
                        Q(requester=swap.requester, target_shift_id=swap.target_shift_id),
                        status=ShiftSwapRequest.Status.PARTNER_ACCEPTED
                    ).exclude(id=swap.id).exists()
                    if conflict_req_receive:
                        return Response({"detail": "Người yêu cầu đã đồng ý nhận ca làm việc này từ một yêu cầu khác."}, status=status.HTTP_400_BAD_REQUEST)

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
