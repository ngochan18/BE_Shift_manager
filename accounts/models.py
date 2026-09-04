from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.utils.translation import gettext_lazy as _
from django.contrib.auth.models import BaseUserManager, AbstractBaseUser

class MyUserManager(BaseUserManager):
    def create_user(self, phone, password=None, **extra_fields):
        if not phone:
            raise ValueError('Số điện thoại không được để trống')
        phone = phone.strip()
        user = self.model(phone=phone, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, phone, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('role', 'admin')
        return self.create_user(phone, password, **extra_fields)

class User(AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        MANAGER = "manager", "Manager"
        EMPLOYEE = "employee", "Employee"

    email = models.EmailField(_("email address"),blank=True, null=True)
    full_name = models.CharField(_("full name"), max_length=150)
    phone = models.CharField(max_length=20, unique=True, blank=True)
    avatar_url = models.URLField(max_length=500, blank=True)  # Cloudinary
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.EMPLOYEE)
    telegram_chat_id = models.CharField(max_length=50, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)
    last_login = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = ["full_name"]
    objects = MyUserManager()
    def __str__(self):
        return f"{self.full_name} ({self.phone})"
    class Meta:
        db_table = "users"


class EmployeeProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    hourly_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    bank_account = models.CharField(max_length=50, blank=True)
    bank_name = models.CharField(max_length=100, blank=True)
    id_card_number = models.CharField(max_length=20, blank=True) # số CCCD
    address = models.TextField(blank=True)
    emergency_contact = models.CharField(max_length=150, blank=True) # thông tin liên hệ khẩn cấp
    start_date = models.DateField(null=True, blank=True) # ngày bắt đầu làm việc
    note = models.TextField(blank=True)

    class Meta:
        db_table = "employee_profiles"
        verbose_name = "Hồ sơ nhân viên"
        verbose_name_plural = "Hồ sơ nhân viên"

    def __str__(self):
        return f"Profile of {self.user.full_name}"