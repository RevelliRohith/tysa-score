import random
import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
import csv
from django.http import HttpResponse, JsonResponse
from .models import DistrictDraw, MasterRoll, MasterRollEntry, JudgePanel, StageBatch
from championships.models import Championship, EventConfiguration, AgeCategory, EventType
from registrations.models import EventEntry, RegistrationStatus
from judges.models import UniversalJudge, ApprovalStatus
from scoring.models import Performance, PerformanceStatus, JudgeSubmission
from accounts.models import RoleChoices
from accounts.permissions import get_active_championship, role_required
from config.districts import TELANGANA_DISTRICTS

@role_required(RoleChoices.ADMIN, RoleChoices.CD)
def cd_dashboard(request):
    """
    Implements Competition Director (CD) Dashboard:
    Championship readiness, technical operational governance, lottery draw status,
    panel configurations, live stage monitoring, and active correction grants.
    """
    championship = get_active_championship(request)
    if not championship:
        messages.error(request, "No active championship selected or assigned.")
        return redirect('no_role_assigned')

    from corrections.models import CDCorrectionGrant, GrantStatus

    # Active grants for this championship
    active_grants = CDCorrectionGrant.objects.filter(
        performance__event_entry__registration__championship=championship,
        status=GrantStatus.OPEN
    ).select_related('performance', 'judge_submission', 'judge_submission__judge')

    locked_rolls = MasterRoll.objects.filter(championship=championship, is_locked=True).count()
    total_rolls = MasterRoll.objects.filter(championship=championship).count()
    cleared_athletes = EventEntry.objects.filter(
        registration__championship=championship,
        registration__status=RegistrationStatus.VENUE_CLEARED
    ).count()

    panels = JudgePanel.objects.filter(championship=championship)
    total_panels = panels.count()
    active_panels = panels.filter(is_active=True).count()
    approved_judges = UniversalJudge.objects.filter(approval_status=ApprovalStatus.APPROVED).count()

    active_batches = StageBatch.objects.filter(panel__championship=championship, status='ACTIVE').count()
    scored_performances = Performance.objects.filter(
        event_entry__registration__championship=championship,
        status__in=[PerformanceStatus.SUBMITTED, PerformanceStatus.CHIEF_REVIEWED, PerformanceStatus.PROVISIONAL_RELEASED, PerformanceStatus.CD_FINALIZED]
    ).count()
    cd_finalized_count = Performance.objects.filter(
        event_entry__registration__championship=championship,
        status=PerformanceStatus.CD_FINALIZED
    ).count()

    district_count = championship.district_quotas.count()

    return render(request, 'competition/cd_dashboard.html', {
        'championship': championship,
        'active_grants': active_grants,
        'locked_rolls': locked_rolls,
        'total_rolls': total_rolls,
        'cleared_athletes': cleared_athletes,
        'total_panels': total_panels,
        'active_panels': active_panels,
        'approved_judges': approved_judges,
        'active_batches': active_batches,
        'scored_performances': scored_performances,
        'cd_finalized_count': cd_finalized_count,
        'district_count': district_count,
    })

