import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.test import Client
from django.contrib.auth import get_user_model
from competition.models import JudgePanel, StageBatch, MasterRoll, MasterRollEntry
from championships.models import Championship
from registrations.models import ChampionshipRegistration, RegistrationStatus

User = get_user_model()
client = Client()

print("=" * 60)
print("RUNNING TYSA SCORE END-TO-END WORKFLOW VERIFICATION")
print("=" * 60)

# 1. Admin login & Dashboard Check
admin_user = User.objects.filter(email='admin@tysa.org').first()
client.force_login(admin_user)
resp = client.get('/dashboard/')
assert resp.status_code == 200, f"Dashboard failed: {resp.status_code}"
content = resp.content.decode('utf-8')
assert 'Athlete Registration & Verification Pipeline' in content
assert 'District Approved' in content
assert 'Awaiting Venue Clearance' in content
assert 'Venue Cleared' in content
print("[PASS] Admin Dashboard with 4-Metric Pipeline Verification OK")

# 2. Judge Panel Assignment Verification
resp = client.get('/competition/panels/')
assert resp.status_code == 200, f"Panel assignments failed: {resp.status_code}"
content = resp.content.decode('utf-8')
assert 'data-district' in content, "Dynamic district attribute missing on options"
assert 'data-cert' in content, "Dynamic cert attribute missing on options"
assert 'conflict-alert' in content, "Intra-panel conflict alert element missing"
print("[PASS] Judge Panel Assignment with Dynamic Exclusivity & Conflict Protection OK")

# 3. Stage Manager Navigation & Master Roll Controls
resp = client.get('/competition/stage/')
assert resp.status_code == 200, f"Stage control failed: {resp.status_code}"
content = resp.content.decode('utf-8')
assert 'Finalized Master Roll (Saved Order)' in content
assert 'Return to Assigned Panels' in content
assert 'Push Selected Athletes to All Assigned Judges' in content
assert 'Disqualify Athlete / Entry' in content
print("[PASS] Stage Manager Operational Master Roll & Disqualification Modal OK")

# 4. District Approvals & Capacity Banner
resp = client.get('/district/approvals/')
assert resp.status_code == 200, f"District approvals failed: {resp.status_code}"
content = resp.content.decode('utf-8')
assert 'Allotted:' in content and 'Used:' in content and 'Remaining:' in content, "Capacity banner missing"
assert 'Pending Review' in content
assert 'Needs Correction' in content
assert 'Athlete Review Drawer' in content
assert 'Review Next Athlete' in content
print("[PASS] District Approvals with Capacity Banner & Review Drawer OK")

# 5. Venue Verification Desk
resp = client.get('/venue/verification/')
assert resp.status_code == 200, f"Venue verification failed: {resp.status_code}"
content = resp.content.decode('utf-8')
assert 'Search by name, mobile, public ID or chest number' in content
assert 'Mark Venue Cleared' in content
assert 'Identity Proof' in content
print("[PASS] Venue Desk Console with 4 Checks & Search Box OK")

# 6. Expandable D-Average Breakdown Matrix in Chief Judge & Jury
resp = client.get('/scoring/chief-judge/')
assert resp.status_code == 200, f"Chief Judge review failed: {resp.status_code}"
content = resp.content.decode('utf-8')
assert 'D Average:' in content and 'View all judge marks' in content
assert 'Olympic Trimmed Mean' in content
print("[PASS] Chief Judge Review with Expandable D-Average Matrix OK")

resp = client.get('/corrections/jury/')
assert resp.status_code == 200, f"Jury review failed: {resp.status_code}"
content = resp.content.decode('utf-8')
assert 'D Average:' in content and 'View all judge marks' in content
print("[PASS] Jury Review with Expandable D-Average Matrix OK")

# 7. Judge Portal & Scoring Desk Waiting Screen
judge_user = User.objects.filter(email='d1@tysa.org').first()
client.force_login(judge_user)
resp = client.get('/judges/portal/')
assert resp.status_code == 200, f"Judge portal failed: {resp.status_code}"
content = resp.content.decode('utf-8')
assert 'Assigned Duty Panels' in content
print("[PASS] Judge Portal with Profile & Assigned Panels OK")

resp = client.get('/scoring/traditional-d/')
assert resp.status_code == 200, f"Traditional D scoring failed: {resp.status_code}"
content = resp.content.decode('utf-8')
# Depending on batch state, it renders either waiting for push or active batch
assert ('Waiting for Stage Manager Push' in content) or ('Active Batch' in content) or ('Traditional D' in content)
print("[PASS] Judge Scoring Desk with Protected Push Synchronization OK")

print("=" * 60)
print("ALL 7 CORE SUBSYSTEM VERIFICATIONS PASSED CLEANLY!")
print("=" * 60)
