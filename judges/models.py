from datetime import timedelta
from django.db import models
from django.conf import settings
from django.utils import timezone

class CertificationLevel(models.TextChoices):
    NATIONAL_A = 'NATIONAL_A', 'National Level - Grade A'
    NATIONAL_B = 'NATIONAL_B', 'National Level - Grade B'
    STATE = 'STATE', 'State Certified Judge'
    DISTRICT = 'DISTRICT', 'District Certified Official'

class ApprovalStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending Admin Approval'
    APPROVED = 'APPROVED', 'Approved Universal Judge'
    REJECTED = 'REJECTED', 'Rejected'

class CredentialSetupStatus(models.TextChoices):
    ACTIVE = 'ACTIVE', 'Active (Admin Provided)'
    TEMPORARY_ACTIVE = 'TEMPORARY_ACTIVE', 'Active (Admin Provided)'
    PASSWORD_SET = 'PASSWORD_SET', 'Active (Admin Provided)'
    EXPIRED = 'EXPIRED', 'Temporary Password Expired'
    NOT_GENERATED = 'NOT_GENERATED', 'No Credentials Generated'

class DeliveryStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending Delivery'
    DELIVERED = 'DELIVERED', 'Delivered to Official'
    ACKNOWLEDGED = 'ACKNOWLEDGED', 'Acknowledged by Official'

class UniversalJudge(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='judge_profile')
    certification_level = models.CharField(max_length=30, choices=CertificationLevel.choices, default=CertificationLevel.STATE)
    certification_authority = models.CharField(max_length=150, default="Yogasana Bharat / TYSA")
    certificate_number = models.CharField(max_length=100, blank=True)
    experience_years = models.PositiveIntegerField(default=1)
    district = models.CharField(max_length=100)
    approval_status = models.CharField(max_length=20, choices=ApprovalStatus.choices, default=ApprovalStatus.PENDING)
    
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_judges')
    approved_at = models.DateTimeField(null=True, blank=True)
    admin_notes = models.TextField(blank=True)
    
    certificate_file = models.FileField(upload_to='judges/certificates/', blank=True, null=True)
    
    # Universal Judge ID Card batch printing & issuance tracking
    is_id_card_printed = models.BooleanField(default=False, verbose_name="ID Card Printed")
    id_card_printed_at = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.full_name or self.user.email} - {self.get_certification_level_display()} [{self.approval_status}]"

    @property
    def judge_code(self):
        return f"TYSA-JDG-{self.id:04d}"

    @property
    def is_approved(self):
        return self.approval_status == ApprovalStatus.APPROVED


class JudgeCredential(models.Model):
    """
    Manages server-encrypted software-generated judge credentials,
    enforcing mandatory first-login password change, expiration policies,
    and administrative reveal/reset governance without storing plaintext secrets.
    """
    judge = models.OneToOneField(UniversalJudge, on_delete=models.CASCADE, related_name='credential')
    encrypted_temp_password = models.TextField(
        blank=True,
        null=True,
        help_text="Authenticated server-encrypted temporary password. Purged immediately once judge changes password."
    )
    temp_password_created_at = models.DateTimeField(null=True, blank=True)
    temp_password_expires_at = models.DateTimeField(null=True, blank=True)
    must_change_password = models.BooleanField(
        default=True,
        help_text="Explicit account-setup flag requiring judge to change password before accessing portal features."
    )
    password_changed_at = models.DateTimeField(null=True, blank=True)
    
    delivery_status = models.CharField(
        max_length=20,
        choices=DeliveryStatus.choices,
        default=DeliveryStatus.PENDING
    )
    delivered_at = models.DateTimeField(null=True, blank=True)
    delivered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='delivered_judge_credentials'
    )
    
    last_reset_at = models.DateTimeField(null=True, blank=True)
    last_reset_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reset_judge_credentials'
    )
    last_revealed_at = models.DateTimeField(null=True, blank=True)
    last_revealed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='revealed_judge_credentials'
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Credential for {self.judge.user.email} [{self.setup_status}]"

    @property
    def is_expired(self):
        if not self.temp_password_expires_at:
            return False
        return timezone.now() > self.temp_password_expires_at

    @property
    def setup_status(self):
        if self.is_expired:
            return CredentialSetupStatus.EXPIRED
        if self.encrypted_temp_password:
            return CredentialSetupStatus.ACTIVE
        return CredentialSetupStatus.NOT_GENERATED

    @property
    def setup_status_display(self):
        status = self.setup_status
        for choice in CredentialSetupStatus.choices:
            if choice[0] == status:
                return choice[1]
        return str(status)

    def revoke_judge_sessions(self):
        """Terminates all active Django sessions for this judge user."""
        from django.contrib.sessions.models import Session
        try:
            user_id_str = str(self.judge.user.id)
            for s in Session.objects.filter(expire_date__gte=timezone.now()):
                data = s.get_decoded()
                if data.get('_auth_user_id') == user_id_str:
                    s.delete()
        except Exception:
            pass

    def generate_new_temporary_password(self, admin_user=None, expiry_days=90):
        """
        Generates a unique software-provided password, sets it directly on the User,
        stores the authenticated encrypted value for Admin retrieval, revokes old sessions,
        and ensures must_change_password=False (judges do not create their own passwords).
        """
        from .crypto import generate_judge_temp_password, encrypt_temp_password
        new_plain_pwd = generate_judge_temp_password()
        self.judge.user.set_password(new_plain_pwd)
        self.judge.user.save()

        self.revoke_judge_sessions()

        self.encrypted_temp_password = encrypt_temp_password(new_plain_pwd)
        self.must_change_password = False
        self.temp_password_created_at = timezone.now()
        self.temp_password_expires_at = timezone.now() + timedelta(days=expiry_days)
        self.last_reset_at = timezone.now()
        if admin_user:
            self.last_reset_by = admin_user
        self.delivery_status = DeliveryStatus.PENDING
        self.save()
        return new_plain_pwd

    generate_provided_password = generate_new_temporary_password
