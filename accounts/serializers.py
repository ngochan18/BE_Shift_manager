from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from django.contrib.auth import get_user_model

User = get_user_model()


from .models import EmployeeProfile

class EmployeeProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployeeProfile
        fields = ['hourly_rate', 'bank_account', 'bank_name', 'id_card_number', 'address', 'emergency_contact', 'start_date', 'note']


class UserSerializer(serializers.ModelSerializer):
    profile = EmployeeProfileSerializer(required=False)
    password = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = User
        fields = ['id', 'phone', 'email', 'full_name', 'role', 'avatar_url', 'is_active', 'password', 'profile']
        read_only_fields = ['id']

    def validate_phone(self, value):
        import re
        if not re.match(r'^(0|\+84)(3|5|7|8|9)\d{8}$', value):
            raise serializers.ValidationError("Số điện thoại không hợp lệ. Vui lòng nhập đúng định dạng số điện thoại Việt Nam.")
        return value

    def create(self, validated_data):
        profile_data = validated_data.pop('profile', None)
        password = validated_data.pop('password', None)
        
        if not password:
            password = 'password123'
            
        user = User.objects.create_user(password=password, **validated_data)
        
        if profile_data:
            EmployeeProfile.objects.create(user=user, **profile_data)
        else:
            EmployeeProfile.objects.create(user=user)
            
        return user

    def update(self, instance, validated_data):
        profile_data = validated_data.pop('profile', None)
        password = validated_data.pop('password', None)
        
        if password:
            instance.set_password(password)
            
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        
        if profile_data and hasattr(instance, 'profile'):
            profile = instance.profile
            for attr, value in profile_data.items():
                setattr(profile, attr, value)
            profile.save()
        elif profile_data:
            EmployeeProfile.objects.create(user=instance, **profile_data)
            
        return instance


class EmployeeUpdateProfileNestedSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployeeProfile
        fields = ['bank_account', 'bank_name', 'address', 'emergency_contact']

class EmployeeProfileUpdateSerializer(serializers.ModelSerializer):
    profile = EmployeeUpdateProfileNestedSerializer(required=False)

    class Meta:
        model = User
        fields = ['id', 'phone', 'email', 'full_name', 'role', 'avatar_url', 'telegram_chat_id', 'profile']
        read_only_fields = ['id', 'phone', 'role']

    def update(self, instance, validated_data):
        profile_data = validated_data.pop('profile', None)
        
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        
        if profile_data and hasattr(instance, 'profile'):
            profile = instance.profile
            for attr, value in profile_data.items():
                setattr(profile, attr, value)
            profile.save()
        elif profile_data:
            EmployeeProfile.objects.create(user=instance, **profile_data)
            
        return instance

class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(required=True)
    new_password = serializers.CharField(required=True)
    confirm_password = serializers.CharField(required=True)

    def validate_old_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError("Mật khẩu cũ không đúng.")
        return value

    def validate(self, attrs):
        if attrs.get('new_password') != attrs.get('confirm_password'):
            raise serializers.ValidationError({"confirm_password": "Mật khẩu xác nhận không khớp."})
        return attrs


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)

        token['phone'] = user.phone
        token['full_name'] = user.full_name
        token['role'] = user.role

        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        data['user'] = UserSerializer(self.user).data
        return data
