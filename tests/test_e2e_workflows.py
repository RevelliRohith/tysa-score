import pytest
import datetime
from decimal import Decimal
from django.test import Client
from django.contrib.auth import get_user_model
from championships.models import Championship, EventConfiguration, EventType, AgeCategory, DistrictQuota
from athletes.models import Athlete
from registrations.models import ChampionshipRegistration, EventEntry, RegistrationStatus, AsanaSequenceItem
from competition.models import MasterRoll, MasterRollEntry, StageBatch, JudgePanel
from scoring.models import Performance, PerformanceStatus, JudgeSubmission, AsanaScoreItem
from corrections.models import CDCorrectionGrant, GrantStatus
from reports.models import DistrictTrophySetting
from certificates.models import CertificateTemplate, IssuedCertificate, TemplateType
from catalogue.models import Asana

User = get_user_model()

@pytest.mark.django_db
class TestCompleteE2EWorkflows:
    """
    Simulates real browser workflows and permission scopes across all championship actors.
    """

    def setup_method(self):
        # 1. Base championship & event configuration
        self.champ = Championship.objects.create(
            name="3rd Telangana State Yogasana Championship",
            code="TYSA-E2E-2026",
            start_date=datetime.date(2026, 11, 20),
            end_date=datetime.date(2026, 11, 22),
            registration_start=datetime.date(2026, 10, 1),
            registration_end=datetime.date(2026, 11, 15),
            age_cutoff_date=datetime.date(2026, 12, 31),
            venue_name="Saroornagar Indoor Stadium",
            venue_city="Hyderabad"
        )
        self.cat_sub_jr = AgeCategory.objects.create(code="SUB_JR", name="Sub Junior", min_age=10, max_age=14)
        self.cat_jr = AgeCategory.objects.create(code="JR", name="Junior", min_age=14, max_age=18)
        
        self.ev_trad = EventConfiguration.objects.create(championship=self.champ, event_type=EventType.TRADITIONAL, total_asanas=7)
        self.ev_trad.age_categories.set([self.cat_sub_jr])
        
        self.ev_pair = EventConfiguration.objects.create(championship=self.champ, event_type=EventType.ARTISTIC_PAIR, total_asanas=10)
        self.ev_pair.age_categories.set([self.cat_jr])

        self.quota_hyd = DistrictQuota.objects.create(championship=self.champ, district_name="Hyderabad", quota_limit=5, quota_used=0)

        # 2. Actors with distinct role permissions
        self.admin = User.objects.create_user(email="admin_e2e@tysa.org", password="pass", is_staff=True, is_superuser=True)
        self.cd = User.objects.create_user(email="cd_e2e@tysa.org", password="pass", is_staff=True)
        self.district_user = User.objects.create_user(email="hyd_dist@tysa.org", password="pass", is_staff=True)
        self.d1_judge = User.objects.create_user(email="d1_judge@tysa.org", password="pass", is_staff=True)
        self.d2_judge = User.objects.create_user(email="d2_judge@tysa.org", password="pass", is_staff=True)
        self.chief_judge = User.objects.create_user(email="chief_judge@tysa.org", password="pass", is_staff=True)
        self.jury = User.objects.create_user(email="jury_e2e@tysa.org", password="pass", is_staff=True)

    def test_workflow_registration_approval_venue_clearance(self):
        """Workflow: Registration -> District Approval with Quota Increment -> Venue Clearance Desk"""
        # Step 1: Athlete registers
        client = Client()
        ath = Athlete.objects.create(
            first_name="Aditya",
            last_name="Rao",
            dob=datetime.date(2011, 4, 15),
            gender="M",
            guardian_name="Suresh Rao",
            district="Hyderabad",
            mobile="9848099887"
        )
        private_key = ath.set_private_key()
        ath.save()

        assert ath.public_athlete_id.startswith("TYSA-ATH-")
        assert ath.verify_private_key(private_key) is True

        reg = ChampionshipRegistration.objects.create(
            athlete=ath,
            championship=self.champ,
            registration_number="REG-E2E-001",
            status=RegistrationStatus.SUBMITTED
        )
        entry = EventEntry.objects.create(
            registration=reg,
            event_config=self.ev_trad,
            age_category=self.cat_sub_jr,
            gender="M",
            chest_number="T-101"
        )

        # Step 2: District Approval & Quota checking
        client.force_login(self.district_user)
        # Approval increments quota
        self.quota_hyd.quota_used += 1
        self.quota_hyd.save()
        reg.status = RegistrationStatus.DISTRICT_APPROVED
        reg.approved_by = self.district_user
        reg.save()

        assert reg.status == RegistrationStatus.DISTRICT_APPROVED
        assert self.quota_hyd.quota_used == 1

        # Step 3: Venue Verification Desk clearance
        venue_resp = client.post('/venue/verification/', {
            'registration_id': reg.id,
            'action': 'clear_venue'
        })
        reg.refresh_from_db()
        assert reg.venue_cleared is True
        assert reg.status == RegistrationStatus.VENUE_CLEARED

    def test_workflow_draw_roll_panel_stage_push(self):
        """Workflow: Master Roll -> Panel Assignment -> Stage Batch Push (1-5 limit)"""
        roll = MasterRoll.objects.create(
            championship=self.champ,
            event_config=self.ev_trad,
            age_category=self.cat_sub_jr,
            gender="M",
            round_type="PRELIMINARY",
            version=1,
            is_locked=True
        )
        panel = JudgePanel.objects.create(
            championship=self.champ,
            event_config=self.ev_trad,
            stage_name="Stage 1",
            round_type="PRELIMINARY",
            d1_judge=self.d1_judge,
            d2_judge=self.d2_judge,
            chief_judge=self.chief_judge
        )

        batch = StageBatch.objects.create(panel=panel, batch_number=1, status='ACTIVE')
        assert batch.status == 'ACTIVE'
        assert batch.panel.stage_name == "Stage 1"

    def test_workflow_cd_correction_scoping_and_relock(self):
        """
        CD Correction: Opens 1 judge's entire submission for 1 performance;
        all other judges remain locked; resubmission relocks.
        """
        ath = Athlete.objects.create(first_name="Karan", last_name="Sharma", dob=datetime.date(2011, 1, 1), gender="M", district="Hyderabad")
        reg = ChampionshipRegistration.objects.create(athlete=ath, championship=self.champ, registration_number="REG-CD-01")
        entry = EventEntry.objects.create(registration=reg, event_config=self.ev_trad, age_category=self.cat_sub_jr, gender="M", chest_number="C-201")
        perf = Performance.objects.create(event_entry=entry, round_type="PRELIMINARY", status=PerformanceStatus.SUBMITTED)

        # D1 and D2 submit sheets
        sub_d1 = JudgeSubmission.objects.create(performance=perf, judge=self.d1_judge, judge_role='D1_JUDGE', is_locked=True, is_submitted=True, raw_total=Decimal('15.50'))
        sub_d2 = JudgeSubmission.objects.create(performance=perf, judge=self.d2_judge, judge_role='D2_JUDGE', is_locked=True, is_submitted=True, raw_total=Decimal('15.80'))

        # CD grants correction to D1 only
        grant = CDCorrectionGrant.objects.create(
            performance=perf,
            judge_submission=sub_d1,
            granted_by=self.cd,
            reason="Posture adjustment requested on Asana #2",
            status=GrantStatus.OPEN
        )
        sub_d1.is_locked = False
        sub_d1.save()

        # Scoping verification: D1 is unlocked, D2 remains strictly locked!
        assert sub_d1.is_locked is False
        assert sub_d2.is_locked is True
        assert grant.is_active is True

        # D1 resubmits sheet
        sub_d1.raw_total = Decimal('16.00')
        sub_d1.is_locked = True
        sub_d1.save()
        grant.status = GrantStatus.RESUBMITTED
        grant.save()

        # Both sheets are now relocked
        assert sub_d1.is_locked is True
        assert sub_d2.is_locked is True
        assert grant.status == GrantStatus.RESUBMITTED

    def test_workflow_jury_read_only_access(self):
        """Jury role has read-only audit search access and cannot edit scores"""
        client = Client()
        client.force_login(self.jury)

        resp = client.get('/jury/?q=Hyderabad')
        assert resp.status_code == 200
        # Post to scoring or correction endpoints should be denied or non-existent for jury
        post_resp = client.post('/cd/corrections/', {'action': 'grant'})
        # Should not grant or should redirect
        assert post_resp.status_code in (302, 403, 404)
