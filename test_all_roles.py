import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.test import Client
from django.contrib.auth import get_user_model
from athletes.models import Athlete

User = get_user_model()

roles_tests = [
    ('admin@tysa.org', '/dashboard/', 'System Admin'),
    ('cd@tysa.org', '/cd/dashboard/', 'Competition Director'),
    ('district@tysa.org', '/district/approvals/', 'District Officer'),
    ('venue@tysa.org', '/venue/verification/', 'Venue Clearance Desk'),
    ('stage@tysa.org', '/competition/stage/', 'Stage Manager'),
    ('chief@tysa.org', '/judges/portal/', 'Chief Judge'),
    ('d1@tysa.org', '/judges/portal/', 'Difficulty Judge (D)'),
    ('jury@tysa.org', '/jury/', 'Jury Official'),
    ('multirole@tysa.org', '/accounts/role-select/', 'Competition Director'),
    ('arun.kumar@gmail.com', '/accounts/no-role/', 'Official Staff'),
]

print("=== TESTING AUTHENTICATED STAFF WORKSPACES ===")
for email, url, expected_role_substr in roles_tests:
    user = User.objects.get(email=email)
    client = Client()
    client.force_login(user)
    resp = client.get(url)
    content = resp.content.decode('utf-8')
    has_sidebar = 'id="adminSidebar"' in content
    has_header = 'Authorized Championships' in content or 'Telangana State Championship' in content or 'acb' in content
    print(f"[{resp.status_code}] {email:<25} -> {url:<25} | Sidebar: {has_sidebar} | RoleSubstr: {expected_role_substr in content}")
    if resp.status_code != 200:
        print(f"   ERROR: Status is {resp.status_code}")

print("\n=== TESTING ATHLETE KEY-ACCESS WORKSPACE ===")
ath = Athlete.objects.first()
client = Client()
s = client.session
s['athlete_id'] = ath.id
s.save()
ath_url = f"/athletes/id-card/{ath.public_athlete_id}/"
resp = client.get(ath_url)
content = resp.content.decode('utf-8')
has_sidebar = 'id="adminSidebar"' in content
has_athlete_role = 'Athlete Workspace' in content or 'Registered Athlete' in content
print(f"[{resp.status_code}] Athlete {ath.public_athlete_id:<16} -> {ath_url:<25} | Sidebar: {has_sidebar} | AthleteRole: {has_athlete_role}")

print("\n=== TESTING PUBLIC STANDALONE SCREENS (NO PRIVATE SIDEBAR) ===")
pub_tests = [
    ('/accounts/login/', 'Login'),
    ('/display/live/', 'Arena Live Display'),
    ('/display/second-screen/', '2nd Screen Display'),
]
pub_client = Client()
for p_url, p_name in pub_tests:
    resp = pub_client.get(p_url)
    content = resp.content.decode('utf-8')
    has_sidebar = 'id="adminSidebar"' in content
    print(f"[{resp.status_code}] {p_name:<25} -> {p_url:<25} | Standalone (No Sidebar): {not has_sidebar}")
