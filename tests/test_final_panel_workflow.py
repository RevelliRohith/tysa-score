import datetime
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from championships.models import Championship, EventConfiguration, AgeCategory
from competition.models import JudgePanel, StageBatch
from registrations.models import ChampionshipRegistration, EventEntry
from athletes.models import Athlete
from accounts.models import RoleChoices, ScopedRole
from judges.models import UniversalJudge
from scoring.models import Performance, JudgeSubmission, AsanaScoreItem
from corrections.services import grant_correction_access
from corrections.services import revoke_correction_access
from audit.models import AuditLog

User = get_user_model()

@pytest.fixture
def live(db):
    day = datetime.date(2026, 11, 1)
    champ = Championship.objects.create(name='Final panel tests', code='FINALTEST', year=2026,
        start_date=day, end_date=day, registration_start=day, registration_end=day, age_cutoff_date=day,
        status='IN_PROGRESS')
    cat = AgeCategory.objects.create(name='Junior', code='FINALJR', min_age=10, max_age=16)
    event = EventConfiguration.objects.create(championship=champ, event_type='TRADITIONAL')
    event.age_categories.add(cat)
    panel = JudgePanel(championship=champ, event_config=event, age_category=cat, gender='M', is_active=True)
    users = {}
    for field in panel.required_seats:
        user = User.objects.create_user(email=field+'@test.org', password='Secret123!', full_name=field)
        UniversalJudge.objects.create(user=user, district='Hyderabad', approval_status='APPROVED')
        users[field] = user
        setattr(panel, field, user)
    panel.save()
    batch = StageBatch.objects.create(panel=panel)
    perfs = []
    for i in range(3):
        athlete = Athlete.objects.create(first_name='Athlete'+str(i), last_name='Test', dob=day,
            gender='M', district='Hyderabad', mobile='9000000000')
        reg = ChampionshipRegistration.objects.create(championship=champ, athlete=athlete, registration_number=f'FINAL-{i}')
        entry = EventEntry.objects.create(registration=reg, event_config=event, age_category=cat,
            gender='M', chest_number=str(100+i))
        perf = Performance.objects.create(event_entry=entry, batch=batch if i<2 else None)
        if i<2: batch.entries.add(entry)
        perfs.append(perf)
    cd = User.objects.create_user(email='director@test.org', password='Secret123!')
    ScopedRole.objects.create(user=cd, role=RoleChoices.CD, championship=champ)
    return champ, cat, event, panel, batch, users, perfs, cd

def post(client, perf, sequence=1, values=None, action='draft', url='/scoring/traditional-d/'):
    return client.post(url, {'performance_id': perf.id, 'sequence': sequence,
        'values': values or {}, 'action': action}, content_type='application/json')

@pytest.mark.django_db
@pytest.mark.parametrize('event,total,d_count', [('TRADITIONAL',9,5),('ARTISTIC_SINGLE',11,4),('ARTISTIC_PAIR',12,4),('RHYTHMIC_PAIR',11,4)])
def test_exact_composition(live,event,total,d_count):
    champ,cat,_,_,_,_,_,_=live
    cfg=EventConfiguration.objects.create(championship=champ,event_type=event) if event!='TRADITIONAL' else live[2]
    p=JudgePanel(championship=champ,event_config=cfg,age_category=cat,is_active=True)
    assert len(p.required_seats)==total
    assert len([f for f in p.required_seats if f.startswith('d')])==d_count
    p.save(); assert not p.is_active
    for i,f in enumerate(p.required_seats):
        setattr(p,f,User.objects.create_user(email=f'{event}{i}@test.org',password='p'))
    p.is_active=True;p.save();assert p.is_active
    p.d1_judge=p.chief_judge;p.save();assert not p.is_active

@pytest.mark.django_db
def test_drafts_navigation_refresh_and_independent_athletes(live,client):
    _,_,_,panel,_,users,perfs,_=live
    client.force_login(users['d5_judge'])
    assert client.get('/scoring/traditional-d/').status_code==200
    assert post(client,perfs[0],values={'posture_score':'5.25','stability_score':'1.50'}).status_code==200
    assert post(client,perfs[0],2,{'posture_score':'4.25','stability_score':'1.00'}).status_code==200
    assert post(client,perfs[0],values={'posture_score':'5.75'}).status_code==200
    assert post(client,perfs[1],values={'posture_score':'3.00','stability_score':'0.50'}).status_code==200
    data=client.get('/scoring/traditional-d/?format=json').json()['athletes']
    assert len(data)==2
    assert data[0]['sheet']['role']=='D5_JUDGE'
    assert data[0]['sheet']['items'][0]['posture_score']=='5.75'
    assert data[1]['sheet']['items'][0]['posture_score']=='3.00'
    assert not AsanaScoreItem.objects.filter(is_locked=True).exists()
    assert post(client,perfs[2],values={'posture_score':'1.00'}).status_code==404
    assert post(client,perfs[0],values={'posture_score':'NaN'}).status_code==400
    assert post(client,perfs[0],values={'posture_score':'7.00'}).status_code==400

