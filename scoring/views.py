import datetime
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.db import transaction
from django.db.models import Q
from .models import Performance, PerformanceStatus, JudgeSubmission, AsanaScoreItem, PerformanceArtistryScore, EvaluatorIncident, IncidentType
from .services import calculate_performance_score, calculate_traditional_timing_contribution, calculate_tw_marks
from competition.models import StageBatch, JudgePanel
from registrations.models import EventEntry, AsanaSequenceItem
from catalogue.models import Asana
from accounts.models import RoleChoices, ScopedRole
from accounts.permissions import role_required, get_active_championship, is_system_admin, get_user_assigned_seats
from audit.models import AuditLog

def get_traditional_compulsory_count(round_type: str) -> int:
    """
    Traditional Yogasana compulsory asana count:
    - Quarter-Final (Preliminary): 5 Compulsory (Items 1-5), 2 Optional (Items 6-7)
    - Semi-Final: 5 Compulsory (Items 1-5), 2 Optional (Items 6-7)
    - Final: 4 Compulsory (Items 1-4), 3 Optional (Items 5-7)
    """
    rt = (round_type or '').upper()
    if 'FINAL' in rt and 'SEMI' not in rt:
        return 4
    return 5

def resolve_asana_compulsory_and_bv(entry, round_type, seq_num, pose_seq=None):
    if pose_seq is None and entry:
        pose_seq = AsanaSequenceItem.objects.filter(event_entry=entry, sequence_order=seq_num, round_type=round_type).first()
        if not pose_seq:
            pose_seq = AsanaSequenceItem.objects.filter(event_entry=entry, sequence_order=seq_num).first()

    cfg = getattr(entry, 'event_config', None)
    is_traditional = bool(cfg and 'TRADITIONAL' in getattr(cfg, 'event_type', '').upper())

    if is_traditional:
        comp_count = get_traditional_compulsory_count(round_type)
        is_comp = (seq_num <= comp_count)
    elif pose_seq and hasattr(pose_seq, 'is_compulsory') and pose_seq.is_compulsory is not None:
        is_comp = pose_seq.is_compulsory
    else:
        is_comp = False

    if is_comp:
        bv = Decimal('1.00')
    elif pose_seq and pose_seq.declared_bv:
        bv = pose_seq.declared_bv
    else:
        bv = Decimal('0.80')

    return is_comp, bv

def get_traditional_target_hold(round_type: str, is_compulsory: bool) -> Decimal:
    """
    Returns the target hold duration for Traditional Yogasana:
    - Quarter-Final / Preliminary Compulsory: 60 seconds
    - Semi-Final Compulsory: 45 seconds
    - Final Compulsory: 30 seconds
    - Optional Asanas (all rounds): 15 seconds
    """
    if not is_compulsory:
        return Decimal('15.00')

    rt = (round_type or '').upper()
    if 'FINAL' in rt and 'SEMI' not in rt:
        return Decimal('30.00')
    elif 'SEMI' in rt:
        return Decimal('45.00')
    else:
        # Quarter-final / Preliminary
        return Decimal('60.00')

def get_upcoming_athletes_in_queue(panel, exclude_entries=None):
    """
    Finds the upcoming athletes in the Master Roll for the panel who are present
    and have not yet completed on stage.
    """
    if not panel:
        return []
    try:
        from competition.models import MasterRoll, MasterRollEntry
        from scoring.models import Performance, PerformanceStatus

        roll = MasterRoll.objects.filter(
            championship=panel.championship,
            event_config=panel.event_config,
            age_category=panel.age_category,
            gender=panel.gender,
            round_type=panel.round_type
        ).first()
        if not roll:
            return []

        completed_entry_ids = set(Performance.objects.filter(
            event_entry__registration__championship=panel.championship,
            status__in=[PerformanceStatus.COMPLETED, PerformanceStatus.PROVISIONAL_RELEASED, PerformanceStatus.OFFICIAL_FINAL]
        ).values_list('event_entry_id', flat=True))

        q = MasterRollEntry.objects.filter(
            master_roll=roll,
            attendance_status='PRESENT'
        ).exclude(event_entry_id__in=completed_entry_ids)

        if exclude_entries:
            exclude_ids = [e.id if hasattr(e, 'id') else e for e in exclude_entries]
            q = q.exclude(event_entry_id__in=exclude_ids)

        return [m.event_entry for m in q.select_related('event_entry__registration__athlete', 'event_entry__registration__district')[:5]]
    except Exception:
        return []


def get_judge_panel_and_batch(request, seat_fields=None, round_type=None, event_type=None, judge_role=None):
    """
    Resolves the judge's assigned panel, seat label, and active pushed StageBatch.
    Returns: (panel, seat_label, batch_or_response)
    If no panel assigned: returns (None, None, HttpResponse to no_panel_assigned)
    If no pushed active batch: returns (panel, seat_label, HttpResponse to waiting_for_push)
    If current active batch is already submitted by this judge and next batch not yet pushed by Stage Manager:
        returns (panel, seat_label, HttpResponse to waiting_for_push with next athletes preview)
    If active batch ready for scoring: returns (panel, seat_label, batch)
    """
    championship = get_active_championship(request)
    panel_id = request.GET.get('panel_id')
    current_panel = None
    seat_label = None

    is_admin_or_cd = is_system_admin(request.user) or ScopedRole.objects.filter(
        user=request.user, championship=championship, role=RoleChoices.CD
    ).exists()

    if not is_admin_or_cd:
        # Non-admin / Non-CD MUST be assigned to an active panel
        assigned_seats = get_user_assigned_seats(request.user, championship=championship, is_active_only=True)
        if seat_fields:
            # Filter seats matching the requested seat_fields
            assigned_seats = [s for s in assigned_seats if any(f.startswith(s['role_group'].lower().replace('_judge', '')) or f == s['seat_code'].lower() for f in seat_fields)]
        
        if not assigned_seats:
            AuditLog.log(
                action='SECURITY_UNASSIGNED_PANEL_ACCESS',
                resource_type='JudgePanel',
                resource_id=panel_id or '',
                details={'path': request.path, 'user_id': request.user.id, 'reason': 'User has no assigned seat on any panel for this view'},
                user=request.user,
                ip_address=request.META.get('REMOTE_ADDR')
            )
            return None, None, render(request, '403.html', {
                'message': "Access Denied: You are not assigned to an active judge seat on this panel or desk.",
                'current_role': 'Unassigned Seat',
            }, status=403)

        if panel_id:
            # Verify the user is actually assigned to this requested panel
            matched_seat = next((s for s in assigned_seats if str(s['panel'].id) == str(panel_id)), None)
            if not matched_seat:
                AuditLog.log(
                    action='SECURITY_TAMPERED_PANEL_ID',
                    resource_type='JudgePanel',
                    resource_id=panel_id,
                    details={'path': request.path, 'attempted_panel_id': panel_id, 'user_id': request.user.id},
                    user=request.user,
                    ip_address=request.META.get('REMOTE_ADDR')
                )
                return None, None, render(request, '403.html', {
                    'message': f"Access Denied: You are not assigned to Panel #{panel_id}. You may only access your dedicated assigned panel.",
                    'current_role': 'Cross-Panel Access Blocked',
                }, status=403)
            current_panel = matched_seat['panel']
            seat_label = matched_seat['seat_label']
        else:
            # Default to the user's first assigned panel
            current_panel = assigned_seats[0]['panel']
            seat_label = assigned_seats[0]['seat_label']
    else:
        # Admin / CD preview
        if panel_id:
            current_panel = JudgePanel.objects.filter(id=panel_id, championship=championship, is_active=True).first()
        if not current_panel:
            panels = JudgePanel.objects.filter(championship=championship, is_active=True, is_closed=False)
            if event_type:
                panels = panels.filter(event_config__event_type__icontains=event_type)
            if round_type:
                panels = panels.filter(round_type=round_type)
            current_panel = panels.first()
        seat_label = 'Preview (CD/Admin)'

    if not current_panel:
        return None, None, render(request, 'scoring/no_panel_assigned.html', {'championship': championship})

    if not seat_label:
        for f in (seat_fields or JudgePanel.SEAT_FIELDS):
            if getattr(current_panel, f"{f}_id", None) == request.user.id:
                seat_label = JudgePanel.SEAT_LABELS.get(f, 'Judge')
                break
        if not seat_label:
            seat_label = 'Assigned Official'

    effective_role = judge_role
    if not effective_role or effective_role in ['TIME_JUDGE', 'ARTISTRY_JUDGE']:
        lbl_upper = (seat_label or '').upper()
        if 'D1' in lbl_upper: effective_role = 'D1_JUDGE'
        elif 'D2' in lbl_upper: effective_role = 'D2_JUDGE'
        elif 'D3' in lbl_upper: effective_role = 'D3_JUDGE'
        elif 'D4' in lbl_upper: effective_role = 'D4_JUDGE'
        elif 'D5' in lbl_upper: effective_role = 'D5_JUDGE'
        elif 'TIME' in lbl_upper:
            effective_role = 'TIME_JUDGE_2' if ('2' in lbl_upper or 'TWO' in lbl_upper) else 'TIME_JUDGE'
        elif 'EVAL' in lbl_upper: effective_role = 'EVALUATOR'
        elif 'ARTISTRY' in lbl_upper:
            effective_role = 'ARTISTRY_JUDGE_2' if ('2' in lbl_upper or 'TWO' in lbl_upper) else 'ARTISTRY_JUDGE'
        elif 'CHIEF' in lbl_upper: effective_role = 'CHIEF_JUDGE'

    batch = StageBatch.objects.filter(panel=current_panel, status='ACTIVE').first()
    if not batch or batch.entries.count() == 0:
        next_athletes = get_upcoming_athletes_in_queue(current_panel)
        return current_panel, seat_label, render(request, 'scoring/waiting_for_push.html', {
            'panel': current_panel,
            'seat_label': seat_label,
            'championship': championship,
            'next_athletes': next_athletes,
            'has_submitted_previous': False,
        })

    # Check if current judge has already submitted the current active batch
    is_review = (request.GET.get('review') == '1')
    if not is_review and effective_role:
        entries = batch.entries.all()
        subs = JudgeSubmission.objects.filter(
            performance__event_entry__in=entries,
            performance__round_type=current_panel.round_type,
            judge_role=effective_role
        )
        if subs.exists() and subs.filter(is_submitted=False).count() == 0:
            sub_entry_ids = set(subs.filter(is_submitted=True).values_list('performance__event_entry_id', flat=True))
            batch_entry_ids = set(entries.values_list('id', flat=True))
            if batch_entry_ids.issubset(sub_entry_ids) and batch_entry_ids:
                next_athletes = get_upcoming_athletes_in_queue(current_panel, exclude_entries=entries)
                return current_panel, seat_label, render(request, 'scoring/waiting_for_push.html', {
                    'panel': current_panel,
                    'seat_label': seat_label,
                    'championship': championship,
                    'batch': batch,
                    'next_athletes': next_athletes,
                    'has_submitted_previous': True,
                    'submitted_batch_number': batch.batch_number,
                })

    return current_panel, seat_label, batch

