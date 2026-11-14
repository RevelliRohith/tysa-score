from django.db import models
from django.conf import settings

class GrantStatus(models.TextChoices):
    OPEN = 'OPEN', 'Correction Access Open'
    RESUBMITTED = 'RESUBMITTED', 'Revised & Resubmitted'
    REVOKED = 'REVOKED', 'Access Revoked by CD'

class CDCorrectionGrant(models.Model):
    performance = models.ForeignKey('scoring.Performance', on_delete=models.CASCADE, related_name='correction_grants')
    judge_submission = models.ForeignKey('scoring.JudgeSubmission', on_delete=models.CASCADE, related_name='correction_grants')
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='granted_corrections')
    reason = models.TextField(help_text="Official reason for reopening judge sheet")
    remarks = models.TextField(blank=True, default="", help_text="Additional administrative remarks")
    status = models.CharField(max_length=20, choices=GrantStatus.choices, default=GrantStatus.OPEN)
    
    original_snapshot = models.JSONField(null=True, blank=True, help_text="Immutable snapshot of submission & items prior to correction")
    corrected_snapshot = models.JSONField(null=True, blank=True, help_text="Snapshot of submission & items after resubmission")
    resubmitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='resubmitted_corrections')

    granted_at = models.DateTimeField(auto_now_add=True)
    resubmitted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='revoked_corrections')

    def __str__(self):
        return f"CD Grant #{self.id}: {self.judge_submission.get_judge_role_display()} for Chest {self.performance.chest_number} [{self.status}]"

    @property
    def is_active(self):
        return self.status == GrantStatus.OPEN

class AppealStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending Review'
    UNDER_REVIEW = 'UNDER_REVIEW', 'Under Review by Jury / CD'
    UPHELD = 'UPHELD', 'Upheld (Correction Required)'
    REJECTED = 'REJECTED', 'Rejected'
    WITHDRAWN = 'WITHDRAWN', 'Withdrawn'

class Appeal(models.Model):
    championship = models.ForeignKey('championships.Championship', on_delete=models.CASCADE, related_name='appeals')
    performance = models.ForeignKey('scoring.Performance', on_delete=models.SET_NULL, null=True, blank=True, related_name='appeals')
    event_entry = models.ForeignKey('registrations.EventEntry', on_delete=models.SET_NULL, null=True, blank=True, related_name='appeals')
    
    chest_number = models.CharField(max_length=50)
    athlete_name = models.CharField(max_length=200, blank=True)
    district = models.CharField(max_length=100)
    
    applicant_name = models.CharField(max_length=150)
    applicant_role = models.CharField(max_length=100, default="Coach / Team Manager")
    applicant_contact = models.CharField(max_length=50, blank=True)
    
    first_score_published_at = models.DateTimeField(null=True, blank=True)
    intake_at = models.DateTimeField(auto_now_add=True)
    appeal_window_minutes = models.PositiveIntegerField(default=30)
    is_within_window = models.BooleanField(default=True)
    
    appeal_fee_amount = models.DecimalField(max_digits=8, decimal_places=2, default=1000.00)
    fee_paid = models.BooleanField(default=True)
    fee_receipt_number = models.CharField(max_length=100, blank=True)
    
    form_document = models.FileField(upload_to='appeals/forms/', blank=True, null=True)
    evidence_file = models.FileField(upload_to='appeals/evidence/', blank=True, null=True)
    reason = models.TextField(help_text="Official ground for appeal under COP rules")
    
    status = models.CharField(max_length=30, choices=AppealStatus.choices, default=AppealStatus.PENDING)
    assigned_reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_appeals')
    decision_notes = models.TextField(blank=True)
    decision_recorded_at = models.DateTimeField(null=True, blank=True)
    decision_recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='decided_appeals')
    
    cd_correction_grant = models.ForeignKey(CDCorrectionGrant, on_delete=models.SET_NULL, null=True, blank=True, related_name='appeals')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-intake_at']

    def __str__(self):
        return f"Appeal #{self.id}: Chest {self.chest_number} [{self.status}]"
