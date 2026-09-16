from datetime import date, datetime
from django.utils import timezone
from django.db import transaction
from django.contrib.auth import get_user_model
from .models import ShiftAssignment

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