@pytest.mark.django_db
def test_final_lock_and_cd_reopen_with_history(live,client):
    champ,_,_,panel,batch,users,perfs,cd=live
    client.force_login(users['d1_judge']); perf=perfs[0]
    assert post(client,perf,action='submit').status_code==400
    for seq in range(1,8):
        assert post(client,perf,seq,{'posture_score':'5.00','stability_score':'1.50'}).status_code==200
    assert post(client,perf,action='submit').status_code==200
    sub=JudgeSubmission.objects.get(performance=perf,judge=users['d1_judge'])
    assert sub.is_locked and sub.items.filter(is_locked=True).count()==7
    assert post(client,perf,values={'posture_score':'4.00'}).status_code==409
    grant_correction_access(cd,perf.id,sub.id,'Technical correction',champ)
    assert AuditLog.objects.filter(action='CD_REOPEN_SCORE').get().details['snapshot']['items'][0]['posture_score']=='5.00'
    batch.status='COMPLETED';batch.save()
    url=f'/scoring/reopened-desk/?submission_id={sub.id}'
    assert client.get(url).status_code==200
    assert post(client,perf,values={'posture_score':'4.00'},url=url).status_code==200
    assert post(client,perf,action='submit',url=url).status_code==200
    sub.refresh_from_db();assert sub.is_locked and sub.version==2
    assert sub.items.get(sequence_order=1).posture_score==4
    assert AuditLog.objects.filter(action='CD_REOPEN_SCORE').get().details['snapshot']['items'][0]['posture_score']=='5.00'

@pytest.mark.django_db
def test_chief_only_active_pushed_panel_and_no_score_writes(live,client):
    _,_,_,panel,batch,users,perfs,_=live
    client.force_login(users['d1_judge']);post(client,perfs[0],values={'posture_score':'5.00'})
    client.force_login(users['chief_judge'])
    assert client.get('/scoring/chief-judge/').status_code==200
    data=client.get('/scoring/chief-judge/?format=json').json()
    assert {a['id'] for a in data['athletes']}=={perfs[0].id,perfs[1].id}
    d1=data['athletes'][0]['judges'][0]
    assert d1['sheet']['items'][0]['posture_score']=='5.00'
    assert not d1['sheet']['submitted'] and d1['missing']
    assert client.get('/scoring/traditional-d/?panel_id='+str(panel.pk)).status_code==404
    assert post(client,perfs[0],values={'posture_score':'2.00'}).status_code!=200
    other=JudgePanel.objects.create(championship=panel.championship,event_config=panel.event_config,age_category=panel.age_category)
    assert client.get(f'/scoring/chief-judge/?format=json&panel_id={other.pk}').status_code==404
    assert client.post('/scoring/chief-judge/',{'performance_id':perfs[2].pk,'action':'provisional_release'}).status_code==404
    batch.status='COMPLETED';batch.save()
    assert client.get('/scoring/chief-judge/?format=json').json()['athletes']==[]

@pytest.mark.django_db
@pytest.mark.parametrize('role', list(RoleChoices.values))
def test_login_every_role_case_insensitive_and_persistent(live,client,role):
    champ=live[0]
    user=User.objects.create_user(email=role.lower()+'@roles.org',password='Secret123!')
    ScopedRole.objects.create(user=user,role=role,championship=champ)
    response=client.post(reverse('login'),{'email':user.email.upper(),'password':'Secret123!'})
    assert response.status_code==302
    assert client.session['_auth_user_id']==str(user.pk)
    destination=client.get(response.url)
    assert destination.status_code in (200,302)
    assert client.session['_auth_user_id']==str(user.pk)

