from django.db import models
from django.conf import settings

class CertificationLevel(models.TextChoices):
    NATIONAL_A = 'NATIONAL_A', 'National Level - Grade A'
    NATIONAL_B = 'NATIONAL_B', 'National Level - Grade B'
    STATE = 'STATE', 'State Certified Judge'
    DISTRICT = 'DISTRICT', 'District Certified Official'

class ApprovalStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending Admin Approval'
    APPROVED = 'APPROVED', 'Approved Universal Judge'
    REJECTED = 'REJECTED', 'Rejected'

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