@role_required(RoleChoices.ADMIN, RoleChoices.CD)
def district_draw(request):
    """
    Implements 14_District_Draw_and_Master_Roll.png:
    - Displays all 33 Telangana districts with inputs for lottery numbering (1-33).
    - Sorts event & age category entries strictly by lottery draw sequence.
    - Minimal neat Master Roll with Chest No, Name, District, Mobile,
      and toggle for With Signature Column vs Without Signature Column.
    """
    championship = get_active_championship(request)
    event_configs = championship.event_configurations.filter(is_enabled=True)
    selected_event_id = request.GET.get('event_id')
    current_event = event_configs.filter(id=selected_event_id).first() if selected_event_id else event_configs.first()

    categories = current_event.age_categories.all() if current_event else []
    selected_cat_id = request.GET.get('cat_id')
    current_cat = categories.filter(id=selected_cat_id).first() if selected_cat_id else categories.first()

    gender = request.GET.get('gender', 'M')
    round_type = request.GET.get('round_type', 'PRELIMINARY')
    view_mode = request.GET.get('view_mode', 'without_sig') # 'without_sig' or 'with_sig'

    # Current District Lottery Draw
    draw = DistrictDraw.objects.filter(
        championship=championship,
        event_config=current_event
    ).first()

    current_districts_order = draw.draw_order if (draw and draw.draw_order) else list(TELANGANA_DISTRICTS)
    # Ensure all 33 districts exist
    for d in TELANGANA_DISTRICTS:
        if d not in current_districts_order:
            current_districts_order.append(d)

    # Master Roll
    master_roll = MasterRoll.objects.filter(
        championship=championship,
        event_config=current_event,
        age_category=current_cat,
        gender=gender,
        round_type=round_type
    ).first()

    roll_entries = master_roll.roll_entries.select_related('event_entry__registration__athlete').order_by('performance_order') if master_roll else []

    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action in ['save_numbering', 'run_draw']:
            if action == 'save_numbering':
                # Parse manual numbering inputs
                district_num_pairs = []
                for d in TELANGANA_DISTRICTS:
                    num_val = request.POST.get(f'number_{d}', '').strip()
                    try:
                        n = int(num_val)
                    except ValueError:
                        n = 999
                    district_num_pairs.append((n, d))
                district_num_pairs.sort(key=lambda x: (x[0], x[1]))
                sorted_districts = [pair[1] for pair in district_num_pairs]
            else:
                # Automated randomized lottery
                sorted_districts = list(TELANGANA_DISTRICTS)
                random.seed(int(datetime.datetime.now().timestamp()))
                random.shuffle(sorted_districts)

            draw, _ = DistrictDraw.objects.update_or_create(
                championship=championship,
                event_config=current_event,
                defaults={'draw_order': sorted_districts, 'created_by': request.user, 'draw_seed': f'DRAW-{datetime.datetime.now().strftime("%Y%m%d%H%M")}'}
            )

            # Generate/Update Master Roll cycling districts in draw order
            if not master_roll:
                master_roll = MasterRoll.objects.create(
                    championship=championship,
                    event_config=current_event,
                    age_category=current_cat,
                    gender=gender,
                    round_type=round_type,
                    version=1
                )

            eligible_entries = list(EventEntry.objects.filter(
                event_config=current_event,
                age_category=current_cat,
                gender=gender,
                registration__championship=championship
            ).select_related('registration__athlete'))

            # Map entries into sorted district buckets
            district_buckets = {d: [] for d in sorted_districts}
            for e in eligible_entries:
                d = e.district
                if d in district_buckets:
                    district_buckets[d].append(e)
                else:
                    district_buckets.setdefault(d, []).append(e)

            # Round robin cycle sequence
            master_roll.roll_entries.all().delete()
            perf_order = 1
            cycle = 1
            has_more = True

            while has_more:
                has_more = False
                for d in sorted_districts:
                    if district_buckets.get(d):
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

            messages.success(request, f"Lottery draw updated! Master Roll generated with {perf_order - 1} athletes.")
            return redirect(f"/competition/draw/?event_id={current_event.id}&cat_id={current_cat.id}&gender={gender}&round_type={round_type}&view_mode={view_mode}")

        elif action == 'lock_roll':
            if master_roll:
                master_roll.is_locked = True
                master_roll.locked_by = request.user
                master_roll.locked_at = datetime.datetime.now()
                master_roll.save()
                messages.success(request, "Master Roll officially locked. Ordinary declarations are now frozen.")
            return redirect(f"/competition/draw/?event_id={current_event.id}&cat_id={current_cat.id}&gender={gender}&round_type={round_type}&view_mode={view_mode}")

    draw_districts_list = []
    for idx, d_name in enumerate(current_districts_order, start=1):
        draw_districts_list.append({
            'name': d_name,
            'number': idx
        })

    state_logo = championship.branding_logos.filter(logo_type='STATE').first() if championship else None
    district_logo = championship.branding_logos.filter(logo_type='DISTRICT').first() if championship else None
    fed_logo = championship.branding_logos.filter(logo_type='FEDERATION').first() if championship else None
    other_logos = championship.branding_logos.exclude(logo_type__in=['STATE', 'DISTRICT', 'FEDERATION']) if championship else None

    return render(request, 'competition/district_draw.html', {
        'championship': championship,
        'event_configs': event_configs,
        'current_event': current_event,
        'categories': categories,
        'current_cat': current_cat,
        'gender': gender,
        'round_type': round_type,
        'view_mode': view_mode,
        'master_roll': master_roll,
        'roll_entries': roll_entries,
        'draw_districts': draw_districts_list,
        'state_logo': state_logo,
        'district_logo': district_logo,
        'fed_logo': fed_logo,
        'other_logos': other_logos,
    })