@role_required(RoleChoices.ADMIN, RoleChoices.D_JUDGE, RoleChoices.CD, 'APPROVED_JUDGE')
def scoring_traditional_d(request):
    """
    Implements 17_Traditional_D_Judge.png:
    Traditional earlier-round batch: 1 to 5 athlete cards shown together.
    Each card: Chest number only, actual asana code/name/photo, posture input (max 6.0),
    stability input (max 2.0), read-only timing contribution, item total.
    7 items asana-wise submission workflow.
    """
    current_panel, seat_label, batch_or_resp = get_judge_panel_and_batch(
        request,
        seat_fields=['d1_judge', 'd2_judge', 'd3_judge', 'd4_judge', 'd5_judge', 'chief_judge'],
        event_type='TRADITIONAL',
        round_type='PRELIMINARY'
    )
    if isinstance(batch_or_resp, HttpResponse):
        return batch_or_resp

    batch = batch_or_resp
    entries = batch.entries.all()
    total_items = 7
    current_asana_seq = int(request.GET.get('seq', 1))
    if current_asana_seq < 1:
        current_asana_seq = 1
    if current_asana_seq > total_items:
        current_asana_seq = total_items

    round_type = current_panel.round_type if current_panel else 'PRELIMINARY'
    is_admin_or_cd = is_system_admin(request.user) or ScopedRole.objects.filter(
        user=request.user, championship=get_active_championship(request), role=RoleChoices.CD
    ).exists()

    if not is_admin_or_cd and current_panel:
        user_seat = next((s for s in get_user_assigned_seats(request.user, is_active_only=True) if s['panel'].id == current_panel.id and s['role_group'] == 'D_JUDGE'), None)
        if user_seat:
            judge_role = user_seat['seat_code']
            seat_label = user_seat['seat_label']
        else:
            return render(request, '403.html', {'message': "Access Denied: You do not hold an assigned D Judge seat on this panel."}, status=403)
    else:
        judge_role = seat_label.replace(' ', '_').upper()
        if judge_role not in ['D1_JUDGE', 'D2_JUDGE', 'D3_JUDGE', 'D4_JUDGE', 'D5_JUDGE']:
            judge_role = 'D1_JUDGE'

    # Optimize DB queries: get or create Performance and JudgeSubmission once per entry
    entry_subs = {}
    for e in entries:
        perf, _ = Performance.objects.get_or_create(
            event_entry=e,
            round_type=current_panel.round_type,
            defaults={'batch': batch, 'status': PerformanceStatus.IN_PROGRESS}
        )
        if perf.batch != batch:
            perf.batch = batch
            perf.save(update_fields=['batch'])
        sub, _ = JudgeSubmission.objects.get_or_create(
            performance=perf,
            judge_role=judge_role,
            version=1,
            defaults={'judge': request.user, 'is_draft': True, 'raw_total': Decimal('0.00')}
        )
        if not is_admin_or_cd and sub.judge != request.user:
            AuditLog.log(
                action='SECURITY_WORKSPACE_TAMPER',
                resource_type='JudgeSubmission',
                resource_id=sub.id,
                details={'user_id': request.user.id, 'owner_id': sub.judge_id if sub.judge else None},
                user=request.user,
                ip_address=request.META.get('REMOTE_ADDR')
            )
            return render(request, '403.html', {'message': "Access Denied: Cannot access another judge's workspace."}, status=403)
        entry_subs[e.id] = (perf, sub)

    # Bulk query locked items across all sequences for progress tabs
    sub_ids = [sub.id for perf, sub in entry_subs.values()]
    locked_items = set(AsanaScoreItem.objects.filter(
        submission_id__in=sub_ids,
        is_locked=True
    ).values_list('submission_id', 'sequence_order'))

    entry_count = len(entry_subs)
    sequence_tabs = []
    for seq_num in range(1, total_items + 1):
        seq_locked_count = sum(1 for sub_id in sub_ids if (sub_id, seq_num) in locked_items)
        sequence_tabs.append({
            'seq': seq_num,
            'is_current': seq_num == current_asana_seq,
            'is_locked': seq_locked_count == entry_count if entry_count else False,
            'is_partial': 0 < seq_locked_count < entry_count,
        })

    # Prepare submissions for current_asana_seq
    submissions = {}
    is_current_seq_locked = True if entries.exists() else False

    for e in entries:
        perf, sub = entry_subs[e.id]
        item, _ = AsanaScoreItem.objects.get_or_create(
            submission=sub,
            sequence_order=current_asana_seq,
            defaults={
                'posture_score': Decimal('0.00'),
                'stability_score': Decimal('0.00'),
                'timing_contribution': Decimal('0.00'),
                'effective_item_score': Decimal('0.00'),
                'is_draft': True,
                'is_locked': False
            }
        )
        if not item.is_locked:
            is_current_seq_locked = False

        pose_seq = AsanaSequenceItem.objects.filter(event_entry=e, sequence_order=current_asana_seq, round_type=round_type).first()
        if not pose_seq:
            pose_seq = AsanaSequenceItem.objects.filter(event_entry=e, sequence_order=current_asana_seq).first()
        is_compulsory, bv_val = resolve_asana_compulsory_and_bv(e, round_type, current_asana_seq, pose_seq)

        # Check Evaluator determination: Evaluator is the sole authority on asana validity
        eval_item = AsanaScoreItem.objects.filter(
            submission__performance=perf,
            submission__judge_role='EVALUATOR',
            sequence_order=current_asana_seq
        ).first()
        is_eval_invalid = bool(eval_item and not eval_item.is_valid)
        invalid_reason = eval_item.invalid_reason if is_eval_invalid else ""

        # Time judge timing contribution (from Annexure A/B)
        time_item = AsanaScoreItem.objects.filter(
            submission__performance=perf,
            submission__judge_role='TIME_JUDGE',
            sequence_order=current_asana_seq
        ).first()
        t_contrib = time_item.timing_contribution if (time_item and time_item.timing_contribution is not None) else Decimal('2.00')

        item.base_value = bv_val
        item.timing_contribution = t_contrib
        raw_calc = (item.posture_score + item.stability_score + t_contrib) * bv_val
        item.raw_item_score = raw_calc
        if is_eval_invalid:
            item.is_valid = False
            item.invalid_reason = invalid_reason or "Evaluator Confirmed Invalid Asana"
            item.effective_item_score = Decimal('0.000')
        elif t_contrib <= Decimal('0.00'):
            item.is_valid = True
            item.invalid_reason = "Hold < 75% (0.0s time): Score is 0.000"
            item.effective_item_score = Decimal('0.000')
        else:
            item.is_valid = True
            item.invalid_reason = ""
            item.effective_item_score = raw_calc
        item.save(update_fields=['base_value', 'timing_contribution', 'is_valid', 'invalid_reason', 'raw_item_score', 'effective_item_score'])

        submissions[e.id] = {
            'entry': e,
            'performance': perf,
            'submission': sub,
            'item': item,
            'pose': pose_seq,
            'declared_code': pose_seq.declared_code if pose_seq else "ASANA",
            'declared_name': pose_seq.declared_name if pose_seq else "Declared Asana",
            'image_url': (pose_seq.image_url if pose_seq and hasattr(pose_seq, 'image_url') and pose_seq.image_url else (pose_seq.declared_asana.image_url if pose_seq and getattr(pose_seq, 'declared_asana', None) and hasattr(pose_seq.declared_asana, 'image_url') else None)),
            'is_invalid': is_eval_invalid,
            'invalid_reason': invalid_reason,
            'timing_contribution': t_contrib,
            'is_compulsory': is_compulsory,
            'base_value': bv_val,
        }

    # Handle POST
    if request.method == 'POST':
        action = request.POST.get('action') # 'save_draft', 'submit_item_next', 'submit_final_batch'

        # Check if any performance in this batch is on HOLD or STOPPED
        active_perfs = Performance.objects.filter(batch=active_batch) if active_batch else []
        held_list = [p for p in active_perfs if p.status == PerformanceStatus.HELD]
        stopped_list = [p for p in active_perfs if p.status == PerformanceStatus.STOPPED_PENDING_DECISION]

        if held_list:
            messages.warning(request, "Performance is currently ON HOLD by Stage Manager. Scoring actions are temporarily paused.")
            return redirect(request.get_full_path())

        if stopped_list:
            messages.error(request, "Performance has been STOPPED by Stage Manager. Scoring desks are locked pending official decision.")
            return redirect(request.get_full_path())

        for e in entries:
            sub_id = request.POST.get(f'submission_{e.id}')
            sub = None
            if sub_id:
                sub = JudgeSubmission.objects.filter(id=sub_id).first()
            if not sub:
                perf = Performance.objects.filter(event_entry=e, round_type=current_panel.round_type).first()
                if perf:
                    sub = JudgeSubmission.objects.filter(performance=perf, judge_role=judge_role, version=1).first()
            if not sub:
                continue
            if request.user.is_authenticated and sub.judge != request.user and not sub.is_locked:
                sub.judge = request.user
                sub.save(update_fields=['judge'])
            item, _ = AsanaScoreItem.objects.get_or_create(submission=sub, sequence_order=current_asana_seq)

            # Judges can navigate back and edit any asana until the entire batch is completed
            pass

            raw_p = request.POST.get(f'posture_{e.id}', '0.0')
            raw_s = request.POST.get(f'stability_{e.id}', '0.0')
            try:
                p_val = min(Decimal('6.00'), max(Decimal('0.00'), Decimal(raw_p)))
            except:
                p_val = Decimal('0.00')
            try:
                s_val = min(Decimal('2.00'), max(Decimal('0.00'), Decimal(raw_s)))
            except:
                s_val = Decimal('0.00')

            eval_item = AsanaScoreItem.objects.filter(
                submission__performance=sub.performance,
                submission__judge_role='EVALUATOR',
                sequence_order=current_asana_seq
            ).first()
            is_eval_invalid = bool(eval_item and not eval_item.is_valid)
            invalid_reason = eval_item.invalid_reason if is_eval_invalid else ""

            time_item = AsanaScoreItem.objects.filter(
                submission__performance=sub.performance,
                submission__judge_role='TIME_JUDGE',
                sequence_order=current_asana_seq
            ).first()
            t_val = time_item.timing_contribution if (time_item and time_item.timing_contribution is not None) else Decimal('2.00')

            pose_seq = AsanaSequenceItem.objects.filter(event_entry=e, sequence_order=current_asana_seq, round_type=round_type).first()
            if not pose_seq:
                pose_seq = AsanaSequenceItem.objects.filter(event_entry=e, sequence_order=current_asana_seq).first()
            is_compulsory, bv_val = resolve_asana_compulsory_and_bv(e, round_type, current_asana_seq, pose_seq)

            raw_calc = (p_val + s_val + t_val) * bv_val
            item.posture_score = p_val
            item.stability_score = s_val
            item.timing_contribution = t_val
            item.base_value = bv_val
            item.raw_item_score = raw_calc
            if is_eval_invalid:
                item.is_valid = False
                item.invalid_reason = invalid_reason or "Evaluator Confirmed Invalid Asana"
                item.effective_item_score = Decimal('0.000')
            elif t_val <= Decimal('0.00'):
                item.is_valid = True
                item.invalid_reason = "Hold < 75% (0.0s time): Score is 0.000"
                item.effective_item_score = Decimal('0.000')
            else:
                item.is_valid = True
                item.invalid_reason = ""
                item.effective_item_score = raw_calc

            if action in ['submit_item_next', 'submit_final_batch']:
                item.is_draft = False
                item.is_submitted = True
                item.is_locked = True
                item.submitted_at = timezone.now()
            else:
                item.is_draft = True
                item.is_submitted = False
                item.is_locked = False
            item.save()

        if action == 'save_draft':
            messages.info(request, f"Draft saved for Asana #{current_asana_seq}.")
            redirect_url = f"{request.path}?seq={current_asana_seq}"
            if request.GET.get('panel_id'):
                redirect_url += f"&panel_id={request.GET.get('panel_id')}"
            return redirect(redirect_url)

        elif action == 'submit_item_next':
            if current_asana_seq < total_items:
                next_seq = current_asana_seq + 1
                messages.success(request, f"Asana #{current_asana_seq} committed & locked. Opened Asana #{next_seq}.")
                redirect_url = f"{request.path}?seq={next_seq}"
                if request.GET.get('panel_id'):
                    redirect_url += f"&panel_id={request.GET.get('panel_id')}"
                return redirect(redirect_url)
            else:
                action = 'submit_final_batch'

        if action == 'submit_final_batch':
            for e in entries:
                sub_id = request.POST.get(f'submission_{e.id}')
                sub = None
                if sub_id:
                    sub = JudgeSubmission.objects.filter(id=sub_id).first()
                if not sub:
                    perf = Performance.objects.filter(event_entry=e, round_type=current_panel.round_type).first()
                    if perf:
                        sub = JudgeSubmission.objects.filter(performance=perf, judge_role=judge_role, version=1).first()
                if not sub:
                    continue
                if request.user.is_authenticated and sub.judge != request.user and not sub.is_locked:
                    sub.judge = request.user
                    sub.save(update_fields=['judge'])
                sub.items.all().update(is_draft=False, is_submitted=True, is_locked=True)
                total = sum(i.effective_item_score for i in sub.items.all())
                sub.raw_total = total
                sub.effective_total = total
                sub.is_draft = False
                sub.is_submitted = True
                sub.is_locked = True
                sub.submitted_at = timezone.now()
                sub.save()
                if sub.performance:
                    calculate_performance_score(sub.performance)

            messages.success(request, f"Official {seat_label} score sheet finalized and locked for all athletes in Batch #{batch.batch_number}!")
            return redirect(request.path)

    return render(request, 'scoring/traditional_d.html', {
        'panel': current_panel,
        'seat_label': seat_label,
        'batch': batch,
        'entries': entries,
        'current_seq': current_asana_seq,
        'total_items': total_items,
        'sequence_tabs': sequence_tabs,
        'submissions': submissions,
        'is_locked': is_current_seq_locked,
        'is_final': False,
    })

@role_required(RoleChoices.ADMIN, RoleChoices.D_JUDGE, RoleChoices.CD, 'APPROVED_JUDGE')
def scoring_traditional_d_final(request):
    """
    Implements 35_Traditional_Final_Multiple_Athletes.png:
    Traditional Final batch view (maximum 3 simultaneous athletes on stage).
    """
    current_panel, seat_label, batch_or_resp = get_judge_panel_and_batch(
        request,
        seat_fields=['d1_judge', 'd2_judge', 'd3_judge', 'd4_judge', 'd5_judge', 'chief_judge'],
        event_type='TRADITIONAL',
        round_type='FINAL'
    )
    if isinstance(batch_or_resp, HttpResponse):
        return batch_or_resp

    batch = batch_or_resp
    entries = batch.entries.all()[:3]
    total_items = 7
    current_asana_seq = int(request.GET.get('seq', 1))
    if current_asana_seq < 1:
        current_asana_seq = 1
    if current_asana_seq > total_items:
        current_asana_seq = total_items

    round_type = current_panel.round_type if current_panel else 'PRELIMINARY'
    judge_role = seat_label.replace(' ', '_').upper()
    if judge_role not in ['D1_JUDGE', 'D2_JUDGE', 'D3_JUDGE', 'D4_JUDGE', 'D5_JUDGE']:
        judge_role = 'D1_JUDGE'

    # Optimize DB queries: get or create Performance and JudgeSubmission once per entry
    entry_subs = {}
    for e in entries:
        perf, _ = Performance.objects.get_or_create(
            event_entry=e,
            round_type='FINAL',
            defaults={'batch': batch, 'status': PerformanceStatus.IN_PROGRESS}
        )
        if perf.batch != batch:
            perf.batch = batch
            perf.save(update_fields=['batch'])
        sub, _ = JudgeSubmission.objects.get_or_create(
            performance=perf,
            judge_role=judge_role,
            version=1,
            defaults={'judge': request.user, 'is_draft': True, 'raw_total': Decimal('0.00')}
        )
        entry_subs[e.id] = (perf, sub)

    # Bulk query locked items across all sequences for progress tabs
    sub_ids = [sub.id for perf, sub in entry_subs.values()]
    locked_items = set(AsanaScoreItem.objects.filter(
        submission_id__in=sub_ids,
        is_locked=True
    ).values_list('submission_id', 'sequence_order'))

    entry_count = len(entry_subs)
    sequence_tabs = []
    for seq_num in range(1, total_items + 1):
        seq_locked_count = sum(1 for sub_id in sub_ids if (sub_id, seq_num) in locked_items)
        sequence_tabs.append({
            'seq': seq_num,
            'is_current': seq_num == current_asana_seq,
            'is_locked': seq_locked_count == entry_count if entry_count else False,
            'is_partial': 0 < seq_locked_count < entry_count,
        })

    # Prepare submissions for current_asana_seq
    submissions = {}
    is_current_seq_locked = True if entries.exists() else False

    for e in entries:
        perf, sub = entry_subs[e.id]
        item, _ = AsanaScoreItem.objects.get_or_create(
            submission=sub,
            sequence_order=current_asana_seq,
            defaults={
                'posture_score': Decimal('0.00'),
                'stability_score': Decimal('0.00'),
                'timing_contribution': Decimal('0.00'),
                'effective_item_score': Decimal('0.00'),
                'is_draft': True,
                'is_locked': False
            }
        )
        if not item.is_locked:
            is_current_seq_locked = False

        pose_seq = AsanaSequenceItem.objects.filter(event_entry=e, sequence_order=current_asana_seq, round_type=round_type).first()
        if not pose_seq:
            pose_seq = AsanaSequenceItem.objects.filter(event_entry=e, sequence_order=current_asana_seq).first()
        is_compulsory, bv_val = resolve_asana_compulsory_and_bv(e, round_type, current_asana_seq, pose_seq)

        # Check Evaluator determination: Evaluator is the sole authority on asana validity
        eval_item = AsanaScoreItem.objects.filter(
            submission__performance=perf,
            submission__judge_role='EVALUATOR',
            sequence_order=current_asana_seq
        ).first()
        is_eval_invalid = bool(eval_item and not eval_item.is_valid)
        invalid_reason = eval_item.invalid_reason if is_eval_invalid else ""

        # Time judge timing contribution (from Annexure A/B)
        time_item = AsanaScoreItem.objects.filter(
            submission__performance=perf,
            submission__judge_role='TIME_JUDGE',
            sequence_order=current_asana_seq
        ).first()
        t_contrib = time_item.timing_contribution if (time_item and time_item.timing_contribution is not None) else Decimal('2.00')

        item.base_value = bv_val
        item.timing_contribution = t_contrib
        raw_calc = (item.posture_score + item.stability_score + t_contrib) * bv_val
        item.raw_item_score = raw_calc
        if is_eval_invalid:
            item.is_valid = False
            item.invalid_reason = invalid_reason or "Evaluator Confirmed Invalid Asana"
            item.effective_item_score = Decimal('0.000')
        elif t_contrib <= Decimal('0.00'):
            item.is_valid = True
            item.invalid_reason = "Hold < 75% (0.0s time): Score is 0.000"
            item.effective_item_score = Decimal('0.000')
        else:
            item.is_valid = True
            item.invalid_reason = ""
            item.effective_item_score = raw_calc
        item.save(update_fields=['base_value', 'timing_contribution', 'is_valid', 'invalid_reason', 'raw_item_score', 'effective_item_score'])

        submissions[e.id] = {
            'entry': e,
            'performance': perf,
            'submission': sub,
            'item': item,
            'pose': pose_seq,
            'declared_code': pose_seq.declared_code if pose_seq else "ASANA",
            'declared_name': pose_seq.declared_name if pose_seq else "Declared Asana",
            'image_url': (pose_seq.image_url if pose_seq and hasattr(pose_seq, 'image_url') and pose_seq.image_url else (pose_seq.declared_asana.image_url if pose_seq and getattr(pose_seq, 'declared_asana', None) and hasattr(pose_seq.declared_asana, 'image_url') else None)),
            'is_invalid': is_eval_invalid,
            'invalid_reason': invalid_reason,
            'timing_contribution': t_contrib,
            'is_compulsory': is_compulsory,
            'base_value': bv_val,
        }

    # Handle POST
    if request.method == 'POST':
        action = request.POST.get('action')

        for e in entries:
            sub_id = request.POST.get(f'submission_{e.id}')
            sub = None
            if sub_id:
                sub = JudgeSubmission.objects.filter(id=sub_id).first()
            if not sub:
                perf = Performance.objects.filter(event_entry=e, round_type=current_panel.round_type).first()
                if perf:
                    sub = JudgeSubmission.objects.filter(performance=perf, judge_role=judge_role, version=1).first()
            if not sub:
                continue
            if request.user.is_authenticated and sub.judge != request.user and not sub.is_locked:
                sub.judge = request.user
                sub.save(update_fields=['judge'])
            item, _ = AsanaScoreItem.objects.get_or_create(submission=sub, sequence_order=current_asana_seq)

            # Judges can navigate back and edit any asana until the entire batch is completed
            pass

            raw_p = request.POST.get(f'posture_{e.id}', '0.0')
            raw_s = request.POST.get(f'stability_{e.id}', '0.0')
            try:
                p_val = min(Decimal('6.00'), max(Decimal('0.00'), Decimal(raw_p)))
            except:
                p_val = Decimal('0.00')
            try:
                s_val = min(Decimal('2.00'), max(Decimal('0.00'), Decimal(raw_s)))
            except:
                s_val = Decimal('0.00')

            eval_item = AsanaScoreItem.objects.filter(
                submission__performance=sub.performance,
                submission__judge_role='EVALUATOR',
                sequence_order=current_asana_seq
            ).first()
            is_eval_invalid = bool(eval_item and not eval_item.is_valid)
            invalid_reason = eval_item.invalid_reason if is_eval_invalid else ""

            time_item = AsanaScoreItem.objects.filter(
                submission__performance=sub.performance,
                submission__judge_role='TIME_JUDGE',
                sequence_order=current_asana_seq
            ).first()
            t_val = time_item.timing_contribution if (time_item and time_item.timing_contribution is not None) else Decimal('2.00')

            pose_seq = AsanaSequenceItem.objects.filter(event_entry=e, sequence_order=current_asana_seq, round_type=round_type).first()
            if not pose_seq:
                pose_seq = AsanaSequenceItem.objects.filter(event_entry=e, sequence_order=current_asana_seq).first()
            is_compulsory, bv_val = resolve_asana_compulsory_and_bv(e, round_type, current_asana_seq, pose_seq)

            raw_calc = (p_val + s_val + t_val) * bv_val
            item.posture_score = p_val
            item.stability_score = s_val
            item.timing_contribution = t_val
            item.base_value = bv_val
            item.raw_item_score = raw_calc
            if is_eval_invalid:
                item.is_valid = False
                item.invalid_reason = invalid_reason or "Evaluator Confirmed Invalid Asana"
                item.effective_item_score = Decimal('0.000')
            elif t_val <= Decimal('0.00'):
                item.is_valid = True
                item.invalid_reason = "Hold < 75% (0.0s time): Score is 0.000"
                item.effective_item_score = Decimal('0.000')
            else:
                item.is_valid = True
                item.invalid_reason = ""
                item.effective_item_score = raw_calc

            if action in ['submit_item_next', 'submit_final_batch']:
                item.is_draft = False
                item.is_submitted = True
                item.is_locked = True
                item.submitted_at = timezone.now()
            else:
                item.is_draft = True
                item.is_submitted = False
                item.is_locked = False
            item.save()

        if action == 'save_draft':
            messages.info(request, f"Draft saved for Final Asana #{current_asana_seq}.")
            redirect_url = f"{request.path}?seq={current_asana_seq}"
            if request.GET.get('panel_id'):
                redirect_url += f"&panel_id={request.GET.get('panel_id')}"
            return redirect(redirect_url)

        elif action == 'submit_item_next':
            if current_asana_seq < total_items:
                next_seq = current_asana_seq + 1
                messages.success(request, f"Final Asana #{current_asana_seq} committed & locked. Opened Asana #{next_seq}.")
                redirect_url = f"{request.path}?seq={next_seq}"
                if request.GET.get('panel_id'):
                    redirect_url += f"&panel_id={request.GET.get('panel_id')}"
                return redirect(redirect_url)
            else:
                action = 'submit_final_batch'

        if action == 'submit_final_batch':
            for e in entries:
                sub_id = request.POST.get(f'submission_{e.id}')
                sub = None
                if sub_id:
                    sub = JudgeSubmission.objects.filter(id=sub_id).first()
                if not sub:
                    perf = Performance.objects.filter(event_entry=e, round_type=current_panel.round_type).first()
                    if perf:
                        sub = JudgeSubmission.objects.filter(performance=perf, judge_role=judge_role, version=1).first()
                if not sub:
                    continue
                if request.user.is_authenticated and sub.judge != request.user and not sub.is_locked:
                    sub.judge = request.user
                    sub.save(update_fields=['judge'])
                sub.items.all().update(is_draft=False, is_submitted=True, is_locked=True)
                total = sum(i.effective_item_score for i in sub.items.all())
                sub.raw_total = total
                sub.effective_total = total
                sub.is_draft = False
                sub.is_submitted = True
                sub.is_locked = True
                sub.submitted_at = timezone.now()
                sub.save()
                if sub.performance:
                    calculate_performance_score(sub.performance)

            messages.success(request, f"Traditional Final {seat_label} score sheet finalized and locked for all athletes in Batch #{batch.batch_number}!")
            return redirect(request.path)

    return render(request, 'scoring/traditional_d.html', {
        'panel': current_panel,
        'seat_label': seat_label,
        'batch': batch,
        'entries': entries,
        'current_seq': current_asana_seq,
        'total_items': total_items,
        'sequence_tabs': sequence_tabs,
        'submissions': submissions,
        'is_locked': is_current_seq_locked,
        'is_final': True,
    })