@pytest.mark.django_db
@pytest.mark.parametrize('event', ['ARTISTIC_SINGLE','ARTISTIC_PAIR','RHYTHMIC_PAIR'])
def test_music_panels_independent_seats_and_live_drafts(live,client,event):
    champ,cat,_,oldpanel,_,users,perfs,_=live
    cfg=EventConfiguration.objects.create(championship=champ,event_type=event,total_asanas=10)
    cfg.age_categories.add(cat)
    panel=JudgePanel(championship=champ,event_config=cfg,age_category=cat,gender='M',is_active=True)
    officials={}
    for field in panel.required_seats:
        official=User.objects.create_user(email=event+field+'@test.org',password='p')
        UniversalJudge.objects.create(user=official,district='Hyderabad',approval_status='APPROVED')
        setattr(panel,field,official);officials[field]=official
    panel.save();assert panel.is_active
    batch=StageBatch.objects.create(panel=panel)
    entry=EventEntry.objects.create(registration=perfs[0].event_entry.registration,event_config=cfg,
        age_category=cat,gender='M',chest_number=event)
    batch.entries.add(entry)
    perf=Performance.objects.create(event_entry=entry,batch=batch)
    url={'ARTISTIC_SINGLE':'/scoring/artistic-single-d/', 'ARTISTIC_PAIR':'/scoring/artistic-pair-d/',
         'RHYTHMIC_PAIR':'/scoring/rhythmic-pair-d/'}[event]+f'?panel_id={panel.pk}'
    values={'execution_score':'8.00'} if event=='ARTISTIC_SINGLE' else {'chest_a_execution':'8.00','chest_b_execution':'6.00'}
    if event=='ARTISTIC_PAIR': values['formation_score']='7.00'
    client.force_login(officials['d2_judge'])
    assert post(client,perf,values=values,url=url).status_code==200
    assert JudgeSubmission.objects.get(performance=perf).judge_role=='D2_JUDGE'
    assert post(client,perfs[0],values=values,url=url).status_code==404
    for field in ['time_judge','time_judge_2']:
        client.force_login(officials[field])
        assert post(client,perf,values={'hold_duration_seconds':'6.00','routine_duration':'160.00'},
            url=f'/scoring/music-time/?panel_id={panel.pk}').status_code==200
        for sequence in range(2,11):
            assert post(client,perf,sequence,{'hold_duration_seconds':'6.00'},url=f'/scoring/music-time/?panel_id={panel.pk}').status_code==200
        assert post(client,perf,action='submit',url=f'/scoring/music-time/?panel_id={panel.pk}').status_code==200
    assert set(JudgeSubmission.objects.filter(performance=perf,judge_role__startswith='TIME').values_list('judge_role',flat=True))=={'TIME_JUDGE','TIME_JUDGE_2'}
    assert all(s.raw_total==160 for s in JudgeSubmission.objects.filter(performance=perf,judge_role__startswith='TIME'))
    time_sub=JudgeSubmission.objects.get(performance=perf,judge_role='TIME_JUDGE_2')
    grant_correction_access(live[7],perf.id,time_sub.id,'Timing review',champ)
    correction_url=f'/scoring/reopened-desk/?submission_id={time_sub.id}'
    assert post(client,perf,values={'routine_duration':'170.00'},url=correction_url).status_code==200
    assert post(client,perf,action='submit',url=correction_url).status_code==200
    time_sub.refresh_from_db();assert time_sub.raw_total==170 and time_sub.is_locked
    for field in ['artistry_judge','artistry_judge_2']:
        client.force_login(officials[field])
        assert post(client,perf,values={'costume':'1.00'},url=f'/scoring/artistry/?panel_id={panel.pk}').status_code==200
    for field in [f for f in panel.required_seats if f.startswith('evaluator')]:
        client.force_login(officials[field])
        assert post(client,perf,values={'is_valid':False,'invalid_reason':'Incorrect asana'},url=f'/scoring/evaluator/?panel_id={panel.pk}').status_code==200
    client.force_login(officials['chief_judge'])
    payload=client.get(f'/scoring/chief-judge/?format=json&panel_id={panel.pk}').json()
    assert len(payload['athletes'])==1
    assert len(payload['athletes'][0]['judges'])==len(panel.required_seats)-2
    assert all(a['id']!=perfs[0].id for a in payload['athletes'])

@pytest.mark.django_db
def test_stage_ownership_and_push_scope(live,client):
    champ,cat,event,panel,batch,users,perfs,cd=live
    client.force_login(users['stage_manager'])
    from competition.models import MasterRoll,MasterRollEntry
    roll=MasterRoll.objects.create(championship=champ,event_config=event,age_category=cat,gender='M')
    MasterRollEntry.objects.create(master_roll=roll,event_entry=perfs[0].event_entry,performance_order=1)
    response=client.post(f'/competition/stage/?panel_id={panel.pk}',{'action':'push_batch','entry_ids':[perfs[2].event_entry_id]})
    assert response.status_code==302
    assert StageBatch.objects.filter(panel=panel).count()==1
    response=client.post(f'/competition/stage/?panel_id={panel.pk}',{'action':'push_batch','entry_ids':[perfs[0].event_entry_id]})
    assert response.status_code==302
    batch.refresh_from_db();assert batch.status=='COMPLETED'
    assert StageBatch.objects.filter(panel=panel,status='ACTIVE').get().entries.get().id==perfs[0].event_entry_id
    client.force_login(users['chief_judge'])
    assert client.post(f'/competition/stage/?panel_id={panel.pk}',{'action':'push_batch','entry_ids':[perfs[0].event_entry_id]}).status_code in (302,403)

@pytest.mark.django_db
def test_revoke_restores_original_and_keeps_abandoned_draft_history(live,client):
    champ,_,_,_,_,users,perfs,cd=live
    client.force_login(users['d1_judge'])
    for seq in range(1,8):
        assert post(client,perfs[0],seq,{'posture_score':'5.00','stability_score':'1.00'}).status_code==200
    assert post(client,perfs[0],action='submit').status_code==200
    sub=JudgeSubmission.objects.get(performance=perfs[0])
    grant=grant_correction_access(cd,perfs[0].id,sub.id,'Review',champ)
    assert post(client,perfs[0],values={'posture_score':'4.00'}).status_code==200
    revoke_correction_access(cd,grant.id,champ)
    sub.refresh_from_db()
    assert sub.is_locked and sub.is_submitted and not sub.is_draft
    assert sub.items.get(sequence_order=1).posture_score==5
    assert AuditLog.objects.get(action='CD_CORRECTION_REVOKED').details['abandoned_draft']['items'][0]['posture_score']=='4.00'
