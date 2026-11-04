import datetime
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.db import transaction
from .models import Performance, PerformanceStatus, JudgeSubmission, AsanaScoreItem, PerformanceArtistryScore, EvaluatorIncident, IncidentType
from .services import calculate_performance_score, calculate_traditional_timing_contribution
from competition.models import StageBatch, JudgePanel
from registrations.models import EventEntry, AsanaSequenceItem
from catalogue.models import Asana
from accounts.models import RoleChoices, ScopedRole
from accounts.permissions import role_required, get_active_championship
from audit.models import AuditLog

def resolve_asana_compulsory_and_bv(entry, round_type, seq_num, pose_seq=None):
    if pose_seq is None and entry:
        pose_seq = AsanaSequenceItem.objects.filter(event_entry=entry, sequence_order=seq_num, round_type=round_type).first()
        if not pose_seq:
            pose_seq = AsanaSequenceItem.objects.filter(event_entry=entry, sequence_order=seq_num).first()

    if pose_seq and hasattr(pose_seq, 'is_compulsory') and pose_seq.is_compulsory is not None:
        is_comp = pose_seq.is_compulsory
    else:
        cfg = getattr(entry, 'event_config', None)
        if cfg and hasattr(cfg, 'final_compulsory_count'):
            comp_count = cfg.final_compulsory_count if round_type == 'FINAL' else cfg.prelim_compulsory_count
        else:
            comp_count = 2 if round_type == 'FINAL' else 4
        is_comp = (seq_num <= comp_count)

    if is_comp:
        bv = Decimal('1.00')
    elif pose_seq and pose_seq.declared_bv:
        bv = pose_seq.declared_bv
    else:
        bv = Decimal('0.80')

    return is_comp, bv

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

    if panel_id:
        current_panel = JudgePanel.objects.filter(id=panel_id, championship=championship, is_active=True).first()

    if not current_panel:
        panels = JudgePanel.objects.filter(championship=championship, is_active=True, is_closed=False)
        if event_type:
            panels = panels.filter(event_config__event_type__icontains=event_type)
        if round_type:
            panels = panels.filter(round_type=round_type)

        fields_to_check = seat_fields or JudgePanel.SEAT_FIELDS
        for p in panels:
            for f in fields_to_check:
                if getattr(p, f"{f}_id", None) == request.user.id:
                    current_panel = p
                    seat_label = JudgePanel.SEAT_LABELS.get(f, 'Judge')
                    break
            if current_panel:
                break

    # Admin / CD preview fallback
    if not current_panel and (request.user.is_superuser or ScopedRole.objects.filter(user=request.user, championship=championship, role=RoleChoices.CD).exists()):
        panels = JudgePanel.objects.filter(championship=championship, is_active=True, is_closed=False)
        if event_type:
            panels = panels.filter(event_config__event_type__icontains=event_type)
        if round_type:
            panels = panels.filter(round_type=round_type)
        current_panel = panels.first()
        if current_panel:
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
    if not effective_role:
        lbl_upper = seat_label.upper()
        if 'D1' in lbl_upper: effective_role = 'D1_JUDGE'
        elif 'D2' in lbl_upper: effective_role = 'D2_JUDGE'
        elif 'D3' in lbl_upper: effective_role = 'D3_JUDGE'
        elif 'D4' in lbl_upper: effective_role = 'D4_JUDGE'
        elif 'D5' in lbl_upper: effective_role = 'D5_JUDGE'
        elif 'TIME' in lbl_upper: effective_role = 'TIME_JUDGE'
        elif 'EVAL' in lbl_upper: effective_role = 'EVALUATOR'
        elif 'ARTISTRY' in lbl_upper: effective_role = 'ARTISTRY_JUDGE'
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
        seat_fields=['d1_judge', 'd2_judge', 'd3_judge', 'd4_judge', 'chief_judge'],
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
    judge_role = seat_label.replace(' ', '_').upper()
    if judge_role not in ['D1_JUDGE', 'D2_JUDGE', 'D3_JUDGE', 'D4_JUDGE']:
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
                'posture_score': Decimal('5.00'),
                'stability_score': Decimal('1.50'),
                'timing_contribution': Decimal('2.00'),
                'effective_item_score': Decimal('8.50'),
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
        if is_eval_invalid:
            item.is_valid = False
            item.invalid_reason = invalid_reason or "Evaluator Confirmed Invalid Asana"
            item.effective_item_score = Decimal('0.000')
        else:
            item.is_valid = True
            item.invalid_reason = ""
            raw_calc = (item.posture_score + item.stability_score + t_contrib) * bv_val
            item.raw_item_score = raw_calc
            item.effective_item_score = raw_calc
        item.save(update_fields=['base_value', 'timing_contribution', 'is_valid', 'invalid_reason', 'raw_item_score', 'effective_item_score'])

        submissions[e.id] = {
            'entry': e,
            'performance': perf,
            'submission': sub,
            'item': item,
            'pose': pose_seq,
            'is_invalid': is_eval_invalid,
            'invalid_reason': invalid_reason,
            'timing_contribution': t_contrib,
            'is_compulsory': is_compulsory,
            'base_value': bv_val,
        }

    # Handle POST
    if request.method == 'POST':
        action = request.POST.get('action') # 'save_draft', 'submit_item_next', 'submit_final_batch'

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

            if item.is_locked and not request.user.is_superuser:
                continue

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
            return redirect(f"{request.path}?seq={current_asana_seq}")

        elif action == 'submit_item_next':
            if current_asana_seq < total_items:
                next_seq = current_asana_seq + 1
                messages.success(request, f"Asana #{current_asana_seq} committed & locked. Opened Asana #{next_seq}.")
                return redirect(f"{request.path}?seq={next_seq}")
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
        seat_fields=['d1_judge', 'd2_judge', 'd3_judge', 'd4_judge', 'chief_judge'],
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
    if judge_role not in ['D1_JUDGE', 'D2_JUDGE', 'D3_JUDGE', 'D4_JUDGE']:
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
                'posture_score': Decimal('5.00'),
                'stability_score': Decimal('1.50'),
                'timing_contribution': Decimal('2.00'),
                'effective_item_score': Decimal('8.50'),
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
        if is_eval_invalid:
            item.is_valid = False
            item.invalid_reason = invalid_reason or "Evaluator Confirmed Invalid Asana"
            item.effective_item_score = Decimal('0.000')
        else:
            item.is_valid = True
            item.invalid_reason = ""
            raw_calc = (item.posture_score + item.stability_score + t_contrib) * bv_val
            item.raw_item_score = raw_calc
            item.effective_item_score = raw_calc
        item.save(update_fields=['base_value', 'timing_contribution', 'is_valid', 'invalid_reason', 'raw_item_score', 'effective_item_score'])

        submissions[e.id] = {
            'entry': e,
            'performance': perf,
            'submission': sub,
            'item': item,
            'pose': pose_seq,
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

            if item.is_locked and not request.user.is_superuser:
                continue

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
            return redirect(f"{request.path}?seq={current_asana_seq}")

        elif action == 'submit_item_next':
            if current_asana_seq < total_items:
                next_seq = current_asana_seq + 1
                messages.success(request, f"Final Asana #{current_asana_seq} committed & locked. Opened Asana #{next_seq}.")
                return redirect(f"{request.path}?seq={next_seq}")
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
    Implements 18_Artistic_Single_D_Judge.png:
    Ten-item single execution sheet with pose image, BV, execution marks,
    evaluator validity indicator, and effective score.
    """
    perf_id = request.GET.get('perf_id')
    batch = StageBatch.objects.filter(status='ACTIVE').first()
    
    if perf_id:
        perf = get_object_or_404(Performance, id=perf_id)
        entry = perf.event_entry
    else:
        entry = batch.entries.first() if batch else EventEntry.objects.filter(event_config__event_type__icontains='ARTISTIC_SINGLE').first()
        perf, _ = Performance.objects.get_or_create(
            event_entry=entry,
            round_type='PRELIMINARY',
            defaults={'status': PerformanceStatus.IN_PROGRESS}
        ) if entry else (None, False)

    judge_role = 'D1_JUDGE'
    sub = JudgeSubmission.objects.filter(performance=perf, judge_role=judge_role).first() if perf else None
    if not sub and perf:
        sub = JudgeSubmission.objects.create(
            performance=perf,
            judge=request.user,
            judge_role=judge_role,
            version=1,
            is_draft=True
        )

    items = []
    if sub and entry:
        for seq in range(1, 11):
            pose_seq = AsanaSequenceItem.objects.filter(event_entry=entry, sequence_order=seq).first()
            score_item, _ = AsanaScoreItem.objects.get_or_create(
                submission=sub,
                sequence_order=seq,
                defaults={
                    'execution_score': Decimal('8.50'),
                    'base_value': pose_seq.declared_bv if pose_seq else Decimal('0.80'),
                    'is_valid': True
                }
            )
            items.append({'score_item': score_item, 'pose': pose_seq, 'seq': seq})

    if request.method == 'POST' and sub:
        action = request.POST.get('action')
        total = Decimal('0.000')
        for seq in range(1, 11):
            exec_val = Decimal(request.POST.get(f'exec_{seq}', '8.00'))
            bv_val = Decimal(request.POST.get(f'bv_{seq}', '0.80'))
            eff = round(exec_val * bv_val, 3)
            total += eff
            AsanaScoreItem.objects.update_or_create(
                submission=sub,
                sequence_order=seq,
                defaults={'execution_score': exec_val, 'base_value': bv_val, 'effective_item_score': eff}
            )
        
        sub.raw_total = total
        sub.effective_total = total
        if action == 'submit_sheet':
            sub.is_draft = False
            sub.is_submitted = True
            sub.is_locked = True
            sub.submitted_at = datetime.datetime.now()
            sub.save()
            if sub.performance:
                calculate_performance_score(sub.performance)
            messages.success(request, f"Artistic Single D scores submitted and locked! Total: {total:.3f}")
            return redirect(request.path)
        else:
            sub.is_draft = True
            sub.save()
            messages.success(request, f"Artistic Single D draft saved. Total: {total:.3f}")

    return render(request, 'scoring/artistic_single_d.html', {
        'batch': batch,
        'entry': entry,
        'perf': perf,
        'sub': sub,
        'items': items,
    })

@role_required(RoleChoices.ADMIN, RoleChoices.D_JUDGE, RoleChoices.CD, 'APPROVED_JUDGE')
def scoring_artistic_pair_d(request):
    """
    Implements 19_Artistic_Pair_D_Judge.png:
    Minimal pair desk: Chest A submitted pose, Chest B submitted pose, pair formation,
    two large execution fields and formation execution field. Ten-item navigation.
    """
    perf_id = request.GET.get('perf_id')
    batch = StageBatch.objects.filter(status='ACTIVE').first()
    current_seq = int(request.GET.get('seq', 1))

    if perf_id:
        perf = get_object_or_404(Performance, id=perf_id)
        entry = perf.event_entry
    else:
        entry = batch.entries.filter(is_pair=True).first() if batch else EventEntry.objects.filter(is_pair=True).first()
        perf, _ = Performance.objects.get_or_create(
            event_entry=entry,
            round_type='FINAL',
            defaults={'status': PerformanceStatus.IN_PROGRESS}
        ) if entry else (None, False)

    sub = JudgeSubmission.objects.filter(performance=perf, judge_role='D1_JUDGE').first() if perf else None
    if not sub and perf:
        sub = JudgeSubmission.objects.create(
            performance=perf,
            judge=request.user,
            judge_role='D1_JUDGE',
            version=1,
            is_draft=True
        )

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
    batch = StageBatch.objects.filter(status='ACTIVE').first()
    current_seq = int(request.GET.get('seq', 1))

    if perf_id:
        perf = get_object_or_404(Performance, id=perf_id)
        entry = perf.event_entry
    else:
        entry = batch.entries.filter(is_pair=True).first() if batch else EventEntry.objects.filter(is_pair=True).first()
        perf, _ = Performance.objects.get_or_create(
            event_entry=entry,
            round_type='FINAL',
            defaults={'status': PerformanceStatus.IN_PROGRESS}
        ) if entry else (None, False)

    sub = JudgeSubmission.objects.filter(performance=perf, judge_role='D1_JUDGE').first() if perf else None
    if not sub and perf:
        sub = JudgeSubmission.objects.create(
            performance=perf,
            judge=request.user,
            judge_role='D1_JUDGE',
            version=1,
            is_draft=True
        )

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
    # Default target hold based on round_type and seq
    default_is_comp = (current_seq <= 2) if round_type == 'FINAL' else (current_seq <= 4)
    is_compulsory = default_is_comp
    target_hold = Decimal('45.00') if is_compulsory else Decimal('30.00')

    submissions = {}
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
        pose_seq = AsanaSequenceItem.objects.filter(event_entry=e, sequence_order=current_seq, round_type=round_type).first()
        if not pose_seq:
            pose_seq = AsanaSequenceItem.objects.filter(event_entry=e, sequence_order=current_seq).first()
        is_comp, bv_val = resolve_asana_compulsory_and_bv(e, round_type, current_seq, pose_seq)
        entry_target_hold = Decimal('45.00') if is_comp else Decimal('30.00')
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
            'is_compulsory': is_comp,
            'base_value': bv_val
        }

    if request.method == 'POST':
        action = request.POST.get('action')
        for e in entries:
            hold_raw = request.POST.get(f'hold_{e.id}', '0.0')
            try:
                hold_secs = max(Decimal('0.00'), Decimal(hold_raw))
            except:
                hold_secs = Decimal('0.00')
            try:
                attempt_num = int(request.POST.get(f'attempt_{e.id}', '1'))
            except:
                attempt_num = 1

            pose_seq = AsanaSequenceItem.objects.filter(event_entry=e, sequence_order=current_seq).first()
            e_target = target_hold
            if not is_compulsory and pose_seq and pose_seq.declared_hold_time:
                e_target = pose_seq.declared_hold_time

            contrib = calculate_traditional_timing_contribution(hold_secs, e_target, attempt=attempt_num)
            sub = submissions[e.id]['sub']
            time_item, _ = AsanaScoreItem.objects.update_or_create(
                submission=sub,
                sequence_order=current_seq,
                defaults={
                    'hold_duration_seconds': hold_secs,
                    'attempt_number': attempt_num,
                    'timing_contribution': contrib,
                    'effective_item_score': contrib
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
                if d_item.is_valid:
                    raw_calc = (d_item.posture_score + d_item.stability_score + contrib) * d_item.base_value
                    d_item.raw_item_score = raw_calc
                    d_item.effective_item_score = raw_calc
                d_item.save(update_fields=['timing_contribution', 'raw_item_score', 'effective_item_score'])

        if action == 'submit_timing':
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
            messages.success(request, f"Traditional Time scores submitted and locked for Batch #{batch.batch_number}!")
            return redirect(request.path)
        else:
            messages.success(request, f"Timing saved for Asana #{current_seq}.")
            next_seq = min(7, current_seq + 1)
            return redirect(f"/scoring/traditional-time/?seq={next_seq}")

    return render(request, 'scoring/traditional_time.html', {
        'batch': batch,
        'entries': entries,
        'current_seq': current_seq,
        'submissions': submissions,
        'is_compulsory': is_compulsory,
        'target_hold': target_hold,
    })

@role_required(RoleChoices.ADMIN, RoleChoices.TIME_JUDGE, RoleChoices.CD, 'APPROVED_JUDGE')
def scoring_music_time(request):
    """
    Implements 21_Music_Event_Time_Judge.png:
    Whole routine stopwatch and 10 individual hold duration timers for music events.
    """
    perf_id = request.GET.get('perf_id')
    batch = StageBatch.objects.filter(status='ACTIVE').first()
    
    if perf_id:
        perf = get_object_or_404(Performance, id=perf_id)
        entry = perf.event_entry
    else:
        entry = batch.entries.first() if batch else EventEntry.objects.filter(event_config__event_type__icontains='ARTISTIC').first()
        perf, _ = Performance.objects.get_or_create(
            event_entry=entry,
            round_type='FINAL',
            defaults={'status': PerformanceStatus.IN_PROGRESS}
        ) if entry else (None, False)

    sub = JudgeSubmission.objects.filter(performance=perf, judge_role='TIME_JUDGE').first() if perf else None
    if not sub and perf:
        sub = JudgeSubmission.objects.create(
            performance=perf,
            judge=request.user,
            judge_role='TIME_JUDGE',
            version=1,
            is_draft=True
        )

    if request.method == 'POST' and sub:
        action = request.POST.get('action')
        routine_duration = Decimal(request.POST.get('routine_duration', '145.0'))
        # Overtime penalty COP: target max 150s. Deduct 0.5 per 5s over 150s.
        penalty = Decimal('0.00')
        if routine_duration > Decimal('150.0'):
            penalty = round(((routine_duration - Decimal('150.0')) / Decimal('5.0')) * Decimal('0.5'), 2)

        timing_score = max(Decimal('0.00'), Decimal('10.00') - penalty)
        sub.raw_total = routine_duration
        sub.effective_total = timing_score
        if action == 'submit_sheet':
            sub.is_draft = False
            sub.is_submitted = True
            sub.is_locked = True
            sub.submitted_at = datetime.datetime.now()
            sub.save()
            if sub.performance:
                calculate_performance_score(sub.performance)
            messages.success(request, f"Music timing submitted! Duration: {routine_duration}s, Timing Score: {timing_score:.2f}")
            return redirect(request.path)
        else:
            sub.save()
            messages.success(request, f"Routine duration draft saved: {routine_duration}s")

    return render(request, 'scoring/music_time.html', {
        'batch': batch,
        'entry': entry,
        'perf': perf,
        'sub': sub,
    })

@role_required(RoleChoices.ADMIN, RoleChoices.ARTISTRY_JUDGE, RoleChoices.CD, 'APPROVED_JUDGE')
def scoring_artistry(request):
    """
    Implements 22_Artistry_Judge.png:
    Performance-level artistry criteria (20-point breakdown), routine sequence, deductions.
    """
    perf_id = request.GET.get('perf_id')
    batch = StageBatch.objects.filter(status='ACTIVE').first()
    
    if perf_id:
        perf = get_object_or_404(Performance, id=perf_id)
        entry = perf.event_entry
    else:
        entry = batch.entries.first() if batch else EventEntry.objects.filter(event_config__event_type__icontains='ARTISTIC').first()
        perf, _ = Performance.objects.get_or_create(
            event_entry=entry,
            round_type='FINAL',
            defaults={'status': PerformanceStatus.IN_PROGRESS}
        ) if entry else (None, False)

    sub = JudgeSubmission.objects.filter(performance=perf, judge_role='ARTISTRY_JUDGE').first() if perf else None
    if not sub and perf:
        sub = JudgeSubmission.objects.create(
            performance=perf,
            judge=request.user,
            judge_role='ARTISTRY_JUDGE',
            version=1,
            is_draft=True
        )

    art_score, _ = PerformanceArtistryScore.objects.get_or_create(
        submission=sub,
        defaults={
            'presentation': Decimal('3.50'),
            'synchronization': Decimal('3.50'),
            'choreography': Decimal('3.50'),
            'transition_flow': Decimal('3.50'),
            'costume_grace': Decimal('3.00'),
            'deductions': Decimal('0.00'),
            'total_artistry': Decimal('17.00')
        }
    ) if sub else (None, False)

    if request.method == 'POST' and sub:
        action = request.POST.get('action')
        art_score.presentation = min(Decimal('4.00'), Decimal(request.POST.get('presentation', '3.50')))
        art_score.synchronization = min(Decimal('4.00'), Decimal(request.POST.get('synchronization', '3.50')))
        art_score.choreography = min(Decimal('4.00'), Decimal(request.POST.get('choreography', '3.50')))
        art_score.transition_flow = min(Decimal('4.00'), Decimal(request.POST.get('transition_flow', '3.50')))
        art_score.costume_grace = min(Decimal('4.00'), Decimal(request.POST.get('costume_grace', '3.00')))
        art_score.deductions = Decimal(request.POST.get('deductions', '0.00'))
        total = art_score.calculate_total()
        art_score.save()

        sub.raw_total = total
        sub.effective_total = total
        if action == 'submit_sheet':
            sub.is_draft = False
            sub.is_submitted = True
            sub.is_locked = True
            sub.submitted_at = datetime.datetime.now()
            sub.save()
            if sub.performance:
                calculate_performance_score(sub.performance)
            messages.success(request, f"Artistry scores submitted and locked! Total: {total:.2f}")
            return redirect(request.path)
        else:
            sub.save()
            messages.success(request, f"Artistry draft saved! Total: {total:.2f}")

    return render(request, 'scoring/artistry.html', {
        'batch': batch,
        'entry': entry,
        'perf': perf,
        'sub': sub,
        'art_score': art_score,
    })

@role_required(RoleChoices.ADMIN, RoleChoices.EVALUATOR, RoleChoices.CD, 'APPROVED_JUDGE')
def scoring_evaluator(request):
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

        sequence_tabs = []
        for s in range(1, total_items + 1):
            sequence_tabs.append({
                'seq': s,
                'is_current': (s == current_seq)
            })

        athletes = []
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
            score_item, _ = AsanaScoreItem.objects.get_or_create(
                submission=sub,
                sequence_order=current_seq,
                defaults={'is_valid': True, 'invalid_reason': ''}
            )
            pose = AsanaSequenceItem.objects.filter(event_entry=e, sequence_order=current_seq, round_type=round_type).first()
            if not pose:
                pose = AsanaSequenceItem.objects.filter(event_entry=e, sequence_order=current_seq).first()
            is_compulsory, declared_bv = resolve_asana_compulsory_and_bv(e, round_type, current_seq, pose)

            seq_inc = EvaluatorIncident.objects.filter(performance=perf, incident_type=IncidentType.SEQUENCE_CHANGE).first()
            seq_penalty = int(seq_inc.penalty_marks) if seq_inc else 0
            is_dq = bool(seq_inc and seq_inc.evidence_notes == 'DQ')

            other_inc = EvaluatorIncident.objects.filter(performance=perf, incident_type=IncidentType.OTHER_RULE).first()
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
                if action == 'submit_sheet':
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
                            defaults={
                                'chest_number': e.chest_number or str(e.id),
                                'reporting_evaluator': request.user,
                                'penalty_marks': pen_marks,
                                'evidence_notes': f'-{pen_marks} marks sequence change penalty',
                                'chief_decision': 'APPROVED',
                            }
                        )
                    else:
                        EvaluatorIncident.objects.filter(
                            performance=perf,
                            incident_type=IncidentType.SEQUENCE_CHANGE
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
                        defaults={
                            'chest_number': e.chest_number or str(e.id),
                            'reporting_evaluator': request.user,
                            'penalty_marks': other_marks,
                            'evidence_notes': other_reason_str or 'Other technical penalty',
                            'chief_decision': 'APPROVED',
                        }
                    )
                else:
                    EvaluatorIncident.objects.filter(
                        performance=perf,
                        incident_type=IncidentType.OTHER_RULE
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
                return redirect(f"{request.path}?seq={current_seq}")
            elif action == 'save_next':
                if current_seq < total_items:
                    next_seq = current_seq + 1
                    messages.success(request, f"Asana #{current_seq} evaluated. Opened Asana #{next_seq}.")
                    return redirect(f"{request.path}?seq={next_seq}")
                else:
                    messages.success(request, "All asana sequences evaluated for batch!")
                    return redirect(request.path)
            elif action == 'submit_sheet':
                messages.success(request, f"Evaluator sheet finalized and locked for Batch #{batch.batch_number if batch else '1'}!")
                return redirect(request.path)

        return render(request, 'scoring/evaluator.html', {
            'is_traditional': True,
            'batch': batch,
            'athletes': athletes,
            'round_type': round_type,
            'current_seq': current_seq,
            'total_items': total_items,
            'sequence_tabs': sequence_tabs,
        })
    else:
        # Non-traditional (Artistic / Rhythmic) routine view
        if perf_id:
            perf = get_object_or_404(Performance, id=perf_id)
            entry = perf.event_entry
        else:
            entry = batch.entries.first() if batch else EventEntry.objects.first()
            perf, _ = Performance.objects.get_or_create(
                event_entry=entry,
                round_type='FINAL',
                defaults={'status': PerformanceStatus.IN_PROGRESS}
            ) if entry else (None, False)

        sub = JudgeSubmission.objects.filter(performance=perf, judge_role='EVALUATOR').first() if perf else None
        if not sub and perf:
            sub = JudgeSubmission.objects.create(
                performance=perf,
                judge=request.user,
                judge_role='EVALUATOR',
                version=1,
                is_draft=True
            )

        item_count = 10
        items = []
        if sub and entry:
            for seq in range(1, item_count + 1):
                pose_seq = AsanaSequenceItem.objects.filter(event_entry=entry, sequence_order=seq).first()
                score_item, _ = AsanaScoreItem.objects.get_or_create(
                    submission=sub,
                    sequence_order=seq,
                    defaults={'is_valid': True, 'invalid_reason': ''}
                )
                items.append({'score_item': score_item, 'pose': pose_seq, 'seq': seq})

        if request.method == 'POST' and sub:
            action = request.POST.get('action')
            invalid_count = 0
            for seq in range(1, item_count + 1):
                is_valid_val = (request.POST.get(f'valid_{seq}') == 'true')
                reason_val = request.POST.get(f'reason_{seq}', '').strip()
                if not is_valid_val:
                    invalid_count += 1

                AsanaScoreItem.objects.update_or_create(
                    submission=sub,
                    sequence_order=seq,
                    defaults={'is_valid': is_valid_val, 'invalid_reason': reason_val}
                )

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
                return redirect(request.path)
            else:
                sub.save()
                messages.success(request, f"Evaluator draft saved. Invalid items: {invalid_count}")

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
    Implements 24_Chief_Judge_Review.png:
    Submission completeness across judges (D1-D4, Time, Evaluator),
    component breakdown, invalid items, and provisional score release.
    Calculates Olympic trimmed mean for D-judges, shows seat status (distinguishing
    missing from intentional zero), and enforces rule certifications before provisional release.
    """
    championship = get_active_championship(request)
    event_configs = championship.event_configurations.filter(is_enabled=True) if championship else []
    
    selected_event_id = request.GET.get('event_id')
    current_event = event_configs.filter(id=selected_event_id).first() if selected_event_id else event_configs.first()

    categories = current_event.age_categories.all() if current_event else []
    selected_cat_id = request.GET.get('cat_id')
    current_cat = categories.filter(id=selected_cat_id).first() if selected_cat_id else categories.first()

    gender = request.GET.get('gender', 'M')
    round_type = request.GET.get('round_type', 'PRELIMINARY')

    performances = Performance.objects.filter(
        event_entry__registration__championship=championship
    ).select_related('event_entry__registration__athlete', 'event_entry__event_config', 'event_entry__age_category')

    if current_event:
        performances = performances.filter(event_entry__event_config=current_event)
    if current_cat:
        performances = performances.filter(event_entry__age_category=current_cat)
    if gender:
        performances = performances.filter(event_entry__gender=gender)
    if round_type:
        performances = performances.filter(round_type=round_type)

    perf_id = request.GET.get('perf_id')
    selected_perf = performances.filter(id=perf_id).first() if perf_id else performances.first()

    score_calc = None
    if selected_perf:
        score_calc = calculate_performance_score(selected_perf)

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

        elif action == 'request_correction':
            return redirect(f"/corrections/cd-undo/?perf_id={perf.id}")

    # Sequence items for active performer
    sequence_items = []
    if selected_perf:
        sequence_items = AsanaSequenceItem.objects.filter(
            event_entry=selected_perf.event_entry,
            round_type=selected_perf.round_type
        ).order_by('sequence_order')

    return render(request, 'scoring/chief_judge.html', {
        'performances': performances,
        'selected_perf': selected_perf,
        'score_calc': score_calc,
        'sequence_items': sequence_items,
        'championship': championship,
        'event_configs': event_configs,
        'current_event': current_event,
        'categories': categories,
        'current_cat': current_cat,
        'gender': gender,
        'round_type': round_type,
    })
