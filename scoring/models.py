from decimal import Decimal
from django.db import models
from django.conf import settings

class PerformanceStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending on Stage'
    IN_PROGRESS = 'IN_PROGRESS', 'Judging in Progress'
    SUBMITTED = 'SUBMITTED', 'Judging Complete (Pending Chief Review)'
    CHIEF_REVIEWED = 'CHIEF_REVIEWED', 'Reviewed by Chief Judge'
    PROVISIONAL_RELEASED = 'PROVISIONAL_RELEASED', 'Provisional Score Released'
    CD_FINALIZED = 'CD_FINALIZED', 'Finalized & Locked by CD'

class Performance(models.Model):
    event_entry = models.ForeignKey('registrations.EventEntry', on_delete=models.CASCADE, related_name='performances')
    batch = models.ForeignKey('competition.StageBatch', on_delete=models.SET_NULL, null=True, blank=True, related_name='performances')
    round_type = models.CharField(max_length=20, default='PRELIMINARY', choices=[
        ('PRELIMINARY', 'Preliminary'),
        ('SEMI_FINAL', 'Semi-Final'),
        ('FINAL', 'Final')
    ])
    status = models.CharField(max_length=30, choices=PerformanceStatus.choices, default=PerformanceStatus.PENDING)
    
    provisional_score = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True)
    final_score = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True)
    rank = models.PositiveIntegerField(null=True, blank=True)
    
    chief_approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='chief_approved_performances')
    chief_approved_at = models.DateTimeField(null=True, blank=True)
    cd_finalized_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='cd_finalized_performances')
    cd_finalized_at = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('event_entry', 'round_type')

    def __str__(self):
        return f"Performance: Chest {self.event_entry.chest_number} [{self.round_type}] - {self.get_status_display()}"

    @property
    def chest_number(self):
        return self.event_entry.chest_number

    @property
    def is_released(self):
        return self.status in (PerformanceStatus.PROVISIONAL_RELEASED, PerformanceStatus.CD_FINALIZED)

class JudgeSubmission(models.Model):
    performance = models.ForeignKey(Performance, on_delete=models.CASCADE, related_name='submissions')
    judge = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='submissions')
    judge_role = models.CharField(max_length=30, choices=[
        ('D1_JUDGE', 'D1 Judge'),
        ('D2_JUDGE', 'D2 Judge'),
        ('D3_JUDGE', 'D3 Judge'),
        ('D4_JUDGE', 'D4 Judge'),
        ('TIME_JUDGE', 'Time Judge'),
        ('EVALUATOR', 'Evaluator'),
        ('ARTISTRY_JUDGE', 'Artistry Judge')
    ])
    is_draft = models.BooleanField(default=True)
    is_submitted = models.BooleanField(default=False)
    is_locked = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)
    
    raw_total = models.DecimalField(max_digits=6, decimal_places=3, default=Decimal('0.000'))
    effective_total = models.DecimalField(max_digits=6, decimal_places=3, default=Decimal('0.000'))
    
    submitted_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('performance', 'judge_role', 'version')

    def __str__(self):
        return f"{self.get_judge_role_display()} - Chest {self.performance.chest_number} (v{self.version}) [{ 'Submitted' if self.is_submitted else 'Draft' }]"

class AsanaScoreItem(models.Model):
    submission = models.ForeignKey(JudgeSubmission, on_delete=models.CASCADE, related_name='items')
    sequence_order = models.PositiveIntegerField()
    
    # Item-level workflow and lock states
    is_draft = models.BooleanField(default=True)
    is_submitted = models.BooleanField(default=False)
    is_locked = models.BooleanField(default=False)
    submitted_at = models.DateTimeField(null=True, blank=True)
    
    # Traditional inputs (Posture max 6.0, Stability max 2.0, Timing contrib read-only max 2.0)
    posture_score = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'))
    stability_score = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'))
    timing_contribution = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'))
    
    # Artistic Single & Rhythmic inputs
    execution_score = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'))
    base_value = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.80'))
    
    # Artistic Pair inputs (Chest A, Chest B, Pair Formation)
    chest_a_execution = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'))
    chest_b_execution = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'))
    formation_score = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'))
    
    # Time Judge inputs
    hold_duration_seconds = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    
    # Evaluator inputs
    is_valid = models.BooleanField(default=True)
    invalid_reason = models.CharField(max_length=150, blank=True)
    observed_asana = models.ForeignKey('catalogue.Asana', on_delete=models.SET_NULL, null=True, blank=True)
    
    # Computed score for this item
    effective_item_score = models.DecimalField(max_digits=5, decimal_places=3, default=Decimal('0.000'))

    class Meta:
        ordering = ['sequence_order']
        unique_together = ('submission', 'sequence_order')

    def __str__(self):
        return f"Seq #{self.sequence_order} for {self.submission}"

class PerformanceArtistryScore(models.Model):
    submission = models.OneToOneField(JudgeSubmission, on_delete=models.CASCADE, related_name='artistry_breakdown')
    presentation = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'))
    synchronization = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'))
    choreography = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'))
    transition_flow = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'))
    costume_grace = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'))
    deductions = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'))
    total_artistry = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))

    def calculate_total(self):
        subtotal = self.presentation + self.synchronization + self.choreography + self.transition_flow + self.costume_grace
        self.total_artistry = max(Decimal('0.00'), subtotal - self.deductions)
        return self.total_artistry