@role_required(RoleChoices.ADMIN, RoleChoices.D_JUDGE, RoleChoices.CD, 'APPROVED_JUDGE')
def scoring_artistic_single_d(request):
    """
    Implements Artistic Single D Judge Scoring Interface:
    Simultaneously displays all 10 asanas: 1 large active asana and 9 compact preview cards.
    Supports seamless instant navigation between all 10 asanas without page reload.
    Supports real-time execution score editing with auto-save and immediate effective score update.
    """
    import json
    perf_id = request.GET.get('perf_id')
    current_panel = None
    seat_label = "D1 Judge"
    batch = None
    
    # 1. Resolve panel & active batch if perf_id not explicitly given
    if perf_id:
        perf = get_object_or_404(Performance, id=perf_id)
        entry = perf.event_entry
        round_type = perf.round_type
        batch = perf.batch
        current_panel = batch.panel if batch else None
        
        # Verify user permission for this performance's panel
        is_admin_or_cd = is_system_admin(request.user) or ScopedRole.objects.filter(
            user=request.user, championship=get_active_championship(request), role=RoleChoices.CD
        ).exists()
        if not is_admin_or_cd:
            user_seats = get_user_assigned_seats(request.user, championship=perf.event_entry.registration.championship if hasattr(perf.event_entry, 'registration') else None)
            matched = next((s for s in user_seats if current_panel and s['panel'].id == current_panel.id and s['role_group'] == 'D_JUDGE'), None)
            if not matched:
                AuditLog.log(
                    action='SECURITY_UNAUTHORIZED_PERF_ACCESS',
                    resource_type='Performance',
                    resource_id=perf_id,
                    details={'path': request.path, 'user_id': request.user.id},
                    user=request.user,
                    ip_address=request.META.get('REMOTE_ADDR')
                )
                return render(request, '403.html', {'message': "Access Denied: You are not authorized to score this performance."}, status=403)
            seat_label = matched['seat_label']
    else:
        current_panel, seat_label, batch_or_resp = get_judge_panel_and_batch(
            request,
            seat_fields=['d1_judge', 'd2_judge', 'd3_judge', 'd4_judge', 'd5_judge', 'chief_judge'],
            event_type='ARTISTIC_SINGLE'
        )
        if isinstance(batch_or_resp, HttpResponse):
            return batch_or_resp

        batch = batch_or_resp
        entry_id = request.GET.get('entry_id')
        if entry_id and batch and batch.entries.filter(id=entry_id).exists():
            entry = batch.entries.filter(id=entry_id).first()
        elif batch and batch.entries.exists():
            entry = batch.entries.first()
        else:
            entry = EventEntry.objects.filter(event_config__event_type__icontains='ARTISTIC_SINGLE').first()

        round_type = current_panel.round_type if current_panel else 'PRELIMINARY'
        perf, _ = Performance.objects.get_or_create(
            event_entry=entry,
            round_type=round_type,
            defaults={'batch': batch, 'status': PerformanceStatus.IN_PROGRESS}
        ) if entry else (None, False)

    if not perf or not entry:
        messages.error(request, "No Artistic Single competitor found for this scoring session.")
        return render(request, 'scoring/artistic_single_d.html', {'error': 'No competitor found'})

    round_type = perf.round_type
    
    # Determine judge role strictly from authenticated user's assignment
    is_admin_or_cd = is_system_admin(request.user) or ScopedRole.objects.filter(
        user=request.user, championship=get_active_championship(request), role=RoleChoices.CD
    ).exists()
    
    if not is_admin_or_cd and current_panel:
        user_seat = next((s for s in get_user_assigned_seats(request.user, is_active_only=True) if s['panel'].id == current_panel.id and s['role_group'] == 'D_JUDGE'), None)
        if user_seat:
            judge_role = user_seat['seat_code']
            seat_label = user_seat['seat_label']
        else:
            return render(request, '403.html', {'message': "Access Denied: You do not hold an assigned D Judge seat on this panel."}, status=403)
    else:
        judge_role = seat_label.replace(' ', '_').upper() if seat_label else 'D1_JUDGE'
        if judge_role not in ['D1_JUDGE', 'D2_JUDGE', 'D3_JUDGE', 'D4_JUDGE', 'D5_JUDGE']:
            judge_role = 'D1_JUDGE'

    sub, _ = JudgeSubmission.objects.get_or_create(
        performance=perf,
        judge_role=judge_role,
        version=1,
        defaults={'judge': request.user, 'is_draft': True, 'raw_total': Decimal('0.000'), 'effective_total': Decimal('0.000')}
    )
    if request.user.is_authenticated and (is_admin_or_cd or sub.judge == request.user):
        if sub.judge != request.user and not sub.is_locked and not is_admin_or_cd:
            sub.judge = request.user
            sub.save(update_fields=['judge'])
    elif not is_admin_or_cd and sub.judge != request.user:
        AuditLog.log(
            action='SECURITY_WORKSPACE_TAMPER',
            resource_type='JudgeSubmission',
            resource_id=sub.id,
            details={'user_id': request.user.id, 'owner_id': sub.judge_id if sub.judge else None},
            user=request.user,
            ip_address=request.META.get('REMOTE_ADDR')
        )
        return render(request, '403.html', {'message': "Access Denied: Cannot access another judge's workspace."}, status=403)

    total_items = 10
    current_seq = int(request.GET.get('seq', 1))
    if current_seq < 1:
        current_seq = 1
    if current_seq > total_items:
        current_seq = total_items

    # Fetch Evaluator validity mappings for this athlete
    eval_items_map = {}
    eval_sub = JudgeSubmission.objects.filter(performance=perf, judge_role='EVALUATOR').first()
    if eval_sub:
        for itm in eval_sub.items.all():
            eval_items_map[itm.sequence_order] = itm

    # Ensure all 10 AsanaScoreItem records exist with correct base values and validity
    all_score_items = {}
    for item in sub.items.all():
        all_score_items[item.sequence_order] = item

    items_data = []
    for seq in range(1, total_items + 1):
        pose_seq = AsanaSequenceItem.objects.filter(event_entry=entry, sequence_order=seq, round_type=round_type).first()
        if not pose_seq:
            pose_seq = AsanaSequenceItem.objects.filter(event_entry=entry, sequence_order=seq).first()

        declared_bv = pose_seq.declared_bv if pose_seq and pose_seq.declared_bv else Decimal('0.80')
        declared_code = pose_seq.declared_code if pose_seq and pose_seq.declared_code else f"ASANA-{seq:02d}"

        # Image URL resolution
        img_url = None
        if pose_seq and pose_seq.image_url:
            img_url = pose_seq.image_url
        elif pose_seq and pose_seq.declared_asana and pose_seq.declared_asana.image_url:
            img_url = pose_seq.declared_asana.image_url

        eval_item = eval_items_map.get(seq)
        is_valid = eval_item.is_valid if eval_item else True
        invalid_reason = eval_item.invalid_reason if eval_item and not eval_item.is_valid else ""

        score_item = all_score_items.get(seq)
        if not score_item:
            score_item = AsanaScoreItem.objects.create(
                submission=sub,
                sequence_order=seq,
                execution_score=Decimal('0.00'),
                base_value=declared_bv,
                is_valid=is_valid,
                invalid_reason=invalid_reason,
                raw_item_score=Decimal('0.000'),
                effective_item_score=Decimal('0.000'),
                is_zero=False,
                is_draft=True,
                is_locked=False
            )
            all_score_items[seq] = score_item
        else:
            # Sync base_value and validity if changed by evaluator
            update_fields = []
            if score_item.base_value != declared_bv:
                score_item.base_value = declared_bv
                update_fields.append('base_value')
            if score_item.is_valid != is_valid or score_item.invalid_reason != invalid_reason:
                score_item.is_valid = is_valid
                score_item.invalid_reason = invalid_reason
                update_fields.extend(['is_valid', 'invalid_reason'])
            
            # Recalculate effective_item_score based on current validity
            eff = Decimal('0.000') if not is_valid else round(score_item.execution_score * score_item.base_value, 3)
            raw = round(score_item.execution_score * score_item.base_value, 3)
            if score_item.raw_item_score != raw:
                score_item.raw_item_score = raw
                update_fields.append('raw_item_score')
            if score_item.effective_item_score != eff:
                score_item.effective_item_score = eff
                update_fields.append('effective_item_score')
            if update_fields:
                score_item.save(update_fields=update_fields)

        # Scored definition: has explicit positive score, explicit recorded zero (is_zero=True), or is locked
        is_completed = (score_item.execution_score > Decimal('0.00') or score_item.is_zero or score_item.is_locked)

        items_data.append({
            'seq': seq,
            'pose': pose_seq,
            'code': declared_code,
            'image_url': img_url,
            'score_item': score_item,
            'is_valid': is_valid,
            'invalid_reason': invalid_reason,
            'is_completed': is_completed,
            'is_zero': score_item.is_zero,
        })

    # Prepare JSON serializable list of all 10 asanas for client-side instant navigation & dynamic state
    asanas_json_list = []
    for d in items_data:
        asanas_json_list.append({
            'seq': d['seq'],
            'code': d['code'],
            'bv': float(d['score_item'].base_value),
            'exec': float(d['score_item'].execution_score),
            'eff': float(d['score_item'].effective_item_score),
            'raw': float(d['score_item'].raw_item_score),
            'is_valid': d['is_valid'],
            'invalid_reason': d['invalid_reason'],
            'is_completed': d['is_completed'],
            'is_zero': d['score_item'].is_zero,
            'is_locked': d['score_item'].is_locked or sub.is_locked,
            'image_url': d['image_url'] or '',
        })
    asanas_json = json.dumps(asanas_json_list)

    # Current item under scoring
    current_item = items_data[current_seq - 1]

    # Handle POST
    if request.method == 'POST' and not sub.is_locked:
        is_ajax = (request.headers.get('x-requested-with') == 'XMLHttpRequest') or (request.POST.get('is_ajax') == '1')
        action = request.POST.get('action') # 'save_draft', 'save_item', 'navigate_prev', 'navigate_next', 'navigate_to', 'submit_sheet'

        if perf and perf.status == PerformanceStatus.HELD:
            msg = "Performance is currently ON HOLD by Stage Manager. Scoring actions are temporarily paused."
            if is_ajax:
                return JsonResponse({'status': 'error', 'message': msg}, status=403)
            messages.warning(request, msg)
            return redirect(request.get_full_path())

        if perf and perf.status == PerformanceStatus.STOPPED_PENDING_DECISION:
            msg = "Performance has been STOPPED by Stage Manager. Scoring desks are locked pending official decision."
            if is_ajax:
                return JsonResponse({'status': 'error', 'message': msg}, status=403)
            messages.error(request, msg)
            return redirect(request.get_full_path())
        target_seq = request.POST.get('target_seq')

        # Check if saving a specific seq via AJAX (item_seq + execution_score)
        item_seq = request.POST.get('item_seq')
        if item_seq:
            try:
                seq_idx = int(item_seq)
            except (ValueError, TypeError):
                seq_idx = current_seq
        else:
            seq_idx = current_seq

        current_raw_exec = request.POST.get('execution_score')
        client_timestamp = request.POST.get('client_timestamp')
        if current_raw_exec is not None:
            try:
                e_val = min(Decimal('10.00'), max(Decimal('0.00'), Decimal(current_raw_exec)))
            except Exception:
                target_score_item = all_score_items.get(seq_idx)
                e_val = target_score_item.execution_score if target_score_item else Decimal('0.00')
            
            c_item = all_score_items.get(seq_idx)
            if c_item:
                c_bv = c_item.base_value
                c_valid = c_item.is_valid
                eff = Decimal('0.000') if not c_valid else round(e_val * c_bv, 3)
                c_item.execution_score = e_val
                c_item.raw_item_score = round(e_val * c_bv, 3)
                c_item.effective_item_score = eff
                # Record explicit zero flag if 0.00 is purposefully passed
                c_item.is_zero = (e_val == Decimal('0.00') and current_raw_exec.strip() != '')
                c_item.is_draft = True
                c_item.save(update_fields=['execution_score', 'raw_item_score', 'effective_item_score', 'is_zero', 'is_draft'])

        # Also support saving all 10 asanas if submitted in batch form
        for seq in range(1, total_items + 1):
            seq_val = request.POST.get(f'exec_{seq}')
            if seq_val is not None and seq_val.strip() != '':
                try:
                    e_val = min(Decimal('10.00'), max(Decimal('0.00'), Decimal(seq_val)))
                except Exception:
                    continue
                s_item = all_score_items.get(seq)
                if s_item:
                    eff = Decimal('0.000') if not s_item.is_valid else round(e_val * s_item.base_value, 3)
                    s_item.execution_score = e_val
                    s_item.raw_item_score = round(e_val * s_item.base_value, 3)
                    s_item.effective_item_score = eff
                    s_item.is_zero = (e_val == Decimal('0.00'))
                    s_item.save(update_fields=['execution_score', 'raw_item_score', 'effective_item_score', 'is_zero'])

        # Recompute submission total
        total_eff = sum(it.effective_item_score for it in sub.items.all())
        total_raw = sum(it.raw_item_score for it in sub.items.all())
        sub.raw_total = total_raw
        sub.effective_total = total_eff
        sub.save(update_fields=['raw_total', 'effective_total'])

        # If AJAX request, return JsonResponse immediately without page reload
        if is_ajax:
            if action == 'submit_sheet':
                sub.items.all().update(is_draft=False, is_submitted=True, is_locked=True, submitted_at=timezone.now())
                sub.is_draft = False
                sub.is_submitted = True
                sub.is_locked = True
                sub.submitted_at = timezone.now()
                sub.save()
                if sub.performance:
                    calculate_performance_score(sub.performance)
                return JsonResponse({
                    'status': 'success',
                    'action': 'submit_sheet',
                    'message': f"Artistic Single D scores submitted and locked! Total: {total_eff:.3f}",
                    'total_effective': float(total_eff),
                    'is_locked': True,
                })
            else:
                saved_item = all_score_items.get(seq_idx)
                completed_count_now = sum(1 for it in sub.items.all() if it.execution_score > Decimal('0.00') or it.is_zero or it.is_locked)
                return JsonResponse({
                    'status': 'success',
                    'action': action or 'save_item',
                    'seq': seq_idx,
                    'execution_score': float(saved_item.execution_score) if saved_item else 0.0,
                    'effective_item_score': float(saved_item.effective_item_score) if saved_item else 0.0,
                    'is_completed': (saved_item.execution_score > Decimal('0.00') or saved_item.is_zero or saved_item.is_locked) if saved_item else False,
                    'is_zero': saved_item.is_zero if saved_item else False,
                    'total_effective': float(total_eff),
                    'completed_count': completed_count_now,
                    'client_timestamp': client_timestamp,
                })

        # Standard non-AJAX POST navigation / submission handling
        def make_url(seq_no):
            base = f"{request.path}?seq={seq_no}"
            if request.GET.get('perf_id'):
                base += f"&perf_id={request.GET.get('perf_id')}"
            if request.GET.get('panel_id'):
                base += f"&panel_id={request.GET.get('panel_id')}"
            if request.GET.get('entry_id'):
                base += f"&entry_id={request.GET.get('entry_id')}"
            return base

        if action == 'save_draft':
            sub.is_draft = True
            sub.save(update_fields=['raw_total', 'effective_total', 'is_draft'])
            messages.info(request, f"Asana #{seq_idx} marks saved. Total: {total_eff:.3f}")
            return redirect(make_url(seq_idx))

        elif action == 'navigate_prev':
            sub.save(update_fields=['raw_total', 'effective_total'])
            prev_seq = max(1, current_seq - 1)
            return redirect(make_url(prev_seq))

        elif action == 'navigate_next':
            sub.save(update_fields=['raw_total', 'effective_total'])
            next_seq = min(total_items, current_seq + 1)
            return redirect(make_url(next_seq))

        elif action == 'navigate_to':
            sub.save(update_fields=['raw_total', 'effective_total'])
            try:
                dest = int(target_seq)
                dest = max(1, min(total_items, dest))
            except (ValueError, TypeError):
                dest = current_seq
            return redirect(make_url(dest))

        elif action == 'submit_sheet':
            sub.items.all().update(is_draft=False, is_submitted=True, is_locked=True, submitted_at=timezone.now())
            sub.is_draft = False
            sub.is_submitted = True
            sub.is_locked = True
            sub.submitted_at = timezone.now()
            sub.save()
            if sub.performance:
                calculate_performance_score(sub.performance)
            messages.success(request, f"Artistic Single D scores submitted and locked! Total: {total_eff:.3f}")
            return redirect(make_url(current_seq))

    # Calculate overall total score
    total_effective = sum(d['score_item'].effective_item_score for d in items_data)
    completed_count = sum(1 for d in items_data if d['is_completed'])

    return render(request, 'scoring/artistic_single_d.html', {
        'panel': current_panel,
        'seat_label': seat_label,
        'batch': batch,
        'entry': entry,
        'perf': perf,
        'sub': sub,
        'current_seq': current_seq,
        'total_items': total_items,
        'items_data': items_data,
        'asanas_json': asanas_json,
        'current_item': current_item,
        'total_effective': total_effective,
        'completed_count': completed_count,
        'is_locked': sub.is_locked,
    })


