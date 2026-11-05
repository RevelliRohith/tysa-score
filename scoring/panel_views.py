"""Panel-scoped score sheets. Every read and write resolves authorization server-side."""
import json
from decimal import Decimal, InvalidOperation
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from accounts.permissions import get_active_championship, is_system_admin
from accounts.models import RoleChoices, ScopedRole
from competition.models import JudgePanel, StageBatch
from registrations.models import AsanaSequenceItem
from audit.models import AuditLog
from .models import Performance, JudgeSubmission, AsanaScoreItem, PerformanceArtistryScore, EvaluatorIncident, IncidentType
from .services import calculate_performance_score, calculate_traditional_timing_contribution, calculate_tw_marks

ROLE_FIELDS = {f'd{i}_judge': f'D{i}_JUDGE' for i in range(1, 6)}
ROLE_FIELDS.update(time_judge='TIME_JUDGE', time_judge_2='TIME_JUDGE_2',
                   artistry_judge='ARTISTRY_JUDGE', artistry_judge_2='ARTISTRY_JUDGE_2',
                   evaluator='EVALUATOR', evaluator_2='EVALUATOR_2')
ART_FIELDS = {'costume': 1, 'theme_music': 1, 'character_expression': 3,
              'rhythm_artistic_steps': 10, 'use_of_floor': 5, 'artistry_deductions': 20}

def director(request, championship):
    return is_system_admin(request.user) or ScopedRole.objects.filter(
        user=request.user, championship=championship, role=RoleChoices.CD).exists()

def resolve_panel(request, chief=False):
    profile = getattr(request.user, 'judge_profile', None)
    if profile and not profile.is_approved and not is_system_admin(request.user):
        raise PermissionDenied('Judge approval is required.')
    champ = get_active_championship(request)
    panels = JudgePanel.objects.filter(championship=champ, is_active=True, is_closed=False)
    allowed = ['chief_judge'] if chief else list(ROLE_FIELDS)
    from django.db.models import Q
    condition = Q(pk__in=[])
    for field in allowed:
        condition |= Q(**{field: request.user})
    # Directors can inspect the Chief dashboard, never impersonate a scoring judge.
    if not (chief and director(request, champ)):
        panels = panels.filter(condition)
    if request.GET.get('panel_id'):
        panel = get_object_or_404(panels, pk=request.GET['panel_id'])
    else:
        panel = panels.order_by('pk').first()
    if not panel:
        return None, None
    seat = next((f for f in allowed if getattr(panel, f + '_id') == request.user.id), None)
    return panel, seat

def performances_for(panel):
    if not panel:
        return Performance.objects.none()
    batch = StageBatch.objects.filter(panel=panel, status='ACTIVE').order_by('-pk').first()
    if not batch:
        return Performance.objects.none()
    queryset = Performance.objects.filter(batch=batch, event_entry__in=batch.entries.all(),
        round_type=panel.round_type, event_entry__event_config=panel.event_config,
        event_entry__age_category=panel.age_category).select_related('event_entry__registration__athlete')
    return queryset.filter(event_entry__gender=panel.gender) if panel.gender else queryset

def schema(panel, role):
    event = panel.event_config.event_type
    if role.startswith('ARTISTRY'):
        return ART_FIELDS
    if role.startswith('TIME'):
        return {'hold_duration_seconds': 999, 'attempt_number': 2,
                'timing_penalty': 99, 'timing_penalty_reason': 'text',
                **({'routine_duration': 999} if event != 'TRADITIONAL' else {})}
    if role.startswith('EVALUATOR'):
        return {'is_valid': 'bool', 'invalid_reason': 'text'}
    if event == 'TRADITIONAL':
        return {'posture_score': 6, 'stability_score': 2}
    if event == 'ARTISTIC_PAIR':
        return {'chest_a_execution': 10, 'chest_b_execution': 10, 'formation_score': 10}
    if event == 'RHYTHMIC_PAIR':
        return {'chest_a_execution': 10, 'chest_b_execution': 10}
    return {'execution_score': 10}

def sequence_orders(perf):
    return list(range(1, 8 if perf.event_entry.event_config.event_type == 'TRADITIONAL' else 11))

