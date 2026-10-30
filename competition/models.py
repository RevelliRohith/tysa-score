from django.db import models
from django.conf import settings

class DistrictDraw(models.Model):
    championship = models.ForeignKey('championships.Championship', on_delete=models.CASCADE, related_name='draws')
    event_config = models.ForeignKey('championships.EventConfiguration', on_delete=models.CASCADE, related_name='draws')
    draw_seed = models.CharField(max_length=50, blank=True)
    draw_order = models.JSONField(help_text="Ordered list of district names determined by auditable lottery")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Draw: {self.championship.code} - {self.event_config.get_event_type_display()}"

class MasterRoll(models.Model):
    championship = models.ForeignKey('championships.Championship', on_delete=models.CASCADE, related_name='master_rolls')
    event_config = models.ForeignKey('championships.EventConfiguration', on_delete=models.CASCADE, related_name='master_rolls')
    age_category = models.ForeignKey('championships.AgeCategory', on_delete=models.CASCADE, related_name='master_rolls')
    gender = models.CharField(max_length=10, choices=[('M', 'Male'), ('F', 'Female')])
    round_type = models.CharField(max_length=20, default='PRELIMINARY', choices=[
        ('PRELIMINARY', 'Preliminary'),
        ('SEMI_FINAL', 'Semi-Final'),
        ('FINAL', 'Final')
    ])
    version = models.PositiveIntegerField(default=1)
    is_locked = models.BooleanField(default=False, help_text="Finalized roll locks entry and routine declarations")
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('championship', 'event_config', 'age_category', 'gender', 'round_type', 'version')

    def __str__(self):
        return f"Master Roll: {self.event_config.get_event_type_display()} - {self.age_category.name} ({self.gender}) [{self.round_type}]"

class MasterRollEntry(models.Model):
    master_roll = models.ForeignKey(MasterRoll, on_delete=models.CASCADE, related_name='roll_entries')
    event_entry = models.ForeignKey('registrations.EventEntry', on_delete=models.CASCADE, related_name='roll_placements')
    cycle_number = models.PositiveIntegerField(default=1)
    performance_order = models.PositiveIntegerField()
    attendance_status = models.CharField(max_length=20, default='PRESENT', choices=[
        ('PRESENT', 'Present'),
        ('ABSENT', 'Absent'),
        ('HOLD', 'On Hold'),
        ('COMPLETED', 'Completed')
    ])

    class Meta:
        ordering = ['performance_order']
        unique_together = ('master_roll', 'performance_order')

    def __str__(self):
        return f"Order #{self.performance_order}: Chest {self.event_entry.chest_number} - {self.event_entry.display_names}"

class JudgePanel(models.Model):
    championship = models.ForeignKey('championships.Championship', on_delete=models.CASCADE, related_name='panels')
    event_config = models.ForeignKey('championships.EventConfiguration', on_delete=models.CASCADE, related_name='panels')
    stage_name = models.CharField(max_length=100, default="Stage 1")
    round_type = models.CharField(max_length=20, default='PRELIMINARY', choices=[
        ('PRELIMINARY', 'Preliminary'),
        ('SEMI_FINAL', 'Semi-Final'),
        ('FINAL', 'Final')
    ])
    
    chief_judge = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='chief_panels')
    d1_judge = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='d1_panels')
    d2_judge = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='d2_panels')
    d3_judge = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='d3_panels')
    d4_judge = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='d4_panels')
    time_judge = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='time_panels')
    time_judge_2 = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='time_panels_2')
    evaluator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='evaluator_panels')
    evaluator_2 = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='evaluator_panels_2')
    artistry_judge = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='artistry_panels')
    artistry_judge_2 = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='artistry_panels_2')
    stage_manager = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='managed_panels')
    jury_member = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='jury_panels', help_text="Optional Jury member appointed by CD from judges list")
    
    age_category = models.ForeignKey('championships.AgeCategory', on_delete=models.SET_NULL, null=True, blank=True, related_name='panels')
    gender = models.CharField(max_length=10, blank=True, choices=[('M', 'Male'), ('F', 'Female')])
    session_name = models.CharField(max_length=100, blank=True, default="Morning Session")
    scheduled_start = models.DateTimeField(null=True, blank=True)
    scheduled_end = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    d_judges_count = models.PositiveIntegerField(default=4, help_text="Configurable D-seats: typically 4 for Traditional")

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        cat_str = f" - {self.age_category.name}" if self.age_category else ""
        gender_str = f" ({self.gender})" if self.gender else ""
        return f"{self.stage_name} - {self.event_config.get_event_type_display()}{cat_str}{gender_str} ({self.round_type})"

class PanelSeatAssignment(models.Model):
    panel = models.ForeignKey(JudgePanel, on_delete=models.CASCADE, related_name='seat_assignments')
    seat_name = models.CharField(max_length=50, help_text="e.g. D1, D2, D3, D4, TIME_1, TIME_2, ARTISTRY_1, ARTISTRY_2, EVALUATOR_1, EVALUATOR_2, CHIEF, STAGE")
    judge = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='panel_seats')
    version = models.PositiveIntegerField(default=1)
    assigned_at = models.DateTimeField(auto_now_add=True)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_seats')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['seat_name']

    def __str__(self):
        return f"{self.panel.stage_name} - {self.seat_name}: {self.judge.email}"

class StageBatch(models.Model):
    panel = models.ForeignKey(JudgePanel, on_delete=models.CASCADE, related_name='batches')
    batch_number = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, default='ACTIVE', choices=[
        ('QUEUED', 'Queued'),
        ('ACTIVE', 'Active on Stage'),
        ('SUBMITTED', 'Judging Submitted'),
        ('COMPLETED', 'Completed & Released')
    ])
    entries = models.ManyToManyField('registrations.EventEntry', related_name='batches')
    current_asana_index = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['batch_number']

    def __str__(self):
        return f"{self.panel.stage_name} - Batch #{self.batch_number} [{self.status}]"
