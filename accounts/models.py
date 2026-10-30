from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.utils.translation import gettext_lazy as _

class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError(_('The Email must be set'))
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        return self.create_user(email, password, **extra_fields)

    async def acreate_user(self, email, password=None, **extra_fields):
        from asgiref.sync import sync_to_async
        return await sync_to_async(self.create_user)(email, password=password, **extra_fields)

    async def acreate_superuser(self, email, password=None, **extra_fields):
        from asgiref.sync import sync_to_async
        return await sync_to_async(self.create_superuser)(email, password=password, **extra_fields)

class User(AbstractUser):
    username = None
    email = models.EmailField(_('email address'), unique=True)
    full_name = models.CharField(max_length=150, blank=True)
    mobile = models.CharField(max_length=20, blank=True)
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['full_name']

    objects = UserManager()

    def __str__(self):
        return f"{self.full_name or self.email} ({self.email})"

class RoleChoices(models.TextChoices):
    ADMIN = 'ADMIN', 'System Admin'
    CD = 'CD', 'Competition Director'
    DISTRICT_USER = 'DISTRICT_USER', 'District User'
    VENUE_DESK = 'VENUE_DESK', 'Venue Desk'
    STAGE_MANAGER = 'STAGE_MANAGER', 'Stage Manager / Announcer'
    D_JUDGE = 'D_JUDGE', 'D Judge'
    TIME_JUDGE = 'TIME_JUDGE', 'Time Judge'
    ARTISTRY_JUDGE = 'ARTISTRY_JUDGE', 'Artistry Judge'
    EVALUATOR = 'EVALUATOR', 'Evaluator'
    CHIEF_JUDGE = 'CHIEF_JUDGE', 'Chief Judge'
    JURY = 'JURY', 'Jury'

class ScopedRole(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='scoped_roles')
    role = models.CharField(max_length=30, choices=RoleChoices.choices)
    championship = models.ForeignKey('championships.Championship', on_delete=models.CASCADE, null=True, blank=True, related_name='scoped_roles')
    district = models.CharField(max_length=100, blank=True, help_text="District scope if applicable")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'role', 'championship', 'district')

    def __str__(self):
        scope = f" [{self.district}]" if self.district else ""
        champ = f" ({self.championship.name})" if self.championship else " (Global)"
        return f"{self.user.email} - {self.get_role_display()}{scope}{champ}"

class Coach(models.Model):
    championship = models.ForeignKey('championships.Championship', on_delete=models.CASCADE, related_name='coaches')
    name = models.CharField(max_length=150)
    mobile = models.CharField(max_length=20)
    district = models.CharField(max_length=100)
    academy_org = models.CharField(max_length=200, blank=True, verbose_name="Academy / Organization")
    qualification = models.CharField(max_length=200, blank=True)
    attendance_status = models.CharField(max_length=20, default='REGISTERED', choices=[
        ('REGISTERED', 'Registered'),
        ('PRESENT', 'Present'),
        ('ABSENT', 'Absent')
    ])
    certificate_issued = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} - {self.district}"

class Volunteer(models.Model):
    championship = models.ForeignKey('championships.Championship', on_delete=models.CASCADE, related_name='volunteers')
    name = models.CharField(max_length=150)
    mobile = models.CharField(max_length=20)
    district = models.CharField(max_length=100)
    role_assigned = models.CharField(max_length=150, help_text="e.g. Stage Logistics, Marshall, Reception")
    attendance_status = models.CharField(max_length=20, default='REGISTERED', choices=[
        ('REGISTERED', 'Registered'),
        ('PRESENT', 'Present'),
        ('ABSENT', 'Absent')
    ])
    certificate_issued = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.role_assigned})"

class Guest(models.Model):
    championship = models.ForeignKey('championships.Championship', on_delete=models.CASCADE, related_name='guests')
    name = models.CharField(max_length=150)
    designation = models.CharField(max_length=150)
    organization = models.CharField(max_length=200, blank=True)
    attendance_status = models.CharField(max_length=20, default='INVITED', choices=[
        ('INVITED', 'Invited'),
        ('PRESENT', 'Present'),
        ('ABSENT', 'Absent')
    ])
    certificate_issued = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} - {self.designation}"