def serialize_sheet(sub, fields):
    return {'id': sub.id, 'role': sub.judge_role, 'judge': sub.judge.full_name or sub.judge.email,
            'submitted': sub.is_submitted and not sub.is_draft, 'locked': sub.is_locked,
            'version': sub.version, 'total': str(sub.effective_total),
            'metadata': entered_metadata(sub, fields),
            'items': list(sub.items.values()),
            'artistry': list(PerformanceArtistryScore.objects.filter(submission=sub).values())}

def entered_metadata(sub, fields):
    metadata = dict(sub.calculation_metadata)
    if 'entered_fields' not in metadata:
        # Older desks persisted items without tracking which fields were entered.
        # Keep those stored values visible when continuing an existing sheet.
        if sub.judge_role.startswith('ARTISTRY'):
            orders = [1] if PerformanceArtistryScore.objects.filter(submission=sub).exists() else []
        else:
            orders = sub.items.values_list('sequence_order', flat=True)
        metadata['entered_fields'] = {str(n): sorted(set(fields) - {'routine_duration'}) for n in orders}
    return metadata

@never_cache
@login_required
def score_desk(request):
    correction_sub = None
    if request.GET.get('submission_id'):
        correction_sub = get_object_or_404(JudgeSubmission,
            pk=request.GET['submission_id'], judge=request.user,
            correction_grants__status='OPEN',
            performance__event_entry__registration__championship=get_active_championship(request))
        panel = correction_sub.performance.batch.panel if correction_sub.performance.batch_id else None
        if not panel:
            perf = correction_sub.performance
            panel = JudgePanel.objects.filter(championship=perf.event_entry.registration.championship,
                event_config=perf.event_entry.event_config, age_category=perf.event_entry.age_category,
                gender=perf.event_entry.gender, round_type=perf.round_type).order_by('-pk').first()
        seat = next((f for f, r in ROLE_FIELDS.items() if r == correction_sub.judge_role), None)
    else:
        panel, seat = resolve_panel(request)
    if not panel:
        if request.method != 'GET':
            raise PermissionDenied('No assigned scoring panel.')
        return render(request, 'scoring/no_panel_assigned.html')
    profile = getattr(request.user, 'judge_profile', None)
    if profile and not profile.is_approved:
        raise PermissionDenied('Judge approval is required.')
    role = ROLE_FIELDS[seat]
    # A different scoring URL must never change the user's actual seat.
    expected = ('time' if role.startswith('TIME') else 'artistry' if role.startswith('ARTISTRY')
                else 'evaluator' if role.startswith('EVALUATOR') else '-d')
    if expected not in request.path and request.path != '/scoring/reopened-desk/':
        raise PermissionDenied('Use your assigned scoring desk.')
    fields = schema(panel, role)
    perfs = Performance.objects.filter(pk=correction_sub.performance_id) if correction_sub else performances_for(panel)
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            if not isinstance(data, dict):
                raise ValueError('Expected a score sheet object')
            with transaction.atomic():
                # Panel lock serializes sheet creation and each final submit against draft saves.
                locked_panel = JudgePanel.objects.select_for_update().get(pk=panel.pk)
                if not correction_sub and (not locked_panel.is_active or locked_panel.is_closed or getattr(locked_panel, seat + '_id') != request.user.id):
                    raise PermissionDenied('Panel assignment changed.')
                perf = get_object_or_404(perfs.select_for_update(), pk=data.get('performance_id'))
                sub = JudgeSubmission.objects.select_for_update().get(pk=correction_sub.pk) if correction_sub else JudgeSubmission.objects.filter(performance=perf, judge_role=role).order_by('-version').first()
                if sub and sub.judge_id != request.user.id:
                    # A replacement receives a new revision; the prior official's sheet is retained.
                    sub = JudgeSubmission.objects.create(performance=perf, judge_role=role,
                        version=sub.version + 1, judge=request.user)
                if not sub:
                    sub = JudgeSubmission.objects.create(performance=perf, judge_role=role, judge=request.user)
                if sub.is_locked or (sub.is_submitted and not sub.is_draft):
                    return JsonResponse({'error': 'This score sheet is locked.'}, status=409)
                action = data.get('action', 'draft')
                if action == 'incident':
                    if not role.startswith('EVALUATOR'):
                        raise PermissionDenied('Only evaluators may report incidents.')
                    incident = data.get('incident', {})
                    kind = incident.get('type')
                    reason = str(incident.get('reason', '')).strip()
                    penalty = Decimal(str(incident.get('penalty', 0)))
                    seq = int(data.get('sequence', 1))
                    if kind not in IncidentType.values or not reason or not penalty.is_finite() or not 0 <= penalty <= 99 or seq not in sequence_orders(perf):
                        raise ValueError('Provide a valid incident type, reason, sequence and penalty.')
                    record = EvaluatorIncident.objects.create(performance=perf, chest_number=perf.chest_number,
                        reporting_evaluator=request.user, evaluator_seat=role, incident_type=kind,
                        declared_position=seq, affected_item_numbers=str(seq), penalty_marks=penalty,
                        evidence_notes=reason, player_target='P2' if role == 'EVALUATOR_2' else 'SOLO')
                    AuditLog.log('EVALUATOR_INCIDENT_REPORTED', 'EvaluatorIncident', record.pk,
                                 {'performance_id': perf.id, 'penalty': str(penalty)}, request.user)
                    return JsonResponse({'incident_id': record.pk})
                if action not in ('draft', 'submit'):
                    raise ValueError('Invalid action')
                orders = [1] if role.startswith('ARTISTRY') else sequence_orders(perf)
                seq = int(data.get('sequence', 1))
                if seq not in orders:
                    raise ValueError('Invalid asana sequence')
                values = data.get('values', {})
                if not isinstance(values, dict) or set(values) - set(fields):
                    raise ValueError('Invalid scoring fields')
                parsed = {}
                for key, value in values.items():
                    limit = fields[key]
                    if limit == 'text':
                        parsed[key] = str(value)[:150]
                    elif limit == 'bool':
                        if not isinstance(value, bool):
                            raise ValueError('Validity must be true or false')
                        parsed[key] = value
                    else:
                        number = Decimal(str(value))
                        if not number.is_finite() or number < 0 or number > limit or number.as_tuple().exponent < -2:
                            raise ValueError('Invalid value for ' + key)
                        if key == 'attempt_number' and number not in (1, 2):
                            raise ValueError('Attempt must be 1 or 2')
                        parsed[key] = number
                metadata = entered_metadata(sub, fields)
                completed = metadata.setdefault('entered_fields', {})
                if parsed:
                    completed[str(seq)] = sorted(set(completed.get(str(seq), [])) | set(parsed))
                if role.startswith('ARTISTRY'):
                    item, _ = PerformanceArtistryScore.objects.get_or_create(submission=sub)
                else:
                    item, _ = AsanaScoreItem.objects.get_or_create(submission=sub, sequence_order=seq)
                for key, value in parsed.items():
                    if key == 'routine_duration':
                        metadata[key] = str(value)
                    else:
                        setattr(item, key, value)
                if isinstance(item, AsanaScoreItem):
                    pose = AsanaSequenceItem.objects.filter(event_entry=perf.event_entry,
                        round_type=perf.round_type, sequence_order=seq).first()
                    from .views import resolve_asana_compulsory_and_bv
                    compulsory, bv = resolve_asana_compulsory_and_bv(perf.event_entry, perf.round_type, seq, pose)
                    item.base_value = bv
                    if role.startswith('TIME'):
                        target = Decimal('45') if compulsory else Decimal('30')
                        item.timing_contribution = calculate_traditional_timing_contribution(
                            item.hold_duration_seconds, target, attempt=int(item.attempt_number))
                        item.raw_item_score = item.effective_item_score = item.timing_contribution
                    elif role.startswith('D'):
                        event = panel.event_config.event_type
                        if event == 'TRADITIONAL':
                            timing = AsanaScoreItem.objects.filter(submission__performance=perf,
                                submission__judge_role='TIME_JUDGE', sequence_order=seq).order_by('-submission__version').first()
                            item.timing_contribution = timing.timing_contribution if timing else Decimal('0')
                            total = (item.posture_score + item.stability_score + item.timing_contribution) * bv
                        elif event == 'ARTISTIC_PAIR':
                            total = ((item.chest_a_execution * item.chest_a_bv + item.chest_b_execution * item.chest_b_bv) / 2 + item.formation_score) / 2
                        elif event == 'RHYTHMIC_PAIR':
                            total = (item.chest_a_execution + item.chest_b_execution) / 2 * bv
                        else:
                            total = item.execution_score * bv
                        item.raw_item_score = item.effective_item_score = total
                    item.is_draft, item.is_submitted, item.is_locked = True, False, False
                else:
                    metadata['uses_cop_artistry'] = True
                    sub.calculation_metadata = metadata
                    sub.save(update_fields=['calculation_metadata'])
                    item.submission = sub
                    item.calculate_total()
                item.save()
                if role.startswith('TIME') and panel.event_config.event_type == 'TRADITIONAL':
                    metadata['traditional_t_penalties'] = [
                        {'sequence': i.sequence_order, 'penalty_amount': str(i.timing_penalty),
                         'reason': i.timing_penalty_reason, 'scope': i.timing_penalty_scope}
                        for i in sub.items.filter(timing_penalty__gt=0)]
                sub.calculation_metadata = metadata
                if role.startswith('ARTISTRY'):
                    sub.raw_total = sub.effective_total = item.total_artistry
                elif role.startswith('TIME') and panel.event_config.event_type != 'TRADITIONAL':
                    sub.raw_total = Decimal(metadata.get('routine_duration', '0'))
                    sub.effective_total = calculate_tw_marks(sub.raw_total) + Decimal('0.5') * sub.items.filter(hold_duration_seconds__gte=5).count()
                else:
                    sub.raw_total = sub.effective_total = sum(i.effective_item_score for i in sub.items.all())
                if action == 'submit':
                    required = set(fields) - {'invalid_reason', 'timing_penalty_reason', 'timing_penalty', 'attempt_number', 'routine_duration'}
                    missing = {str(n): sorted(required - set(completed.get(str(n), []))) for n in orders}
                    missing = {n: keys for n, keys in missing.items() if keys}
                    if 'routine_duration' in fields and 'routine_duration' not in metadata:
                        missing['routine'] = ['routine_duration']
                    if role.startswith('EVALUATOR') and sub.items.filter(is_valid=False, invalid_reason='').exists():
                        raise ValueError('An invalid asana requires a reason.')
                    if role.startswith('TIME') and sub.items.filter(timing_penalty__gt=0, timing_penalty_reason='').exists():
                        raise ValueError('A timing penalty requires a reason.')
                    if missing:
                        raise ValueError('Complete all required fields before submitting: ' + str(missing))
                    sub.is_draft, sub.is_submitted, sub.is_locked = False, True, True
                    sub.submitted_at = timezone.now()
                    sub.items.update(is_draft=False, is_submitted=True, is_locked=True, submitted_at=sub.submitted_at)
                    AuditLog.log('JUDGE_SHEET_SUBMITTED', 'JudgeSubmission', sub.id,
                                 {'version': sub.version, 'performance_id': perf.id}, request.user)
                sub.save()
                if action == 'submit':
                    grant = sub.correction_grants.filter(status='OPEN').first()
                    if grant:
                        from corrections.services import resubmit_judge_correction
                        resubmit_judge_correction(request.user, grant.id, panel.championship)
                    else:
                        calculate_performance_score(perf)
                return JsonResponse({'sheet': serialize_sheet(sub, fields)})
        except (ValueError, TypeError, InvalidOperation, ValidationError) as exc:
            return JsonResponse({'error': str(exc)}, status=400)
    athletes = []
    for perf in perfs:
        sub = JudgeSubmission.objects.filter(performance=perf, judge_role=role, judge=request.user).order_by('-version').first()
        athletes.append({'id': perf.id, 'name': perf.event_entry.display_names,
            'chest': perf.chest_number, 'orders': [1] if role.startswith('ARTISTRY') else sequence_orders(perf),
            'sheet': serialize_sheet(sub, fields) if sub else None,
            'poses': list(AsanaSequenceItem.objects.filter(event_entry=perf.event_entry,
                 round_type=perf.round_type).values('sequence_order', 'declared_name'))})
    context = {'panel': panel, 'role': role, 'fields': fields, 'athletes': athletes,
               'is_evaluator': role.startswith('EVALUATOR'), 'incident_types': IncidentType.choices}
    if request.GET.get('format') == 'json':
        return JsonResponse({'athletes': athletes, 'fields': fields, 'role': role})
    return render(request, 'scoring/score_sheet.html', context)