@role_required(RoleChoices.ADMIN, RoleChoices.CD)
def panel_assignments(request):
    """
    Implements 15_CD_Judge_Panel_Assignment.png:
    - Step 1: Filter/Select Event, Age Category, Gender Division, Round.
    - Step 2: Assign approved judges via searchable dropdowns.
    - Step 3: Print preview, printable format, downloadable CSV file.
    """
    championship = get_active_championship(request)
    event_configs = championship.event_configurations.filter(is_enabled=True) if championship else EventConfiguration.objects.none()
    
    selected_event_id = request.GET.get('event_id')
    current_event = event_configs.filter(id=selected_event_id).first() if selected_event_id else event_configs.first()

    categories = current_event.age_categories.all() if current_event else AgeCategory.objects.all().order_by('display_order')
    selected_cat_id = request.GET.get('cat_id')
    current_cat = categories.filter(id=selected_cat_id).first() if selected_cat_id else categories.first()

    gender = request.GET.get('gender', 'M')
    round_type = request.GET.get('round_type', 'PRELIMINARY')

    approved_judges = UniversalJudge.objects.filter(approval_status=ApprovalStatus.APPROVED).select_related('user').order_by('district', 'user__full_name')

    # Find or auto-create panel for this specific event / category / gender / round
    current_panel = None
    if championship and current_event:
        current_panel, _ = JudgePanel.objects.get_or_create(
            championship=championship,
            event_config=current_event,
            age_category=current_cat,
            gender=gender,
            round_type=round_type,
            defaults={
                'stage_name': 'Stage 1',
                'session_name': 'Morning Session',
                'is_active': True
            }
        )

    # CSV Download Export
    if request.GET.get('export') == 'csv' and current_panel:
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="judge_panel_{current_panel.event_config.event_type}_{gender}_{round_type}.csv"'
        writer = csv.writer(response)
        writer.writerow(['TYSA Score - Official Judge Panel Assignment'])
        writer.writerow(['Championship', championship.name if championship else ''])
        writer.writerow(['Event', current_panel.event_config.get_event_type_display()])
        writer.writerow(['Category', current_cat.name if current_cat else ''])
        writer.writerow(['Gender', 'Male' if gender == 'M' else 'Female'])
        writer.writerow(['Round', round_type])
        writer.writerow([])
        writer.writerow(['Seat / Duty', 'Assigned Judge', 'Email', 'District', 'Certification Level'])
        
        seats = [
            ('Chief Judge', current_panel.chief_judge),
            ('D1 Judge', current_panel.d1_judge),
            ('D2 Judge', current_panel.d2_judge),
            ('D3 Judge', current_panel.d3_judge),
            ('D4 Judge', current_panel.d4_judge),
            ('Time Judge', current_panel.time_judge),
            ('Evaluator', current_panel.evaluator),
            ('Stage Manager', current_panel.stage_manager),
            ('Jury Member', current_panel.jury_member),
        ]
        for seat_name, judge_user in seats:
            if judge_user:
                j_prof = getattr(judge_user, 'judge_profile', None)
                writer.writerow([
                    seat_name,
                    judge_user.full_name or judge_user.email,
                    judge_user.email,
                    j_prof.district if j_prof else '',
                    j_prof.get_certification_level_display() if j_prof else ''
                ])
            else:
                writer.writerow([seat_name, 'Unassigned', '', '', ''])
        return response

    if request.method == 'POST':
        target_panel_id = request.POST.get('panel_id')
        panel = get_object_or_404(JudgePanel, id=target_panel_id, championship=championship)

        d1_id = request.POST.get('d1_judge')
        d2_id = request.POST.get('d2_judge')
        d3_id = request.POST.get('d3_judge')
        d4_id = request.POST.get('d4_judge')
        time_id = request.POST.get('time_judge')
        evaluator_id = request.POST.get('evaluator')
        chief_id = request.POST.get('chief_judge')
        stage_manager_id = request.POST.get('stage_manager')
        jury_id = request.POST.get('jury_member')

        panel.d1_judge_id = d1_id if d1_id else None
        panel.d2_judge_id = d2_id if d2_id else None
        panel.d3_judge_id = d3_id if d3_id else None
        panel.d4_judge_id = d4_id if d4_id else None
        panel.time_judge_id = time_id if time_id else None
        panel.evaluator_id = evaluator_id if evaluator_id else None
        panel.chief_judge_id = chief_id if chief_id else None
        panel.stage_manager_id = stage_manager_id if stage_manager_id else None
        panel.jury_member_id = jury_id if jury_id else None
        panel.save()

        messages.success(request, f"Judge panel successfully confirmed & saved for {panel.event_config.get_event_type_display()} ({gender}, {round_type}).")
        return redirect(f"/competition/panels/?event_id={current_event.id}&cat_id={current_cat.id}&gender={gender}&round_type={round_type}")

    state_logo = championship.branding_logos.filter(logo_type='STATE').first() if championship else None
    district_logo = championship.branding_logos.filter(logo_type='DISTRICT').first() if championship else None
    fed_logo = championship.branding_logos.filter(logo_type='FEDERATION').first() if championship else None

    return render(request, 'competition/panel_assignments.html', {
        'championship': championship,
        'event_configs': event_configs,
        'current_event': current_event,
        'categories': categories,
        'current_cat': current_cat,
        'gender': gender,
        'round_type': round_type,
        'current_panel': current_panel,
        'approved_judges': approved_judges,
        'state_logo': state_logo,
        'district_logo': district_logo,
        'fed_logo': fed_logo,
    })

@role_required(RoleChoices.ADMIN, RoleChoices.STAGE_MANAGER, RoleChoices.CD)
def stage_manager(request):
    """
    Implements 16. stage manager.jpg:
    Stage queue, batch push controls (1-5 prelims / 1-3 finals), per-judge submission monitor,
    second-screen controls.
    """
    championship = get_active_championship(request)
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
    championship = get_active_championship(request)
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