@role_required(RoleChoices.ADMIN, RoleChoices.D_JUDGE, RoleChoices.CD, 'APPROVED_JUDGE')
def scoring_artistic_pair_d(request):
    """
    Implements 19_Artistic_Pair_D_Judge.png:
    Minimal pair desk: Chest A submitted pose, Chest B submitted pose, pair formation,
    two large execution fields and formation execution field. Ten-item navigation.
    """
    perf_id = request.GET.get('perf_id')
    current_seq = int(request.GET.get('seq', 1))

    current_panel, seat_label, batch_or_resp = get_judge_panel_and_batch(
        request,
        seat_fields=['d1_judge', 'd2_judge', 'd3_judge', 'd4_judge', 'd5_judge', 'chief_judge'],
        event_type='ARTISTIC_PAIR'
    )
    if isinstance(batch_or_resp, HttpResponse):
        return batch_or_resp

    batch = batch_or_resp

    if perf_id:
        perf = get_object_or_404(Performance, id=perf_id)
        entry = perf.event_entry
    else:
        entry = batch.entries.filter(is_pair=True).first() if batch else EventEntry.objects.filter(is_pair=True).first()
        perf, _ = Performance.objects.get_or_create(
            event_entry=entry,
            round_type=current_panel.round_type if current_panel else 'FINAL',
            defaults={'batch': batch, 'status': PerformanceStatus.IN_PROGRESS}
        ) if entry else (None, False)

    is_admin_or_cd = is_system_admin(request.user) or ScopedRole.objects.filter(
        user=request.user, championship=get_active_championship(request), role=RoleChoices.CD
    ).exists()

    if not is_admin_or_cd and current_panel:
        user_seat = next((s for s in get_user_assigned_seats(request.user, is_active_only=True) if s['panel'].id == current_panel.id and s['role_group'] == 'D_JUDGE'), None)
        if user_seat:
            judge_role = user_seat['seat_code']
        else:
            return render(request, '403.html', {'message': "Access Denied: You do not hold an assigned D Judge seat on this panel."}, status=403)
    else:
        judge_role = seat_label.replace(' ', '_').upper() if seat_label else 'D1_JUDGE'
        if judge_role not in ['D1_JUDGE', 'D2_JUDGE', 'D3_JUDGE', 'D4_JUDGE', 'D5_JUDGE']:
            judge_role = 'D1_JUDGE'

    sub, _ = JudgeSubmission.objects.get_or_create(
        performance=perf,
        judge_role=judge_role,
        version=1,
        defaults={'judge': request.user, 'is_draft': True, 'raw_total': Decimal('0.000'), 'effective_total': Decimal('0.000')}
    )
    if request.user.is_authenticated and (is_admin_or_cd or sub.judge == request.user):
        if sub.judge != request.user and not sub.is_locked and not is_admin_or_cd:
            sub.judge = request.user
            sub.save(update_fields=['judge'])
    elif not is_admin_or_cd and sub.judge != request.user:
        AuditLog.log(
            action='SECURITY_WORKSPACE_TAMPER',
            resource_type='JudgeSubmission',
            resource_id=sub.id,
            details={'user_id': request.user.id, 'owner_id': sub.judge_id if sub.judge else None},
            user=request.user,
            ip_address=request.META.get('REMOTE_ADDR')
        )
        return render(request, '403.html', {'message': "Access Denied: Cannot access another judge's workspace."}, status=403)

    score_item = None
    pose_a = None
    pose_b = None
    if sub and entry:
        score_item, _ = AsanaScoreItem.objects.get_or_create(
            submission=sub,
            sequence_order=current_seq,
            defaults={
                'chest_a_execution': Decimal('8.00'),
                'chest_b_execution': Decimal('7.00'),
                'formation_score': Decimal('8.00'),
                'effective_item_score': Decimal('7.667')
            }
        )
        pose_a = AsanaSequenceItem.objects.filter(event_entry=entry, sequence_order=current_seq).first()
        pose_b = pose_a

    if request.method == 'POST' and sub:
        action = request.POST.get('action')
        a_exec = Decimal(request.POST.get('chest_a_exec', '8.00'))
        b_exec = Decimal(request.POST.get('chest_b_exec', '7.00'))
        form_exec = Decimal(request.POST.get('formation_exec', '8.00'))
        eff = round(((a_exec + b_exec) / Decimal('2.0') + form_exec) / Decimal('2.0'), 3)

        AsanaScoreItem.objects.update_or_create(
            submission=sub,
            sequence_order=current_seq,
            defaults={
                'chest_a_execution': a_exec,
                'chest_b_execution': b_exec,
                'formation_score': form_exec,
                'effective_item_score': eff
            }
        )

        if action == 'submit_sheet':
            total = sum(item.effective_item_score for item in sub.items.all())
            sub.raw_total = total
            sub.effective_total = total
            sub.is_draft = False
            sub.is_submitted = True
            sub.is_locked = True
            sub.submitted_at = datetime.datetime.now()
            sub.save()
            if sub.performance:
                calculate_performance_score(sub.performance)
            messages.success(request, f"Artistic Pair D marks submitted and locked! Total: {total:.3f}")
            return redirect(request.path)
        else:
            messages.success(request, f"Artistic Pair marks saved for Item #{current_seq}.")
            next_seq = min(10, current_seq + 1)
            return redirect(f"/scoring/artistic-pair-d/?seq={next_seq}&perf_id={perf.id if perf else ''}")

    return render(request, 'scoring/artistic_pair_d.html', {
        'batch': batch,
        'entry': entry,
        'perf': perf,
        'sub': sub,
        'current_seq': current_seq,
        'score_item': score_item,
        'pose_a': pose_a,
        'pose_b': pose_b,
    })

@role_required(RoleChoices.ADMIN, RoleChoices.D_JUDGE, RoleChoices.CD, 'APPROVED_JUDGE')
def scoring_rhythmic_pair_d(request):
    """
    Implements 20_Rhythmic_Pair_D_Judge.png:
    Ten-item rhythmic pair scoring with shared BV calculation and compact result.
    """
    perf_id = request.GET.get('perf_id')
    current_seq = int(request.GET.get('seq', 1))

    current_panel, seat_label, batch_or_resp = get_judge_panel_and_batch(
        request,
        seat_fields=['d1_judge', 'd2_judge', 'd3_judge', 'd4_judge', 'd5_judge', 'chief_judge'],
        event_type='RHYTHMIC_PAIR'
    )
    if isinstance(batch_or_resp, HttpResponse):
        return batch_or_resp

    batch = batch_or_resp

    if perf_id:
        perf = get_object_or_404(Performance, id=perf_id)
        entry = perf.event_entry
    else:
        entry = batch.entries.filter(is_pair=True).first() if batch else EventEntry.objects.filter(is_pair=True).first()
        perf, _ = Performance.objects.get_or_create(
            event_entry=entry,
            round_type=current_panel.round_type if current_panel else 'FINAL',
            defaults={'batch': batch, 'status': PerformanceStatus.IN_PROGRESS}
        ) if entry else (None, False)

    is_admin_or_cd = is_system_admin(request.user) or ScopedRole.objects.filter(
        user=request.user, championship=get_active_championship(request), role=RoleChoices.CD
    ).exists()

    if not is_admin_or_cd and current_panel:
        user_seat = next((s for s in get_user_assigned_seats(request.user, is_active_only=True) if s['panel'].id == current_panel.id and s['role_group'] == 'D_JUDGE'), None)
        if user_seat:
            judge_role = user_seat['seat_code']
        else:
            return render(request, '403.html', {'message': "Access Denied: You do not hold an assigned D Judge seat on this panel."}, status=403)
    else:
        judge_role = seat_label.replace(' ', '_').upper() if seat_label else 'D1_JUDGE'
        if judge_role not in ['D1_JUDGE', 'D2_JUDGE', 'D3_JUDGE', 'D4_JUDGE', 'D5_JUDGE']:
            judge_role = 'D1_JUDGE'

    sub, _ = JudgeSubmission.objects.get_or_create(
        performance=perf,
        judge_role=judge_role,
        version=1,
        defaults={'judge': request.user, 'is_draft': True, 'raw_total': Decimal('0.000'), 'effective_total': Decimal('0.000')}
    )
    if request.user.is_authenticated and (is_admin_or_cd or sub.judge == request.user):
        if sub.judge != request.user and not sub.is_locked and not is_admin_or_cd:
            sub.judge = request.user
            sub.save(update_fields=['judge'])
    elif not is_admin_or_cd and sub.judge != request.user:
        AuditLog.log(
            action='SECURITY_WORKSPACE_TAMPER',
            resource_type='JudgeSubmission',
            resource_id=sub.id,
            details={'user_id': request.user.id, 'owner_id': sub.judge_id if sub.judge else None},
            user=request.user,
            ip_address=request.META.get('REMOTE_ADDR')
        )
        return render(request, '403.html', {'message': "Access Denied: Cannot access another judge's workspace."}, status=403)

    score_item = None
    pose = None
    if sub and entry:
        pose = AsanaSequenceItem.objects.filter(event_entry=entry, sequence_order=current_seq).first()
        score_item, _ = AsanaScoreItem.objects.get_or_create(
            submission=sub,
            sequence_order=current_seq,
            defaults={
                'execution_score': Decimal('8.00'),
                'base_value': pose.declared_bv if pose else Decimal('0.80'),
                'formation_score': Decimal('7.50'),
                'effective_item_score': Decimal('6.400')
            }
        )

    if request.method == 'POST' and sub:
        action = request.POST.get('action')
        exec_val = Decimal(request.POST.get('exec_score', '8.00'))
        bv_val = Decimal(request.POST.get('bv_score', '0.80'))
        eff = round(exec_val * bv_val, 3)

        AsanaScoreItem.objects.update_or_create(
            submission=sub,
            sequence_order=current_seq,
            defaults={'execution_score': exec_val, 'base_value': bv_val, 'effective_item_score': eff}
        )

        if action == 'submit_sheet':
            total = sum(item.effective_item_score for item in sub.items.all())
            sub.raw_total = total
            sub.effective_total = total
            sub.is_draft = False
            sub.is_submitted = True
            sub.is_locked = True
            sub.submitted_at = datetime.datetime.now()
            sub.save()
            if sub.performance:
                calculate_performance_score(sub.performance)
            messages.success(request, f"Rhythmic Pair D marks submitted and locked! Total: {total:.3f}")
            return redirect(request.path)
        else:
            messages.success(request, f"Rhythmic Pair marks saved for Item #{current_seq}.")
            next_seq = min(10, current_seq + 1)
            return redirect(f"/scoring/rhythmic-pair-d/?seq={next_seq}&perf_id={perf.id if perf else ''}")

    return render(request, 'scoring/rhythmic_pair_d.html', {
        'batch': batch,
        'entry': entry,
        'perf': perf,
        'sub': sub,
        'current_seq': current_seq,
        'score_item': score_item,
        'pose': pose,
    })

