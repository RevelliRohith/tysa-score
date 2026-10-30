from django.db import models
from django.utils import timezone

class ChampionshipStatus(models.TextChoices):
    DRAFT = 'DRAFT', 'Draft'
    PUBLISHED = 'PUBLISHED', 'Published'
    IN_PROGRESS = 'IN_PROGRESS', 'In Progress'
    COMPLETED = 'COMPLETED', 'Completed'

class EventType(models.TextChoices):
    TRADITIONAL = 'TRADITIONAL', 'Traditional Yogasana'
    ARTISTIC_SINGLE = 'ARTISTIC_SINGLE', 'Artistic Yogasana Single'
    ARTISTIC_PAIR = 'ARTISTIC_PAIR', 'Artistic Yogasana Pair'
    RHYTHMIC_PAIR = 'RHYTHMIC_PAIR', 'Rhythmic Yogasana Pair'

class AgeCategory(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    min_age = models.IntegerField(help_text="Minimum age inclusive")
    max_age = models.IntegerField(help_text="Maximum age inclusive")
    display_order = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['display_order']

    def __str__(self):
        return f"{self.name} ({self.min_age}–{self.max_age} yrs)"

class Championship(models.Model):
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=50, unique=True, help_text="e.g. TYSA-STATE-2026")
    year = models.PositiveIntegerField(default=2026)
    start_date = models.DateField()
    end_date = models.DateField()
    registration_start = models.DateField()
    registration_end = models.DateField()
    age_cutoff_date = models.DateField(help_text="Official reference date for calculating competitor age")
    
    venue_name = models.CharField(max_length=200)
    venue_city = models.CharField(max_length=100, default="Hyderabad")
    venue_address = models.TextField(blank=True)
    
    rules_edition = models.CharField(max_length=150, default="Yogasana Bharat COP & Circular 2024–2026")
    is_rules_preset_verified = models.BooleanField(
        default=False, 
        help_text="Official score publication is blocked until rules preset is independently certified and verified against COP"
    )
    status = models.CharField(max_length=20, choices=ChampionshipStatus.choices, default=ChampionshipStatus.DRAFT)
    logo = models.ImageField(upload_to='championships/logos/', blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.year})"

class EventConfiguration(models.Model):
    championship = models.ForeignKey(Championship, on_delete=models.CASCADE, related_name='event_configurations')
    event_type = models.CharField(max_length=30, choices=EventType.choices)
    is_enabled = models.BooleanField(default=True)
    
    # Division configuration (Male, Female - strictly no mixed pairs per sporting rules)
    allow_male = models.BooleanField(default=True)
    allow_female = models.BooleanField(default=True)
    
    # Independent age categories for this specific event
    age_categories = models.ManyToManyField(AgeCategory, related_name='event_configs')
    
    total_asanas = models.PositiveIntegerField(default=7, help_text="7 for Traditional, 10 for Artistic/Rhythmic")
    max_batch_size = models.PositiveIntegerField(default=5, help_text="Simultaneous athletes on stage: 1-5 prelims, 1-3 finals for Traditional")
    
    prelim_compulsory_count = models.PositiveIntegerField(default=4)
    prelim_optional_count = models.PositiveIntegerField(default=3)
    final_compulsory_count = models.PositiveIntegerField(default=2)
    final_optional_count = models.PositiveIntegerField(default=5)
    
    rule_preset_version = models.CharField(max_length=50, default="v1.0-official")

    class Meta:
        unique_together = ('championship', 'event_type')

    def __str__(self):
        return f"{self.championship.name} - {self.get_event_type_display()}"

    @property
    def is_pair(self):
        return self.event_type in (EventType.ARTISTIC_PAIR, EventType.RHYTHMIC_PAIR)

class DistrictQuota(models.Model):
    championship = models.ForeignKey(Championship, on_delete=models.CASCADE, related_name='district_quotas')
    district_name = models.CharField(max_length=100)
    quota_limit = models.PositiveIntegerField(default=20)
    quota_used = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('championship', 'district_name')

    def __str__(self):
        return f"{self.district_name}: {self.quota_used}/{self.quota_limit} in {self.championship.code}"
