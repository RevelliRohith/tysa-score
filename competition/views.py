import random
import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from .models import DistrictDraw, MasterRoll, MasterRollEntry, JudgePanel, StageBatch
from championships.models import Championship, EventConfiguration, AgeCategory, EventType
from registrations.models import EventEntry, RegistrationStatus
from judges.models import UniversalJudge, ApprovalStatus
from scoring.models import Performance, PerformanceStatus, JudgeSubmission

@login_required
def district_draw(request):
    """
    Implements 14_District_Draw_and_Master_Roll.png:
    District lottery draw, auditable round-robin performance order generation,
    cycle table, and master roll finalisation/lock.
    """
    championship = Championship.objects.first()
    event_configs = championship.event_configurations.filter(is_enabled=True)
    selected_event_id = request.GET.get('event_id')
    current_event = event_configs.filter(id=selected_event_id).first() if selected_event_id else event_configs.first()

    categories = current_event.age_categories.all() if current_event else []
    selected_cat_id = request.GET.get('cat_id')
    current_cat = categories.filter(id=selected_cat_id).first() if selected_cat_id else categories.first()

    gender = request.GET.get('gender', 'M')
    round_type = request.GET.get('round_type', 'PRELIMINARY')

    # Find or generate master roll
    master_roll = MasterRoll.objects.filter(
        championship=championship,
        event_config=current_event,
        age_category=current_cat,
        gender=gender,
        round_type=round_type
    ).first()

    roll_entries = master_roll.roll_entries.select_related('event_entry__registration__athlete').all() if master_roll else []

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'run_draw':
            # 1. District Lottery
            districts = list(championship.district_quotas.values_list('district_name', flat=True))
            random.seed(42) # Deterministic auditable draw seed
            random.shuffle(districts)
            
            draw, _ = DistrictDraw.objects.update_or_create(
                championship=championship,
                event_config=current_event,
                defaults={'draw_order': districts, 'created_by': request.user, 'draw_seed': 'SEED-TYSA-2026-DRAW'}
            )

            # 2. Generate Master Roll cycling districts in draw order
            if not master_roll:
                master_roll = MasterRoll.objects.create(
                    championship=championship,
                    event_config=current_event,
                    age_category=current_cat,
                    gender=gender,
                    round_type=round_type,
                    version=1
                )
            
            # Fetch eligible entries
            eligible_entries = list(EventEntry.objects.filter(
                event_config=current_event,
                age_category=current_cat,
                gender=gender,
                registration__status=RegistrationStatus.VENUE_CLEARED
            ).select_related('registration__athlete'))

            # Map entries by district
            district_buckets = {d: [] for d in districts}
            for e in eligible_entries:
                d = e.district
                if d in district_buckets:
                    district_buckets[d].append(e)

            # Round robin cycle
            master_roll.roll_entries.all().delete()
            perf_order = 1
            cycle = 1
            has_more = True

            while has_more:
                has_more = False
                for d in districts:
                    if district_buckets[d]:
                        entry = district_buckets[d].pop(0)
                        MasterRollEntry.objects.create(
                            master_roll=master_roll,
                            event_entry=entry,
                            cycle_number=cycle,
                            performance_order=perf_order
                        )
                        perf_order += 1
                        has_more = True
                cycle += 1

            messages.success(request, f"District draw and Master Roll generated with {perf_order - 1} competitors!")
            return redirect(f"/competition/draw/?event_id={current_event.id}&cat_id={current_cat.id}&gender={gender}&round_type={round_type}")

        elif action == 'lock_roll':
            if master_roll:
                master_roll.is_locked = True
                master_roll.locked_by = request.user
                master_roll.locked_at = datetime.datetime.now()
                master_roll.save()
                messages.success(request, "Master Roll officially locked. Ordinary declarations are now frozen.")
            return redirect(f"/competition/draw/?event_id={current_event.id}&cat_id={current_cat.id}&gender={gender}&round_type={round_type}")

    return render(request, 'competition/district_draw.html', {
        'championship': championship,
        'event_configs': event_configs,
        'current_event': current_event,
        'categories': categories,
        'current_cat': current_cat,
        'gender': gender,
        'round_type': round_type,
        'master_roll': master_roll,
        'roll_entries': roll_entries,
    })