@role_required(RoleChoices.ADMIN, RoleChoices.TIME_JUDGE, RoleChoices.CD, 'APPROVED_JUDGE')
def scoring_traditional_time(request):
    """
    Implements 33_Traditional_Time_Judge.png:
    Independent batch timers (up to 5 athletes earlier rounds, 3 in final),
    with 7-item timing log, manual entry, attempts, and COP Annexure A & B timing tables.
    """
    current_panel, seat_label, batch_or_resp = get_judge_panel_and_batch(
        request,
        seat_fields=['time_judge', 'time_judge_2'],
        event_type='TRADITIONAL',
        judge_role='TIME_JUDGE'
    )
    if isinstance(batch_or_resp, HttpResponse):
        return batch_or_resp

    batch = batch_or_resp
    entries = list(batch.entries.all()[:5])
    try:
        current_seq = int(request.GET.get('seq', 1))
    except:
        current_seq = 1
    current_seq = max(1, min(7, current_seq))

    round_type = batch.panel.round_type if (batch and batch.panel) else 'PRELIMINARY'
    comp_count = get_traditional_compulsory_count(round_type)
    is_compulsory = (current_seq <= comp_count)
    target_hold = get_traditional_target_hold(round_type, is_compulsory)

    submissions = {}
    entry_subs = {}
    for e in entries:
        perf, _ = Performance.objects.get_or_create(
            event_entry=e,
            round_type=round_type,
            defaults={'batch': batch, 'status': PerformanceStatus.IN_PROGRESS}
        )
        sub, _ = JudgeSubmission.objects.get_or_create(
            performance=perf,
            judge_role='TIME_JUDGE',
            version=1,
            defaults={'judge': request.user, 'is_draft': True}
        )
        entry_subs[e.id] = (perf, sub)
        pose_seq = AsanaSequenceItem.objects.filter(event_entry=e, sequence_order=current_seq, round_type=round_type).first()
        if not pose_seq:
            pose_seq = AsanaSequenceItem.objects.filter(event_entry=e, sequence_order=current_seq).first()
        is_comp, bv_val = resolve_asana_compulsory_and_bv(e, round_type, current_seq, pose_seq)
        entry_target_hold = get_traditional_target_hold(round_type, is_comp)
        item, _ = AsanaScoreItem.objects.get_or_create(
            submission=sub,
            sequence_order=current_seq,
            defaults={'hold_duration_seconds': entry_target_hold, 'timing_contribution': Decimal('2.00')}
        )
        submissions[e.id] = {
            'entry': e,
            'perf': perf,
            'sub': sub,
            'item': item,
            'pose': pose_seq,
            'declared_code': pose_seq.declared_code if pose_seq else "ASANA",
            'declared_name': pose_seq.declared_name if pose_seq else "Declared Asana",
            'is_compulsory': is_comp,
            'base_value': bv_val
        }

    # Sequence progress tabs tracking committed/locked items
    sub_ids = [sub.id for perf, sub in entry_subs.values()]
    locked_items = set(AsanaScoreItem.objects.filter(
        submission_id__in=sub_ids,
        is_locked=True
    ).values_list('submission_id', 'sequence_order'))

    entry_count = len(entries)
    sequence_tabs = []
    for seq_num in range(1, 8):
        seq_locked_count = sum(1 for sub_id in sub_ids if (sub_id, seq_num) in locked_items)
        sequence_tabs.append({
            'seq': seq_num,
            'is_current': seq_num == current_seq,
            'is_locked': seq_locked_count == entry_count if entry_count else False,
            'is_partial': 0 < seq_locked_count < entry_count,
        })

    if request.method == 'POST':
        action = request.POST.get('action')
        for e in entries:
            hold_raw = request.POST.get(f'hold_{e.id}', '0').strip()
            try:
                hold_secs = Decimal(str(int(round(float(hold_raw))))) if hold_raw else Decimal('0.00')
            except Exception:
                hold_secs = Decimal('0.00')
            hold_secs = max(Decimal('0.00'), hold_secs)
            try:
                attempt_num = int(request.POST.get(f'attempt_{e.id}', '1'))
            except:
                attempt_num = 1

            pose_seq = AsanaSequenceItem.objects.filter(event_entry=e, sequence_order=current_seq).first()
            is_comp, _ = resolve_asana_compulsory_and_bv(e, round_type, current_seq, pose_seq)
            e_target = get_traditional_target_hold(round_type, is_comp)
            if not is_comp and pose_seq and getattr(pose_seq, 'declared_hold_time', None):
                e_target = pose_seq.declared_hold_time

            contrib = calculate_traditional_timing_contribution(hold_secs, e_target, attempt=attempt_num)
            sub = submissions[e.id]['sub']
            is_item_locked = (action in ['submit_item_next', 'submit_final_batch'])
            time_item, _ = AsanaScoreItem.objects.update_or_create(
                submission=sub,
                sequence_order=current_seq,
                defaults={
                    'hold_duration_seconds': hold_secs,
                    'attempt_number': attempt_num,
                    'timing_contribution': contrib,
                    'effective_item_score': contrib,
                    'is_draft': not is_item_locked,
                    'is_submitted': is_item_locked,
                    'is_locked': is_item_locked,
                }
            )

            # Synchronize timing contribution to D-Judges for this athlete
            d_items = AsanaScoreItem.objects.filter(
                submission__performance=sub.performance,
                submission__judge_role__startswith='D',
                sequence_order=current_seq
            )
            for d_item in d_items:
                d_item.timing_contribution = contrib
                raw_calc = (d_item.posture_score + d_item.stability_score + contrib) * d_item.base_value
                d_item.raw_item_score = raw_calc
                if not d_item.is_valid:
                    d_item.effective_item_score = Decimal('0.000')
                elif contrib <= Decimal('0.00'):
                    d_item.effective_item_score = Decimal('0.000')
                else:
                    d_item.effective_item_score = raw_calc
                d_item.save(update_fields=['timing_contribution', 'raw_item_score', 'effective_item_score'])

        if action == 'submit_final_batch':
            for e in entries:
                sub = submissions[e.id]['sub']
                sub.is_draft = False
                sub.is_submitted = True
                sub.is_locked = True
                sub.submitted_at = timezone.now()
                total = sum(i.timing_contribution for i in sub.items.all())
                sub.raw_total = total
                sub.effective_total = total
                sub.save()
                if sub.performance:
                    calculate_performance_score(sub.performance)
            messages.success(request, f"Traditional Time score sheet finalized and locked for Batch #{batch.batch_number}!")
            return redirect(f"{request.path}?review=1")
        elif action == 'submit_item_next':
            messages.success(request, f"Timing saved for Asana #{current_seq}.")
            next_seq = min(7, current_seq + 1)
            redirect_url = f"{request.path}?seq={next_seq}"
            if request.GET.get('panel_id'):
                redirect_url += f"&panel_id={request.GET.get('panel_id')}"
            return redirect(redirect_url)
        else:
            messages.success(request, f"Timing draft saved for Asana #{current_seq}.")
            redirect_url = f"{request.path}?seq={current_seq}"
            if request.GET.get('panel_id'):
                redirect_url += f"&panel_id={request.GET.get('panel_id')}"
            return redirect(redirect_url)

    return render(request, 'scoring/traditional_time.html', {
        'batch': batch,
        'entries': entries,
        'current_seq': current_seq,
        'sequence_tabs': sequence_tabs,
        'submissions': submissions,
        'is_compulsory': is_compulsory,
        'target_hold': target_hold,
    })

@role_required(RoleChoices.ADMIN, RoleChoices.TIME_JUDGE, RoleChoices.CD, 'APPROVED_JUDGE')
def scoring_music_time(request):
    """
    Implements Artistic Single Pair Time Judge Desk:
    - Displays all 10 asanas on a single screen with actual reference photographs, asana codes, manual duration entry, TA marks, and status badges.
    - Manual individual hold-time entry only (no individual stopwatches).
    - Automatically calculates TA marks: >= 5.0s -> 0.50 marks; < 5.0s -> 0.00 marks.
    - Holds < 5.0s flag the item for invalidity across D Judges in composite scoring.
    - Universal whole-performance stopwatch (TW) positioned at the bottom of the interface.
    - COP TW brackets: <150s = 0, 150-160s = 3, 161-170s = 4, 171-180s = 5; >180s = 0.50 marks/sec overtime deduction.
    - Two Time Judge reconciliation (T1 & T2) preserved separately.
    - AJAX auto-save and manual Save Draft / Submit Sheet actions.
    """
    perf_id = request.GET.get('perf_id')
    panel_id_param = request.GET.get('panel_id')
    role_param = request.GET.get('judge_role', '').strip().upper()
    current_panel = None
    seat_label = None
    batch = None

    if panel_id_param:
        current_panel = JudgePanel.objects.filter(id=panel_id_param).first()
    
    # 1. Resolve panel & active batch if perf_id not explicitly given
    if perf_id:
        perf = get_object_or_404(Performance, id=perf_id)
        entry = perf.event_entry
        round_type = perf.round_type
        batch = perf.batch
        if not current_panel and batch:
            current_panel = batch.panel
    else:
        current_panel, seat_label, batch_or_resp = get_judge_panel_and_batch(
            request,
            seat_fields=['time_judge', 'time_judge_2'],
            judge_role=role_param if role_param in ['TIME_JUDGE', 'TIME_JUDGE_2'] else None
        )
        if isinstance(batch_or_resp, HttpResponse):
            return batch_or_resp

        batch = batch_or_resp
        entry_id = request.GET.get('entry_id')
        if entry_id and batch and batch.entries.filter(id=entry_id).exists():
            entry = batch.entries.filter(id=entry_id).first()
        elif batch and batch.entries.exists():
            entry = batch.entries.first()
        else:
            entry = EventEntry.objects.filter(event_config__event_type__icontains='ARTISTIC').first()

        round_type = current_panel.round_type if current_panel else 'PRELIMINARY'
        perf, _ = Performance.objects.get_or_create(
            event_entry=entry,
            round_type=round_type,
            defaults={'batch': batch, 'status': PerformanceStatus.IN_PROGRESS}
        ) if entry else (None, False)

    if not perf or not entry:
        messages.error(request, 'No active competitor found for this timing session.')
        return render(request, 'scoring/music_time.html', {'error': 'No competitor found'})

    round_type = perf.round_type

    # Precise role resolution for Time Judge 1 vs Time Judge 2
    is_admin_or_cd = is_system_admin(request.user) or ScopedRole.objects.filter(
        user=request.user, championship=get_active_championship(request), role=RoleChoices.CD
    ).exists()

    if not is_admin_or_cd and current_panel:
        user_seat = next((s for s in get_user_assigned_seats(request.user, is_active_only=True) if s['panel'].id == current_panel.id and s['role_group'] == 'TIME_JUDGE'), None)
        if user_seat:
            judge_role = user_seat['seat_code']
            seat_label = user_seat['seat_label']
        else:
            return render(request, '403.html', {'message': "Access Denied: You do not hold an assigned Time Judge seat on this panel."}, status=403)
    elif role_param in ['TIME_JUDGE', 'TIME_JUDGE_2']:
        judge_role = role_param
        seat_label = 'Time Judge 2' if judge_role == 'TIME_JUDGE_2' else 'Time Judge 1'
    elif current_panel and current_panel.time_judge_2_id == request.user.id:
        judge_role = 'TIME_JUDGE_2'
        seat_label = 'Time Judge 2'
    elif current_panel and current_panel.time_judge_id == request.user.id:
        judge_role = 'TIME_JUDGE'
        seat_label = 'Time Judge 1'
    elif seat_label and ('2' in seat_label or 'TWO' in seat_label.upper()):
        judge_role = 'TIME_JUDGE_2'
        seat_label = 'Time Judge 2'
    else:
        judge_role = 'TIME_JUDGE'
        seat_label = 'Time Judge 1'

    sub, _ = JudgeSubmission.objects.get_or_create(
        performance=perf,
        judge_role=judge_role,
        version=1,
        defaults={'judge': request.user, 'is_draft': True, 'raw_total': Decimal('0.00'), 'effective_total': Decimal('0.000')}
    )
    if request.user.is_authenticated and (is_admin_or_cd or sub.judge == request.user):
        if sub.judge != request.user and not sub.is_locked and not is_admin_or_cd:
            sub.judge = request.user
            sub.save(update_fields=['judge'])
    elif not is_admin_or_cd and sub.judge != request.user:
        AuditLog.log(
            action='SECURITY_WORKSPACE_TAMPER',
            resource_type='JudgeSubmission',
            resource_id=sub.id,
            details={'user_id': request.user.id, 'owner_id': sub.judge_id if sub.judge else None},
            user=request.user,
            ip_address=request.META.get('REMOTE_ADDR')
        )
        return render(request, '403.html', {'message': "Access Denied: Cannot access another judge's workspace."}, status=403)

    total_items = 10

    # Fetch sequence items (all 10 asanas) for this event entry
    seq_items = list(AsanaSequenceItem.objects.filter(
        event_entry=entry,
        round_type=round_type
    ).order_by('sequence_order'))
    if not seq_items:
        seq_items = list(AsanaSequenceItem.objects.filter(
            event_entry=entry
        ).order_by('sequence_order'))

    seq_map = {item.sequence_order: item for item in seq_items}

    # Fetch or create all 10 AsanaScoreItem objects for this submission
    score_items_map = {}
    for item in sub.items.all():
        score_items_map[item.sequence_order] = item

    items_data = []
    ta_valid_count = 0
    completed_holds_count = 0

    for seq in range(1, total_items + 1):
        score_item = score_items_map.get(seq)
        if not score_item:
            score_item, _ = AsanaScoreItem.objects.get_or_create(
                submission=sub,
                sequence_order=seq,
                defaults={'hold_duration_seconds': Decimal('0.00'), 'timing_contribution': Decimal('0.00')}
            )
            score_items_map[seq] = score_item

        pose = seq_map.get(seq)
        hold_secs = score_item.hold_duration_seconds or Decimal('0.00')
        is_completed = (hold_secs > Decimal('0.00') or score_item.is_zero)
        is_valid = (hold_secs >= Decimal('5.00'))
        
        if is_completed:
            completed_holds_count += 1
        if is_valid:
            ta_valid_count += 1

        items_data.append({
            'seq': seq,
            'score_item': score_item,
            'pose': pose,
            'hold_duration': float(hold_secs),
            'is_valid': is_valid,
            'is_completed': is_completed,
            'ta_marks': 0.50 if is_valid else 0.00,
        })

    # Handle POST submission (both standard form submission and AJAX auto-save)
    if request.method == 'POST' and sub:
        action = request.POST.get('action') # 'auto_save', 'save_draft', 'submit_sheet'
        is_ajax = (request.headers.get('X-Requested-With') == 'XMLHttpRequest' or action == 'auto_save')

        if perf and perf.status == PerformanceStatus.HELD:
            msg = "Performance is currently ON HOLD by Stage Manager. Artistry scoring is temporarily paused."
            if is_ajax:
                return JsonResponse({'status': 'error', 'message': msg}, status=403)
            messages.warning(request, msg)
            return redirect(request.get_full_path())

        if perf and perf.status == PerformanceStatus.STOPPED_PENDING_DECISION:
            msg = "Performance has been STOPPED by Stage Manager. Artistry desk is locked pending official decision."
            if is_ajax:
                return JsonResponse({'status': 'error', 'message': msg}, status=403)
            messages.error(request, msg)
            return redirect(request.get_full_path())

        if perf and perf.status == PerformanceStatus.HELD:
            msg = "Performance is currently ON HOLD by Stage Manager. Timing actions are temporarily paused."
            if is_ajax:
                return JsonResponse({'status': 'error', 'message': msg}, status=403)
            messages.warning(request, msg)
            return redirect(request.get_full_path())

        if perf and perf.status == PerformanceStatus.STOPPED_PENDING_DECISION:
            msg = "Performance has been STOPPED by Stage Manager. Timing desks are locked pending official decision."
            if is_ajax:
                return JsonResponse({'status': 'error', 'message': msg}, status=403)
            messages.error(request, msg)
            return redirect(request.get_full_path())

        # 1. Update whole performance duration (TW) - Whole seconds only (no milliseconds/decimals)
        raw_dur = request.POST.get('routine_duration', '').strip()
        try:
            routine_duration = Decimal(str(int(round(float(raw_dur))))) if raw_dur else Decimal(str(int(round(float(sub.raw_total or 0)))))
        except Exception:
            routine_duration = Decimal(str(int(round(float(sub.raw_total or 0)))))
        routine_duration = max(Decimal('0.00'), routine_duration)

        # 2. Update hold durations for all 10 asanas - Whole seconds only (no milliseconds/decimals)
        updated_seq = None
        for itm_info in items_data:
            s_num = itm_info['seq']
            h_key = f'hold_{s_num}'
            if h_key in request.POST:
                h_val_raw = request.POST.get(h_key, '').strip()
                try:
                    h_val = Decimal(str(int(round(float(h_val_raw))))) if h_val_raw else Decimal('0.00')
                except Exception:
                    h_val = Decimal(str(int(round(float(itm_info['hold_duration'])))))
                
                h_val = max(Decimal('0.00'), min(Decimal('120.00'), h_val))
                score_item = itm_info['score_item']
                score_item.hold_duration_seconds = h_val
                is_val = (h_val >= Decimal('5.00'))
                score_item.timing_contribution = Decimal('0.50') if is_val else Decimal('0.00')
                score_item.is_valid = is_val
                if h_val == Decimal('0.00') and h_val_raw == '0':
                    score_item.is_zero = True
                elif h_val > Decimal('0.00'):
                    score_item.is_zero = False
                
                if action == 'submit_sheet':
                    score_item.is_draft = False
                    score_item.is_submitted = True
                    score_item.is_locked = True
                else:
                    score_item.is_draft = True

                score_item.save(update_fields=['hold_duration_seconds', 'timing_contribution', 'is_valid', 'is_zero', 'is_draft', 'is_submitted', 'is_locked'])
                updated_seq = s_num

        # Recalculate marks under COP
        tw_marks = calculate_tw_marks(routine_duration)
        ot_secs = max(0, int(round(routine_duration)) - 180)
        ot_deduction = Decimal(str(ot_secs)) * Decimal('0.500')

        # Recount valid holds from saved items
        all_hold_items = AsanaScoreItem.objects.filter(submission=sub)
        final_ta_valid = all_hold_items.filter(hold_duration_seconds__gte=Decimal('5.00')).count()
        ta_marks = min(Decimal('5.000'), Decimal(str(final_ta_valid)) * Decimal('0.500'))
        total_timing_score = max(Decimal('0.000'), tw_marks + ta_marks - ot_deduction)

        sub.raw_total = routine_duration
        sub.effective_total = total_timing_score

        if action == 'submit_sheet':
            sub.is_draft = False
            sub.is_submitted = True
            sub.is_locked = True
            sub.submitted_at = timezone.now()
            sub.save(update_fields=['raw_total', 'effective_total', 'is_draft', 'is_submitted', 'is_locked', 'submitted_at'])
            if sub.performance:
                calculate_performance_score(sub.performance)

            if is_ajax:
                return JsonResponse({
                    'status': 'success',
                    'action': 'submit_sheet',
                    'message': f'Timing sheet submitted and locked! TW: {tw_marks:.2f}, TA: {ta_marks:.2f}, Net: {total_timing_score:.2f}',
                    'is_locked': True,
                    'tw_marks': float(tw_marks),
                    'ta_marks': float(ta_marks),
                    'ot_deduction': float(ot_deduction),
                    'total_timing_score': float(total_timing_score),
                })
            messages.success(request, f'Timing sheet submitted and locked! TW: {tw_marks:.2f}, TA: {ta_marks:.2f}, Net: {total_timing_score:.2f}')
            redirect_url = request.path
            params = []
            if perf_id:
                params.append(f'perf_id={perf_id}')
            elif request.GET.get('panel_id'):
                params.append(f"panel_id={request.GET.get('panel_id')}")
            if judge_role:
                params.append(f'judge_role={judge_role}')
            if params:
                redirect_url += '?' + '&'.join(params)
            return redirect(redirect_url)

        else: # auto_save or save_draft
            sub.save(update_fields=['raw_total', 'effective_total', 'is_draft'])
            if sub.performance:
                calculate_performance_score(sub.performance)

            if is_ajax:
                return JsonResponse({
                    'status': 'success',
                    'action': action,
                    'routine_duration': float(routine_duration),
                    'tw_marks': float(tw_marks),
                    'ta_marks': float(ta_marks),
                    'ta_valid_count': final_ta_valid,
                    'ot_seconds': ot_secs,
                    'ot_deduction': float(ot_deduction),
                    'total_timing_score': float(total_timing_score),
                    'updated_seq': updated_seq,
                })
            messages.info(request, f'Timing draft saved. Whole duration: {routine_duration}s.')
            redirect_url = request.path
            params = []
            if perf_id:
                params.append(f'perf_id={perf_id}')
            elif request.GET.get('panel_id'):
                params.append(f"panel_id={request.GET.get('panel_id')}")
            if judge_role:
                params.append(f'judge_role={judge_role}')
            if params:
                redirect_url += '?' + '&'.join(params)
            return redirect(redirect_url)

    # Blind Judging Isolation:
    # Neither Time Judge (T1 or T2) may view the other judge's readings, scores, or reconciliation.
    # T1 sees only T1; T2 sees only T2.
    # Reconciled timing data is exclusively visible to authorized supervisory staff (Chief Judge, CD, Admin).
    time_judge_1 = JudgeSubmission.objects.filter(performance=perf, judge_role='TIME_JUDGE').first()
    time_judge_2 = JudgeSubmission.objects.filter(performance=perf, judge_role='TIME_JUDGE_2').first()
    reconciliation_info = None
    
    championship = get_active_championship(request)
    can_view_reconciliation = (
        request.user.is_authenticated and (
            is_system_admin(request.user) or
            ScopedRole.objects.filter(user=request.user, championship=championship, role__in=[RoleChoices.CD, RoleChoices.CHIEF_JUDGE]).exists() or
            (current_panel and current_panel.chief_judge_id == request.user.id)
        )
    )

    if can_view_reconciliation and time_judge_1 and time_judge_2 and (time_judge_1.raw_total or time_judge_2.raw_total):
        from .services import reconcile_music_timing
        t1_holds = [itm.hold_duration_seconds for itm in time_judge_1.items.all().order_by('sequence_order')]
        t2_holds = [itm.hold_duration_seconds for itm in time_judge_2.items.all().order_by('sequence_order')]
        t1_tw = time_judge_1.raw_total or Decimal('0.00')
        t2_tw = time_judge_2.raw_total or Decimal('0.00')
        reconciliation_info = reconcile_music_timing(t1_tw, t2_tw, t1_holds, t2_holds)

    # Calculate live COP preview for display
    current_tw = sub.raw_total or Decimal('0.00')
    tw_marks_preview = calculate_tw_marks(current_tw)
    ot_secs_preview = max(0, int(round(current_tw)) - 180)
    ot_ded_preview = Decimal(str(ot_secs_preview)) * Decimal('0.500')
    ta_marks_preview = min(Decimal('5.000'), Decimal(str(ta_valid_count)) * Decimal('0.500'))
    total_preview = max(Decimal('0.000'), tw_marks_preview + ta_marks_preview - ot_ded_preview)

    # In Artistic Single / Artistic Pair / Rhythmic Pair, only 1 performer performs at a time
    is_traditional = (current_panel.event_config.event_type == 'TRADITIONAL') if (current_panel and current_panel.event_config) else False
    batch_entries = list(batch.entries.all()) if (batch and is_traditional) else [entry]

    return render(request, 'scoring/music_time.html', {
        'panel': current_panel,
        'seat_label': seat_label,
        'judge_role': judge_role,
        'batch': batch,
        'entry': entry,
        'perf': perf,
        'sub': sub,
        'batch_entries': batch_entries,
        'items': items_data,
        'total_items': total_items,
        'completed_holds_count': completed_holds_count,
        'ta_valid_count': ta_valid_count,
        'tw_marks': tw_marks_preview,
        'ta_marks': ta_marks_preview,
        'ot_seconds': ot_secs_preview,
        'ot_deduction': ot_ded_preview,
        'total_timing_score': total_preview,
        'is_locked': sub.is_locked,
        'can_view_reconciliation': can_view_reconciliation,
        'reconciliation_info': reconciliation_info,
        'time_judge_1': time_judge_1 if can_view_reconciliation else (sub if judge_role == 'TIME_JUDGE' else None),
        'time_judge_2': time_judge_2 if can_view_reconciliation else (sub if judge_role == 'TIME_JUDGE_2' else None),
    })


