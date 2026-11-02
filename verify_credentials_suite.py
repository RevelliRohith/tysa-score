import os
import django
import json

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.test import RequestFactory, Client
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.messages.middleware import MessageMiddleware
from judges.models import UniversalJudge, JudgeCredential, CredentialSetupStatus, DeliveryStatus
from judges.crypto import generate_judge_temp_password, encrypt_temp_password, decrypt_temp_password
from audit.models import AuditLog

User = get_user_model()

def run_tests():
    print("=" * 70)
    print("TYSA SCORE - SOFTWARE-GENERATED JUDGE CREDENTIALS TEST SUITE")
    print("=" * 70)

    # 1. Cryptography & Uniqueness Test
    print("\n--- 1. Testing Cryptography & Entropy ---")
    pwds = set()
    for _ in range(50):
        p = generate_judge_temp_password(length=12)
        assert len(p) >= 12, "Password length too short"
        assert p not in pwds, "Duplicate temporary password detected!"
        pwds.add(p)
        # Check roundtrip encryption
        token = encrypt_temp_password(p)
        assert token is not None
        decrypted = decrypt_temp_password(token)
        assert decrypted == p, f"Decryption mismatch: {p} != {decrypted}"
    print(f"  [PASS] Generated 50 unique temporary passwords with 100% round-trip AES-Fernet integrity.")

    # 2. Database Model & Expiry
    print("\n--- 2. Testing JudgeCredential Model Methods ---")
    admin_user = User.objects.filter(is_superuser=True).first()
    if not admin_user:
        admin_user = User.objects.filter(email='admin@tysa.org').first()

    # Create a test judge
    test_user, _ = User.objects.get_or_create(email='test_judge_temp@tysa.org', defaults={'full_name': 'Test Official'})
    test_user.set_unusable_password()
    test_user.save()

    test_judge, _ = UniversalJudge.objects.get_or_create(
        user=test_user,
        defaults={'district': 'Hyderabad', 'approval_status': 'APPROVED'}
    )

    cred, _ = JudgeCredential.objects.get_or_create(judge=test_judge)
    initial_pwd = cred.generate_new_temporary_password(admin_user=admin_user)
    assert cred.must_change_password is True, "must_change_password should be True"
    assert cred.encrypted_temp_password is not None, "Encrypted password should be present"
    assert cred.is_expired is False, "Newly created password should not be expired"
    assert cred.setup_status == CredentialSetupStatus.TEMPORARY_ACTIVE, "Setup status should be TEMPORARY_ACTIVE"
    print(f"  [PASS] Initial credential generation successful. Plaintext: {initial_pwd}")

    # Check AuditLog for plain password exposure
    latest_audit = AuditLog.objects.filter(resource_type="JudgeCredential", resource_id=str(cred.id)).order_by('-timestamp').first()
    if latest_audit:
        audit_str = json.dumps(latest_audit.details)
        assert initial_pwd not in audit_str, "CRITICAL: Plaintext password leaked into AuditLog!"
        print(f"  [PASS] AuditLog verified: zero sensitive secrets recorded in audit details.")

    # 3. First-login Password Change
    print("\n--- 3. Testing First-Login Password Change ---")
    test_user.refresh_from_db()
    # Verify temporary password check
    assert test_user.check_password(initial_pwd) is True, "User cannot authenticate with temporary password"

    # Simulate judge setting permanent password
    new_perm_pwd = "MySecretJudgePass@2026"
    test_user.set_password(new_perm_pwd)
    test_user.save()
    cred.mark_password_changed()

    assert cred.must_change_password is False, "must_change_password must be False after change"
    assert cred.encrypted_temp_password is None, "encrypted_temp_password must be purged after change"
    assert cred.setup_status == CredentialSetupStatus.PASSWORD_SET, "Setup status must be PASSWORD_SET"
    assert not decrypt_temp_password(cred.encrypted_temp_password), "Cannot decrypt purged credential"
    assert test_user.check_password(new_perm_pwd) is True, "User cannot authenticate with new permanent password"
    assert test_user.check_password(initial_pwd) is False, "User can still authenticate with old temporary password!"
    print("  [PASS] First-login password change cleanly purged temporary ciphertext and activated confidential permanent password.")

    # 4. Access Control (Non-Admin Blocked)
    print("\n--- 4. Testing Role Access Restrictions ---")
    client = Client()

    # Non-admin users
    non_admin_emails = ['d1@tysa.org', 'stage@tysa.org', 'district.hyderabad@tysa.org', 'venue@tysa.org']
    for e in non_admin_emails:
        u = User.objects.filter(email=e).first()
        if u:
            client.force_login(u)
            resp = client.get('/judges/credentials/')
            assert resp.status_code in [302, 403], f"Non-admin {e} accessed credentials page! Status: {resp.status_code}"
            print(f"  [PASS] Non-admin '{e}' correctly rejected with status {resp.status_code}.")

    # Admin access
    client.force_login(admin_user)
    resp = client.get('/judges/credentials/')
    assert resp.status_code == 200, f"Admin could not access /judges/credentials/! Status: {resp.status_code}"
    print(f"  [PASS] System Admin granted access to /judges/credentials/ (Status: 200).")

    # 5. Clean up test judge
    test_judge.delete()
    test_user.delete()
    print("  [PASS] Test records cleaned up.")

    # 6. Retrieve Credentials of Judges from User Screenshot
    print("\n" + "=" * 70)
    print("PANEL OFFICIALS CREDENTIALS (FROM USER SCREENSHOT):")
    print("=" * 70)
    target_emails = [
        ('D1 Judge', 'Smt. Lakshmi Devi', 'd1@tysa.org'),
        ('D2 Judge', 'Sri R. Venkatraman', 'd2@tysa.org'),
        ('D3 Judge', 'Dr. Sunita Reddy', 'd3@tysa.org'),
        ('D4 Judge', 'Sri M. Ramesh', 'd4@tysa.org'),
        ('Time Judge', 'Sri K. Prakash', 'time@tysa.org'),
        ('Evaluator', 'Smt. Priya Varun', 'evaluator@tysa.org'),
        ('Artistry Judge', 'Sri Arvind Swamy', 'artistry@tysa.org'),
        ('Chief Judge', 'Dr. V. Nageshwar Rao', 'chief@tysa.org'),
    ]

    for role_name, official_name, email in target_emails:
        user = User.objects.filter(email=email).first()
        if not user:
            print(f"{role_name} ({official_name}): User not found for {email}")
            continue
        judge = UniversalJudge.objects.filter(user=user).first()
        cred = getattr(judge, 'credential', None) if judge else None

        # Check existing password
        working_pw = None
        for test_pw in ['judge123', 'admin123', 'tysa123', 'judge@123']:
            if user.check_password(test_pw):
                working_pw = test_pw
                break

        print(f"Role: {role_name:<16} | Name: {official_name:<22}")
        print(f"  - Login Email:  {email}")
        print(f"  - Universal ID: {judge.judge_code if judge else 'N/A'}")
        print(f"  - District:     {judge.district if judge else 'N/A'}")
        print(f"  - Active Pwd:   {working_pw if working_pw else '[Custom Permanent Password Set]'}")
        print(f"  - Setup Status: {cred.setup_status_display if cred else 'N/A'}")
        print(f"  - Delivery:     {cred.get_delivery_status_display() if cred else 'N/A'}")
        print("-" * 50)

if __name__ == '__main__':
    run_tests()
