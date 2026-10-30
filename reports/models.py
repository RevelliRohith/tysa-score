from django.db import models
from django.conf import settings

class DistrictTrophySetting(models.Model):
    championship = models.OneToOneField('championships.Championship', on_delete=models.CASCADE, related_name='trophy_settings')
    gold_points = models.PositiveIntegerField(default=5)
    silver_points = models.PositiveIntegerField(default=3)
    bronze_points = models.PositiveIntegerField(default=2)
    is_approved = models.BooleanField(default=False)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Trophy Points Policy: {self.championship.code} (G:{self.gold_points}, S:{self.silver_points}, B:{self.bronze_points})"

class CeremonyRecord(models.Model):
    championship = models.ForeignKey('championships.Championship', on_delete=models.CASCADE, related_name='ceremonies')
    ceremony_type = models.CharField(max_length=20, choices=[
        ('OPENING', 'Opening Ceremony'),
        ('CLOSING', 'Closing & Prize Distribution Ceremony')
    ])
    date = models.DateField()
    venue = models.CharField(max_length=200)
    summary = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.get_ceremony_type_display()} - {self.championship.name}"

class GuestDignitary(models.Model):
    ceremony = models.ForeignKey(CeremonyRecord, on_delete=models.CASCADE, related_name='dignitaries')
    name = models.CharField(max_length=150)
    designation = models.CharField(max_length=150)
    organization = models.CharField(max_length=200, blank=True)
    speech_notes = models.TextField(blank=True)
    attended = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.designation})"

class PhotoRecord(models.Model):
    championship = models.ForeignKey('championships.Championship', on_delete=models.CASCADE, related_name='photos')
    title = models.CharField(max_length=200)
    caption = models.TextField(blank=True)
    category = models.CharField(max_length=30, choices=[
        ('CEREMONY', 'Ceremonies & Protocols'),
        ('STAGE', 'Competition Stage Action'),
        ('PODIUM', 'Medal Ceremony / Podium'),
        ('PRESS', 'Press & Dignitaries')
    ], default='STAGE')
    image_file = models.ImageField(upload_to='reports/photos/')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title

class PressClipping(models.Model):
    championship = models.ForeignKey('championships.Championship', on_delete=models.CASCADE, related_name='press_clippings')
    headline = models.CharField(max_length=250)
    publication_name = models.CharField(max_length=150)
    published_date = models.DateField()
    url = models.URLField(blank=True)
    clipping_image = models.ImageField(upload_to='reports/press/', blank=True, null=True)
    summary = models.TextField(blank=True)

    def __str__(self):
        return f"{self.headline} ({self.publication_name})"
