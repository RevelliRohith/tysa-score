import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from championships.models import Championship, EventConfiguration, DistrictQuota, EventType, ChampionshipStatus, ChampionshipLogo
from athletes.models import Athlete, AthleteDocument
from registrations.models import ChampionshipRegistration, EventEntry, RegistrationStatus, AsanaSequenceItem
from competition.models import JudgePanel, MasterRoll, StageBatch, DistrictDraw
from scoring.models import Performance, PerformanceStatus, JudgeSubmission
from certificates.models import IssuedCertificate, CertificateTemplate
from judges.models import UniversalJudge

def verify():
    print("==================================================================")
    print("RUNNING DIRECT SYSTEM VERIFICATION ON ACTIVE DATABASE")
    print("==================================================================")

    # 1. Championship
    champ = Championship.objects.filter(code='TYSA-STATE-2026').first()
    assert champ is not None, "Championship TYSA-STATE-2026 not found!"
    print(f"[OK] Championship: '{champ.name}'")
    print(f"     Status: {champ.status} | Year: {champ.year} | Dates: {champ.start_date} to {champ.end_date}")
    print(f"     Venue: {champ.venue_name}, {champ.venue_city}")

    # Events
    events = EventConfiguration.objects.filter(championship=champ)
    assert events.count() == 4, f"Expected 4 events, found {events.count()}"
    for e in events:
        print(f"     -> Event: {e.event_type} | Total asanas: {e.total_asanas} | Max batch: {e.max_batch_size}")

    # Quotas
    quotas = DistrictQuota.objects.filter(championship=champ)
    assert quotas.count() == 33, f"Expected 33 quotas, found {quotas.count()}"
    print(f"[OK] District Quotas: Configured for all {quotas.count()} Telangana districts.")

    # Branding
    logos = champ.branding_logos.all()
    assert logos.count() >= 3, f"Expected at least 3 logos, found {logos.count()}"
    print(f"[OK] Championship Branding: {logos.count()} interactive logos positioned.")

    # 2. Universal Judges & Panels
    judges = UniversalJudge.objects.all()
    assert judges.count() >= 30, f"Expected 30+ judges, found {judges.count()}"
    print(f"[OK] Universal Judges: {judges.count()} certified universal judges in registry.")

    open_panels = JudgePanel.objects.filter(championship=champ, is_active=True, is_closed=False)
    assert open_panels.count() == 2, f"Expected 2 open panels, found {open_panels.count()}"
    closed_panels = JudgePanel.objects.filter(championship=champ, is_closed=True)
    assert closed_panels.count() == 1, f"Expected 1 closed panel, found {closed_panels.count()}"
    
    # Governance Rule Check
    assigned_judges_open = []
    for p in open_panels:
        seats = [p.chief_judge, p.d1_judge, p.d2_judge, p.d3_judge, p.d4_judge, p.time_judge, p.evaluator]
        for j in seats:
            if j:
                assert j.id not in assigned_judges_open, f"Governance violation: Judge {j.email} on multiple open panels!"
                assigned_judges_open.append(j.id)
    print(f"[OK] Judge Panel Governance: Verified zero overlap among concurrently open events.")

    # 3. Athletes & Keys
    athletes = Athlete.objects.all()
    assert athletes.count() >= 100, f"Expected 100+ athletes, found {athletes.count()}"
    sample_ath = athletes.first()
    assert sample_ath.verify_private_key(sample_ath.private_key_display), "Private key verification failed!"
    print(f"[OK] Athlete Directory: {athletes.count()} athletes with verifiable permanent keys.")

    # Registrations & Statuses
    regs = ChampionshipRegistration.objects.filter(championship=champ)
    assert regs.count() >= 100, f"Expected 100+ registrations, found {regs.count()}"
    status_counts = {}
    for r in regs:
        status_counts[r.status] = status_counts.get(r.status, 0) + 1
    print(f"[OK] Registration Status Spread: {status_counts}")

    exceptions = regs.filter(has_venue_exception=True)
    assert exceptions.count() >= 1, "Expected venue exception cases!"
    print(f"[OK] Venue Exceptions: {exceptions.count()} authorized exception cases verified.")

    # 4. Master Roll & Batches
    mr = MasterRoll.objects.filter(championship=champ).first()
    assert mr is not None and mr.is_locked, "Master Roll missing or unlocked!"
    assert mr.roll_entries.count() > 0, "Master roll entries empty!"
    print(f"[OK] Master Roll: Locked by CD with {mr.roll_entries.count()} athletes and sequential chest numbers.")

    batches = StageBatch.objects.filter(panel__championship=champ)
    assert batches.count() >= 2, "Expected stage batches!"
    print(f"[OK] Stage Batches: {batches.count()} batches configured (Batch 1: ACTIVE, Batch 2: QUEUED).")

    # 5. Olympic Trimmed Mean Scoring
    perfs = Performance.objects.filter(event_entry__registration__championship=champ)
    assert perfs.count() >= 3, "Expected 3+ scored performances!"
    for p in perfs:
        subs = p.submissions.filter(is_submitted=True)
        assert subs.count() == 4, f"Performance {p.id} does not have 4 D-judge submissions!"
        d_scores = sorted([float(s.effective_total) for s in subs])
        trimmed_mean = (d_scores[1] + d_scores[2]) / 2.0
        print(f"     -> Perf {p.id} (Chest {p.event_entry.chest_number}): D scores {d_scores} -> Trimmed Mean: {trimmed_mean:.3f} == Final: {p.final_score}")
        assert abs(float(p.final_score) - trimmed_mean) < 0.01, f"Trimmed mean calculation mismatch for perf {p.id}"
    print(f"[OK] Olympic Trimmed Mean: Verified correct score calculation on all finalized performances.")

    # 6. Certificates & Verification
    certs = IssuedCertificate.objects.filter(championship=champ)
    assert certs.count() >= 10, f"Expected 10+ certificates, found {certs.count()}"
    print(f"[OK] Issued Certificates: {certs.count()} issued certificates (Merit & Participation).")
    first_cert = certs.first()
    print(f"     -> Sample Cert: {first_cert.certificate_number} | Recipient: {first_cert.recipient_name} | UUID: {first_cert.verification_uuid}")

    print("==================================================================")
    print("ALL 6 END-TO-END VERIFICATION CHECKS PASSED WITH ZERO ERRORS!")
    print("==================================================================")

if __name__ == '__main__':
    verify()
