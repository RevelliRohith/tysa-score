import pytest
from decimal import Decimal
from django.utils import timezone
from championships.models import Championship, EventConfiguration, AgeCategory, EventType
from registrations.models import ChampionshipRegistration, EventEntry, RegistrationStatus, AsanaSequenceItem
from athletes.models import Athlete
from competition.models import (
    MasterRoll, MasterRollEntry, MasterRollStatus, MasterRollVersionHistory,
    EvaluatorAsanaVerification, EvaluatorVerificationStatus
)
from competition.views import ensure_evaluator_verifications
from accounts.models import User, RoleChoices, ScopedRole

@pytest.mark.django_db
def test_master_roll_and_evaluator_reference_lifecycle():
    # 1. Create Championship & Event Configs
    champ = Championship.objects.create(
        name="State Yogasana 2026",
        year=2026,
        code="TEST26",
        start_date="2026-11-20",
        end_date="2026-11-25",
        registration_start="2026-10-01",
        registration_end="2026-11-15",
        age_cutoff_date="2026-12-31",
        venue_name="Hyderabad Stadium"
    )
    ev_trad = EventConfiguration.objects.create(championship=champ, event_type=EventType.TRADITIONAL, prelim_compulsory_count=5)
    ev_art = EventConfiguration.objects.create(championship=champ, event_type=EventType.ARTISTIC_SINGLE)
    cat = AgeCategory.objects.create(code="SUB_JR", name="Sub-Junior", min_age=10, max_age=14)

    # 2. CD User
    cd_user = User.objects.create_user(email="cd@test.org", password="pwd", full_name="CD Officer")
    ScopedRole.objects.create(user=cd_user, role=RoleChoices.CD, championship=champ)

    # 3. Athletes & Event Entries
    ath1 = Athlete.objects.create(first_name="Athlete", last_name="One", district="HYDERABAD", gender="M", dob="2012-01-01")
    reg1 = ChampionshipRegistration.objects.create(athlete=ath1, championship=champ, registration_number="REG001")
    entry_trad = EventEntry.objects.create(registration=reg1, event_config=ev_trad, age_category=cat, gender="M", chest_number="101")

    ath2 = Athlete.objects.create(first_name="Athlete", last_name="Two", district="RANGAREDDY", gender="M", dob="2012-01-01")
    reg2 = ChampionshipRegistration.objects.create(athlete=ath2, championship=champ, registration_number="REG002")
    entry_art = EventEntry.objects.create(registration=reg2, event_config=ev_art, age_category=cat, gender="M", chest_number="201")

    # 4. Declare Asanas
    # Traditional: 5 compulsory (Items 1-5), 2 optional (Items 6-7)
    for seq in range(1, 8):
        AsanaSequenceItem.objects.create(
            event_entry=entry_trad,
            sequence_order=seq,
            round_type='PRELIMINARY',
            is_compulsory=(seq <= 5),
            declared_code=f"TRAD-A{seq}",
            declared_bv=Decimal('1.00') if seq <= 5 else Decimal('0.80')
        )

    # Artistic Single: 10 declared asanas
    for seq in range(1, 11):
        AsanaSequenceItem.objects.create(
            event_entry=entry_art,
            sequence_order=seq,
            round_type='PRELIMINARY',
            is_compulsory=False,
            declared_code=f"ART-A{seq}",
            declared_bv=Decimal('0.85')
        )

    # 5. Create Traditional Master Roll
    mr_trad = MasterRoll.objects.create(
        championship=champ,
        event_config=ev_trad,
        age_category=cat,
        gender="M",
        round_type='PRELIMINARY',
        version=1,
        status=MasterRollStatus.DRAFT,
        created_by=cd_user
    )
    MasterRollEntry.objects.create(master_roll=mr_trad, event_entry=entry_trad, performance_order=1)

    # Publish Traditional Master Roll
    mr_trad.status = MasterRollStatus.PUBLISHED
    mr_trad.published_by = cd_user
    mr_trad.published_at = timezone.now()
    mr_trad.save()
    ensure_evaluator_verifications(mr_trad)

    # Verify EvaluatorAsanaVerification generated
    verifs_trad = list(EvaluatorAsanaVerification.objects.filter(master_roll=mr_trad).order_by('sequence_order'))
    assert len(verifs_trad) == 7
    assert verifs_trad[0].is_compulsory is True
    assert verifs_trad[0].declared_bv == Decimal('1.00')
    assert verifs_trad[5].is_compulsory is False
    assert verifs_trad[5].declared_bv == Decimal('0.80')

    # 6. Create Artistic Single Master Roll
    mr_art = MasterRoll.objects.create(
        championship=champ,
        event_config=ev_art,
        age_category=cat,
        gender="M",
        round_type='PRELIMINARY',
        version=1,
        status=MasterRollStatus.DRAFT,
        created_by=cd_user
    )
    MasterRollEntry.objects.create(master_roll=mr_art, event_entry=entry_art, performance_order=1)

    mr_art.status = MasterRollStatus.PUBLISHED
    mr_art.published_by = cd_user
    mr_art.published_at = timezone.now()
    mr_art.save()
    ensure_evaluator_verifications(mr_art)

    verifs_art = list(EvaluatorAsanaVerification.objects.filter(master_roll=mr_art).order_by('sequence_order'))
    assert len(verifs_art) == 10
    assert all(not v.is_compulsory for v in verifs_art)
    assert verifs_art[0].declared_bv == Decimal('0.85')

    # 7. Evaluator Manual Verification & Discrepancy Recording
    eval_user = User.objects.create_user(email="eval@test.org", password="pwd", full_name="Evaluator Judge")
    ScopedRole.objects.create(user=eval_user, role=RoleChoices.EVALUATOR, championship=champ)
    v1 = verifs_trad[0]
    v1.status = EvaluatorVerificationStatus.VERIFIED
    v1.is_verified = True
    v1.evaluator = eval_user
    v1.save()
    assert v1.is_verified is True

    # Record discrepancy on item 6 (Optional asana performed differently)
    v6 = verifs_trad[5]
    v6.status = EvaluatorVerificationStatus.DISCREPANCY_FOUND
    v6.has_discrepancy = True
    v6.discrepancy_type = 'INCORRECT_CODE'
    v6.observed_code = 'FBS-B2'
    v6.discrepancy_remarks = 'Athlete performed FBS-B2 instead of declared TRAD-A6'
    v6.evaluator = eval_user
    v6.save()

    assert v6.has_discrepancy is True
    assert v6.observed_code == 'FBS-B2'
    # Master Roll declaration itself remains unchanged
    assert v6.declared_code == 'TRAD-A6'

    # 8. Locking and Version History
    mr_trad.is_locked = True
    mr_trad.status = MasterRollStatus.LOCKED
    mr_trad.locked_by = cd_user
    mr_trad.locked_at = timezone.now()
    mr_trad.save()
    assert mr_trad.is_locked is True

    # Version history record
    vh = MasterRollVersionHistory.objects.create(
        master_roll=mr_trad,
        version=mr_trad.version,
        action='LOCKED',
        changes_summary="Locked by CD",
        performed_by=cd_user
    )
    assert vh.version == 1
    assert mr_trad.version_history.count() == 1
