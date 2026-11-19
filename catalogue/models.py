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
    def get_category_info(cls, code_or_type):
        """
        Returns (major_category, sub_category, code_prefix) for any asana code or raw type.
        Official Yogasana Bharat COP taxonomy:
        1. Forward Bend Yogasana:
           i. Forward Bend Standing (FBS)
           ii. Forward Bend Floor (FBF)
        2. Backward Bend Yogasana:
           i. Backbend Standing (BBS)
           ii. Backbend Floor (BBF)
        3. Twisting Body Yogasana:
           i. Twisting Body Forward Bend (TBF)
           ii. Twisting Body Backbend (TBB)
        4. Leg Balance Yogasana:
           i. Leg Balance Forward Bend (LBF)
           ii. Leg Balance Backward Bend (LBB)
        5. Hand Balance Yogasana:
           i. Hand Balance Forward Bend (HBF)
           ii. Hand Balance Backbend (HBB)
        """
        val = str(code_or_type or '').strip().upper()
        prefix = val.split('-')[0] if '-' in val else val[:3]

        taxonomy = {
            'FBS': ('Forward Bend Yogasana', 'Forward Bend Standing (FBS)', 'FBS'),
            'FBF': ('Forward Bend Yogasana', 'Forward Bend Floor (FBF)', 'FBF'),
            'BBS': ('Backward Bend Yogasana', 'Backbend Standing (BBS)', 'BBS'),
            'BBF': ('Backward Bend Yogasana', 'Backbend Floor (BBF)', 'BBF'),
            'TBF': ('Twisting Body Yogasana', 'Twisting Body Forward Bend (TBF)', 'TBF'),
            'TBB': ('Twisting Body Yogasana', 'Twisting Body Backbend (TBB)', 'TBB'),
            'LBF': ('Leg Balance Yogasana', 'Leg Balance Forward Bend (LBF)', 'LBF'),
            'LBB': ('Leg Balance Yogasana', 'Leg Balance Backward Bend (LBB)', 'LBB'),
            'HBF': ('Hand Balance Yogasana', 'Hand Balance Forward Bend (HBF)', 'HBF'),
            'HBB': ('Hand Balance Yogasana', 'Hand Balance Backbend (HBB)', 'HBB'),
        }

        if prefix in taxonomy:
            return taxonomy[prefix]

        # Fallback matching by type string
        val_lower = val.lower()
        if 'forward bend standing' in val_lower or 'standing forward' in val_lower:
            return taxonomy['FBS']
        elif 'forward bend floor' in val_lower or 'floor forward' in val_lower:
            return taxonomy['FBF']
        elif 'back bend standing' in val_lower or 'backbend standing' in val_lower:
            return taxonomy['BBS']
        elif 'back bend floor' in val_lower or 'backbend floor' in val_lower:
            return taxonomy['BBF']
        elif 'twisting' in val_lower and ('forward' in val_lower or 'floor' in val_lower):
            return taxonomy['TBF']
        elif 'twisting' in val_lower and ('back' in val_lower or 'balance' in val_lower):
            return taxonomy['TBB']
        elif 'leg' in val_lower and 'forward' in val_lower:
            return taxonomy['LBF']
        elif 'leg' in val_lower and ('back' in val_lower or 'backward' in val_lower):
            return taxonomy['LBB']
        elif 'hand' in val_lower and 'forward' in val_lower:
            return taxonomy['HBF']
        elif 'hand' in val_lower and ('back' in val_lower or 'backward' in val_lower):
            return taxonomy['HBB']

        return ('General / Base', val, prefix)

    @classmethod
    def normalize_category(cls, raw_type, code=''):
        lookup = code or raw_type
        major, sub, prefix = cls.get_category_info(lookup)
        if prefix in ['FBS', 'FBF', 'BBS', 'BBF', 'TBF', 'TBB', 'LBF', 'LBB', 'HBF', 'HBB']:
            return f"{major} - {sub}"
        return raw_type.strip()