@role_required(RoleChoices.ADMIN, RoleChoices.ARTISTRY_JUDGE, RoleChoices.CD, 'APPROVED_JUDGE')
def scoring_artistry(request):
    """
    Implements Artistry Judge Score Sheet (COP 20-Point Breakdown):
    5 Scoring Components:
    1. Costume (Base 1)
    2. Theme & Music (Base 1)
    3. Character & Expression (Base 3)
    4. Rhythm & Artistic Steps (Base 10, with penalty field)
    5. Use of Floor (Base 5, with penalty field)
    Other Penalties section with type, amount, reason, remarks.
    Supports independent records for Artistry Judge 1 and Artistry Judge 2.
    """
    import json
    perf_id = request.GET.get('perf_id')
    current_panel = None
    seat_label = "Artistry Judge"
    batch = None

    if perf_id:
        perf = get_object_or_404(Performance, id=perf_id)
        entry = perf.event_entry
        round_type = perf.round_type
        batch = perf.batch
    else:
        current_panel, seat_label, batch_or_resp = get_judge_panel_and_batch(
            request,
            seat_fields=['artistry_judge', 'artistry_judge_2'],
            judge_role='ARTISTRY_JUDGE'
        )
        if isinstance(batch_or_resp, HttpResponse):
            return batch_or_resp

        batch = batch_or_resp
        entry_id = request.GET.get('entry_id')
        if entry_id and batch and batch.entries.filter(id=entry_id).exists():
            entry = batch.entries.filter(id=entry_id).first()
        elif batch and batch.entries.exists():
            entry = batch.entries.first()
        else:
            entry = EventEntry.objects.filter(event_config__event_type__icontains='ARTISTIC').first()

        round_type = current_panel.round_type if current_panel else 'PRELIMINARY'
        perf, _ = Performance.objects.get_or_create(
            event_entry=entry,
            round_type=round_type,
            defaults={'batch': batch, 'status': PerformanceStatus.IN_PROGRESS}
        ) if entry else (None, False)

    if not perf or not entry:
        messages.error(request, 'No active competitor found for this artistry scoring session.')
        return render(request, 'scoring/artistry.html', {'error': 'No competitor found'})

    round_type = perf.round_type
    # Support Artistry Judge 1 vs Artistry Judge 2
    is_admin_or_cd = is_system_admin(request.user) or ScopedRole.objects.filter(
        user=request.user, championship=get_active_championship(request), role=RoleChoices.CD
    ).exists()

    role_param = request.GET.get('judge_role', '').strip().upper()
    if not is_admin_or_cd and current_panel:
        user_seat = next((s for s in get_user_assigned_seats(request.user, is_active_only=True) if s['panel'].id == current_panel.id and s['role_group'] == 'ARTISTRY_JUDGE'), None)
        if user_seat:
            judge_role = user_seat['seat_code']
            seat_label = user_seat['seat_label']
        else:
            return render(request, '403.html', {'message': "Access Denied: You do not hold an assigned Artistry Judge seat on this panel."}, status=403)
    elif role_param in ['ARTISTRY_JUDGE', 'ARTISTRY_JUDGE_2']:
        judge_role = role_param
    elif '2' in (seat_label or '') or 'TWO' in (seat_label or '').upper():
        judge_role = 'ARTISTRY_JUDGE_2'
    else:
        judge_role = 'ARTISTRY_JUDGE'

    sub, _ = JudgeSubmission.objects.get_or_create(
        performance=perf,
        judge_role=judge_role,
        version=1,
        defaults={'judge': request.user, 'is_draft': True, 'raw_total': Decimal('0.00'), 'effective_total': Decimal('0.00')}
    )
    if request.user.is_authenticated and (is_admin_or_cd or sub.judge == request.user):
        if sub.judge != request.user and not sub.is_locked and not is_admin_or_cd:
            sub.judge = request.user
            sub.save(update_fields=['judge'])
    elif not is_admin_or_cd and sub.judge != request.user:
        AuditLog.log(
            action='SECURITY_WORKSPACE_TAMPER',
            resource_type='JudgeSubmission',
            resource_id=sub.id,
            details={'user_id': request.user.id, 'owner_id': sub.judge_id if sub.judge else None},
            user=request.user,
            ip_address=request.META.get('REMOTE_ADDR')
        )
        return render(request, '403.html', {'message': "Access Denied: Cannot access another judge's workspace."}, status=403)

    art_score, _ = PerformanceArtistryScore.objects.get_or_create(
        submission=sub,
        defaults={
            'costume': Decimal('0.00'),
            'theme_music': Decimal('0.00'),
            'character_expression': Decimal('0.00'),
            'rhythm_artistic_steps': Decimal('0.00'),
            'use_of_floor': Decimal('0.00'),
            'artistry_deductions': Decimal('0.00'),
            'total_artistry': Decimal('0.00')
        }
    )

    # Calculation metadata stores detailed component breakdown & other penalties
    meta = sub.calculation_metadata or {}
    other_penalties = meta.get('other_penalties', [])
    rhythm_penalty = Decimal(str(meta.get('rhythm_penalty', '0.00')))
    floor_penalty = Decimal(str(meta.get('floor_penalty', '0.00')))

    if request.method == 'POST' and sub:
        action = request.POST.get('action') # 'auto_save', 'save_draft', 'submit_sheet'
        is_ajax = (request.headers.get('X-Requested-With') == 'XMLHttpRequest' or action == 'auto_save')

        if not sub.is_locked:
            # 1. Parse Component Execution Values
            def parse_dec(val_str, max_limit):
                try:
                    d = Decimal(str(float(val_str)))
                    return max(Decimal('0.00'), min(Decimal(str(max_limit)), d))
                except Exception:
                    return Decimal('0.00')

            def parse_pen(val_str):
                try:
                    d = Decimal(str(float(val_str)))
                    return max(Decimal('0.00'), d)
                except Exception:
                    return Decimal('0.00')

            costume_exec = parse_dec(request.POST.get('costume', art_score.costume), 1.00)
            theme_music_exec = parse_dec(request.POST.get('theme_music', art_score.theme_music), 1.00)
            char_expr_exec = parse_dec(request.POST.get('character_expression', art_score.character_expression), 3.00)
            rhythm_steps_exec = parse_dec(request.POST.get('rhythm_artistic_steps', art_score.rhythm_artistic_steps), 10.00)
            use_floor_exec = parse_dec(request.POST.get('use_of_floor', art_score.use_of_floor), 5.00)

            # 2. Parse Criterion Penalties
            rhythm_pen = parse_pen(request.POST.get('rhythm_penalty', rhythm_penalty))
            floor_pen = parse_pen(request.POST.get('floor_penalty', floor_penalty))
            criterion_penalties_total = rhythm_pen + floor_pen

            # 3. Parse Other Penalties list (JSON or individual fields)
            raw_other_penalties = request.POST.get('other_penalties_json')
            if raw_other_penalties:
                try:
                    parsed_other = json.loads(raw_other_penalties)
                    other_penalties = []
                    for item in parsed_other:
                        p_amt = parse_pen(item.get('amount', 0))
                        p_type = str(item.get('type', 'General Infraction')).strip()
                        p_reason = str(item.get('reason', '')).strip()
                        p_remarks = str(item.get('remarks', '')).strip()
                        p_approved = bool(item.get('is_approved', True))
                        if p_amt > Decimal('0.00') or p_reason:
                            other_penalties.append({
                                'type': p_type,
                                'amount': str(p_amt),
                                'reason': p_reason,
                                'remarks': p_remarks,
                                'is_approved': p_approved
                            })
                except Exception:
                    pass

            # Approved other penalties sum
            other_approved_total = sum(Decimal(p['amount']) for p in other_penalties if p.get('is_approved', True))
            total_penalties = criterion_penalties_total + other_approved_total

            # 4. Totals and Net Calculation
            exec_total = costume_exec + theme_music_exec + char_expr_exec + rhythm_steps_exec + use_floor_exec
            final_net_score = max(Decimal('0.00'), exec_total - total_penalties)

            # Save model fields
            art_score.costume = costume_exec
            art_score.theme_music = theme_music_exec
            art_score.character_expression = char_expr_exec
            art_score.rhythm_artistic_steps = rhythm_steps_exec
            art_score.use_of_floor = use_floor_exec
            art_score.artistry_deductions = total_penalties
            art_score.total_artistry = final_net_score
            art_score.save()

            meta['rhythm_penalty'] = str(rhythm_pen)
            meta['floor_penalty'] = str(floor_pen)
            meta['criterion_penalties'] = str(criterion_penalties_total)
            meta['other_penalties'] = other_penalties
            meta['other_approved_total'] = str(other_approved_total)
            meta['execution_total'] = str(exec_total)
            meta['total_penalties'] = str(total_penalties)
            meta['final_artistry_score'] = str(final_net_score)

            sub.raw_total = exec_total
            sub.effective_total = final_net_score
            sub.calculation_metadata = meta

            if action == 'submit_sheet':
                sub.is_draft = False
                sub.is_submitted = True
                sub.is_locked = True
                sub.submitted_at = timezone.now()
                sub.save()
                if sub.performance:
                    calculate_performance_score(sub.performance)

                if is_ajax:
                    return JsonResponse({
                        'status': 'success',
                        'message': f"Artistry scores submitted and locked! Final Score: {final_net_score:.2f} / 20.00",
                        'is_locked': True,
                        'execution_total': float(exec_total),
                        'penalty_total': float(total_penalties),
                        'final_score': float(final_net_score)
                    })
                messages.success(request, f"Artistry scores submitted and locked! Final Score: {final_net_score:.2f} / 20.00")
                return redirect(request.path)
            else:
                sub.save()
                if is_ajax:
                    return JsonResponse({
                        'status': 'success',
                        'message': 'Artistry draft saved successfully.',
                        'is_locked': False,
                        'execution_total': float(exec_total),
                        'penalty_total': float(total_penalties),
                        'final_score': float(final_net_score)
                    })
                messages.success(request, f"Artistry draft saved! Final Score: {final_net_score:.2f} / 20.00")

    # In Artistic Single / Artistic Pair / Rhythmic Pair, only 1 performer performs at a time
    is_traditional = (current_panel.event_config.event_type == 'TRADITIONAL') if (current_panel and current_panel.event_config) else False
    batch_entries = list(batch.entries.all()) if (batch and is_traditional) else ([entry] if entry else [])

    # Get sister judge score if exists for composite preview
    other_role = 'ARTISTRY_JUDGE_2' if judge_role == 'ARTISTRY_JUDGE' else 'ARTISTRY_JUDGE'
    other_sub = JudgeSubmission.objects.filter(performance=perf, judge_role=other_role).first()
    # Desk Isolation: Artistry judges work independently in their own workspace without viewing the other judge's score or panel average
    other_score = None
    panel_average = None

    return render(request, 'scoring/artistry.html', {
        'batch': batch,
        'batch_entries': batch_entries,
        'entry': entry,
        'perf': perf,
        'sub': sub,
        'art_score': art_score,
        'judge_role': judge_role,
        'seat_label': "Artistry Judge 2" if judge_role == 'ARTISTRY_JUDGE_2' else "Artistry Judge 1",
        'rhythm_penalty': rhythm_penalty,
        'floor_penalty': floor_penalty,
        'other_penalties': other_penalties,
        'other_approved_total': Decimal(str(meta.get('other_approved_total', '0.00'))),
        'other_sub': other_sub,
        'panel_average': panel_average,
    })


