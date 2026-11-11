import datetime
import pytest
from decimal import Decimal
from django.utils import timezone
from django.contrib.auth import get_user_model
from competition.models import JudgePanel, StageBatch, MasterRoll, MasterRollEntry
from championships.models import Championship, EventConfiguration, AgeCategory, EventType
from athletes.models import Athlete
from registrations.models import ChampionshipRegistration, EventEntry, RegistrationStatus
from accounts.models import ScopedRole, RoleChoices
from scoring.models import (
    Performance, PerformanceStatus, JudgeSubmission, AsanaScoreItem,
    PerformanceInterruptionLog, InterruptionAction, InterruptionReason
)

User = get_user_model()

@pytest.mark.django_db
def test_stage_manager_interruption_controls(client):
    # Setup test users
    sm_user = User.objects.create_user(email='sm_test@tysa.org', password='password123', full_name='Stage Manager SM')
    cj_user = User.objects.create_user(email='cj_test@tysa.org', password='password123', full_name='Chief Judge CJ')
    other_sm = User.objects.create_user(email='other_sm@tysa.org', password='password123', full_name='Other Stage Manager')
    d1_user = User.objects.create_user(email='d1@tysa.org', password='password123', full_name='D1 Judge')

    champ = Championship.objects.create(
        name='Test Champ 2026',
        code='TYSA-TEST-2026',
        start_date=datetime.date(2026, 11, 1),
        end_date=datetime.date(2026, 11, 5),
        registration_start=datetime.date(2026, 10, 1),
        registration_end=datetime.date(2026, 10, 25),
        age_cutoff_date=datetime.date(2026, 12, 31),
        venue_name='Test Stadium'
    )
    ev_config = EventConfiguration.objects.create(championship=champ, event_type=EventType.ARTISTIC_SINGLE)
    cat = AgeCategory.objects.create(code='SUB_JR', name='Sub Junior (10-14)', min_age=10, max_age=14)

    # Assign ScopedRoles
    ScopedRole.objects.create(user=sm_user, championship=champ, role=RoleChoices.STAGE_MANAGER)
    ScopedRole.objects.create(user=other_sm, championship=champ, role=RoleChoices.STAGE_MANAGER)
    ScopedRole.objects.create(user=cj_user, championship=champ, role=RoleChoices.CHIEF_JUDGE)
    ScopedRole.objects.create(user=d1_user, championship=champ, role=RoleChoices.D_JUDGE)

    panel = JudgePanel.objects.create(
        championship=champ,
        event_config=ev_config,
        age_category=cat,
        gender='M',
        round_type='PRELIMINARY',
        stage_name='Arena Test 1',
        stage_manager=sm_user,
        chief_judge=cj_user,
        d1_judge=d1_user,
        is_active=True
    )

    other_panel = JudgePanel.objects.create(
        championship=champ,
        event_config=ev_config,
        age_category=cat,
        gender='M',
        round_type='PRELIMINARY',
        stage_name='Arena Test 2',
        stage_manager=other_sm,
        is_active=True
    )

    ath1 = Athlete.objects.create(first_name='Athlete', last_name='One', dob=datetime.date(2014, 1, 1), gender='M', district='Hyderabad')
    reg1 = ChampionshipRegistration.objects.create(
        championship=champ,
        athlete=ath1,
        registration_number='REG-TEST-001',
        venue_cleared=True,
        status=RegistrationStatus.VENUE_CLEARED
    )
    entry1 = EventEntry.objects.create(registration=reg1, event_config=ev_config, age_category=cat, gender='M', chest_number='CH-901')

    ath2 = Athlete.objects.create(first_name='Athlete', last_name='Two', dob=datetime.date(2014, 1, 1), gender='M', district='Warangal')
    reg2 = ChampionshipRegistration.objects.create(
        championship=champ,
        athlete=ath2,
        registration_number='REG-TEST-002',
        venue_cleared=True,
        status=RegistrationStatus.VENUE_CLEARED
    )
    entry2 = EventEntry.objects.create(registration=reg2, event_config=ev_config, age_category=cat, gender='M', chest_number='CH-902')

    roll = MasterRoll.objects.create(
        championship=champ,
        event_config=ev_config,
        age_category=cat,
        gender='M',
        round_type='PRELIMINARY',
        version=1,
        is_locked=True
    )
    MasterRollEntry.objects.create(master_roll=roll, event_entry=entry1, performance_order=1, attendance_status='PRESENT')
    MasterRollEntry.objects.create(master_roll=roll, event_entry=entry2, performance_order=2, attendance_status='PRESENT')

    # 1. Login as sm_user and push entry1
    client.force_login(sm_user)
    session = client.session
    session['active_championship_id'] = champ.id
    session.save()

    resp = client.post(f'/competition/stage/?panel_id={panel.id}', {
        'action': 'push_batch',
        'entry_ids': [entry1.id]
    }, follow=True)
    assert resp.status_code == 200

    perf = Performance.objects.get(event_entry=entry1, round_type='PRELIMINARY')
    assert perf.status == PerformanceStatus.IN_PROGRESS
    assert perf.attempt_number == 1

    # 2. Test Hold Performance by Stage Manager
    resp = client.post(f'/competition/stage/?panel_id={panel.id}', {
        'action': 'hold_performance',
        'performance_id': perf.id,
        'reason': 'Athlete discomfort',
        'remarks': 'Water break requested'
    }, follow=True)
    assert resp.status_code == 200

    perf.refresh_from_db()
    assert perf.status == PerformanceStatus.HELD
    assert perf.interruption_reason == 'Athlete discomfort'
    assert perf.interruption_remarks == 'Water break requested'
    assert perf.interrupted_at is not None

    # Check audit log
    log_hold = PerformanceInterruptionLog.objects.filter(performance=perf, action=InterruptionAction.HOLD).first()
    assert log_hold is not None
    assert log_hold.reason == 'Athlete discomfort'
    assert log_hold.user == sm_user

    # 3. Test that pushing Athlete 2 while Athlete 1 is HELD is BLOCKED
    resp = client.post(f'/competition/stage/?panel_id={panel.id}', {
        'action': 'push_batch',
        'entry_ids': [entry2.id]
    }, follow=True)
    assert "ON HOLD" in resp.content.decode()
    # Batch still has only entry1
    active_batch = StageBatch.objects.get(panel=panel, status='ACTIVE')
    assert list(active_batch.entries.values_list('id', flat=True)) == [entry1.id]

    # 4. Test that judges cannot score while performance is HELD
    client.force_login(d1_user)
    session = client.session
    session['active_championship_id'] = champ.id
    session.save()

    resp = client.post(f'/scoring/artistic-single-d/?panel_id={panel.id}', {
        'action': 'save_item',
        'item_seq': 1,
        'execution_score': '8.5'
    }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    assert resp.status_code == 403
    assert 'ON HOLD' in resp.json()['message']

    # 5. Cross-panel security: other_sm cannot manipulate this panel's performance
    client.force_login(other_sm)
    session = client.session
    session['active_championship_id'] = champ.id
    session.save()

    resp = client.post(f'/competition/stage/?panel_id={panel.id}', {
        'action': 'resume_performance',
        'performance_id': perf.id
    }, follow=True)
    assert resp.status_code == 403

    # 6. Resume Performance by assigned sm_user
    client.force_login(sm_user)
    session = client.session
    session['active_championship_id'] = champ.id
    session.save()

    resp = client.post(f'/competition/stage/?panel_id={panel.id}', {
        'action': 'resume_performance',
        'performance_id': perf.id,
        'remarks': 'Athlete resumed'
    }, follow=True)
    assert resp.status_code == 200

    perf.refresh_from_db()
    assert perf.status == PerformanceStatus.IN_PROGRESS
    assert perf.resumed_at is not None

    log_resume = PerformanceInterruptionLog.objects.filter(performance=perf, action=InterruptionAction.RESUME).first()
    assert log_resume is not None

    # 7. Stop Performance with mandatory reason
    resp = client.post(f'/competition/stage/?panel_id={panel.id}', {
        'action': 'stop_performance',
        'performance_id': perf.id,
        'reason': 'Technical problem',
        'remarks': 'Stage surface slip'
    }, follow=True)
    assert resp.status_code == 200

    perf.refresh_from_db()
    assert perf.status == PerformanceStatus.STOPPED_PENDING_DECISION

    # 8. Authorize Repush / New Attempt
    resp = client.post(f'/competition/stage/?panel_id={panel.id}', {
        'action': 'authorize_repush',
        'performance_id': perf.id,
        'remarks': 'Re-attempt granted under technical equipment rule'
    }, follow=True)
    assert resp.status_code == 200

    perf.refresh_from_db()
    assert perf.repush_authorized is True
    assert perf.repush_authorized_by == sm_user

    # While repush is authorized for entry1, trying to push entry2 must be blocked!
    resp = client.post(f'/competition/stage/?panel_id={panel.id}', {
        'action': 'push_batch',
        'entry_ids': [entry2.id]
    }, follow=True)
    assert "A New Attempt is currently authorized" in resp.content.decode()

    # 9. Push New Attempt (Attempt #2)
    resp = client.post(f'/competition/stage/?panel_id={panel.id}', {
        'action': 'push_repush',
        'performance_id': perf.id
    }, follow=True)
    assert resp.status_code == 200

    perf.refresh_from_db()
    assert perf.attempt_number == 2
    assert perf.attempt_id == f"ATT-{perf.id}-2"
    assert perf.status == PerformanceStatus.IN_PROGRESS
    assert perf.repush_authorized is False

    log_repush = PerformanceInterruptionLog.objects.filter(performance=perf, action=InterruptionAction.REPUSH).first()
    assert log_repush is not None
    assert log_repush.attempt_number == 2

    # Verify that Chief Judge view renders cleanly with Attempt #2 and history
    client.force_login(cj_user)
    session = client.session
    session['active_championship_id'] = champ.id
    session.save()

    resp = client.get(f'/scoring/chief-judge/?panel_id={panel.id}')
    assert resp.status_code == 200
    content = resp.content.decode()
    assert "Attempt #2" in content
    assert "Stage Interruption History" in content
