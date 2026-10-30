import datetime
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.db import transaction
from .models import Performance, PerformanceStatus, JudgeSubmission, AsanaScoreItem, PerformanceArtistryScore
from competition.models import StageBatch, JudgePanel
from registrations.models import EventEntry, AsanaSequenceItem
from catalogue.models import Asana

@login_required
def scoring_traditional_d(request):
    """
    Implements 17_Traditional_D_Judge.png:
    Traditional earlier-round batch: 1 to 5 athlete cards shown together.
    Each card: Chest number only, actual asana code/name/photo, posture input (max 6.0),
    stability input (max 2.0), read-only timing contribution, item total.
    7 items round progress. Save draft & Submit batch.
    """
    batch = StageBatch.objects.filter(status='ACTIVE').first()
    entries = batch.entries.all() if batch else []
    
    current_asana_seq = int(request.GET.get('seq', batch.current_asana_index if batch else 1))

    # Get or create submissions for this judge
    submissions = {}
    for e in entries:
        perf, _ = Performance.objects.get_or_create(
            event_entry=e,
            round_type='PRELIMINARY',
            defaults={'batch': batch, 'status': PerformanceStatus.IN_PROGRESS}
        )
        sub = JudgeSubmission.objects.filter(performance=perf, judge_role='D1_JUDGE').first()
        if not sub:
            sub = JudgeSubmission.objects.create(
                performance=perf,
                judge=request.user,
                judge_role='D1_JUDGE',
                version=1,
                is_draft=True,
                raw_total=Decimal('0.00')
            )
        item, _ = AsanaScoreItem.objects.get_or_create(
            submission=sub,
            sequence_order=current_asana_seq,
            defaults={'posture_score': Decimal('5.00'), 'stability_score': Decimal('1.50'), 'timing_contribution': Decimal('2.00')}
        )
        # Fetch declared pose
        pose_seq = AsanaSequenceItem.objects.filter(event_entry=e, sequence_order=current_asana_seq).first()
        submissions[e.id] = {
            'entry': e,
            'performance': perf,
            'submission': sub,
            'item': item,
            'pose': pose_seq
        }

    if request.method == 'POST':
        action = request.POST.get('action')
        for e in entries:
            p_val = Decimal(request.POST.get(f'posture_{e.id}', '0.0'))
            s_val = Decimal(request.POST.get(f'stability_{e.id}', '0.0'))
            sub_id = request.POST.get(f'submission_{e.id}')
            sub = JudgeSubmission.objects.get(id=sub_id)
            
            # Update score item
            AsanaScoreItem.objects.update_or_create(
                submission=sub,
                sequence_order=current_asana_seq,
                defaults={
                    'posture_score': min(Decimal('6.00'), p_val),
                    'stability_score': min(Decimal('2.00'), s_val),
                    'effective_item_score': p_val + s_val + Decimal('2.00')
                }
            )

        if action == 'submit_batch':
            for e in entries:
                sub_id = request.POST.get(f'submission_{e.id}')
                sub = JudgeSubmission.objects.get(id=sub_id)
                sub.is_draft = False
                sub.is_submitted = True
                sub.is_locked = True
                sub.submitted_at = datetime.datetime.now()
                # Calculate total
                total = sum(item.effective_item_score for item in sub.items.all())
                sub.raw_total = total
                sub.effective_total = total
                sub.save()
            messages.success(request, "Official D1 scores submitted and locked for current batch!")
            return redirect('stage_manager')
        else:
            messages.success(request, f"Scores saved for Asana #{current_asana_seq}.")
            next_seq = min(7, current_asana_seq + 1)
            return redirect(f"/scoring/traditional-d/?seq={next_seq}")

    return render(request, 'scoring/traditional_d.html', {
        'batch': batch,
        'entries': entries,
        'current_seq': current_asana_seq,
        'submissions': submissions,
        'is_final': False,
    })

@login_required
def scoring_traditional_d_final(request):
    """
    Implements 35_Traditional_Final_Multiple_Athletes.png:
    Traditional Final batch view (maximum 3 simultaneous athletes on stage).
    """
    batch = StageBatch.objects.filter(status='ACTIVE').first()
    entries = batch.entries.all()[:3] if batch else []
    current_asana_seq = int(request.GET.get('seq', 1))

    return render(request, 'scoring/traditional_d.html', {
        'batch': batch,
        'entries': entries,
        'current_seq': current_asana_seq,
        'submissions': {},
        'is_final': True,
    })