@role_required(RoleChoices.ADMIN, RoleChoices.EVALUATOR, RoleChoices.CD, 'APPROVED_JUDGE')
def scoring_evaluator(request, perf_id=None):
    if not perf_id:
        perf_id = request.GET.get('perf_id')
    """
    Evaluator Desk for Traditional Yogasana (multi-athlete batch 3-5 players)
    and Single/Pair events.
    The Evaluator is the sole authority on asana validity.
    When Evaluator marks an asana invalid, all D-judges for that athlete & sequence
    automatically have effective_item_score = 0.000 and is_valid = False.
    When Evaluator marks it valid, all D-judges have is_valid = True
    and positive calculated score.
    """
    current_panel, seat_label, batch_or_resp = get_judge_panel_and_batch(
        request,
        seat_fields=['evaluator', 'evaluator_2'],
        judge_role='EVALUATOR'
    )
    if isinstance(batch_or_resp, HttpResponse):
        return batch_or_resp

    batch = batch_or_resp
    panel = current_panel
    round_type = panel.round_type if panel else 'PRELIMINARY'
    is_traditional = bool(panel and 'TRADITIONAL' in panel.event_config.event_type.upper()) if panel else True

    if is_traditional:
        entries = list(batch.entries.all()[:5])
        total_items = 7
        try:
            current_seq = int(request.GET.get('seq', 1))
        except:
            current_seq = 1
        current_seq = max(1, min(total_items, current_seq))

        athletes = []
        entry_subs = {}
        for e in entries:
            perf, _ = Performance.objects.get_or_create(
                event_entry=e,
                round_type=round_type,
                defaults={'batch': batch, 'status': PerformanceStatus.IN_PROGRESS}
            )
            sub, _ = JudgeSubmission.objects.get_or_create(
                performance=perf,
                judge_role='EVALUATOR',
                version=1,
                defaults={'judge': request.user, 'is_draft': True}
            )
            entry_subs[e.id] = (perf, sub)

        sub_ids = [sub.id for perf, sub in entry_subs.values()]
        locked_items = set(AsanaScoreItem.objects.filter(
            submission_id__in=sub_ids,
            is_locked=True
        ).values_list('submission_id', 'sequence_order'))

        entry_count = len(entries)
        sequence_tabs = []
        for s in range(1, total_items + 1):
            seq_locked_count = sum(1 for sub_id in sub_ids if (sub_id, s) in locked_items)
            sequence_tabs.append({
                'seq': s,
                'is_current': (s == current_seq),
                'is_locked': seq_locked_count == entry_count if entry_count else False,
                'is_partial': 0 < seq_locked_count < entry_count,
            })

        for e in entries:
            perf, sub = entry_subs[e.id]
            score_item, _ = AsanaScoreItem.objects.get_or_create(
                submission=sub,
                sequence_order=current_seq,
                defaults={'is_valid': True, 'invalid_reason': ''}
            )
            pose = AsanaSequenceItem.objects.filter(event_entry=e, sequence_order=current_seq, round_type=round_type).first()
            if not pose:
                pose = AsanaSequenceItem.objects.filter(event_entry=e, sequence_order=current_seq).first()
            is_compulsory, declared_bv = resolve_asana_compulsory_and_bv(e, round_type, current_seq, pose)

            seq_inc = EvaluatorIncident.objects.filter(
                performance=perf,
                incident_type=IncidentType.SEQUENCE_CHANGE,
                affected_item_numbers=str(current_seq)
            ).first()
            seq_penalty = int(seq_inc.penalty_marks) if seq_inc else 0
            is_dq = bool(seq_inc and seq_inc.evidence_notes == 'DQ')

            other_inc = EvaluatorIncident.objects.filter(
                performance=perf,
                incident_type=IncidentType.OTHER_RULE,
                affected_item_numbers=str(current_seq)
            ).first()
            other_penalty = other_inc.penalty_marks if other_inc else Decimal('0.00')
            other_reason = other_inc.evidence_notes if other_inc else ''

            athletes.append({
                'entry': e,
                'perf': perf,
                'sub': sub,
                'score_item': score_item,
                'pose': pose,
                'is_compulsory': is_compulsory,
                'declared_bv': declared_bv,
                'seq_penalty': seq_penalty,
                'is_dq': is_dq,
                'other_penalty': other_penalty,
                'other_reason': other_reason,
            })

        if request.method == 'POST':
            action = request.POST.get('action') # 'save_draft', 'save_next', 'submit_sheet'

            for a in athletes:
                e = a['entry']
                perf = a['perf']
                sub = a['sub']
                score_item = a['score_item']

                # 1. Validity
                valid_str = request.POST.get(f'valid_{e.id}', 'true').strip().lower()
                is_valid_val = (valid_str == 'true')
                reason_val = request.POST.get(f'reason_{e.id}', '').strip()

                score_item.is_valid = is_valid_val
                score_item.invalid_reason = reason_val if not is_valid_val else ''
                if action in ['save_next', 'submit_sheet']:
                    score_item.is_draft = False
                    score_item.is_submitted = True
                    score_item.is_locked = True
                else:
                    score_item.is_draft = True
                score_item.save()

                # 2. Synchronize to all D-Judges for this athlete & sequence
                d_items = AsanaScoreItem.objects.filter(
                    submission__performance=perf,
                    submission__judge_role__startswith='D',
                    sequence_order=current_seq
                )
                for d_item in d_items:
                    if not is_valid_val:
                        d_item.is_valid = False
                        d_item.invalid_reason = reason_val or "Evaluator Confirmed Invalid Asana"
                        d_item.effective_item_score = Decimal('0.000')
                    else:
                        d_item.is_valid = True
                        d_item.invalid_reason = ""
                        time_item = AsanaScoreItem.objects.filter(
                            submission__performance=perf,
                            submission__judge_role='TIME_JUDGE',
                            sequence_order=current_seq
                        ).first()
                        t_val = time_item.timing_contribution if (time_item and time_item.timing_contribution > Decimal('0.00')) else Decimal('2.00')
                        pose_seq = AsanaSequenceItem.objects.filter(event_entry=e, sequence_order=current_seq, round_type=round_type).first()
                        if not pose_seq:
                            pose_seq = AsanaSequenceItem.objects.filter(event_entry=e, sequence_order=current_seq).first()
                        is_comp, bv = resolve_asana_compulsory_and_bv(e, round_type, current_seq, pose_seq)
                        d_item.timing_contribution = t_val
                        d_item.base_value = bv
                        raw_calc = (d_item.posture_score + d_item.stability_score + t_val) * bv
                        d_item.raw_item_score = raw_calc
                        d_item.effective_item_score = raw_calc
                    d_item.save(update_fields=['is_valid', 'invalid_reason', 'timing_contribution', 'base_value', 'raw_item_score', 'effective_item_score'])

                # 3. Sequence Change Penalty
                seq_pen_val = request.POST.get(f'seq_penalty_{e.id}', '0')
                if seq_pen_val == 'DQ':
                    EvaluatorIncident.objects.update_or_create(
                        performance=perf,
                        incident_type=IncidentType.SEQUENCE_CHANGE,
                        affected_item_numbers=str(current_seq),
                        defaults={
                            'chest_number': e.chest_number or str(e.id),
                            'reporting_evaluator': request.user,
                            'penalty_marks': Decimal('0.00'),
                            'evidence_notes': 'DQ',
                            'chief_decision': 'APPROVED',
                        }
                    )
                else:
                    try:
                        pen_marks = Decimal(seq_pen_val)
                    except:
                        pen_marks = Decimal('0.00')
                    if pen_marks > Decimal('0.00'):
                        EvaluatorIncident.objects.update_or_create(
                            performance=perf,
                            incident_type=IncidentType.SEQUENCE_CHANGE,
                            affected_item_numbers=str(current_seq),
                            defaults={
                                'chest_number': e.chest_number or str(e.id),
                                'reporting_evaluator': request.user,
                                'penalty_marks': pen_marks,
                                'evidence_notes': f'-{pen_marks} marks sequence change penalty on Asana #{current_seq}',
                                'chief_decision': 'APPROVED',
                            }
                        )
                    else:
                        EvaluatorIncident.objects.filter(
                            performance=perf,
                            incident_type=IncidentType.SEQUENCE_CHANGE,
                            affected_item_numbers=str(current_seq)
                        ).delete()

                # 4. Other Penalties
                other_pen_str = request.POST.get(f'other_penalty_{e.id}', '0.00')
                other_reason_str = request.POST.get(f'other_reason_{e.id}', '').strip()
                try:
                    other_marks = Decimal(other_pen_str)
                except:
                    other_marks = Decimal('0.00')
                if other_marks > Decimal('0.00'):
                    EvaluatorIncident.objects.update_or_create(
                        performance=perf,
                        incident_type=IncidentType.OTHER_RULE,
                        affected_item_numbers=str(current_seq),
                        defaults={
                            'chest_number': e.chest_number or str(e.id),
                            'reporting_evaluator': request.user,
                            'penalty_marks': other_marks,
                            'evidence_notes': other_reason_str or f'Other technical penalty on Asana #{current_seq}',
                            'chief_decision': 'APPROVED',
                        }
                    )
                else:
                    EvaluatorIncident.objects.filter(
                        performance=perf,
                        incident_type=IncidentType.OTHER_RULE,
                        affected_item_numbers=str(current_seq)
                    ).delete()

                if action == 'submit_sheet':
                    sub.is_draft = False
                    sub.is_submitted = True
                    sub.is_locked = True
                    sub.submitted_at = timezone.now()
                    sub.save()
                else:
                    sub.is_draft = True
                    sub.save()

                calculate_performance_score(perf)

            if action == 'save_draft':
                messages.info(request, f"Evaluator decisions saved for Asana #{current_seq}.")
                redirect_url = f"{request.path}?seq={current_seq}"
                if request.GET.get('panel_id'):
                    redirect_url += f"&panel_id={request.GET.get('panel_id')}"
                return redirect(redirect_url)
            elif action == 'save_next':
                if current_seq < total_items:
                    next_seq = current_seq + 1
                    messages.success(request, f"Asana #{current_seq} evaluated. Opened Asana #{next_seq}.")
                    redirect_url = f"{request.path}?seq={next_seq}"
                    if request.GET.get('panel_id'):
                        redirect_url += f"&panel_id={request.GET.get('panel_id')}"
                    return redirect(redirect_url)
                else:
                    messages.success(request, "All asana sequences evaluated for batch!")
                    redirect_url = request.path
                    if request.GET.get('panel_id'):
                        redirect_url += f"?panel_id={request.GET.get('panel_id')}"
                    return redirect(redirect_url)
            elif action == 'submit_sheet':
                messages.success(request, f"Evaluator sheet finalized and locked for Batch #{batch.batch_number if batch else '1'}!")
                redirect_url = request.path
                if request.GET.get('panel_id'):
                    redirect_url += f"?panel_id={request.GET.get('panel_id')}"
                return redirect(redirect_url)

        all_sub_objs = [a['sub'] for a in athletes]
        is_batch_submitted = bool(all_sub_objs and all(s.is_submitted for s in all_sub_objs))

        return render(request, 'scoring/evaluator.html', {
            'is_traditional': True,
            'batch': batch,
            'athletes': athletes,
            'round_type': round_type,
            'current_seq': current_seq,
            'total_items': total_items,
            'sequence_tabs': sequence_tabs,
            'is_batch_submitted': is_batch_submitted,
        })
    else:
        # Non-traditional (Artistic / Rhythmic) routine view
        entry_id_param = request.GET.get('entry_id')
        if perf_id:
            perf = get_object_or_404(Performance, id=perf_id)
            entry = perf.event_entry
            round_type = perf.round_type
        else:
            if entry_id_param and batch and batch.entries.filter(id=entry_id_param).exists():
                entry = batch.entries.filter(id=entry_id_param).first()
            elif batch and batch.entries.exists():
                entry = batch.entries.first()
            else:
                entry = EventEntry.objects.filter(event_config__event_type__icontains='ARTISTIC').first()

            round_type = panel.round_type if panel else 'PRELIMINARY'
            perf, _ = Performance.objects.get_or_create(
                event_entry=entry,
                round_type=round_type,
                defaults={'batch': batch, 'status': PerformanceStatus.IN_PROGRESS}
            ) if entry else (None, False)

        is_admin_or_cd = is_system_admin(request.user) or ScopedRole.objects.filter(
            user=request.user, championship=get_active_championship(request), role=RoleChoices.CD
        ).exists()

        role_param = request.GET.get('judge_role', '').strip().upper()
        if not is_admin_or_cd and panel:
            user_seat = next((s for s in get_user_assigned_seats(request.user, is_active_only=True) if s['panel'].id == panel.id and s['role_group'] == 'EVALUATOR'), None)
            if user_seat:
                eval_role = user_seat['seat_code']
                seat_label = user_seat['seat_label']
            else:
                return render(request, '403.html', {'message': "Access Denied: You do not hold an assigned Evaluator seat on this panel."}, status=403)
        else:
            eval_role = 'EVALUATOR_2' if (role_param == 'EVALUATOR_2' or (panel and panel.evaluator_2_id == request.user.id) or ('2' in (seat_label or ''))) else 'EVALUATOR'

        sub, _ = JudgeSubmission.objects.get_or_create(
            performance=perf,
            judge_role=eval_role,
            version=1,
            defaults={'judge': request.user, 'is_draft': True}
        ) if perf else (None, False)

        if sub and request.user.is_authenticated and (is_admin_or_cd or sub.judge == request.user):
            if sub.judge != request.user and not sub.is_locked and not is_admin_or_cd:
                sub.judge = request.user
                sub.save(update_fields=['judge'])
        elif sub and not is_admin_or_cd and sub.judge != request.user:
            AuditLog.log(
                action='SECURITY_WORKSPACE_TAMPER',
                resource_type='JudgeSubmission',
                resource_id=sub.id,
                details={'user_id': request.user.id, 'owner_id': sub.judge_id if sub.judge else None},
                user=request.user,
                ip_address=request.META.get('REMOTE_ADDR')
            )
            return render(request, '403.html', {'message': "Access Denied: Cannot access another evaluator's workspace."}, status=403)

        item_count = 10
        items = []
        if sub and entry:
            for seq in range(1, item_count + 1):
                pose_seq = AsanaSequenceItem.objects.filter(event_entry=entry, sequence_order=seq, round_type=round_type).first()
                if not pose_seq:
                    pose_seq = AsanaSequenceItem.objects.filter(event_entry=entry, sequence_order=seq).first()
                score_item, _ = AsanaScoreItem.objects.get_or_create(
                    submission=sub,
                    sequence_order=seq,
                    defaults={'is_valid': True, 'invalid_reason': ''}
                )
                # Resolve image_url
                img_url = ''
                if pose_seq:
                    if hasattr(pose_seq, 'image_url') and pose_seq.image_url:
                        img_url = pose_seq.image_url
                    elif getattr(pose_seq, 'declared_asana', None) and pose_seq.declared_asana.image_url:
                        img_url = pose_seq.declared_asana.image_url
                
                # Check sequence incident for this asana
                seq_inc = EvaluatorIncident.objects.filter(
                    performance=perf,
                    incident_type=IncidentType.SEQUENCE_CHANGE,
                    affected_item_numbers=str(seq)
                ).first()
                seq_pen = int(seq_inc.penalty_marks) if seq_inc else 0
                is_dq = bool(seq_inc and seq_inc.evidence_notes == 'DQ')

                items.append({
                    'score_item': score_item,
                    'pose': pose_seq,
                    'seq': seq,
                    'code': pose_seq.declared_code if pose_seq else f'A{seq}',
                    'image_url': img_url,
                    'bv': pose_seq.declared_bv if pose_seq else Decimal('0.80'),
                    'seq_penalty': seq_pen,
                    'is_dq': is_dq,
                })

        if request.method == 'POST' and sub:
            action = request.POST.get('action')
            invalid_count = 0
            for seq in range(1, item_count + 1):
                is_valid_val = (request.POST.get(f'valid_{seq}') == 'true')
                reason_val = request.POST.get(f'reason_{seq}', '').strip()
                if not is_valid_val:
                    invalid_count += 1

                item_defaults = {
                    'is_valid': is_valid_val,
                    'invalid_reason': reason_val,
                    'is_draft': (action != 'submit_sheet'),
                    'is_submitted': (action == 'submit_sheet'),
                    'is_locked': (action == 'submit_sheet'),
                }
                AsanaScoreItem.objects.update_or_create(
                    submission=sub,
                    sequence_order=seq,
                    defaults=item_defaults
                )

                # Synchronize to all D-Judges for this performance & sequence
                d_items = AsanaScoreItem.objects.filter(
                    submission__performance=perf,
                    submission__judge_role__startswith='D',
                    sequence_order=seq
                )
                for d_item in d_items:
                    if not is_valid_val:
                        d_item.is_valid = False
                        d_item.invalid_reason = reason_val or "Evaluator Confirmed Invalid Asana"
                        d_item.effective_item_score = Decimal('0.000')
                    else:
                        d_item.is_valid = True
                        d_item.invalid_reason = ""
                        d_item.effective_item_score = quantize_score(d_item.execution_score * d_item.base_value) if d_item.execution_score > 0 else d_item.raw_item_score
                    d_item.save(update_fields=['is_valid', 'invalid_reason', 'effective_item_score'])

            sub.raw_total = Decimal(str(invalid_count))
            sub.effective_total = Decimal(str(invalid_count * 2))
            if action == 'submit_sheet':
                sub.is_draft = False
                sub.is_submitted = True
                sub.is_locked = True
                sub.submitted_at = timezone.now()
                sub.save()
                if sub.performance:
                    calculate_performance_score(sub.performance)
                messages.success(request, f"Evaluator sheet submitted and locked! Invalid items flagged: {invalid_count}")
                redirect_url = request.path
                params = []
                if perf_id:
                    params.append(f"perf_id={perf_id}")
                elif request.GET.get('panel_id'):
                    params.append(f"panel_id={request.GET.get('panel_id')}")
                if params:
                    redirect_url += '?' + '&'.join(params)
                return redirect(redirect_url)
            else:
                sub.save()
                messages.success(request, f"Evaluator draft saved. Invalid items: {invalid_count}")
                redirect_url = request.path
                params = []
                if perf_id:
                    params.append(f"perf_id={perf_id}")
                elif request.GET.get('panel_id'):
                    params.append(f"panel_id={request.GET.get('panel_id')}")
                if params:
                    redirect_url += '?' + '&'.join(params)
                return redirect(redirect_url)

        return render(request, 'scoring/evaluator.html', {
            'is_traditional': False,
            'batch': batch,
            'entry': entry,
            'perf': perf,
            'sub': sub,
            'items': items,
            'item_count': item_count,
        })

