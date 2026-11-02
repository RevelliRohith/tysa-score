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
    DISQUALIFIED = 'DISQUALIFIED', 'Disqualified'

class Performance(models.Model):
    event_entry = models.ForeignKey('registrations.EventEntry', on_delete=models.CASCADE, related_name='performances')
    batch = models.ForeignKey('competition.StageBatch', on_delete=models.SET_NULL, null=True, blank=True, related_name='performances')
    round_type = models.CharField(max_length=20, default='PRELIMINARY', choices=[
        ('PRELIMINARY', 'Preliminary'),
        ('SEMI_FINAL', 'Semi-Final'),
        ('FINAL', 'Final')
    ])
    status = models.CharField(max_length=30, choices=PerformanceStatus.choices, default=PerformanceStatus.PENDING)
    disqualification_reason = models.TextField(blank=True)
    
    provisional_score = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True)
    final_score = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True)
    rank = models.PositiveIntegerField(null=True, blank=True)
    
    chief_approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='chief_approved_performances')
    chief_approved_at = models.DateTimeField(null=True, blank=True)
    cd_finalized_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='cd_finalized_performances')
    cd_finalized_at = models.DateTimeField(null=True, blank=True)

    # Complete authoritative audit trace of calculation
    calculation_data = models.JSONField(default=dict, blank=True)
    
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

    @property
    def is_disqualified(self):
        return self.status == PerformanceStatus.DISQUALIFIED

class JudgeSubmission(models.Model):
    performance = models.ForeignKey(Performance, on_delete=models.CASCADE, related_name='submissions')
    judge = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='submissions')
    judge_role = models.CharField(max_length=30, choices=[
        ('D1_JUDGE', 'D1 Judge'),
        ('D2_JUDGE', 'D2 Judge'),
        ('D3_JUDGE', 'D3 Judge'),
        ('D4_JUDGE', 'D4 Judge'),
        ('D5_JUDGE', 'D5 Judge'),
        ('TIME_JUDGE', 'Time Judge'),
        ('TIME_JUDGE_2', 'Time Judge 2'),
        ('EVALUATOR', 'Evaluator'),
        ('EVALUATOR_2', 'Evaluator 2'),
        ('ARTISTRY_JUDGE', 'Artistry Judge'),
        ('ARTISTRY_JUDGE_2', 'Artistry Judge 2'),
        ('CHIEF_JUDGE', 'Chief Judge'),
    ])
    is_draft = models.BooleanField(default=True)
    is_submitted = models.BooleanField(default=False)
    is_locked = models.BooleanField(default=False)
    is_zero = models.BooleanField(default=False, help_text="Explicit intentional zero recorded")
    version = models.PositiveIntegerField(default=1)
    
    raw_total = models.DecimalField(max_digits=6, decimal_places=3, default=Decimal('0.000'))
    effective_total = models.DecimalField(max_digits=6, decimal_places=3, default=Decimal('0.000'))
    
    calculation_metadata = models.JSONField(default=dict, blank=True)
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
    is_zero = models.BooleanField(default=False, help_text="Explicit intentional zero recorded")
    submitted_at = models.DateTimeField(null=True, blank=True)
    
    # Traditional inputs (Posture max 6.0, Stability max 2.0, Timing contrib read-only max 2.0)
    posture_score = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'))
    stability_score = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'))
    timing_contribution = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'))

    # Optional detailed execution fields for Traditional (Accuracy /4, Mounting /2, Stability /1, Grace /1)
    detailed_accuracy = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    detailed_mounting_dismounting = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    detailed_stability = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    detailed_grace = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    
    # Artistic Single & Rhythmic inputs
    execution_score = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'))
    base_value = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.80'))
    
    # Artistic Pair inputs (Chest A, Chest B, Pair Formation)
    chest_a_execution = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'))
    chest_a_bv = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('1.00'))
    chest_b_execution = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'))
    chest_b_bv = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('1.00'))
    formation_score = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'))
    
    # Time Judge inputs
    hold_duration_seconds = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    
    # Evaluator inputs
    is_valid = models.BooleanField(default=True)
    invalid_reason = models.CharField(max_length=150, blank=True)
    observed_asana = models.ForeignKey('catalogue.Asana', on_delete=models.SET_NULL, null=True, blank=True)
    
    # Computed raw score before invalidation
    raw_item_score = models.DecimalField(max_digits=5, decimal_places=3, default=Decimal('0.000'))
    # Computed effective score for this item (after invalidity zeroing if applicable)
    effective_item_score = models.DecimalField(max_digits=5, decimal_places=3, default=Decimal('0.000'))

    class Meta:
        ordering = ['sequence_order']
        unique_together = ('submission', 'sequence_order')

    def __str__(self):
        return f"Seq #{self.sequence_order} for {self.submission}"

class PerformanceArtistryScore(models.Model):
    submission = models.OneToOneField(JudgeSubmission, on_delete=models.CASCADE, related_name='artistry_breakdown')
    
    # COP 5 Official Criteria (Total max 20.0)
    costume = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'), help_text="Costume (max 1.0)")
    theme_music = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'), help_text="Theme and Music (max 1.0)")
    character_expression = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'), help_text="Character and Expression (max 3.0)")
    rhythm_artistic_steps = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'), help_text="Rhythm & Artistic Steps / Synchronisation (max 10.0)")
    use_of_floor = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'), help_text="Use of Floor (max 5.0)")
    artistry_deductions = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'), help_text="Applicable Artistry Deductions")

    # Legacy fields maintained for backward compatibility
    presentation = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'))
    synchronization = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'))
    choreography = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'))
    transition_flow = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'))
    costume_grace = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'))
    deductions = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.00'))
    
    total_artistry = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))

    def calculate_total(self):
        # Check if official COP criteria fields are provided
        cop_sum = self.costume + self.theme_music + self.character_expression + self.rhythm_artistic_steps + self.use_of_floor
        if cop_sum > Decimal('0.00') or self.artistry_deductions > Decimal('0.00'):
            self.total_artistry = max(Decimal('0.00'), cop_sum - self.artistry_deductions)
        else:
            legacy_subtotal = self.presentation + self.synchronization + self.choreography + self.transition_flow + self.costume_grace
            self.total_artistry = max(Decimal('0.00'), legacy_subtotal - self.deductions)
        return self.total_artistry
