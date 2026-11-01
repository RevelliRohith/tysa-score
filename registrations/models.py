from decimal import Decimal
from django.db import models
from django.conf import settings

class RegistrationStatus(models.TextChoices):
    DRAFT = 'DRAFT', 'Draft'
    SUBMITTED = 'SUBMITTED', 'Submitted'
    NEEDS_CORRECTION = 'NEEDS_CORRECTION', 'Needs Correction'
    DISTRICT_APPROVED = 'DISTRICT_APPROVED', 'District Approved'
    DISTRICT_REJECTED = 'DISTRICT_REJECTED', 'District Rejected'
    VENUE_CLEARED = 'VENUE_CLEARED', 'Venue Cleared'

class ChampionshipRegistration(models.Model):
    athlete = models.ForeignKey('athletes.Athlete', on_delete=models.CASCADE, related_name='registrations')
    championship = models.ForeignKey('championships.Championship', on_delete=models.CASCADE, related_name='registrations')
    registration_number = models.CharField(max_length=50, unique=True)
    status = models.CharField(max_length=30, choices=RegistrationStatus.choices, default=RegistrationStatus.DRAFT)
    
    # District Level Approval
    district_approval_notes = models.TextField(blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='district_approvals')
    approved_at = models.DateTimeField(null=True, blank=True)
    
    # Venue Clearance Desk
    venue_cleared = models.BooleanField(default=False)
    venue_cleared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='venue_clearances')
    venue_cleared_at = models.DateTimeField(null=True, blank=True)
    venue_notes = models.TextField(blank=True)
    
    # Venue Desk Exception Tracking
    has_venue_exception = models.BooleanField(default=False)
    exception_reason = models.TextField(blank=True)
    exception_accepted_by_role = models.CharField(max_length=50, blank=True, help_text="CD, Admin, Organizer")
    exception_authorizer_name = models.CharField(max_length=150, blank=True)
    exception_authorizer_mobile = models.CharField(max_length=20, blank=True)
    exception_granted_at = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('athlete', 'championship')

    def __str__(self):
        return f"{self.registration_number} - {self.athlete.full_name} ({self.get_status_display()})"

class EventEntry(models.Model):
    registration = models.ForeignKey(ChampionshipRegistration, on_delete=models.CASCADE, related_name='event_entries')
    event_config = models.ForeignKey('championships.EventConfiguration', on_delete=models.CASCADE, related_name='entries')
    age_category = models.ForeignKey('championships.AgeCategory', on_delete=models.CASCADE, related_name='entries')
    gender = models.CharField(max_length=10, choices=[('M', 'Male'), ('F', 'Female')])
    
    # Pair event partner (one pair entry links to primary athlete via registration and partner via partner_athlete)
    partner_athlete = models.ForeignKey('athletes.Athlete', on_delete=models.SET_NULL, null=True, blank=True, related_name='partner_entries')
    
    chest_number = models.CharField(max_length=20, blank=True, db_index=True)
    is_pair = models.BooleanField(default=False)
    is_scratched = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('registration', 'event_config')

    def __str__(self):
        pair_info = f" & {self.partner_athlete.full_name}" if self.partner_athlete else ""
        chest = f" [Chest: {self.chest_number}]" if self.chest_number else ""
        return f"{self.registration.athlete.full_name}{pair_info} - {self.event_config.get_event_type_display()}{chest}"

    @property
    def display_names(self):
        if self.is_pair and self.partner_athlete:
            return f"{self.registration.athlete.full_name} & {self.partner_athlete.full_name}"
        return self.registration.athlete.full_name

    @property
    def district(self):
        return self.registration.athlete.district

class AsanaSequenceItem(models.Model):
    event_entry = models.ForeignKey(EventEntry, on_delete=models.CASCADE, related_name='sequence_items')
    sequence_order = models.PositiveIntegerField(help_text="1 to 7 for Traditional; 1 to 10 for Artistic/Rhythmic")
    round_type = models.CharField(max_length=20, default='PRELIMINARY', choices=[
        ('PRELIMINARY', 'Preliminary'),
        ('SEMI_FINAL', 'Semi-Final'),
        ('FINAL', 'Final')
    ])
    is_compulsory = models.BooleanField(default=False)
    declared_asana = models.ForeignKey('catalogue.Asana', on_delete=models.SET_NULL, null=True)
    
    # Snapshotted fields to ensure catalog updates never mutate submitted routines
    declared_code = models.CharField(max_length=30)
    declared_name = models.CharField(max_length=200)
    declared_bv = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.80'))
    image_url = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        ordering = ['sequence_order']
        unique_together = ('event_entry', 'round_type', 'sequence_order')

    def __str__(self):
        return f"Item {self.sequence_order}: {self.declared_code} ({self.declared_name})"
