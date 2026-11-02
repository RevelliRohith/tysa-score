import base64
import hashlib
import secrets
from django.conf import settings
from cryptography.fernet import Fernet

def get_encryption_key() -> bytes:
    """
    Returns 32-byte urlsafe base64 key derived from server settings.
    If settings.JUDGE_CREDENTIALS_ENCRYPTION_KEY is provided, uses that.
    Otherwise derives deterministically from settings.SECRET_KEY.
    """
    custom_key = getattr(settings, 'JUDGE_CREDENTIALS_ENCRYPTION_KEY', None)
    if custom_key:
        if isinstance(custom_key, str):
            return custom_key.encode('utf-8')
        return custom_key
    # Deterministic SHA-256 derivation from settings.SECRET_KEY
    digest = hashlib.sha256(f"judge-credentials-encryption-key:{settings.SECRET_KEY}".encode('utf-8')).digest()
    return base64.urlsafe_b64encode(digest)

def encrypt_temp_password(plain_password: str) -> str:
    """
    Encrypts a plaintext temporary password into an authenticated Fernet token.
    """
    if not plain_password:
        return ""
    f = Fernet(get_encryption_key())
    token = f.encrypt(plain_password.encode('utf-8'))
    return token.decode('utf-8')

def decrypt_temp_password(encrypted_token: str) -> str:
    """
    Decrypts an authenticated Fernet token back to plaintext password.
    Returns empty string if token is missing or corrupted.
    """
    if not encrypted_token:
        return ""
    try:
        f = Fernet(get_encryption_key())
        decrypted = f.decrypt(encrypted_token.encode('utf-8'))
        return decrypted.decode('utf-8')
    except Exception:
        return ""

def generate_judge_temp_password(length: int = 12) -> str:
    """
    Generates a cryptographically strong, unique random temporary password.
    Format guarantees uppercase, lowercase, digits, and special characters.
    Avoids visually ambiguous characters (0, O, 1, l, I).
    Example: Jdg#7kP9$mR2
    """
    upper_pool = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    lower_pool = "abcdefghijkmnpqrstuvwxyz"
    digits_pool = "23456789"
    special_pool = "!@#$%&*+?"

    prefix = "Jdg"
    spec1 = secrets.choice(special_pool)
    dig1 = secrets.choice(digits_pool)
    low1 = secrets.choice(lower_pool)
    up1 = secrets.choice(upper_pool)
    dig2 = secrets.choice(digits_pool)
    spec2 = secrets.choice(special_pool)

    all_chars = upper_pool + lower_pool + digits_pool + special_pool
    remaining_len = max(0, length - 9)
    remaining = "".join(secrets.choice(all_chars) for _ in range(remaining_len))

    return f"{prefix}{spec1}{dig1}{low1}{up1}{dig2}{spec2}{remaining}"
