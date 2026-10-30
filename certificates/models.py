import uuid
from django.db import models
from django.conf import settings

class TemplateType(models.TextChoices):
    PARTICIPATION = 'PARTICIPATION', 'Certificate of Participation'
    MERIT = 'MERIT', 'Certificate of Merit (Medal / Rank)'
    JUDGE_DUTY = 'JUDGE_DUTY', 'Certificate of Officiating Duty (Judge)'
    COACH_RECOGNITION = 'COACH_RECOGNITION', 'Coach Appreciation Certificate'
    VOLUNTEER_SERVICE = 'VOLUNTEER_SERVICE', 'Volunteer Service Certificate'
    GUEST_APPRECIATION = 'GUEST_APPRECIATION', 'Distinguished Guest Memento'

class CertificateTemplate(models.Model):
    name = models.CharField(max_length=150)
    template_type = models.CharField(max_length=30, choices=TemplateType.choices, default=TemplateType.PARTICIPATION)
    background_image = models.ImageField(upload_to='certificates/templates/', blank=True, null=True)
    
    # JSON schema of dynamic canvas fields:
    # {
    #   "recipient_name": {"x": 200, "y": 300, "fontSize": 28, "fontWeight": "bold", "color": "#0F172A", "align": "center"},
    #   "athlete_id": {"x": 200, "y": 340, "fontSize": 14, ...},
    #   "event_name": {"x": 200, "y": 380, ...},
    #   "district": {"x": 200, "y": 420, ...},
    #   "rank": {"x": 200, "y": 460, ...},
    #   "cert_number": {"x": 100, "y": 550, ...},
    #   "issue_date": {"x": 400, "y": 550, ...},
    #   "qr_code": {"x": 480, "y": 500, "width": 80, "height": 80}
    # }
    field_layout = models.JSONField(default=dict, blank=True)
    is_published = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.get_template_type_display()}) v{self.version}"

class IssuedCertificate(models.Model):
    template = models.ForeignKey(CertificateTemplate, on_delete=models.CASCADE, related_name='issued_certificates')
    championship = models.ForeignKey('championships.Championship', on_delete=models.CASCADE, related_name='certificates')
    certificate_number = models.CharField(max_length=50, unique=True, db_index=True)
    verification_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    
    recipient_type = models.CharField(max_length=30, choices=[
        ('ATHLETE', 'Athlete'),
        ('JUDGE', 'Universal Judge'),
        ('COACH', 'Coach'),
        ('VOLUNTEER', 'Volunteer'),
        ('GUEST', 'Guest')
    ])
    
    recipient_name = models.CharField(max_length=150)
    public_athlete_id = models.CharField(max_length=50, blank=True)
    district = models.CharField(max_length=100, blank=True)
    event_name = models.CharField(max_length=150, blank=True)
    age_category = models.CharField(max_length=100, blank=True)
    gender = models.CharField(max_length=20, blank=True)
    rank_achieved = models.CharField(max_length=50, blank=True)
    
    pdf_file = models.FileField(upload_to='certificates/issued/', blank=True, null=True)
    is_revoked = models.BooleanField(default=False)
    revoke_reason = models.TextField(blank=True)
    
    issued_at = models.DateTimeField(auto_now_add=True)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-issued_at']

    def __str__(self):
        return f"{self.certificate_number} - {self.recipient_name} ({self.template.get_template_type_display()})"


class CertificateSequence(models.Model):
    """
    Dedicated atomic sequence counter per championship and prefix for collision-free certificate numbers.
    """
    championship = models.ForeignKey('championships.Championship', on_delete=models.CASCADE, related_name='cert_sequences')
    prefix = models.CharField(max_length=20)
    last_number = models.PositiveIntegerField(default=1000)

    class Meta:
        unique_together = ('championship', 'prefix')

    def __str__(self):
        return f"{self.championship.code} - {self.prefix}: {self.last_number}"
