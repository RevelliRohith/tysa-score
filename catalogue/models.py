from decimal import Decimal
from django.db import models

class AsanaStatus(models.TextChoices):
    PUBLISHED = 'PUBLISHED', 'Published'
    DRAFT = 'DRAFT', 'Draft'
    ARCHIVED = 'ARCHIVED', 'Archived'

class Asana(models.Model):
    source_id = models.IntegerField(unique=True, help_text="Original source dataset ID")
    sno = models.IntegerField(help_text="Serial number from official catalogue")
    raw_type = models.CharField(max_length=150, help_text="Raw unedited category string from source")
    display_category = models.CharField(max_length=150, help_text="Normalized clean category label for UI display")
    
    code = models.CharField(max_length=30, unique=True, db_index=True)
    base_value = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.80'))
    
    image_filename = models.CharField(max_length=150, blank=True, null=True)
    image_url = models.CharField(max_length=255, blank=True, null=True)
    
    description = models.TextField(help_text="Full official description")
    display_name = models.CharField(max_length=200, help_text="Derived pose name for crisp display")
    
    is_reference_record = models.BooleanField(default=False, help_text="True for BAC-A..BAC-E base difficulty scales")
    is_published = models.BooleanField(default=True, help_text="Only published poses are selectable by athletes")
    status = models.CharField(max_length=20, choices=AsanaStatus.choices, default=AsanaStatus.PUBLISHED, db_index=True)
    version = models.PositiveIntegerField(default=1)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sno']

    def __str__(self):
        return f"{self.code} - {self.display_name} (BV: {self.base_value})"

    @classmethod
    def normalize_category(cls, raw_type):
        mapping = {
            'Forward Bend Standings': 'Forward Bend Standing',
            'Forward Bend Floor': 'Forward Bend Floor',
            'Back Bend Standing': 'Back Bend Standing',
            'Back Bend Floor': 'Back Bend Floor',
            'Twisting Body Floor': 'Twisting Body Floor',
            'Twisting Body Balance': 'Twisting Body Balance',
            'Leg Balance Backward Bend': 'Leg Balance Backward Bend',
            'gle Leg balance Forward Be': 'Single Leg Balance Forward Bend',
            'Hand Balance Forward Bend': 'Hand Balance Forward Bend',
            'and Balance Backward Ben': 'Hand Balance Backward Bend',
            'base asana code': 'Base Difficulty Reference',
        }
        return mapping.get(raw_type.strip(), raw_type.strip())
