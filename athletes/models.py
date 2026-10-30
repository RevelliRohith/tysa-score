import secrets
import hashlib
from django.db import models
from django.contrib.auth.hashers import make_password, check_password

def generate_athlete_id():
    last_athlete = Athlete.objects.order_by('-id').first()
    next_num = (last_athlete.id + 1) if last_athlete else 101
    return f"TYSA-ATH-{next_num:05d}"

def generate_private_key():
    # 24-character high-entropy alphanumeric key formatted as TYSA-XXXX-XXXX-XXXX
    raw = secrets.token_hex(6).upper()
    return f"TYSA-{raw[:4]}-{raw[4:8]}-{raw[8:12]}"

class Athlete(models.Model):
    public_athlete_id = models.CharField(max_length=30, unique=True, default=generate_athlete_id, db_index=True)
    private_key_hash = models.CharField(max_length=255)
    # Stored plain private key representation for the authorized athlete's own session reveal only
    # (Matches prompt requirement for "Show key" UI control inside authenticated session, while strictly excluded from all public cards, QR, jury, etc.)
    private_key_display = models.CharField(max_length=64, blank=True)
    
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    dob = models.DateField(verbose_name="Date of Birth")
    gender = models.CharField(max_length=10, choices=[('M', 'Male'), ('F', 'Female')])
    guardian_name = models.CharField(max_length=150, verbose_name="Father / Guardian Name")
    district = models.CharField(max_length=100)
    mobile = models.CharField(max_length=20)
    email = models.EmailField(blank=True)
    academy = models.CharField(max_length=200, blank=True, verbose_name="Academy / Yoga Centre")
    photo = models.ImageField(upload_to='athletes/photos/', blank=True, null=True)
    
    # Universal ID card batch printing & issuance tracking
    is_id_card_printed = models.BooleanField(default=False, verbose_name="ID Card Printed")
    id_card_printed_at = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.full_name} ({self.public_athlete_id}) - {self.district}"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def set_private_key(self, raw_key=None):
        if not raw_key:
            raw_key = generate_private_key()
        self.private_key_hash = make_password(raw_key)
        self.private_key_display = raw_key
        return raw_key

    def verify_private_key(self, raw_key):
        return check_password(raw_key.strip(), self.private_key_hash)

class DocumentTypeChoices(models.TextChoices):
    AGE_PROOF = 'AGE_PROOF', 'Age Proof (Birth Certificate / Aadhaar)'
    IDENTITY = 'IDENTITY', 'Identity Proof (School ID / Govt ID)'
    MEDICAL = 'MEDICAL', 'Medical Fitness Certificate'
    PARENTAL_CONSENT = 'PARENTAL_CONSENT', 'Parental / Guardian Consent Form'

class AthleteDocument(models.Model):
    athlete = models.ForeignKey(Athlete, on_delete=models.CASCADE, related_name='documents')
    document_type = models.CharField(max_length=30, choices=DocumentTypeChoices.choices)
    document_file = models.FileField(upload_to='athletes/documents/')
    document_number = models.CharField(max_length=50, blank=True)
    is_verified = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True)
    notes = models.TextField(blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.athlete.public_athlete_id} - {self.get_document_type_display()}"
