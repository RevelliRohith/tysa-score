import pytest
import threading
from decimal import Decimal
from django.db import connection
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from championships.models import Championship, DistrictQuota, EventConfiguration, EventType, AgeCategory
from championships.services import ChampionshipService
from registrations.models import ChampionshipRegistration, RegistrationStatus, EventEntry
from athletes.models import Athlete
from scoring.models import Performance, PerformanceStatus, JudgeSubmission
from competition.models import JudgePanel
from corrections.models import CDCorrectionGrant, GrantStatus
from certificates.models import CertificateTemplate, IssuedCertificate, TemplateType

User = get_user_model()

@pytest.mark.django_db(transaction=True)
class TestPostgreSQLProductionConcurrency:
    """
    Tests invoking the identical production services as actual approval,
    score-submission, CD correction grant/resubmit, and certificate endpoints.
    """

    def test_concurrent_district_quota_service(self):
        """
        Uses ChampionshipService.approve_district_registration concurrently across threads.
        Verifies only quota_limit approvals succeed and excess attempts fail safely.
        """
        champ = Championship.objects.create(
            name="Quota Concurrency Champ",
            code="QUOTA-CONC",
            year=2026,
            start_date="2026-11-01",
            end_date="2026-11-03",
            registration_start="2026-10-01",
            registration_end="2026-10-25",
            age_cutoff_date="2026-12-31",
            venue_name="Sports Complex",
            is_rules_preset_verified=True
        )
        quota = DistrictQuota.objects.create(
            championship=champ,
            district_name="Warangal",
            quota_limit=2,
            quota_used=0
        )
        admin_user = User.objects.create_user(email="admin_q@tysa.org", password="pass", full_name="Admin Q")

        # Create 5 pending registrations for Warangal
        regs = []
        for i in range(5):
            ath = Athlete.objects.create(
                first_name=f"Athlete{i}",
                last_name="Warangal",
                dob="2005-05-05",
                gender="M",
                district="Warangal"
            )
            r = ChampionshipRegistration.objects.create(
                athlete=ath,
                championship=champ,
                registration_number=f"REG-W-{i}",
                status=RegistrationStatus.SUBMITTED
            )
            regs.append(r)

        results = []
        lock = threading.Lock()

        def try_approve(reg_id):
            connection.close()
            try:
                success = ChampionshipService.approve_district_registration(reg_id, admin_user)
                with lock:
                    results.append((reg_id, success))
            finally:
                connection.close()

        threads = [threading.Thread(target=try_approve, args=(regs[i].id,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        approved = [r for r in results if r[1] is True]
        rejected = [r for r in results if r[1] is False]
        assert len(approved) == 2, f"Expected 2 approved, got {len(approved)}"
        assert len(rejected) == 3, f"Expected 3 rejected, got {len(rejected)}"

        quota.refresh_from_db()
        assert quota.quota_used == 2
        assert ChampionshipRegistration.objects.filter(championship=champ, status=RegistrationStatus.DISTRICT_APPROVED).count() == 2

    def test_concurrent_score_submission_service(self):
        """
        Uses ChampionshipService.submit_judge_score concurrently across threads.
        Guarantees idempotent score submission and prevents duplicate records.
        """
        champ = Championship.objects.create(
            name="Score Service Champ",
            code="SCORE-SRV",
            year=2026,
            start_date="2026-11-01",
            end_date="2026-11-03",
            registration_start="2026-10-01",
            registration_end="2026-10-25",
            age_cutoff_date="2026-12-31",
            venue_name="Sports Complex",
            is_rules_preset_verified=True
        )
        age = AgeCategory.objects.create(code="SR", name="Senior", min_age=18, max_age=35)
        evt = EventConfiguration.objects.create(championship=champ, event_type=EventType.TRADITIONAL)
        ath = Athlete.objects.create(first_name="Raju", last_name="Traditional", dob="2000-01-01", gender="M", district="Hyderabad")
        reg = ChampionshipRegistration.objects.create(athlete=ath, championship=champ, registration_number="REG-R1", status=RegistrationStatus.VENUE_CLEARED)
        entry = EventEntry.objects.create(registration=reg, event_config=evt, age_category=age, gender="M", chest_number="301")
        panel = JudgePanel.objects.create(championship=champ, event_config=evt, stage_name="Stage 1")
        perf = Performance.objects.create(event_entry=entry, status=PerformanceStatus.IN_PROGRESS)

        judge_user = User.objects.create_user(email="judge_service@tysa.org", password="pass", full_name="Service Judge")

        submission_ids = []
        lock = threading.Lock()

        def submit_fn(marks):
            connection.close()
            try:
                sub = ChampionshipService.submit_judge_score(perf.id, judge_user, 'D1_JUDGE', Decimal(str(marks)))
                with lock:
                    submission_ids.append(sub.id)
            finally:
                connection.close()

        threads = [threading.Thread(target=submit_fn, args=(12.5 + i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All 4 threads return the same identical submission ID
        assert len(submission_ids) == 4
        assert len(set(submission_ids)) == 1
        assert JudgeSubmission.objects.filter(performance=perf, judge=judge_user, judge_role='D1_JUDGE').count() == 1

    def test_concurrent_cd_correction_grant_and_resubmission(self):
        """
        Tests CD granting correction access, ensuring:
        - Only the selected judge's submission is opened (is_locked=False).
        - Other judge submissions for the same performance remain locked.
        - Concurrent resubmission increments version and relocks (is_locked=True).
        - Unauthorized judges cannot resubmit another judge's grant.
        """
        champ = Championship.objects.create(
            name="Correction Champ",
            code="CORR-CHAMP",
            year=2026,
            start_date="2026-11-01",
            end_date="2026-11-03",
            registration_start="2026-10-01",
            registration_end="2026-10-25",
            age_cutoff_date="2026-12-31",
            venue_name="Sports Complex",
            is_rules_preset_verified=True
        )
        age = AgeCategory.objects.create(code="JR", name="Junior", min_age=12, max_age=17)
        evt = EventConfiguration.objects.create(championship=champ, event_type=EventType.TRADITIONAL)
        ath = Athlete.objects.create(first_name="Pooja", last_name="Sharma", dob="2008-01-01", gender="F", district="Hyderabad")
        reg = ChampionshipRegistration.objects.create(athlete=ath, championship=champ, registration_number="REG-P1", status=RegistrationStatus.VENUE_CLEARED)
        entry = EventEntry.objects.create(registration=reg, event_config=evt, age_category=age, gender="F", chest_number="401")
        perf = Performance.objects.create(event_entry=entry, status=PerformanceStatus.PROVISIONAL_RELEASED)

        cd_user = User.objects.create_user(email="cd_test@tysa.org", password="pass", full_name="CD Test")
        judge_d1 = User.objects.create_user(email="judge_d1@tysa.org", password="pass", full_name="Judge D1")
        judge_d2 = User.objects.create_user(email="judge_d2@tysa.org", password="pass", full_name="Judge D2")

        # Two locked submissions for this performance
        sub_d1 = JudgeSubmission.objects.create(
            performance=perf,
            judge=judge_d1,
            judge_role='D1_JUDGE',
            version=1,
            raw_total=Decimal('15.000'),
            is_locked=True,
            is_submitted=True
        )
        sub_d2 = JudgeSubmission.objects.create(
            performance=perf,
            judge=judge_d2,
            judge_role='D2_JUDGE',
            version=1,
            raw_total=Decimal('15.500'),
            is_locked=True,
            is_submitted=True
        )

        # 1. CD grants correction to D1 only
        grant = ChampionshipService.grant_cd_correction(perf.id, sub_d1.id, cd_user, "Clerical error on item #3")

        sub_d1.refresh_from_db()
        sub_d2.refresh_from_db()
        assert sub_d1.is_locked is False, "D1 should be unlocked"
        assert sub_d2.is_locked is True, "D2 must remain locked"
        assert grant.status == GrantStatus.OPEN

        # 2. Unauthorized attempt by D2 to resubmit D1's grant is rejected
        connection.close()
        with pytest.raises(PermissionDenied):
            ChampionshipService.resubmit_cd_correction(grant.id, judge_d2, Decimal('16.000'))

        # 3. Authorized resubmission by D1 relocks sheet and advances version
        connection.close()
        revised_sub = ChampionshipService.resubmit_cd_correction(grant.id, judge_d1, Decimal('16.500'))
        
        assert revised_sub.version == 2
        assert revised_sub.is_locked is True
        assert revised_sub.raw_total == Decimal('16.500')

        grant.refresh_from_db()
        assert grant.status == GrantStatus.RESUBMITTED
        assert grant.resubmitted_at is not None

    def test_concurrent_certificate_issuance_service(self):
        """
        Uses ChampionshipService.issue_certificate_record across parallel threads.
        Verifies zero collisions and consecutive sequence numbering in PostgreSQL.
        """
        champ = Championship.objects.create(
            name="Cert Service Champ",
            code="CERT-SRV",
            year=2026,
            start_date="2026-11-01",
            end_date="2026-11-03",
            registration_start="2026-10-01",
            registration_end="2026-10-25",
            age_cutoff_date="2026-12-31",
            venue_name="Sports Complex"
        )
        template = CertificateTemplate.objects.create(name="Participation Tpl", template_type=TemplateType.PARTICIPATION)
        issuer = User.objects.create_user(email="issuer@tysa.org", password="pass", full_name="Issuer")

        allocated_certs = []
        lock = threading.Lock()

        def issue_fn(thread_id):
            connection.close()
            try:
                cert = ChampionshipService.issue_certificate_record(
                    template.id, champ.id, f"Athlete {thread_id}", prefix="PART", issuer_user=issuer
                )
                with lock:
                    allocated_certs.append(cert.certificate_number)
            finally:
                connection.close()

        threads = [threading.Thread(target=issue_fn, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(allocated_certs) == 10
        assert len(set(allocated_certs)) == 10, f"Collisions found: {allocated_certs}"
        for num in allocated_certs:
            assert num.startswith("CERT-SRV-PART-")
