import pytest
import datetime
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from championships.models import Championship, ChampionshipStatus, EventConfiguration, AgeCategory, EventType
from competition.models import JudgePanel, PanelSeatAssignment
from judges.models import UniversalJudge, ApprovalStatus
from accounts.models import RoleChoices, ScopedRole

User = get_user_model()

class JudgePanelExclusiveAssignmentTest(TestCase):
    def setUp(self):
        self.client = Client()
        
        # Create CD user
        self.cd_user = User.objects.create_user(
            email='cd_director@tysa.org',
            password='Password123!',
            full_name='Competition Director User'
        )
        
        # Create Championship
        self.championship = Championship.objects.create(
            name='National Yogasana Championship 2026',
            code='NYC-2026',
            year=2026,
            start_date=datetime.date(2026, 11, 1),
            end_date=datetime.date(2026, 11, 5),
            registration_start=datetime.date(2026, 10, 1),
            registration_end=datetime.date(2026, 10, 25),
            age_cutoff_date=datetime.date(2026, 12, 31),
            status=ChampionshipStatus.IN_PROGRESS
        )
        
        # Assign CD ScopedRole
        ScopedRole.objects.create(
            user=self.cd_user,
            championship=self.championship,
            role=RoleChoices.CD
        )
        
        # Create EventConfigurations
        self.event_trad = EventConfiguration.objects.create(
            championship=self.championship,
            event_type=EventType.TRADITIONAL,
            is_enabled=True
        )
        self.event_art = EventConfiguration.objects.create(
            championship=self.championship,
            event_type=EventType.ARTISTIC_SINGLE,
            is_enabled=True
        )
        
        # Age categories
        self.cat_subjr = AgeCategory.objects.create(
            name='Sub-Junior',
            code='SUB_JR_TEST',
            min_age=9,
            max_age=14,
            display_order=1
        )
        self.cat_jr = AgeCategory.objects.create(
            name='Junior',
            code='JR_TEST',
            min_age=15,
            max_age=18,
            display_order=2
        )
        self.event_trad.age_categories.add(self.cat_subjr, self.cat_jr)
        self.event_art.age_categories.add(self.cat_subjr, self.cat_jr)
        
        # Create 3 Approved Judges
        self.judge1_user = User.objects.create_user(email='judge1@tysa.org', password='pwd', full_name='Judge Alpha')
        self.judge2_user = User.objects.create_user(email='judge2@tysa.org', password='pwd', full_name='Judge Beta')
        self.judge3_user = User.objects.create_user(email='judge3@tysa.org', password='pwd', full_name='Judge Gamma')
        
        UniversalJudge.objects.create(user=self.judge1_user, approval_status=ApprovalStatus.APPROVED, district='Hyderabad')
        UniversalJudge.objects.create(user=self.judge2_user, approval_status=ApprovalStatus.APPROVED, district='Warangal')
        UniversalJudge.objects.create(user=self.judge3_user, approval_status=ApprovalStatus.APPROVED, district='Nizamabad')

        # Log in CD
        self.client.force_login(self.cd_user)
        session = self.client.session
        session['active_championship_id'] = self.championship.id
        session['active_role'] = RoleChoices.CD
        session.save()

    def test_assign_judge_to_event_and_prevent_assignment_to_other_open_event(self):
        # Panel 1: Traditional - Sub-Junior - Male - Preliminary
        panel1 = JudgePanel.objects.create(
            championship=self.championship,
            event_config=self.event_trad,
            age_category=self.cat_subjr,
            gender='M',
            round_type='PRELIMINARY',
            stage_name='Stage 1',
            is_active=True,
            is_closed=False
        )

        # Panel 2: Traditional - Junior - Male - Preliminary
        panel2 = JudgePanel.objects.create(
            championship=self.championship,
            event_config=self.event_trad,
            age_category=self.cat_jr,
            gender='M',
            round_type='PRELIMINARY',
            stage_name='Stage 2',
            is_active=True,
            is_closed=False
        )

        # 1. Assign Judge Alpha (id: self.judge1_user.id) to Panel 1 as D1
        post_data1 = {
            'panel_id': panel1.id,
            'action': 'save_panel',
            'd1_judge': self.judge1_user.id,
            'chief_judge': self.judge2_user.id,
        }
        res1 = self.client.post('/competition/panels/', post_data1)
        self.assertEqual(res1.status_code, 302)
        panel1.refresh_from_db()
        self.assertEqual(panel1.d1_judge_id, self.judge1_user.id)
        self.assertEqual(panel1.chief_judge_id, self.judge2_user.id)

        # 2. Try to assign Judge Alpha to Panel 2 while Panel 1 is still OPEN
        post_data2 = {
            'panel_id': panel2.id,
            'action': 'save_panel',
            'd1_judge': self.judge1_user.id, # Conflict!
            'chief_judge': self.judge3_user.id,
        }
        res2 = self.client.post('/competition/panels/', post_data2, follow=True)
        panel2.refresh_from_db()
        # Panel 2 D1 must NOT be saved as Judge Alpha
        self.assertIsNone(panel2.d1_judge_id)
        # Verify conflict message in response messages
        messages = list(res2.context['messages'])
        conflict_msg = any('already assigned to active event' in str(m) for m in messages)
        self.assertTrue(conflict_msg, "Should produce conflict message for already assigned judge in open event")

    def test_intra_panel_duplicate_prevention(self):
        panel = JudgePanel.objects.create(
            championship=self.championship,
            event_config=self.event_trad,
            age_category=self.cat_subjr,
            gender='M',
            round_type='PRELIMINARY',
            stage_name='Stage 1',
            is_active=True,
            is_closed=False
        )
        # Attempt to assign Judge Alpha as both D1 and D2 in the same panel
        post_data = {
            'panel_id': panel.id,
            'action': 'save_panel',
            'd1_judge': self.judge1_user.id,
            'd2_judge': self.judge1_user.id, # Intra-panel duplicate!
        }
        res = self.client.post('/competition/panels/', post_data, follow=True)
        panel.refresh_from_db()
        self.assertIsNone(panel.d1_judge_id)
        self.assertIsNone(panel.d2_judge_id)
        messages = list(res.context['messages'])
        dup_msg = any('cannot be assigned to both' in str(m) for m in messages)
        self.assertTrue(dup_msg, "Should produce intra-panel duplicate error")

    def test_event_closure_frees_judge_for_next_event(self):
        panel1 = JudgePanel.objects.create(
            championship=self.championship,
            event_config=self.event_trad,
            age_category=self.cat_subjr,
            gender='M',
            round_type='PRELIMINARY',
            d1_judge=self.judge1_user,
            is_active=True,
            is_closed=False
        )
        panel2 = JudgePanel.objects.create(
            championship=self.championship,
            event_config=self.event_trad,
            age_category=self.cat_jr,
            gender='M',
            round_type='PRELIMINARY',
            is_active=True,
            is_closed=False
        )

        # Close Panel 1
        res_close = self.client.post('/competition/panels/', {
            'panel_id': panel1.id,
            'action': 'close_event'
        })
        self.assertEqual(res_close.status_code, 302)
        panel1.refresh_from_db()
        self.assertTrue(panel1.is_closed)
        self.assertIsNotNone(panel1.closed_at)

        # Now assign Judge Alpha to Panel 2 - should SUCCEED because Panel 1 is closed!
        post_data2 = {
            'panel_id': panel2.id,
            'action': 'save_panel',
            'd1_judge': self.judge1_user.id,
        }
        res_assign = self.client.post('/competition/panels/', post_data2)
        self.assertEqual(res_assign.status_code, 302)
        panel2.refresh_from_db()
        self.assertEqual(panel2.d1_judge_id, self.judge1_user.id)