@never_cache
@login_required
@transaction.atomic
def chief_dashboard(request):
    panel, _ = resolve_panel(request, chief=True)
    if panel and request.method == 'POST':
        panel = JudgePanel.objects.select_for_update().get(pk=panel.pk)
        if not panel.is_active or panel.is_closed:
            raise PermissionDenied('Panel is no longer active.')
        if panel.chief_judge_id != request.user.id and not director(request, panel.championship):
            raise PermissionDenied('This Chief Judge assignment has changed.')
    perfs = performances_for(panel)
    if request.method != 'GET':
        # Preserve the existing release workflow, scoped to the same live panel.
        perf = get_object_or_404(perfs, pk=request.POST.get('performance_id'))
        if request.POST.get('action') == 'incident_decision':
            incident = get_object_or_404(EvaluatorIncident, pk=request.POST.get('incident_id'), performance=perf)
            decision = request.POST.get('decision')
            if decision not in ('APPROVED', 'REJECTED'):
                return JsonResponse({'error': 'Invalid decision'}, status=400)
            incident.chief_decision = decision
            incident.chief_decision_notes = request.POST.get('notes', '')
            incident.chief_decided_at = timezone.now()
            incident.save()
            AuditLog.log('CHIEF_INCIDENT_DECISION', 'EvaluatorIncident', incident.pk,
                         {'decision': decision, 'notes': incident.chief_decision_notes}, request.user)
            return JsonResponse({'decision': decision})
        if request.POST.get('action') != 'provisional_release':
            raise PermissionDenied('Chief Judge has read-only access to judge scores.')
        calc = calculate_performance_score(perf)
        if not calc['can_release']:
            return JsonResponse({'error': calc['block_reasons']}, status=400)
        perf.status = 'PROVISIONAL_RELEASED'
        perf.provisional_score = perf.final_score = calc['provisional_total']
        perf.chief_approved_by, perf.chief_approved_at = request.user, timezone.now()
        perf.save()
        AuditLog.log('CHIEF_PROVISIONAL_RELEASE', 'Performance', perf.id, user=request.user)
    athletes = []
    if panel:
        for perf in perfs:
            judges = []
            for field in panel.required_seats:
                if field not in ROLE_FIELDS:
                    continue
                role = ROLE_FIELDS[field]
                user = getattr(panel, field)
                sub = JudgeSubmission.objects.filter(performance=perf, judge_role=role, judge=user).order_by('-version').first()
                orders = [1] if role.startswith('ARTISTRY') else sequence_orders(perf)
                required = set(schema(panel, role)) - {'invalid_reason', 'timing_penalty_reason', 'timing_penalty', 'attempt_number', 'routine_duration'}
                entered = entered_metadata(sub, schema(panel, role)).get('entered_fields', {}) if sub else {}
                missing = {n: sorted(required - set(entered.get(str(n), []))) for n in orders
                           if required - set(entered.get(str(n), []))}
                if 'routine_duration' in schema(panel, role) and (not sub or 'routine_duration' not in sub.calculation_metadata):
                    missing['routine'] = ['routine_duration']
                judges.append({'role': role, 'name': user.full_name or user.email if user else 'Unassigned',
                    'sheet': serialize_sheet(sub, schema(panel, role)) if sub else None,
                    'missing': missing})
            athletes.append({'id': perf.id, 'name': perf.event_entry.display_names, 'chest': perf.chest_number,
                'batch': perf.batch.batch_number, 'judges': judges,
                'incidents': list(perf.evaluator_incidents.values()) if hasattr(perf, 'evaluator_incidents') else []})
    payload = {'athletes': athletes, 'panel': panel.id if panel else None}
    if request.GET.get('format') == 'json':
        return JsonResponse(payload)
    return render(request, 'scoring/chief_live.html', {'panel': panel, 'live': payload})
