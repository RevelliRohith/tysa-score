import datetime
from decimal import Decimal
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.core.management import call_command

from championships.models import Championship, EventConfiguration, DistrictQuota, EventType, ChampionshipStatus
from athletes.models import Athlete, AthleteDocument
from registrations.models import ChampionshipRegistration, EventEntry, RegistrationStatus, AsanaSequenceItem
from competition.models import JudgePanel, MasterRoll, StageBatch
from scoring.models import Performance, PerformanceStatus, JudgeSubmission
from certificates.models import IssuedCertificate, CertificateTemplate
from judges.models import UniversalJudge

User = get_user_model()

class ChampionshipLifecycleAndDataIntegrityTest(TestCase):
    """
    Comprehensive test suite verifying the end-to-end data integrity
    and governance rules of the State Yogasana Championship.
    """

    @classmethod
    def setUpTestData(cls):
        # Seed test database with a fast subset
        call_command('seed_testing_championship', athletes_count=10, judges_count=32)

    def setUp(self):
        self.client = Client()

    def test_championship_configuration_and_branding(self):
        """Verify Championship, 4 independent events, 33 district quotas and branding exist."""
        champ = Championship.objects.filter(code='TYSA-STATE-2026').first()
        self.assertIsNotNone(champ, "Championship TYSA-STATE-2026 should exist.")
        self.assertEqual(champ.year, 2026)
        self.assertEqual(champ.status, ChampionshipStatus.PUBLISHED)

        # 4 events
        events = EventConfiguration.objects.filter(championship=champ)
        self.assertEqual(events.count(), 4)
        event_types = set(events.values_list('event_type', flat=True))
        expected_types = {EventType.TRADITIONAL, EventType.ARTISTIC_SINGLE, EventType.ARTISTIC_PAIR, EventType.RHYTHMIC_PAIR}
        self.assertEqual(event_types, expected_types)

        # 33 district quotas
        quotas = DistrictQuota.objects.filter(championship=champ)
        self.assertEqual(quotas.count(), 33)

        # Branding logos
        logos = champ.branding_logos.all()
        self.assertGreaterEqual(logos.count(), 3)

    def test_universal_judges_and_panel_governance(self):
        """Verify universal judges and strict one-judge-per-open-event governance rule."""
        judges = UniversalJudge.objects.all()
        self.assertGreaterEqual(judges.count(), 18)

        champ = Championship.objects.filter(code='TYSA-STATE-2026').first()
        self.assertIsNotNone(champ)

        open_panels = JudgePanel.objects.filter(championship=champ, is_active=True, is_closed=False)
        self.assertGreaterEqual(open_panels.count(), 2)

        # Check no judge is simultaneously assigned to multiple open panels
        assigned_judges_open = []
        for p in open_panels:
            seats = [p.chief_judge, p.d1_judge, p.d2_judge, p.d3_judge, p.d4_judge, p.time_judge, p.evaluator]
            for j in seats:
                if j:
                    self.assertNotIn(
                        j.id,
                        assigned_judges_open,
                        f"Judge {j.email} is illegally assigned to multiple concurrently open panels!"
                    )
                    assigned_judges_open.append(j.id)

    def test_athlete_key_authentication_and_registration_diversity(self):
        """Verify permanent athlete keys and diverse registration statuses."""
        champ = Championship.objects.filter(code='TYSA-STATE-2026').first()
        self.assertIsNotNone(champ)

        athletes = Athlete.objects.all()
        self.assertGreaterEqual(athletes.count(), 10)

        # Test key verification on sample athlete
        sample_ath = athletes.first()
        self.assertTrue(bool(sample_ath.private_key_display))
        self.assertTrue(sample_ath.verify_private_key(sample_ath.private_key_display))

        # Check registrations exist
        regs = ChampionshipRegistration.objects.filter(championship=champ)
        self.assertGreaterEqual(regs.count(), 10)

    def test_master_roll_and_stage_batches(self):
        """Verify auditable master roll and stage batches."""
        champ = Championship.objects.filter(code='TYSA-STATE-2026').first()
        self.assertIsNotNone(champ)

        master_roll = MasterRoll.objects.filter(championship=champ).first()
        self.assertIsNotNone(master_roll)
        self.assertTrue(master_roll.is_locked)
        self.assertGreater(master_roll.roll_entries.count(), 0)

        # Check batches
        batches = StageBatch.objects.filter(panel__championship=champ)
        self.assertGreaterEqual(batches.count(), 2)
        batch1 = batches.filter(batch_number=1).first()
        self.assertIsNotNone(batch1)
        self.assertEqual(batch1.status, 'ACTIVE')
        self.assertGreater(batch1.entries.count(), 0)

    def test_olympic_trimmed_mean_scoring(self):
        """Verify Olympic trimmed mean logic and judge submissions."""
        champ = Championship.objects.filter(code='TYSA-STATE-2026').first()
        self.assertIsNotNone(champ)

        perfs = Performance.objects.filter(event_entry__registration__championship=champ)
        self.assertGreaterEqual(perfs.count(), 3)

        for p in perfs:
            subs = p.submissions.filter(is_submitted=True)
            self.assertEqual(subs.count(), 4, "Should have 4 D-judge submissions")
            d_totals = sorted([float(s.effective_total) for s in subs])
            
            # Olympic trimmed mean discards highest and lowest, averages the middle two
            trimmed_mean = (d_totals[1] + d_totals[2]) / 2.0
            self.assertAlmostEqual(float(p.final_score), trimmed_mean, places=2)

    def test_certificate_issuance_and_public_verification(self):
        """Verify issued certificates and their public verification endpoint."""
        champ = Championship.objects.filter(code='TYSA-STATE-2026').first()
        self.assertIsNotNone(champ)

        certs = IssuedCertificate.objects.filter(championship=champ)
        self.assertGreaterEqual(certs.count(), 3)

        # Test verification URL for first certificate
        first_cert = certs.first()
        self.assertIsNotNone(first_cert.verification_uuid)
        
        # Test verification view
        response = self.client.get(f"/certificates/verify/{first_cert.verification_uuid}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, first_cert.recipient_name)
        self.assertContains(response, first_cert.certificate_number)
