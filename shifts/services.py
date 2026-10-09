from datetime import date, datetime
from django.utils import timezone
from django.db import transaction
from django.contrib.auth import get_user_model
from .models import Shift, ShiftAssignment, ShiftSwapRequest
from notifications.services import NotificationService

class ShiftService:
    @staticmethod
    def is_shift_ended(shift):
        """Kiểm tra ca đã kết thúc chưa so với thời gian hiện tại"""
        shift_end_datetime = timezone.make_aware(
            datetime.combine(shift.date, shift.end_time)
        )
        return shift_end_datetime <= timezone.now()

    @staticmethod
    def is_shift_past(shift):
        """Kiểm tra ngày diễn ra ca đã qua chưa (so với hôm nay)"""
        return shift.date < date.today()

    @staticmethod
    def has_overlapping_shift(user, shift, exclude_assignment_ids=None, exclude_shift=None):
        """Kiểm tra nhân viên có ca trùng khung giờ không"""
        qs = ShiftAssignment.objects.filter(
            user=user,
            status__in=[ShiftAssignment.Status.PENDING, ShiftAssignment.Status.CONFIRMED],
            shift__date=shift.date,
            shift__start_time__lt=shift.end_time,
            shift__end_time__gt=shift.start_time,
        )
        if exclude_assignment_ids:
            if not isinstance(exclude_assignment_ids, list):
                exclude_assignment_ids = [exclude_assignment_ids]
            qs = qs.exclude(id__in=exclude_assignment_ids)
            
        if exclude_shift:
            qs = qs.exclude(shift=exclude_shift)
            
        return qs.exists()

    @staticmethod
    def auto_update_shift_status(shift):
        """
        Tự động đóng/mở ca dựa trên số lượng người đã confirmed so với max/min workers.
        """
        confirmed_count = ShiftAssignment.objects.filter(
            shift=shift, status=ShiftAssignment.Status.CONFIRMED
        ).count()
        
        max_workers = shift.max_workers if shift.max_workers else (shift.template.max_workers if shift.template else 1)
        
        if confirmed_count >= max_workers:
            if shift.is_open:
                shift.is_open = False
                shift.save(update_fields=['is_open'])
            return False 
            
        min_workers = shift.template.min_workers if shift.template else 1
        if not shift.is_open and confirmed_count < min_workers:
            shift.is_open = True
            shift.save(update_fields=['is_open'])
            return True 
            
        return shift.is_open

    @staticmethod
    def claim_open_shift(user, shift, from_user=None, assigned_by=None):
        """
        Xử lý đăng ký nhận một ca trống 
        """
        try:
            with transaction.atomic():
                locked_user = get_user_model().objects.select_for_update().get(id=user.id)
                
                if ShiftService.has_overlapping_shift(locked_user, shift, exclude_shift=shift if from_user else None):
                    return None, "Bạn đã có ca khác trùng khung giờ này."

                if from_user:
                    target_assignment = ShiftAssignment.objects.filter(
                        shift=shift, 
                        user=from_user, 
                        status=ShiftAssignment.Status.CONFIRMED
                    ).select_for_update().first()
                    
                    if not target_assignment:
                        return None, "Người nhượng không còn giữ ca này hoặc thông tin không hợp lệ."
                    
                    target_assignment.status = ShiftAssignment.Status.CANCELLED 
                    target_assignment.save()

                assignment, created = ShiftAssignment.objects.get_or_create(
                    shift=shift,
                    user=locked_user,
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

    @staticmethod
    def create_emergency_shift(validated_data, phones, creator):
        """
        Tạo ca khẩn cấp và phân công nhân viên theo danh sách số điện thoại.
        """
        User = get_user_model()
        
        users_to_assign = []
        if isinstance(phones, list) and phones:
            users_to_assign = list(User.objects.filter(phone__in=phones))
            found_phones = set(u.phone for u in users_to_assign)
            missing_phones = set(phones) - found_phones
            
            if missing_phones:
                return None, f"Không tìm thấy nhân viên với số điện thoại: {', '.join(missing_phones)}"

        try:
            with transaction.atomic():
                shift = Shift.objects.create(created_by=creator, **validated_data)
                
                if users_to_assign:
                    for user in users_to_assign:
                        ShiftAssignment.objects.get_or_create(
                            shift=shift,
                            user=user,
                            defaults={
                                'assigned_by': creator,
                                'status': ShiftAssignment.Status.CONFIRMED
                            }
                        )
                        
                    ShiftService.auto_update_shift_status(shift)

                # Gửi thông báo ca khẩn cấp
                if users_to_assign:
                    NotificationService.notify_emergency_shift(users_to_assign, shift, is_assigned=True)
                else:
                    busy_user_ids = ShiftAssignment.objects.filter(
                        status__in=[ShiftAssignment.Status.PENDING, ShiftAssignment.Status.CONFIRMED],
                        shift__date=shift.date,
                        shift__start_time__lt=shift.end_time,
                        shift__end_time__gt=shift.start_time,
                    ).values_list('user_id', flat=True)

                    # Lấy ra danh sách các nhân viên đang rảnh
                    available_employees = User.objects.filter(
                        role=User.Role.EMPLOYEE, 
                        is_active=True
                    ).exclude(id__in=busy_user_ids)

                    if available_employees.exists():
                        NotificationService.notify_emergency_shift(available_employees, shift, is_assigned=False)
                return shift, None
        except Exception as e:
            return None, f"Lỗi hệ thống: {str(e)}"

    @staticmethod
    def employee_claim_shift(shift, user):
        """
        Nhân viên đăng ký nhận ca trống
        """
        try:
            with transaction.atomic():
                locked_shift = Shift.objects.select_for_update().get(pk=shift.pk)

                if ShiftService.is_shift_ended(locked_shift):
                    return None, "Ca làm việc này đã kết thúc, không thể đăng ký.", False

                if not locked_shift.is_open:
                    return None, "Ca này không phải ca trống hoặc đã đủ người, không thể đăng ký.", False

                assignment, error = ShiftService.claim_open_shift(user=user, shift=locked_shift, assigned_by=None)
                if error:
                    return None, error, False

                is_auto_confirmed = False
                if locked_shift.is_emergency:
                    assignment.status = ShiftAssignment.Status.CONFIRMED
                    assignment.save()
                    ShiftService.auto_update_shift_status(locked_shift)
                    is_auto_confirmed = True
                    NotificationService.notify_emergency_claimed(user, locked_shift)
                else:
                    NotificationService.notify_claim_pending(user, locked_shift)

                return assignment, None, is_auto_confirmed
        except Exception as e:
            return None, f"Lỗi hệ thống: {str(e)}", False

    @staticmethod
    def approve_shift_claim(shift, assignment_id, admin_user):
        """
        Duyệt nguyện vọng đăng ký ca tự do.
        """
        try:
            with transaction.atomic():
                locked_shift = Shift.objects.select_for_update().get(pk=shift.pk)
                
                try:
                    assignment = ShiftAssignment.objects.get(id=assignment_id, shift=locked_shift, status=ShiftAssignment.Status.PENDING)
                except ShiftAssignment.DoesNotExist:
                    return None, "Không tìm thấy nguyện vọng chờ duyệt."

                # Kiểm tra nhân viên có bị trùng ca cùng khung giờ không 
                if ShiftService.has_overlapping_shift(assignment.user, locked_shift, exclude_assignment_ids=[assignment.id]):
                    return None, f"{assignment.user.full_name} đã có ca trùng khung giờ trong ngày này."

                # Duyệt nguyện vọng này
                assignment.status = ShiftAssignment.Status.CONFIRMED
                assignment.assigned_by = admin_user
                assignment.save()

                # Kiểm tra đã đủ max_workers chưa
                is_open = ShiftService.auto_update_shift_status(locked_shift)

                # Gửi thông báo duyệt trước khi cancel các nguyện vọng còn lại
                NotificationService.notify_claim_approved(assignment)

                if not is_open:
                    # Lấy ID các nguyện vọng PENDING còn lại trước khi cancel
                    pending_ids = list(ShiftAssignment.objects.filter(
                        shift=locked_shift,
                        status=ShiftAssignment.Status.PENDING
                    ).values_list('id', flat=True))

                    if pending_ids:
                        ShiftAssignment.objects.filter(id__in=pending_ids).update(
                            status=ShiftAssignment.Status.CANCELLED
                        )
                        # Chỉ thông báo cho đúng những người vừa bị cancel lần này
                        newly_cancelled = ShiftAssignment.objects.filter(
                            id__in=pending_ids
                        ).select_related('user')
                        for ca in newly_cancelled:
                            NotificationService.notify_claim_rejected(ca)

                return assignment, None
        except Exception as e:
            return None, f"Lỗi hệ thống: {str(e)}"

    @staticmethod
    def approve_swap_request(swap, admin_user):
        """
        Duyệt yêu cầu đổi/tranh/nhượng ca.
        """
        try:
            with transaction.atomic():
                locked_swap = ShiftSwapRequest.objects.select_for_update().get(pk=swap.pk)
                
                if locked_swap.status != ShiftSwapRequest.Status.PARTNER_ACCEPTED:
                    return None, "Yêu cầu này không ở trạng thái chờ Admin duyệt."

                # TH1: Đổi ca
                if locked_swap.swap_type == ShiftSwapRequest.SwapType.EXCHANGE and locked_swap.offered_shift:
                    if not locked_swap.partner:
                        return None, "Yêu cầu đổi ca phải có người chấp nhận."

                    target_shift = locked_swap.target_shift
                    offered_shift = locked_swap.offered_shift

                    try:
                        # Tìm assignment của NV A
                        assignment_a = ShiftAssignment.objects.select_for_update().get(
                            shift=offered_shift, user=locked_swap.requester, status=ShiftAssignment.Status.CONFIRMED
                        )
                        # Tìm assignment của NV B (partner)
                        assignment_b = ShiftAssignment.objects.select_for_update().get(
                            shift=target_shift, user=locked_swap.partner, status=ShiftAssignment.Status.CONFIRMED
                        )
                    except ShiftAssignment.DoesNotExist:
                        return None, "Một trong hai nhân viên không còn phân công tại ca này."

                    # Kiểm tra NV A có trùng ca với target_shift không
                    if ShiftService.has_overlapping_shift(locked_swap.requester, target_shift, exclude_assignment_ids=[assignment_a.id]):
                        return None, f"{locked_swap.requester.full_name} bị trùng khung giờ với ca muốn đổi."

                    # Kiểm tra NV B có trùng ca với offered_shift không
                    if ShiftService.has_overlapping_shift(locked_swap.partner, offered_shift, exclude_assignment_ids=[assignment_b.id]):
                        return None, f"{locked_swap.partner.full_name} bị trùng khung giờ với ca được đổi sang."

                    assignment_a.status = ShiftAssignment.Status.CANCELLED
                    assignment_a.save()
                    assignment_b.status = ShiftAssignment.Status.CANCELLED
                    assignment_b.save()

                    ShiftAssignment.objects.update_or_create(
                        shift=offered_shift, user=locked_swap.partner,
                        defaults={'assigned_by': admin_user, 'status': ShiftAssignment.Status.CONFIRMED}
                    )
                    ShiftAssignment.objects.update_or_create(
                        shift=target_shift, user=locked_swap.requester,
                        defaults={'assigned_by': admin_user, 'status': ShiftAssignment.Status.CONFIRMED}
                    )

                # TH2: Nhượng ca
                elif locked_swap.swap_type == ShiftSwapRequest.SwapType.TRANSFER and locked_swap.offered_shift:
                    if not locked_swap.partner:
                        return None, "Yêu cầu nhượng ca phải có người nhận (partner)."

                    offered_shift = locked_swap.offered_shift

                    try:
                        assignment_requester = ShiftAssignment.objects.select_for_update().get(
                            shift=offered_shift,
                            user=locked_swap.requester,
                            status=ShiftAssignment.Status.CONFIRMED
                        )
                    except ShiftAssignment.DoesNotExist:
                        return None, "Người nhượng không còn giữ ca này nữa."

                    if ShiftAssignment.objects.filter(
                        shift=offered_shift,
                        user=locked_swap.partner,
                        status__in=[ShiftAssignment.Status.PENDING, ShiftAssignment.Status.CONFIRMED]
                    ).exists():
                        return None, f"{locked_swap.partner.full_name} đã đăng ký hoặc đang giữ ca này rồi."

                    if ShiftService.has_overlapping_shift(locked_swap.partner, offered_shift, exclude_shift=offered_shift):
                        return None, f"{locked_swap.partner.full_name} đã có ca làm trùng khung giờ với ca được nhượng."

                    assignment_requester.status = ShiftAssignment.Status.CANCELLED
                    assignment_requester.save()
                    ShiftAssignment.objects.update_or_create(
                        shift=offered_shift,
                        user=locked_swap.partner,
                        defaults={
                            'assigned_by': admin_user,
                            'status': ShiftAssignment.Status.CONFIRMED,
                        }
                    )

                    ShiftService.auto_update_shift_status(offered_shift)

                # TH3: Tranh ca
                elif locked_swap.swap_type == ShiftSwapRequest.SwapType.CLAIM:
                    assignment, error = ShiftService.claim_open_shift(user=locked_swap.requester, shift=locked_swap.target_shift, assigned_by=admin_user)
                    if not assignment:
                        return None, error

                locked_swap.status = ShiftSwapRequest.Status.ACCEPTED
                locked_swap.resolved_by = admin_user
                locked_swap.resolved_at = timezone.now()
                locked_swap.save()

                # Thông báo phản hồi chung
                msg_map = {
                    ShiftSwapRequest.SwapType.EXCHANGE: f"Đã duyệt đổi ca thành công giữa {locked_swap.requester.full_name} và {locked_swap.partner.full_name}.",
                    ShiftSwapRequest.SwapType.TRANSFER: f"Đã duyệt nhượng ca thành công. {locked_swap.partner.full_name} đã nhận thêm ca.",
                    ShiftSwapRequest.SwapType.CLAIM: f"Đã duyệt tranh ca thành công cho {locked_swap.requester.full_name}."
                }
                # Gửi thông báo cho cả hai bên
                NotificationService.notify_swap_approved(locked_swap)

                return msg_map.get(locked_swap.swap_type, "Đã duyệt yêu cầu thành công."), None

        except Exception as e:
            return None, f"Lỗi hệ thống: {str(e)}"
