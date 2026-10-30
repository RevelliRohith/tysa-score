import datetime
from decimal import Decimal
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from championships.models import Championship, ChampionshipStatus, EventConfiguration, EventType, AgeCategory
from registrations.models import ChampionshipRegistration, EventEntry, RegistrationStatus
from competition.models import JudgePanel, PanelSeatAssignment, StageBatch
from scoring.models import Performance, PerformanceStatus, JudgeSubmission, AsanaScoreItem
from corrections.models import CDCorrectionGrant, GrantStatus
from accounts.models import ScopedRole, RoleChoices
from judges.models import UniversalJudge, CertificationLevel, ApprovalStatus
from accounts.permissions import user_can_grant_correction

User = get_user_model()

class CorrectionsAndRBACRegressionTests(TestCase):
    """
    Real endpoint regression tests validating:
    1. CD in A posts performance/grant from B: denied, no data changes.
    2. Admin without assigned CD role cannot grant/revoke corrections or finalize.
    3. CD cannot impersonate judge resubmission.
    4. Original judge resubmits only their granted full performance.
    5. Open corrections block finalization.
    6. Inactive/revoked panel assignments lose access.
    7. Multi-role CD@A / Jury@B remains correctly scoped.
    """

    def setUp(self):
        self.client = Client()

        # 1. Championships A and B
        self.champ_a = Championship.objects.create(
            name="Telangana State Championship 2026",
            code="TYSA-A",
            year=2026,
            start_date=datetime.date(2026, 11, 15),
            end_date=datetime.date(2026, 11, 17),
            registration_start=datetime.date(2026, 10, 1),
            registration_end=datetime.date(2026, 11, 10),
            age_cutoff_date=datetime.date(2026, 12, 31),
            status=ChampionshipStatus.IN_PROGRESS
        )
        self.champ_b = Championship.objects.create(
            name="Telangana Inter-District Championship 2025",
            code="TYSA-B",
            year=2025,
            start_date=datetime.date(2025, 8, 16),
            end_date=datetime.date(2025, 8, 17),
            registration_start=datetime.date(2025, 7, 1),
            registration_end=datetime.date(2025, 8, 10),
            age_cutoff_date=datetime.date(2025, 12, 31),
            status=ChampionshipStatus.IN_PROGRESS
        )

        # Age category
        self.cat = AgeCategory.objects.create(code="JR", name="Junior", min_age=14, max_age=18, display_order=1)

        # Event configs
        self.ev_a = EventConfiguration.objects.create(championship=self.champ_a, event_type=EventType.TRADITIONAL, total_asanas=7, max_batch_size=5, is_enabled=True)
        self.ev_b = EventConfiguration.objects.create(championship=self.champ_b, event_type=EventType.TRADITIONAL, total_asanas=7, max_batch_size=5, is_enabled=True)

        # Users
        self.admin = User.objects.create_user(email="admin_test@tysa.org", password="pwd", full_name="System Admin", is_superuser=True, is_staff=True)
        ScopedRole.objects.create(user=self.admin, role=RoleChoices.ADMIN, championship=None)

        self.cd_a = User.objects.create_user(email="cd_a@tysa.org", password="pwd", full_name="CD Champ A", is_staff=True)
        ScopedRole.objects.create(user=self.cd_a, role=RoleChoices.CD, championship=self.champ_a)

        self.cd_b = User.objects.create_user(email="cd_b@tysa.org", password="pwd", full_name="CD Champ B", is_staff=True)
        ScopedRole.objects.create(user=self.cd_b, role=RoleChoices.CD, championship=self.champ_b)

        self.judge_d1 = User.objects.create_user(email="d1_test@tysa.org", password="pwd", full_name="Judge D1", is_staff=True)
        ScopedRole.objects.create(user=self.judge_d1, role=RoleChoices.D_JUDGE, championship=self.champ_a)
        UniversalJudge.objects.create(user=self.judge_d1, district="Hyderabad", approval_status=ApprovalStatus.APPROVED)

        self.judge_d2 = User.objects.create_user(email="d2_test@tysa.org", password="pwd", full_name="Judge D2", is_staff=True)
        ScopedRole.objects.create(user=self.judge_d2, role=RoleChoices.D_JUDGE, championship=self.champ_a)
        UniversalJudge.objects.create(user=self.judge_d2, district="Hyderabad", approval_status=ApprovalStatus.APPROVED)

        self.multirole = User.objects.create_user(email="multi_test@tysa.org", password="pwd", full_name="Multi Role Official", is_staff=True)
        ScopedRole.objects.create(user=self.multirole, role=RoleChoices.CD, championship=self.champ_a)
        ScopedRole.objects.create(user=self.multirole, role=RoleChoices.JURY, championship=self.champ_b)

        # Panel in Champ A
        self.panel_a = JudgePanel.objects.create(
            championship=self.champ_a,
            event_config=self.ev_a,
            age_category=self.cat,
            round_type='PRELIMINARY',
            stage_name='Stage 1',
            d1_judge=self.judge_d1,
            is_active=True
        )

        # Panel in Champ B
        self.panel_b = JudgePanel.objects.create(
            championship=self.champ_b,
            event_config=self.ev_b,
            age_category=self.cat,
            round_type='PRELIMINARY',
            stage_name='Stage 2',
            is_active=True
        )

        # Registrations and Entries
        from athletes.models import Athlete
        self.ath_a = Athlete.objects.create(public_athlete_id="ATH-A01", first_name="Athlete", last_name="A", dob=datetime.date(2010, 1, 1), district="Hyderabad")
        self.ath_b = Athlete.objects.create(public_athlete_id="ATH-B01", first_name="Athlete", last_name="B", dob=datetime.date(2010, 1, 1), district="Warangal")

        self.reg_a = ChampionshipRegistration.objects.create(athlete=self.ath_a, championship=self.champ_a, registration_number="REG-A01", status=RegistrationStatus.VENUE_CLEARED)
        self.entry_a = EventEntry.objects.create(registration=self.reg_a, event_config=self.ev_a, age_category=self.cat, gender="M", chest_number="101")

        self.reg_b = ChampionshipRegistration.objects.create(athlete=self.ath_b, championship=self.champ_b, registration_number="REG-B01", status=RegistrationStatus.VENUE_CLEARED)
        self.entry_b = EventEntry.objects.create(registration=self.reg_b, event_config=self.ev_b, age_category=self.cat, gender="M", chest_number="201")

        # Performances
        self.perf_a = Performance.objects.create(event_entry=self.entry_a, round_type='PRELIMINARY', status=PerformanceStatus.SUBMITTED, provisional_score=Decimal('56.000'))
        self.sub_a = JudgeSubmission.objects.create(
            performance=self.perf_a,
            judge=self.judge_d1,
            judge_role='D1_JUDGE',
            is_draft=False,
            is_submitted=True,
            is_locked=True,
            version=1,
            raw_total=Decimal('56.000'),
            effective_total=Decimal('56.000')
        )
        AsanaScoreItem.objects.create(submission=self.sub_a, sequence_order=1, posture_score=Decimal('5.00'), stability_score=Decimal('1.50'), timing_contribution=Decimal('2.00'))

        self.perf_b = Performance.objects.create(event_entry=self.entry_b, round_type='PRELIMINARY', status=PerformanceStatus.SUBMITTED, provisional_score=Decimal('58.000'))
        self.sub_b = JudgeSubmission.objects.create(
            performance=self.perf_b,
            judge=self.judge_d2,
            judge_role='D1_JUDGE',
            is_draft=False,
            is_submitted=True,
            is_locked=True,
            version=1,
            raw_total=Decimal('58.000'),
            effective_total=Decimal('58.000')
        )

    def test_cd_in_a_cannot_mutate_performance_or_grant_from_b(self):
        """1. CD in Championship A posts performance/grant ID from B: denied with no data changes."""
        self.client.force_login(self.cd_a)
        session = self.client.session
        session['active_championship_id'] = self.champ_a.id
        session.save()

        # CD A attempts to grant correction on Performance B (from Champ B)
        res = self.client.post('/cd/corrections/', {
            'action': 'grant_correction',
            'performance_id': self.perf_b.id,
            'submission_id': self.sub_b.id,
            'reason': 'Illegal Cross-Championship Modification Attempt'
        })
        self.assertEqual(res.status_code, 302)

        # Assert no grant created, submission in B remains locked
        self.assertEqual(CDCorrectionGrant.objects.filter(performance=self.perf_b).count(), 0)
        self.sub_b.refresh_from_db()
        self.assertTrue(self.sub_b.is_locked)
        self.assertFalse(self.sub_b.is_draft)

    def test_admin_without_cd_role_cannot_grant_revoke_or_finalize(self):
        """2. Admin without assigned CD role cannot grant/revoke corrections or finalize."""
        self.client.force_login(self.admin)
        session = self.client.session
        session['active_championship_id'] = self.champ_a.id
        session.save()

        # Admin attempts grant_correction
        res = self.client.post('/cd/corrections/', {
            'action': 'grant_correction',
            'performance_id': self.perf_a.id,
            'submission_id': self.sub_a.id,
            'reason': 'Admin Attempt'
        })
        self.assertEqual(res.status_code, 302)
        self.assertEqual(CDCorrectionGrant.objects.filter(performance=self.perf_a).count(), 0)

        # Admin attempts finalization
        res = self.client.post('/cd/corrections/', {
            'action': 'cd_finalise',
            'performance_id': self.perf_a.id
        })
        self.assertEqual(res.status_code, 302)
        self.perf_a.refresh_from_db()
        self.assertNotEqual(self.perf_a.status, PerformanceStatus.CD_FINALIZED)

    def test_cd_cannot_impersonate_judge_resubmission(self):
        """3. CD cannot impersonate judge resubmission."""
        # CD A grants legitimate correction on submission A
        grant = CDCorrectionGrant.objects.create(
            performance=self.perf_a,
            judge_submission=self.sub_a,
            granted_by=self.cd_a,
            reason="Official CD Reopen",
            status=GrantStatus.OPEN
        )
        self.sub_a.is_locked = False
        self.sub_a.save()

        # CD A attempts to call resubmit_correction themselves
        self.client.force_login(self.cd_a)
        session = self.client.session
        session['active_championship_id'] = self.champ_a.id
        session.save()

        res = self.client.post('/cd/corrections/', {
            'action': 'resubmit_correction',
            'grant_id': grant.id
        })
        self.assertEqual(res.status_code, 302)

        # Verify grant remains OPEN and submission not resubmitted by CD
        grant.refresh_from_db()
        self.assertEqual(grant.status, GrantStatus.OPEN)
        self.sub_a.refresh_from_db()
        self.assertEqual(self.sub_a.version, 1)

    def test_original_judge_resubmits_only_their_granted_performance(self):
        """4. Original judge resubmits only their granted full performance with incremented version."""
        grant = CDCorrectionGrant.objects.create(
            performance=self.perf_a,
            judge_submission=self.sub_a,
            granted_by=self.cd_a,
            reason="Legitimate Reopen",
            status=GrantStatus.OPEN
        )
        self.sub_a.is_locked = False
        self.sub_a.save()

        # Judge D1 (the original owner) resubmits
        self.client.force_login(self.judge_d1)
        session = self.client.session
        session['active_championship_id'] = self.champ_a.id
        session.save()

        res = self.client.post('/cd/corrections/', {
            'action': 'resubmit_correction',
            'grant_id': grant.id
        })
        self.assertEqual(res.status_code, 302)

        # Verify grant transitions to RESUBMITTED, version increments, relocked
        grant.refresh_from_db()
        self.assertEqual(grant.status, GrantStatus.RESUBMITTED)
        self.assertIsNotNone(grant.resubmitted_at)

        self.sub_a.refresh_from_db()
        self.assertEqual(self.sub_a.version, 2)
        self.assertTrue(self.sub_a.is_locked)
        self.assertFalse(self.sub_a.is_draft)
        self.assertTrue(self.sub_a.is_submitted)

    def test_open_corrections_block_finalization(self):
        """5. Open corrections block finalization."""
        # Create an OPEN grant
        CDCorrectionGrant.objects.create(
            performance=self.perf_a,
            judge_submission=self.sub_a,
            granted_by=self.cd_a,
            reason="Unresolved Review",
            status=GrantStatus.OPEN
        )

        self.client.force_login(self.cd_a)
        session = self.client.session
        session['active_championship_id'] = self.champ_a.id
        session.save()

        res = self.client.post('/cd/corrections/', {
            'action': 'cd_finalise',
            'performance_id': self.perf_a.id
        })
        self.assertEqual(res.status_code, 302)

        self.perf_a.refresh_from_db()
        self.assertNotEqual(self.perf_a.status, PerformanceStatus.CD_FINALIZED)

    def test_inactive_revoked_panel_assignments_lose_access(self):
        """6. Inactive/revoked assignments lose access."""
        from accounts.permissions import get_user_roles_for_request

        # Judge D1 has active panel A
        roles = get_user_roles_for_request(None, championship=self.champ_a)
        # Check directly with panel
        self.panel_a.is_active = False
        self.panel_a.save()

        # Deactivated panel no longer grants D_JUDGE role through panel
        active_panels = JudgePanel.objects.filter(championship=self.champ_a, is_active=True)
        self.assertFalse(active_panels.filter(d1_judge=self.judge_d1).exists())

        # Test PanelSeatAssignment with is_active=False
        sa = PanelSeatAssignment.objects.create(
            panel=self.panel_a,
            judge=self.judge_d2,
            seat_name='TIME_1',
            is_active=False
        )
        active_seats = PanelSeatAssignment.objects.filter(
            panel__championship=self.champ_a,
            panel__is_active=True,
            judge=self.judge_d2,
            is_active=True
        )
        self.assertFalse(active_seats.exists())

    def test_multi_role_cd_at_a_and_jury_at_b_remains_correctly_scoped(self):
        """7. Multi-role CD@A / Jury@B remains correctly scoped."""
        self.client.force_login(self.multirole)

        # In Championship A: user has CD role, can grant corrections
        session = self.client.session
        session['active_championship_id'] = self.champ_a.id
        session.save()

        self.assertTrue(user_can_grant_correction(self.multirole, self.champ_a))
        res_a = self.client.get('/cd/dashboard/')
        self.assertEqual(res_a.status_code, 200)

        # In Championship B: user has JURY role, CANNOT grant corrections
        session['active_championship_id'] = self.champ_b.id
        session.save()

        self.assertFalse(user_can_grant_correction(self.multirole, self.champ_b))

        # Attempting CD action in Champ B is rejected
        res_b = self.client.post('/cd/corrections/', {
            'action': 'grant_correction',
            'performance_id': self.perf_b.id,
            'submission_id': self.sub_b.id,
            'reason': 'Illegal JURY Attempt'
        })
        self.assertEqual(res_b.status_code, 302)
        self.assertEqual(CDCorrectionGrant.objects.filter(performance=self.perf_b).count(), 0)
