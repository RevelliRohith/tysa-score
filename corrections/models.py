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
    status = models.CharField(max_length=20, choices=GrantStatus.choices, default=GrantStatus.OPEN)
    
    granted_at = models.DateTimeField(auto_now_add=True)
    resubmitted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='revoked_corrections')

    def __str__(self):
        return f"CD Grant #{self.id}: {self.judge_submission.get_judge_role_display()} for Chest {self.performance.chest_number} [{self.status}]"

    @property
    def is_active(self):
        return self.status == GrantStatus.OPEN
