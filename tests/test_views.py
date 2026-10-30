import pytest
from django.urls import reverse
from django.contrib.auth import get_user_model
from championships.models import Championship
from athletes.models import Athlete
from certificates.models import CertificateTemplate

User = get_user_model()

@pytest.mark.django_db
class TestViewEndpoints:

    @pytest.fixture(autouse=True)
    def setup_data(self):
        from django.core.management import call_command
        call_command('seed_dev_data')
        self.admin = User.objects.get(email='admin@tysa.org')
        self.champ = Championship.objects.first()
        self.athlete = Athlete.objects.first()
        self.template = CertificateTemplate.objects.first()

    def test_public_views(self, client):
        """Test public endpoints (login, athlete entry, public live display, second screen, scoreboard)."""
        res = client.get(reverse('login'))
        assert res.status_code == 200

        res = client.get(reverse('athlete_register_step1'))
        assert res.status_code == 200

        res = client.get(reverse('athlete_returning'))
        assert res.status_code == 200

        res = client.get(reverse('universal_judge_register'))
        assert res.status_code == 200

        res = client.get(reverse('public_live_display'))
        assert res.status_code == 200

        res = client.get(reverse('second_screen'))
        assert res.status_code == 200

        res = client.get(reverse('scoreboard_view'))
        assert res.status_code == 200

    def test_authenticated_admin_and_scoring_views(self, client):
        """Test admin dashboard, users, championships, draw, stage, scoring, cd corrections, certificates, reports."""
        client.force_login(self.admin)

        routes = [
            'admin_dashboard',
            'users_and_permissions',
            'registers_view',
            'championship_create',
            'catalogue_view',
            'judge_approval_queue',
            'district_approvals',
            'venue_verification',
            'district_draw',
            'panel_assignments',
            'stage_manager',
            'scoring_traditional_d',
            'scoring_traditional_d_final',
            'scoring_artistic_single_d',
            'scoring_artistic_pair_d',
            'scoring_rhythmic_pair_d',
            'scoring_traditional_time',
            'scoring_music_time',
            'scoring_artistry',
            'scoring_evaluator',
            'scoring_chief_judge',
            'cd_corrections',
            'jury_view',
            'certificate_templates',
            'certificate_generate',
            'report_statistics',
            'report_trophies',
            'report_ceremonies',
            'report_complete',
        ]

        for route_name in routes:
            url = reverse(route_name)
            res = client.get(url)
            assert res.status_code == 200, f"Route {route_name} returned status code {res.status_code}"

        # Parameterized routes
        setup_url = reverse('championship_setup', kwargs={'champ_id': self.champ.id})
        res = client.get(setup_url)
        assert res.status_code == 200

        id_card_url = reverse('athlete_id_card', kwargs={'athlete_id': self.athlete.public_athlete_id})
        res = client.get(id_card_url)
        assert res.status_code == 200

        cert_edit_url = reverse('certificate_template_edit', kwargs={'template_id': self.template.id})
        res = client.get(cert_edit_url)
        assert res.status_code == 200
