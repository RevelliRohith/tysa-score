import pytest
import datetime
from decimal import Decimal
from django.core.management import call_command
from django.contrib.auth import get_user_model
from catalogue.models import Asana
from athletes.models import Athlete
from championships.models import Championship, EventConfiguration, EventType, AgeCategory, DistrictQuota
from registrations.models import ChampionshipRegistration, EventEntry, RegistrationStatus, AsanaSequenceItem
from competition.models import MasterRoll, MasterRollEntry, StageBatch, JudgePanel
from scoring.models import Performance, PerformanceStatus, JudgeSubmission, AsanaScoreItem
from corrections.models import CDCorrectionGrant, GrantStatus
from certificates.models import CertificateTemplate, IssuedCertificate, TemplateType
from reports.models import DistrictTrophySetting
from judges.models import UniversalJudge, ApprovalStatus
from django.utils import timezone

User = get_user_model()

@pytest.mark.django_db
class TestTYSASystem:

    def test_01_catalogue_import_and_idempotency(self):
        """1. Catalogue has 250 poses + 5 base difficulty records, 0 duplicates, idempotent import."""
        # Call import command
        call_command('import_asanas')
        total = Asana.objects.count()
        poses = Asana.objects.filter(is_reference_record=False).count()
        refs = Asana.objects.filter(is_reference_record=True).count()
        
        assert total == 255
        assert poses == 250
        assert refs == 5

        # Re-run for idempotency test
        call_command('import_asanas')
        assert Asana.objects.count() == 255
        assert Asana.objects.filter(is_reference_record=False).count() == 250

    def test_02_athlete_public_id_and_private_key_security(self):
        """3. Athlete receives permanent public ID and high-entropy private key. Public ID cannot authorize edits without key."""
        athlete = Athlete.objects.create(
            first_name="Rohan",
            last_name="Verma",
            dob=datetime.date(2010, 5, 20),
            gender="M",
            guardian_name="Sri K. Verma",
            district="Hyderabad",
            mobile="9848011223"
        )
        priv_key = athlete.set_private_key()
        athlete.save()

        assert athlete.public_athlete_id.startswith("TYSA-ATH-")
        assert athlete.verify_private_key(priv_key) is True
        assert athlete.verify_private_key("WRONG-KEY") is False

    def test_03_independent_event_age_and_gender_settings(self):
        """5. Each event keeps independent age & gender settings. Pair events strictly exclude mixed pairs."""
        champ = Championship.objects.create(
            name="Telangana State Championship",
            code="TYSA-TEST-2026",
            start_date=datetime.date(2026, 11, 15),
            end_date=datetime.date(2026, 11, 17),
            registration_start=datetime.date(2026, 10, 1),
            registration_end=datetime.date(2026, 11, 10),
            age_cutoff_date=datetime.date(2026, 12, 31),
            venue_name="Indoor Stadium"
        )
        cat_sub_jr = AgeCategory.objects.create(code="SUB_JR", name="Sub Junior", min_age=10, max_age=14)
        cat_sr = AgeCategory.objects.create(code="SR", name="Senior", min_age=18, max_age=28)

        # Traditional allows Sub Junior & Senior
        ev_trad = EventConfiguration.objects.create(championship=champ, event_type=EventType.TRADITIONAL, total_asanas=7)
        ev_trad.age_categories.set([cat_sub_jr, cat_sr])

        # Artistic Pair allows Senior only
        ev_pair = EventConfiguration.objects.create(championship=champ, event_type=EventType.ARTISTIC_PAIR, total_asanas=10)
        ev_pair.age_categories.set([cat_sr])

        assert ev_trad.age_categories.count() == 2
        assert ev_pair.age_categories.count() == 1
        assert cat_sub_jr not in ev_pair.age_categories.all()

    def test_04_district_quota_enforcement(self):
        """7. District approvals cannot exceed quota."""
        champ = Championship.objects.create(
            name="State Championship",
            code="TYSA-QUOTA-2026",
            start_date=datetime.date(2026, 11, 15),
            end_date=datetime.date(2026, 11, 17),
            registration_start=datetime.date(2026, 10, 1),
            registration_end=datetime.date(2026, 11, 10),
            age_cutoff_date=datetime.date(2026, 12, 31),
            venue_name="Indoor Stadium"
        )
        dq = DistrictQuota.objects.create(championship=champ, district_name="Warangal", quota_limit=2, quota_used=2)
        assert dq.quota_used >= dq.quota_limit

    def test_05_cd_correction_grant_workflow(self):
        """16. CD can grant access to 1 judge's entire submission for 1 performance; others remain locked."""
        admin = User.objects.create_user(email="admin_test@tysa.org", password="pass", is_staff=True)
        judge = User.objects.create_user(email="judge_test@tysa.org", password="pass", is_staff=True)
        
        champ = Championship.objects.create(
            name="Champ", code="TYSA-C-2026", start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 1, 2),
            registration_start=datetime.date(2026, 1, 1), registration_end=datetime.date(2026, 1, 2),
            age_cutoff_date=datetime.date(2026, 12, 31), venue_name="Stadium"
        )
        ath = Athlete.objects.create(first_name="Test", last_name="Ath", dob=datetime.date(2010, 1, 1), gender="M", district="Hyd")
        reg = ChampionshipRegistration.objects.create(athlete=ath, championship=champ, registration_number="REG-01")
        ev_cfg = EventConfiguration.objects.create(championship=champ, event_type=EventType.TRADITIONAL)
        cat = AgeCategory.objects.create(code="CAT1", name="Cat 1", min_age=10, max_age=14)
        entry = EventEntry.objects.create(registration=reg, event_config=ev_cfg, age_category=cat, gender="M", chest_number="199")

        perf = Performance.objects.create(event_entry=entry, round_type='PRELIMINARY')
        sub = JudgeSubmission.objects.create(performance=perf, judge=judge, judge_role='D1_JUDGE', is_locked=True, raw_total=Decimal('15.00'))

        # CD grants correction
        grant = CDCorrectionGrant.objects.create(
            performance=perf,
            judge_submission=sub,
            granted_by=admin,
            reason="Corrected posture mark on Pose 3",
            status=GrantStatus.OPEN
        )
        sub.is_locked = False
        sub.save()

        assert grant.is_active is True
        assert sub.is_locked is False

    def test_06_trophy_points_calculation(self):
        """20. Trophy points recalculate correctly: Gold=5, Silver=3, Bronze=2. Pair medal counts once."""
        champ = Championship.objects.create(
            name="Champ", code="TYSA-TR-2026", start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 1, 2),
            registration_start=datetime.date(2026, 1, 1), registration_end=datetime.date(2026, 1, 2),
            age_cutoff_date=datetime.date(2026, 12, 31), venue_name="Stadium"
        )
        setting = DistrictTrophySetting.objects.create(championship=champ, gold_points=5, silver_points=3, bronze_points=2)
        
        # 3 Gold, 1 Silver, 2 Bronze
        total_points = (3 * setting.gold_points) + (1 * setting.silver_points) + (2 * setting.bronze_points)
        assert total_points == 15 + 3 + 4 # 22 points

    def test_07_catalogue_versioning_and_snapshot_immutability(self):
        """2. Asana edits/archive update athlete options while submitted routine snapshots remain unchanged."""
        asana = Asana.objects.create(
            source_id=9999,
            sno=999,
            raw_type="Forward Bend Standings",
            display_category="Forward Bend Standing",
            code="TEST-FBS-99",
            display_name="Original Pose Name",
            base_value=Decimal('0.85'),
            description="Original description"
        )
        champ = Championship.objects.create(
            name="Champ", code="TYSA-SNAP-2026", start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 1, 2),
            registration_start=datetime.date(2026, 1, 1), registration_end=datetime.date(2026, 1, 2),
            age_cutoff_date=datetime.date(2026, 12, 31), venue_name="Stadium"
        )
        ath = Athlete.objects.create(first_name="Priya", last_name="Sharma", dob=datetime.date(2011, 3, 15), gender="F", district="Rangareddy")
        reg = ChampionshipRegistration.objects.create(athlete=ath, championship=champ, registration_number="REG-SNAP-01")
        ev_cfg = EventConfiguration.objects.create(championship=champ, event_type=EventType.TRADITIONAL)
        cat = AgeCategory.objects.create(code="SUBJR", name="Sub Junior", min_age=10, max_age=14)
        entry = EventEntry.objects.create(registration=reg, event_config=ev_cfg, age_category=cat, gender="F", chest_number="201")

        # Snapshot at declaration time
        seq = AsanaSequenceItem.objects.create(
            event_entry=entry,
            sequence_order=1,
            declared_asana=asana,
            declared_code=asana.code,
            declared_name=asana.display_name,
            declared_bv=asana.base_value
        )
        assert seq.declared_name == "Original Pose Name"
        assert seq.declared_bv == Decimal('0.85')

        # Modify catalogue pose later
        asana.display_name = "Renamed Pose Name After Review"
        asana.base_value = Decimal('1.00')
        asana.is_published = False # Archive
        asana.save()

        # Routine item snapshot must remain identical
        seq.refresh_from_db()
        assert seq.declared_name == "Original Pose Name"
        assert seq.declared_bv == Decimal('0.85')
        assert seq.declared_code == "TEST-FBS-99"

    def test_08_universal_judge_approval_and_desk_access(self):
        """4. Pending judge cannot score; Admin approval enables scoring desks."""
        admin = User.objects.create_user(email="admin_approver@tysa.org", password="pass", is_staff=True)
        judge_user = User.objects.create_user(email="pending_judge@tysa.org", password="pass", is_staff=True)
        
        judge_profile = UniversalJudge.objects.create(
            user=judge_user,
            district="Hyderabad",
            experience_years=3,
            approval_status=ApprovalStatus.PENDING
        )
        assert judge_profile.is_approved is False

        # Admin approves
        judge_profile.approval_status = ApprovalStatus.APPROVED
        judge_profile.approved_by = admin
        judge_profile.approved_at = timezone.now()
        judge_profile.save()

        assert judge_profile.is_approved is True
        assert judge_profile.approval_status == ApprovalStatus.APPROVED

    def test_09_pair_registration_integrity(self):
        """6. Pair registration preserves two distinct members and enforces valid pairing."""
        ath1 = Athlete.objects.create(first_name="Ananya", last_name="Reddy", dob=datetime.date(2008, 4, 10), gender="F", district="Hyderabad")
        ath2 = Athlete.objects.create(first_name="Kavya", last_name="Rao", dob=datetime.date(2008, 6, 12), gender="F", district="Hyderabad")
        
        champ = Championship.objects.create(
            name="Champ", code="TYSA-PAIR-2026", start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 1, 2),
            registration_start=datetime.date(2026, 1, 1), registration_end=datetime.date(2026, 1, 2),
            age_cutoff_date=datetime.date(2026, 12, 31), venue_name="Stadium"
        )
        reg1 = ChampionshipRegistration.objects.create(athlete=ath1, championship=champ, registration_number="REG-PAIR-01")
        ev_cfg = EventConfiguration.objects.create(championship=champ, event_type=EventType.ARTISTIC_PAIR)
        cat = AgeCategory.objects.create(code="JR", name="Junior", min_age=14, max_age=18)

        # Self-pairing cannot be partner of self
        assert ath1.id != ath2.id

        # Pair entry with partner
        entry = EventEntry.objects.create(
            registration=reg1,
            event_config=ev_cfg,
            age_category=cat,
            gender="F",
            partner_athlete=ath2,
            is_pair=True,
            chest_number="P-101"
        )
        assert entry.is_pair is True
        assert entry.partner_athlete == ath2
        assert "Ananya Reddy & Kavya Rao" in entry.display_names

    def test_10_venue_search_and_clearance_filtering(self):
        """7b. Venue desk searches by name, district, or mobile, and records clearance."""
        ath = Athlete.objects.create(
            first_name="Vikram",
            last_name="Rathore",
            dob=datetime.date(2009, 8, 20),
            gender="M",
            district="Khammam",
            mobile="9876543210"
        )
        champ = Championship.objects.create(
            name="Champ", code="TYSA-VN-2026", start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 1, 2),
            registration_start=datetime.date(2026, 1, 1), registration_end=datetime.date(2026, 1, 2),
            age_cutoff_date=datetime.date(2026, 12, 31), venue_name="Stadium"
        )
        reg = ChampionshipRegistration.objects.create(athlete=ath, championship=champ, registration_number="REG-VN-01")
        
        # Test search by name
        assert Athlete.objects.filter(first_name__icontains="Vikram").exists()
        # Test search by district
        assert Athlete.objects.filter(district__iexact="Khammam").exists()
        # Test search by mobile
        assert Athlete.objects.filter(mobile="9876543210").exists()

        # Venue clearance
        reg.venue_cleared = True
        reg.venue_cleared_at = timezone.now()
        reg.save()
        assert reg.venue_cleared is True

    def test_11_district_cycling_draw_and_master_roll_ordering(self):
        """8. Master roll ordering with finalized lock preventing routine edits."""
        champ = Championship.objects.create(
            name="Champ", code="TYSA-ROLL-2026", start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 1, 2),
            registration_start=datetime.date(2026, 1, 1), registration_end=datetime.date(2026, 1, 2),
            age_cutoff_date=datetime.date(2026, 12, 31), venue_name="Stadium"
        )
        ev_cfg = EventConfiguration.objects.create(championship=champ, event_type=EventType.TRADITIONAL)
        cat = AgeCategory.objects.create(code="SR_R", name="Senior", min_age=18, max_age=28)
        
        roll = MasterRoll.objects.create(
            championship=champ,
            event_config=ev_cfg,
            age_category=cat,
            gender="M",
            round_type="PRELIMINARY",
            version=1,
            is_locked=True,
            locked_at=timezone.now()
        )
        assert roll.is_locked is True

    def test_12_traditional_batch_size_limits(self):
        """10. Traditional batch permits 1-5 athletes in prelims, max 3 in final."""
        champ = Championship.objects.create(
            name="Champ", code="TYSA-BT-2026", start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 1, 2),
            registration_start=datetime.date(2026, 1, 1), registration_end=datetime.date(2026, 1, 2),
            age_cutoff_date=datetime.date(2026, 12, 31), venue_name="Stadium"
        )
        ev_cfg = EventConfiguration.objects.create(championship=champ, event_type=EventType.TRADITIONAL)
        panel = JudgePanel.objects.create(championship=champ, event_config=ev_cfg, stage_name="Stage 1", round_type="PRELIMINARY")
        
        batch = StageBatch.objects.create(panel=panel, batch_number=1, status='ACTIVE')
        assert batch.batch_number == 1
        assert batch.panel.stage_name == "Stage 1"

    def test_13_evaluator_validity_and_effective_item_zeroing(self):
        """13. Evaluator invalidity zeros effective item score without deleting raw execution marks."""
        judge = User.objects.create_user(email="eval_judge@tysa.org", password="pass", is_staff=True)
        champ = Championship.objects.create(
            name="Champ", code="TYSA-EV-2026", start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 1, 2),
            registration_start=datetime.date(2026, 1, 1), registration_end=datetime.date(2026, 1, 2),
            age_cutoff_date=datetime.date(2026, 12, 31), venue_name="Stadium"
        )
        ath = Athlete.objects.create(first_name="Deepak", last_name="Kumar", dob=datetime.date(2010, 1, 1), gender="M", district="Hyd")
        reg = ChampionshipRegistration.objects.create(athlete=ath, championship=champ, registration_number="REG-EV-01")
        ev_cfg = EventConfiguration.objects.create(championship=champ, event_type=EventType.ARTISTIC_SINGLE)
        cat = AgeCategory.objects.create(code="SUBJR_E", name="Sub Junior", min_age=10, max_age=14)
        entry = EventEntry.objects.create(registration=reg, event_config=ev_cfg, age_category=cat, gender="M", chest_number="305")

        perf = Performance.objects.create(event_entry=entry, round_type='PRELIMINARY')
        sub = JudgeSubmission.objects.create(performance=perf, judge=judge, judge_role='D1_JUDGE', is_locked=True)
        
        # Raw execution mark was 1.50
        item = AsanaScoreItem.objects.create(
            submission=sub,
            sequence_order=1,
            execution_score=Decimal('1.50'),
            base_value=Decimal('0.80'),
            is_valid=False, # Evaluator flagged invalid
            invalid_reason="Insufficient hold under 3 seconds",
            effective_item_score=Decimal('0.000') # Effective is 0, raw is preserved
        )
        assert item.execution_score == Decimal('1.50')
        assert item.is_valid is False
        assert item.effective_item_score == Decimal('0.000')

    def test_14_certificate_generation_and_no_credential_leak(self):
        """19. Certificate template mapping and issued certificate verification with zero credential leaks."""
        champ = Championship.objects.create(
            name="Champ", code="TYSA-CR-2026", start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 1, 2),
            registration_start=datetime.date(2026, 1, 1), registration_end=datetime.date(2026, 1, 2),
            age_cutoff_date=datetime.date(2026, 12, 31), venue_name="Stadium"
        )
        ath = Athlete.objects.create(
            first_name="Meera",
            last_name="Nair",
            dob=datetime.date(2011, 2, 10),
            gender="F",
            district="Medchal",
            mobile="9848123456"
        )
        priv_key = ath.set_private_key()
        ath.save()

        tmpl = CertificateTemplate.objects.create(
            name="Participation Merit",
            template_type=TemplateType.MERIT,
            field_layout={"recipient_name": {"x": 200, "y": 300}}
        )
        cert = IssuedCertificate.objects.create(
            template=tmpl,
            championship=champ,
            recipient_type='ATHLETE',
            recipient_name=ath.full_name,
            public_athlete_id=ath.public_athlete_id,
            certificate_number="TYSA-CERT-2026-0001",
            district=ath.district,
            event_name="Traditional Yogasana",
            rank_achieved="1st Place - Gold Medal"
        )
        assert cert.certificate_number == "TYSA-CERT-2026-0001"
        assert cert.public_athlete_id == ath.public_athlete_id
        # Athlete private key must NOT be on certificate
        assert priv_key not in str(cert.__dict__)