@role_required(RoleChoices.ADMIN, RoleChoices.CHIEF_JUDGE, RoleChoices.CD, 'APPROVED_JUDGE')
def scoring_chief_judge(request):
    """
    Dedicated Chief Judge Review Desk:
    - Scoped strictly to the active panel assigned to this Chief Judge.
    - Displays ONLY the competitors pushed by the Stage Manager for this panel.
    - Provides a full breakdown of scores for all 7 asanas across D1-D5, Time Judge, and Evaluator.
    """
    championship = get_active_championship(request)
    
    # 1. Identify current panel for this Chief Judge
    panel_qs = JudgePanel.objects.filter(championship=championship).select_related(
        'championship', 'event_config', 'age_category',
        'chief_judge', 'd1_judge', 'd2_judge', 'd3_judge', 'd4_judge', 'd5_judge',
        'time_judge', 'time_judge_2', 'evaluator', 'evaluator_2',
        'artistry_judge', 'artistry_judge_2', 'stage_manager'
    )
    is_admin_or_cd = is_system_admin(request.user) or ScopedRole.objects.filter(
        user=request.user, championship=championship, role=RoleChoices.CD
    ).exists()

    panel_id = request.GET.get('panel_id')
    assigned_panel = None

    if not is_admin_or_cd:
        # Non-admin/CD MUST be the assigned chief_judge for their panel
        user_chief_panels = panel_qs.filter(chief_judge=request.user, is_active=True)
        if not user_chief_panels.exists():
            AuditLog.log(
                action='SECURITY_UNASSIGNED_CHIEF_ACCESS',
                resource_type='JudgePanel',
                resource_id=panel_id or '',
                details={'path': request.path, 'user_id': request.user.id},
                user=request.user,
                ip_address=request.META.get('REMOTE_ADDR')
            )
            return render(request, '403.html', {'message': "Access Denied: You are not assigned as Chief Judge to any active panel in this championship."}, status=403)
        
        if panel_id:
            # Prevent accessing another panel's chief judge console
            matched_panel = user_chief_panels.filter(id=panel_id).first()
            if not matched_panel:
                AuditLog.log(
                    action='SECURITY_CROSS_PANEL_CHIEF_TAMPER',
                    resource_type='JudgePanel',
                    resource_id=panel_id,
                    details={'path': request.path, 'user_id': request.user.id, 'attempted_panel_id': panel_id},
                    user=request.user,
                    ip_address=request.META.get('REMOTE_ADDR')
                )
                return render(request, '403.html', {'message': f"Access Denied: You are not authorized to view or manage Panel #{panel_id} Chief Judge Console."}, status=403)
            assigned_panel = matched_panel
        else:
            assigned_panel = user_chief_panels.first()
    else:
        # Admin / CD can view requested panel or fallback to first
        if panel_id:
            assigned_panel = panel_qs.filter(id=panel_id).first()
        if not assigned_panel:
            assigned_panel = panel_qs.filter(is_active=True).first()

    if not assigned_panel:
        return render(request, 'scoring/no_panel_assigned.html', {'championship': championship})

    # 2. Get active batch and only pushed athletes for this panel
    active_batch = StageBatch.objects.filter(panel=assigned_panel, status='ACTIVE').first()
    batch_entries = active_batch.entries.all() if active_batch else []

    # Get performances ONLY for pushed athletes in this active batch
    if active_batch and batch_entries.exists():
        entry_id_list = list(batch_entries.values_list('id', flat=True))
        order_map = {eid: idx for idx, eid in enumerate(entry_id_list)}
        performances = list(Performance.objects.filter(
            event_entry__in=batch_entries,
            round_type=assigned_panel.round_type
        ).select_related(
            'event_entry__registration__championship',
            'event_entry__registration__athlete',
            'event_entry__event_config',
            'event_entry__age_category'
        ))
        performances.sort(key=lambda p: order_map.get(p.event_entry_id, 9999))
    else:
        performances = []

    current_event = assigned_panel.event_config
    current_cat = assigned_panel.age_category
    gender = assigned_panel.gender
    round_type = assigned_panel.round_type
    event_configs = [current_event] if current_event else []
    categories = [current_cat] if current_cat else []

    perf_id = request.GET.get('perf_id')
    selected_perf = None
    if performances:
        if perf_id:
            try:
                p_id_int = int(perf_id)
                selected_perf = next((p for p in performances if p.id == p_id_int), None)
                if not selected_perf:
                    messages.warning(request, f"Requested competitor (Performance #{perf_id}) is not active in the current batch on {assigned_panel.stage_name}.")
            except (ValueError, TypeError):
                messages.warning(request, "Invalid performance ID requested.")
        else:
            selected_perf = performances[0]

    score_calc = None
    if selected_perf:
        score_calc = calculate_performance_score(selected_perf, persist=False)
        # Enrich seat_status with official assigned judge names and friendly role labels
        seat_judge_map = {
            'D1_JUDGE': ('D1 Judge (Difficulty)', getattr(assigned_panel.d1_judge, 'full_name', 'D1 Official')),
            'D2_JUDGE': ('D2 Judge (Difficulty)', getattr(assigned_panel.d2_judge, 'full_name', 'D2 Official')),
            'D3_JUDGE': ('D3 Judge (Difficulty)', getattr(assigned_panel.d3_judge, 'full_name', 'D3 Official')),
            'D4_JUDGE': ('D4 Judge (Difficulty)', getattr(assigned_panel.d4_judge, 'full_name', 'D4 Official')),
            'D5_JUDGE': ('D5 Judge (Difficulty)', getattr(assigned_panel.d5_judge, 'full_name', 'D5 Official')),
            'TIME_JUDGE': ('Time Judge 1 (Hold Timer)', getattr(assigned_panel.time_judge, 'full_name', 'Time Official')),
            'TIME_JUDGE_2': ('Time Judge 2 (Routine)', getattr(assigned_panel.time_judge_2, 'full_name', 'Time Official 2')),
            'EVALUATOR': ('Evaluator 1 (Rules/Sequence)', getattr(assigned_panel.evaluator, 'full_name', 'Evaluator')),
            'EVALUATOR_2': ('Evaluator 2 (Pair Sync)', getattr(assigned_panel.evaluator_2, 'full_name', 'Evaluator 2')),
            'ARTISTRY_JUDGE': ('Artistry Judge 1', getattr(assigned_panel.artistry_judge, 'full_name', 'Artistry Official')),
            'ARTISTRY_JUDGE_2': ('Artistry Judge 2', getattr(assigned_panel.artistry_judge_2, 'full_name', 'Artistry Official 2')),
        }
        for seat in score_calc.get('seat_status', []):
            rc = seat.get('role_code')
            info = seat_judge_map.get(rc, (rc.replace('_', ' ').title(), 'Official'))
            seat['role_label'] = info[0]
            seat['judge_name'] = info[1]
            if seat.get('is_submitted'):
                seat['badge_class'] = 'bg-emerald-100 text-emerald-800 border-emerald-200'
            else:
                seat['badge_class'] = 'bg-amber-100 text-amber-800 border-amber-200'

    if request.method == 'POST':
        action = request.POST.get('action')
        p_id = request.POST.get('performance_id')
        perf = get_object_or_404(Performance, id=p_id)

        if action == 'provisional_release':
            calc = calculate_performance_score(perf)
            if not calc['can_release']:
                for r in calc['block_reasons']:
                    messages.error(request, f"Release Blocked: {r}")
                return redirect(f"/scoring/chief-judge/?perf_id={perf.id}&event_id={current_event.id if current_event else ''}&cat_id={current_cat.id if current_cat else ''}&gender={gender}&round_type={round_type}")

            perf.status = PerformanceStatus.PROVISIONAL_RELEASED
            perf.provisional_score = calc['provisional_total']
            perf.final_score = calc['provisional_total']
            perf.chief_approved_by = request.user
            perf.chief_approved_at = datetime.datetime.now()
            perf.save()

            AuditLog.log(
                action='CHIEF_PROVISIONAL_RELEASE',
                resource_type='Performance',
                resource_id=str(perf.id),
                details={
                    'chest_number': perf.chest_number,
                    'provisional_score': str(calc['provisional_total']),
                    'retained_d': str(calc['retained_d']),
                    'penalties': str(calc['penalties']),
                    'seat_count': len(calc['seat_status'])
                },
                user=request.user,
                ip_address=request.META.get('REMOTE_ADDR')
            )
            messages.success(request, f"Provisional score of {calc['provisional_total']} released for Chest {perf.chest_number}! Broadcast to Live Public Screen.")
            return redirect(f"/scoring/chief-judge/?perf_id={perf.id}&event_id={current_event.id if current_event else ''}&cat_id={current_cat.id if current_cat else ''}&gender={gender}&round_type={round_type}")

        elif action == 'approve_incident':
            inc_id = request.POST.get('incident_id')
            inc = get_object_or_404(EvaluatorIncident, id=inc_id, performance=perf)
            inc.chief_decision = 'APPROVED'
            inc.chief_decided_at = timezone.now()
            inc.save(update_fields=['chief_decision', 'chief_decided_at'])
            messages.success(request, f"Incident #{inc.id} ({inc.get_incident_type_display()}) penalty confirmed by Chief Judge.")
            return redirect(f"/scoring/chief-judge/?perf_id={perf.id}&event_id={current_event.id if current_event else ''}&cat_id={current_cat.id if current_cat else ''}&gender={gender}&round_type={round_type}")

        elif action == 'reject_incident':
            inc_id = request.POST.get('incident_id')
            inc = get_object_or_404(EvaluatorIncident, id=inc_id, performance=perf)
            inc.chief_decision = 'REJECTED'
            inc.chief_decided_at = timezone.now()
            inc.save(update_fields=['chief_decision', 'chief_decided_at'])
            messages.info(request, f"Incident #{inc.id} penalty dismissed/rejected by Chief Judge.")
            return redirect(f"/scoring/chief-judge/?perf_id={perf.id}&event_id={current_event.id if current_event else ''}&cat_id={current_cat.id if current_cat else ''}&gender={gender}&round_type={round_type}")

        elif action == 'request_correction':
            return redirect(f"/corrections/cd-undo/?perf_id={perf.id}")

    # Sequence items for active performer
    sequence_items = []
    if selected_perf:
        sequence_items = AsanaSequenceItem.objects.filter(
            event_entry=selected_perf.event_entry,
            round_type=selected_perf.round_type
        ).order_by('sequence_order')

    incidents = EvaluatorIncident.objects.filter(performance=selected_perf).order_by('-created_at') if selected_perf else []

    # Calculate previous and next performance for navigation
    prev_perf = None
    next_perf = None
    if selected_perf and performances and len(performances) > 1:
        try:
            curr_idx = next(i for i, p in enumerate(performances) if p.id == selected_perf.id)
            if curr_idx > 0:
                prev_perf = performances[curr_idx - 1]
            if curr_idx < len(performances) - 1:
                next_perf = performances[curr_idx + 1]
        except StopIteration:
            pass

    return render(request, 'scoring/chief_judge.html', {
        'panel': assigned_panel,
        'batch': active_batch,
        'performances': performances,
        'selected_perf': selected_perf,
        'prev_perf': prev_perf,
        'next_perf': next_perf,
        'score_calc': score_calc,
        'sequence_items': sequence_items,
        'incidents': incidents,
        'perf': selected_perf,
        'interruption_logs': selected_perf.interruption_logs.all() if selected_perf else [],
        'championship': championship,
        'event_configs': event_configs,
        'current_event': current_event,
        'categories': categories,
        'current_cat': current_cat,
        'gender': gender,
        'round_type': round_type,
    })

@login_required
def judge_my_scores(request):
    """
    Judge Submitted Scores & Verdict History Module:
    Allows any judge to inspect the scores and judgements they have entered across
    all competitors, batches, and panels.
    - Read-only review with asana-wise breakdown (posture, stability, timing, invalidities, deductions).
    - Grouped by batch and athlete for fast review.
    """
    championship = get_active_championship(request)
    user = request.user

    # Fetch all submissions made by this judge or where this judge is assigned
    submissions_qs = JudgeSubmission.objects.filter(
        judge=user
    ).select_related(
        'performance__event_entry__event_config',
        'performance__event_entry__age_category',
        'performance__batch__panel',
    ).prefetch_related('items').order_by('-submitted_at', '-id')

    if championship:
        submissions_qs = submissions_qs.filter(
            Q(performance__event_entry__event_config__championship=championship) |
            Q(performance__batch__panel__championship=championship)
        )

    # Also include EvaluatorIncident reports filed by this evaluator
    evaluator_incidents = list(EvaluatorIncident.objects.filter(
        reporting_evaluator=user
    ).select_related('performance__event_entry').order_by('-created_at'))

    from registrations.models import AsanaSequenceItem

    # Group submissions by batch
    batch_map = {}
    total_evaluations = 0

    for s in submissions_qs:
        perf = s.performance
        batch = perf.batch if perf else None
        batch_id = batch.id if batch else 0
        panel = batch.panel if batch else None

        if batch_id not in batch_map:
            batch_map[batch_id] = {
                'batch': batch,
                'batch_number': batch.batch_number if batch else None,
                'panel': panel,
                'event_config': perf.event_entry.event_config if (perf and perf.event_entry) else (panel.event_config if panel else None),
                'age_category': perf.event_entry.age_category if (perf and perf.event_entry) else (panel.age_category if panel else None),
                'gender': perf.event_entry.gender if (perf and perf.event_entry) else (panel.gender if panel else 'M'),
                'round_type': perf.round_type if perf else (panel.round_type if panel else 'PRELIMINARY'),
                'stage_name': panel.stage_name if panel else 'Arena',
                'submissions': []
            }

        # Pre-resolve asana codes and names for each item
        items_detail = []
        for itm in s.items.all().order_by('sequence_order'):
            pose = None
            if perf and perf.event_entry:
                pose = AsanaSequenceItem.objects.filter(
                    event_entry=perf.event_entry,
                    sequence_order=itm.sequence_order
                ).first()

            items_detail.append({
                'item': itm,
                'seq': itm.sequence_order,
                'code': pose.declared_code if pose else f"A{itm.sequence_order}",
                'name': pose.declared_name if pose else f"Asana #{itm.sequence_order}",
                'is_compulsory': getattr(pose, 'is_compulsory', None),
                'posture': itm.posture_score,
                'stability': itm.stability_score,
                'timing_contrib': itm.timing_contrib if hasattr(itm, 'timing_contrib') else itm.timing_contribution,
                'hold_duration': itm.hold_duration_seconds,
                'base_value': itm.base_value,
                'net_score': itm.effective_item_score,
                'is_valid': itm.is_valid,
                'invalid_reason': itm.invalid_reason,
            })

        batch_map[batch_id]['submissions'].append({
            'sub': s,
            'perf': perf,
            'entry': perf.event_entry if perf else None,
            'chest_number': perf.chest_number if perf else '--',
            'athlete_name': perf.event_entry.display_names if (perf and perf.event_entry) else 'Athlete',
            'role_label': s.get_judge_role_display() if hasattr(s, 'get_judge_role_display') else s.judge_role,
            'items': items_detail,
            'effective_total': s.effective_total,
            'is_submitted': s.is_submitted,
            'submitted_at': s.submitted_at,
        })
        total_evaluations += 1

    batches_list = list(batch_map.values())

    return render(request, 'scoring/judge_my_scores.html', {
        'championship': championship,
        'batches': batches_list,
        'total_evaluations': total_evaluations,
        'evaluator_incidents': evaluator_incidents,
        'judge_user': user,
    })