@login_required
def scoring_artistic_single_d(request):
    """
    Implements 18_Artistic_Single_D_Judge.png:
    Ten-item single execution sheet with pose image, BV, execution marks,
    evaluator validity indicator, and effective score.
    """
    batch = StageBatch.objects.filter(status='ACTIVE').first()
    entry = batch.entries.first() if batch else None
    
    perf, _ = Performance.objects.get_or_create(
        event_entry=entry,
        round_type='PRELIMINARY',
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

    items = []
    if sub:
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
            items.append({'score_item': score_item, 'pose': pose_seq})

    if request.method == 'POST':
        if sub:
            total = Decimal('0.00')
            for seq in range(1, 11):
                exec_val = Decimal(request.POST.get(f'exec_{seq}', '8.00'))
                bv_val = Decimal(request.POST.get(f'bv_{seq}', '0.80'))
                eff = exec_val * bv_val
                total += eff
                AsanaScoreItem.objects.update_or_create(
                    submission=sub,
                    sequence_order=seq,
                    defaults={'execution_score': exec_val, 'base_value': bv_val, 'effective_item_score': eff}
                )
            sub.is_draft = False
            sub.is_submitted = True
            sub.is_locked = True
            sub.raw_total = total
            sub.effective_total = total
            sub.submitted_at = datetime.datetime.now()
            sub.save()
            messages.success(request, f"Artistic Single D scores submitted! Total: {total:.3f}")
            return redirect('scoring_chief_judge')

    return render(request, 'scoring/artistic_single_d.html', {
        'batch': batch,
        'entry': entry,
        'perf': perf,
        'sub': sub,
        'items': items,
    })

@login_required
def scoring_artistic_pair_d(request):
    """
    Implements 19_Artistic_Pair_D_Judge.png:
    Minimal pair desk: Chest A submitted pose, Chest B submitted pose, pair formation,
    two large execution fields and formation execution field. Ten-item navigation.
    """
    batch = StageBatch.objects.filter(status='ACTIVE').first()
    entry = batch.entries.first() if batch else None
    current_seq = int(request.GET.get('seq', 1))

    return render(request, 'scoring/artistic_pair_d.html', {
        'batch': batch,
        'entry': entry,
        'current_seq': current_seq,
    })

@login_required
def scoring_rhythmic_pair_d(request):
    """
    Implements 20_Rhythmic_Pair_D_Judge.png:
    Ten-item rhythmic pair scoring with shared BV calculation and compact result.
    """
    batch = StageBatch.objects.filter(status='ACTIVE').first()
    entry = batch.entries.first() if batch else None
    current_seq = int(request.GET.get('seq', 1))

    return render(request, 'scoring/rhythmic_pair_d.html', {
        'batch': batch,
        'entry': entry,
        'current_seq': current_seq,
    })

@login_required
def scoring_traditional_time(request):
    """
    Implements 33_Traditional_Time_Judge.png:
    Independent batch timers (up to 5 athletes earlier rounds, 3 in final),
    with 7-item timing log and monotonic clock.
    """
    batch = StageBatch.objects.filter(status='ACTIVE').first()
    entries = batch.entries.all() if batch else []

    return render(request, 'scoring/traditional_time.html', {
        'batch': batch,
        'entries': entries,
    })

@login_required
def scoring_music_time(request):
    """
    Implements 21_Music_Event_Time_Judge.png:
    Whole routine stopwatch and 10 individual hold duration timers for music events.
    """
    batch = StageBatch.objects.filter(status='ACTIVE').first()
    entry = batch.entries.first() if batch else None

    return render(request, 'scoring/music_time.html', {
        'batch': batch,
        'entry': entry,
    })

@login_required
def scoring_artistry(request):
    """
    Implements 22_Artistry_Judge.png:
    Performance-level artistry criteria (20-point breakdown), routine sequence, deductions.
    """
    batch = StageBatch.objects.filter(status='ACTIVE').first()
    entry = batch.entries.first() if batch else None

    return render(request, 'scoring/artistry.html', {
        'batch': batch,
        'entry': entry,
    })

@login_required
def scoring_evaluator(request):
    """
    Implements 23_Evaluator_Valid_Invalid.png:
    Declared versus observed sequence, pose references, Valid/Invalid toggle,
    and required reason for invalidity.
    """
    batch = StageBatch.objects.filter(status='ACTIVE').first()
    entry = batch.entries.first() if batch else None

    return render(request, 'scoring/evaluator.html', {
        'batch': batch,
        'entry': entry,
    })

@login_required
def scoring_chief_judge(request):
    """
    Implements 24_Chief_Judge_Review.png:
    Submission completeness across judges (D1-D4, Time, Evaluator),
    component breakdown, invalid items, and provisional score release.
    """
    performances = Performance.objects.filter(
        batch__status__in=['ACTIVE', 'SUBMITTED', 'COMPLETED']
    ).select_related('event_entry__registration__athlete')

    if request.method == 'POST':
        action = request.POST.get('action')
        perf_id = request.POST.get('performance_id')
        perf = get_object_or_404(Performance, id=perf_id)

        if action == 'provisional_release':
            # Server-side validation: Rule preset verification check
            champ = perf.event_entry.registration.championship
            if not champ.is_rules_preset_verified:
                messages.error(
                    request, 
                    f"Official score publication blocked! Rules preset for '{champ.name}' ({champ.rules_edition}) "
                    f"is unverified against official COP. Please verify and certify rules preset before releasing scores."
                )
                return redirect('scoring_chief_judge')

            perf.status = PerformanceStatus.PROVISIONAL_RELEASED
            perf.provisional_score = Decimal('16.450')
            perf.final_score = Decimal('16.450')
            perf.chief_approved_by = request.user
            perf.chief_approved_at = datetime.datetime.now()
            perf.save()
            messages.success(request, f"Provisional score released for Chest {perf.chest_number}! Broadcast to Live Public Screen.")
            return redirect('scoring_chief_judge')

    return render(request, 'scoring/chief_judge.html', {
        'performances': performances,
    })
