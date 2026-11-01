import datetime
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.db import transaction
from .models import Performance, PerformanceStatus, JudgeSubmission, AsanaScoreItem, PerformanceArtistryScore
from .services import calculate_performance_score
from competition.models import StageBatch, JudgePanel
from registrations.models import EventEntry, AsanaSequenceItem
from catalogue.models import Asana
from accounts.models import RoleChoices, ScopedRole
from accounts.permissions import role_required, get_active_championship
from audit.models import AuditLog

def get_judge_panel_and_batch(request, seat_fields=None, round_type=None, event_type=None):
    """
    Resolves the judge's assigned panel, seat label, and active pushed StageBatch.
    Returns: (panel, seat_label, batch_or_response)
    If no panel assigned: returns (None, None, HttpResponse to no_panel_assigned)
    If no pushed active batch: returns (panel, seat_label, HttpResponse to waiting_for_push)
    If active batch ready: returns (panel, seat_label, batch)
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
        current_panel = JudgePanel.objects.filter(championship=championship, is_active=True, is_closed=False).first()
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

    batch = StageBatch.objects.filter(panel=current_panel, status='ACTIVE').first()
    if not batch or batch.entries.count() == 0:
        return current_panel, seat_label, render(request, 'scoring/waiting_for_push.html', {
            'panel': current_panel,
            'seat_label': seat_label,
            'championship': championship,
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

    judge_role = seat_label.replace(' ', '_').upper()
    if judge_role not in ['D1_JUDGE', 'D2_JUDGE', 'D3_JUDGE', 'D4_JUDGE']:
        judge_role = 'D1_JUDGE'

    # Build sequence status tabs for 1 to 7
    sequence_tabs = []
    for seq_num in range(1, total_items + 1):
        seq_locked_count = 0
        for e in entries:
            perf, _ = Performance.objects.get_or_create(
                event_entry=e,
                round_type=current_panel.round_type,
                defaults={'batch': batch, 'status': PerformanceStatus.IN_PROGRESS}
            )
            sub, _ = JudgeSubmission.objects.get_or_create(
                performance=perf,
                judge_role=judge_role,
                version=1,
                defaults={'judge': request.user, 'is_draft': True, 'raw_total': Decimal('0.00')}
            )
            score_item = AsanaScoreItem.objects.filter(submission=sub, sequence_order=seq_num).first()
            if score_item and score_item.is_locked:
                seq_locked_count += 1
        
        sequence_tabs.append({
            'seq': seq_num,
            'is_current': seq_num == current_asana_seq,
            'is_locked': seq_locked_count == entries.count() if entries.exists() else False,
            'is_partial': 0 < seq_locked_count < entries.count(),
        })

    # Prepare submissions for current_asana_seq
    submissions = {}
    is_current_seq_locked = True if entries.exists() else False

    for e in entries:
        perf, _ = Performance.objects.get_or_create(
            event_entry=e,
            round_type=current_panel.round_type,
            defaults={'batch': batch, 'status': PerformanceStatus.IN_PROGRESS}
        )
        sub, _ = JudgeSubmission.objects.get_or_create(
            performance=perf,
            judge_role=judge_role,
            version=1,
            defaults={'judge': request.user, 'is_draft': True, 'raw_total': Decimal('0.00')}
        )
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

        pose_seq = AsanaSequenceItem.objects.filter(event_entry=e, sequence_order=current_asana_seq).first()
        submissions[e.id] = {
            'entry': e,
            'performance': perf,
            'submission': sub,
            'item': item,
            'pose': pose_seq
        }

    # Handle POST
    if request.method == 'POST':
        action = request.POST.get('action') # 'save_draft', 'submit_item_next', 'submit_final_batch'

        for e in entries:
            sub_id = request.POST.get(f'submission_{e.id}')
            sub = JudgeSubmission.objects.get(id=sub_id, judge=request.user)
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

            t_val = Decimal('2.00')
            item.posture_score = p_val
            item.stability_score = s_val
            item.timing_contribution = t_val
            item.effective_item_score = p_val + s_val + t_val

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
                sub = JudgeSubmission.objects.get(id=sub_id, judge=request.user)
                sub.items.all().update(is_draft=False, is_submitted=True, is_locked=True)
                total = sum(i.effective_item_score for i in sub.items.all())
                sub.raw_total = total
                sub.effective_total = total
                sub.is_draft = False
                sub.is_submitted = True
                sub.is_locked = True
                sub.submitted_at = timezone.now()
                sub.save()

            messages.success(request, f"Official {seat_label} score sheet finalized and locked for all athletes in Batch #{batch.batch_number}!")
            return redirect('judge_portal')

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

    judge_role = seat_label.replace(' ', '_').upper()
    if judge_role not in ['D1_JUDGE', 'D2_JUDGE', 'D3_JUDGE', 'D4_JUDGE']:
        judge_role = 'D1_JUDGE'

    # Build sequence status tabs for 1 to 7
    sequence_tabs = []
    for seq_num in range(1, total_items + 1):
        seq_locked_count = 0
        for e in entries:
            perf, _ = Performance.objects.get_or_create(
                event_entry=e,
                round_type='FINAL',
                defaults={'batch': batch, 'status': PerformanceStatus.IN_PROGRESS}
            )
            sub, _ = JudgeSubmission.objects.get_or_create(
                performance=perf,
                judge_role=judge_role,
                version=1,
                defaults={'judge': request.user, 'is_draft': True, 'raw_total': Decimal('0.00')}
            )
            score_item = AsanaScoreItem.objects.filter(submission=sub, sequence_order=seq_num).first()
            if score_item and score_item.is_locked:
                seq_locked_count += 1
        
        sequence_tabs.append({
            'seq': seq_num,
            'is_current': seq_num == current_asana_seq,
            'is_locked': seq_locked_count == entries.count() if entries.exists() else False,
            'is_partial': 0 < seq_locked_count < entries.count(),
        })

    # Prepare submissions for current_asana_seq
    submissions = {}
    is_current_seq_locked = True if entries.exists() else False

    for e in entries:
        perf, _ = Performance.objects.get_or_create(
            event_entry=e,
            round_type='FINAL',
            defaults={'batch': batch, 'status': PerformanceStatus.IN_PROGRESS}
        )
        sub, _ = JudgeSubmission.objects.get_or_create(
            performance=perf,
            judge_role=judge_role,
            version=1,
            defaults={'judge': request.user, 'is_draft': True, 'raw_total': Decimal('0.00')}
        )
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

        pose_seq = AsanaSequenceItem.objects.filter(event_entry=e, sequence_order=current_asana_seq).first()
        submissions[e.id] = {
            'entry': e,
            'performance': perf,
            'submission': sub,
            'item': item,
            'pose': pose_seq
        }

    # Handle POST
    if request.method == 'POST':
        action = request.POST.get('action')

        for e in entries:
            sub_id = request.POST.get(f'submission_{e.id}')
            if not sub_id:
                continue
            sub = JudgeSubmission.objects.get(id=sub_id, judge=request.user)
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

            t_val = Decimal('2.00')
            item.posture_score = p_val
            item.stability_score = s_val
            item.timing_contribution = t_val
            item.effective_item_score = p_val + s_val + t_val

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
                if sub_id:
                    sub = JudgeSubmission.objects.get(id=sub_id, judge=request.user)
                    sub.items.all().update(is_draft=False, is_submitted=True, is_locked=True)
                    total = sum(i.effective_item_score for i in sub.items.all())
                    sub.raw_total = total
                    sub.effective_total = total
                    sub.is_draft = False
                    sub.is_submitted = True
                    sub.is_locked = True
                    sub.submitted_at = timezone.now()
                    sub.save()

            messages.success(request, f"Traditional Final {seat_label} score sheet finalized and locked for all athletes in Batch #{batch.batch_number}!")
            return redirect('judge_portal')

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
            messages.success(request, f"Artistic Single D scores submitted and locked! Total: {total:.3f}")
            return redirect('judge_portal')
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
            messages.success(request, f"Artistic Pair D marks submitted and locked! Total: {total:.3f}")
            return redirect('judge_portal')
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
            messages.success(request, f"Rhythmic Pair D marks submitted and locked! Total: {total:.3f}")
            return redirect('judge_portal')
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
    with 7-item timing log and monotonic clock.
    """
    batch = StageBatch.objects.filter(status='ACTIVE').first()
    entries = list(batch.entries.all()[:5]) if batch else []
    current_seq = int(request.GET.get('seq', 1))

    submissions = {}
    for e in entries:
        perf, _ = Performance.objects.get_or_create(
            event_entry=e,
            round_type='PRELIMINARY',
            defaults={'batch': batch, 'status': PerformanceStatus.IN_PROGRESS}
        )
        sub, _ = JudgeSubmission.objects.get_or_create(
            performance=perf,
            judge_role='TIME_JUDGE',
            version=1,
            defaults={'judge': request.user, 'is_draft': True}
        )
        item, _ = AsanaScoreItem.objects.get_or_create(
            submission=sub,
            sequence_order=current_seq,
            defaults={'hold_duration_seconds': Decimal('15.00'), 'timing_contribution': Decimal('2.00')}
        )
        submissions[e.id] = {'entry': e, 'perf': perf, 'sub': sub, 'item': item}

    if request.method == 'POST':
        action = request.POST.get('action')
        for e in entries:
            hold_secs = Decimal(request.POST.get(f'hold_{e.id}', '15.00'))
            contrib = Decimal('2.00') if hold_secs >= Decimal('15.00') else round(Decimal('2.00') * (hold_secs / Decimal('15.00')), 2)
            sub = submissions[e.id]['sub']
            AsanaScoreItem.objects.update_or_create(
                submission=sub,
                sequence_order=current_seq,
                defaults={'hold_duration_seconds': hold_secs, 'timing_contribution': contrib, 'effective_item_score': contrib}
            )

        if action == 'submit_timing':
            for e in entries:
                sub = submissions[e.id]['sub']
                sub.is_draft = False
                sub.is_submitted = True
                sub.is_locked = True
                sub.submitted_at = datetime.datetime.now()
                total = sum(i.timing_contribution for i in sub.items.all())
                sub.raw_total = total
                sub.effective_total = total
                sub.save()
            messages.success(request, "Traditional Time scores submitted and locked for batch!")
            return redirect('judge_portal')
        else:
            messages.success(request, f"Timing saved for Asana #{current_seq}.")
            next_seq = min(7, current_seq + 1)
            return redirect(f"/scoring/traditional-time/?seq={next_seq}")

    return render(request, 'scoring/traditional_time.html', {
        'batch': batch,
        'entries': entries,
        'current_seq': current_seq,
        'submissions': submissions,
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
            messages.success(request, f"Music timing submitted! Duration: {routine_duration}s, Timing Score: {timing_score:.2f}")
            return redirect('judge_portal')
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
            messages.success(request, f"Artistry scores submitted and locked! Total: {total:.2f}")
            return redirect('judge_portal')
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
    Implements 23_Evaluator_Valid_Invalid.png:
    Declared versus observed sequence, pose references, Valid/Invalid toggle,
    and required reason for invalidity.
    """
    perf_id = request.GET.get('perf_id')
    batch = StageBatch.objects.filter(status='ACTIVE').first()
    
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

    # 7 items for Traditional, 10 for Artistic/Rhythmic
    is_traditional = 'TRADITIONAL' in (entry.event_config.event_type.upper() if entry and entry.event_config else '')
    item_count = 7 if is_traditional else 10

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
        sub.effective_total = Decimal(str(invalid_count * 2))  # 2 points deduction per invalid pose
        if action == 'submit_sheet':
            sub.is_draft = False
            sub.is_submitted = True
            sub.is_locked = True
            sub.submitted_at = datetime.datetime.now()
            sub.save()
            messages.success(request, f"Evaluator sheet submitted and locked! Invalid items flagged: {invalid_count}")
            return redirect('judge_portal')
        else:
            sub.save()
            messages.success(request, f"Evaluator draft saved. Invalid items: {invalid_count}")

    return render(request, 'scoring/evaluator.html', {
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