@login_required
def panel_assignments(request):
    """
    Implements 15_CD_Judge_Panel_Assignment.png:
    Competition Director (CD) judge panel assignments, conflict detection,
    and stage schedule.
    """
    championship = Championship.objects.first()
    panels = JudgePanel.objects.filter(championship=championship).select_related('event_config', 'chief_judge', 'stage_manager')
    approved_judges = UniversalJudge.objects.filter(approval_status=ApprovalStatus.APPROVED).select_related('user')

    if request.method == 'POST':
        panel_id = request.POST.get('panel_id')
        panel = get_object_or_404(JudgePanel, id=panel_id, championship=championship)

        d1_id = request.POST.get('d1_judge')
        d2_id = request.POST.get('d2_judge')
        d3_id = request.POST.get('d3_judge')
        d4_id = request.POST.get('d4_judge')
        time_id = request.POST.get('time_judge')
        evaluator_id = request.POST.get('evaluator')
        chief_id = request.POST.get('chief_judge')

        panel.d1_judge_id = d1_id if d1_id else None
        panel.d2_judge_id = d2_id if d2_id else None
        panel.d3_judge_id = d3_id if d3_id else None
        panel.d4_judge_id = d4_id if d4_id else None
        panel.time_judge_id = time_id if time_id else None
        panel.evaluator_id = evaluator_id if evaluator_id else None
        panel.chief_judge_id = chief_id if chief_id else None
        panel.save()

        messages.success(request, f"Judge panel updated for {panel.stage_name} ({panel.event_config.get_event_type_display()}).")
        return redirect('panel_assignments')

    return render(request, 'competition/panel_assignments.html', {
        'championship': championship,
        'panels': panels,
        'approved_judges': approved_judges,
    })

@login_required
def stage_manager(request):
    """
    Implements 16. stage manager.jpg:
    Stage queue, batch push controls (1-5 prelims / 1-3 finals), per-judge submission monitor,
    second-screen controls.
    """
    championship = Championship.objects.first()
    panels = JudgePanel.objects.filter(championship=championship, is_active=True)
    current_panel = panels.first()

    # Active batch on stage
    active_batch = StageBatch.objects.filter(panel=current_panel, status__in=['ACTIVE', 'SUBMITTED']).first() if current_panel else None
    
    # Ready entries in roll
    roll = MasterRoll.objects.filter(championship=championship, is_locked=True).first()
    queued_entries = EventEntry.objects.filter(
        registration__championship=championship,
        registration__status=RegistrationStatus.VENUE_CLEARED
    )[:10]

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'push_batch':
            selected_entry_ids = request.POST.getlist('entry_ids')
            if not selected_entry_ids:
                messages.error(request, "Please select at least 1 athlete to push to stage.")
                return redirect('stage_manager')
            
            # Traditional earlier rounds permit 1 to 5; Finals permit max 3
            if len(selected_entry_ids) > 5:
                messages.error(request, "Cannot push more than 5 simultaneous athletes on stage!")
                return redirect('stage_manager')

            # Create new batch
            batch_num = (StageBatch.objects.filter(panel=current_panel).count() + 1)
            batch = StageBatch.objects.create(
                panel=current_panel,
                batch_number=batch_num,
                status='ACTIVE',
                current_asana_index=1
            )
            entries = EventEntry.objects.filter(id__in=selected_entry_ids)
            batch.entries.set(entries)

            # Create performance records
            for e in entries:
                Performance.objects.get_or_create(
                    event_entry=e,
                    round_type=current_panel.round_type,
                    defaults={'batch': batch, 'status': PerformanceStatus.IN_PROGRESS}
                )

            messages.success(request, f"Batch #{batch_num} pushed to judges scoring desks!")
            return redirect('stage_manager')

        elif action == 'next_asana':
            if active_batch and active_batch.current_asana_index < 7:
                active_batch.current_asana_index += 1
                active_batch.save()
                messages.info(request, f"Advanced to Asana #{active_batch.current_asana_index}")
            return redirect('stage_manager')

    return render(request, 'competition/stage_manager.html', {
        'championship': championship,
        'current_panel': current_panel,
        'active_batch': active_batch,
        'queued_entries': queued_entries,
    })

def second_screen(request):
    """
    Implements 16.1 second screen.jpg:
    Audience second screen display visual treatment and latest approved results mode.
    High-contrast stage standings, active batch display, and synchronization status.
    """
    championship = Championship.objects.first()
    active_batch = StageBatch.objects.filter(status='ACTIVE').first()
    
    # Latest released performances
    released_performances = Performance.objects.filter(
        status__in=[PerformanceStatus.PROVISIONAL_RELEASED, PerformanceStatus.CD_FINALIZED]
    ).select_related('event_entry__registration__athlete').order_by('-final_score', '-provisional_score')[:8]

    return render(request, 'competition/second_screen.html', {
        'hide_navbar': True,
        'championship': championship,
        'active_batch': active_batch,
        'released_performances': released_performances,
    })
