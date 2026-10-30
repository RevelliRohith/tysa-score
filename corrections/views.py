import datetime
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q
from .models import CDCorrectionGrant, GrantStatus
from scoring.models import Performance, PerformanceStatus, JudgeSubmission, AsanaScoreItem
from championships.models import Championship, EventConfiguration

@login_required
def cd_corrections(request):
    """
    Implements 26_CD_Correction_and_Finalisation.png:
    CD searches performance by chest number, inspects marks,
    grants access to 1 judge's entire submission for 1 performance with reason,
    revokes access or finalises score. Restricted to Staff/Admin/CD.
    """
    # Strict permission check: CD, Admin, Superuser only
    is_authorized = request.user.is_superuser or request.user.scoped_roles.filter(role__in=['CD', 'ADMIN']).exists()
    if not is_authorized:
        messages.error(request, "Permission denied. Only Competition Director or Admin can access CD Corrections.")
        return redirect('admin_dashboard')

    championship = Championship.objects.first()
    chest_query = request.GET.get('chest', '').strip()
    
    selected_perf = None
    if chest_query:
        selected_perf = Performance.objects.filter(
            event_entry__registration__championship=championship,
            event_entry__chest_number=chest_query
        ).select_related('event_entry__registration__athlete').first()

    grants = CDCorrectionGrant.objects.filter(status=GrantStatus.OPEN).select_related('performance__event_entry', 'judge_submission__judge')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'grant_correction':
            perf_id = request.POST.get('performance_id')
            sub_id = request.POST.get('submission_id')
            reason = request.POST.get('reason', '').strip()

            perf = get_object_or_404(Performance, id=perf_id)
            sub = get_object_or_404(JudgeSubmission, id=sub_id, performance=perf)

            # Open 1 judge's entire submission only
            grant = CDCorrectionGrant.objects.create(
                performance=perf,
                judge_submission=sub,
                granted_by=request.user,
                reason=reason,
                status=GrantStatus.OPEN
            )
            # Unlock that judge's sheet
            sub.is_locked = False
            sub.is_draft = True
            sub.save()

            messages.success(request, f"Correction access granted to {sub.get_judge_role_display()} for Chest {perf.chest_number}. Reason recorded: {reason}")
            return redirect(f"/cd/corrections/?chest={perf.chest_number}")

        elif action == 'revoke_grant':
            grant_id = request.POST.get('grant_id')
            grant = get_object_or_404(CDCorrectionGrant, id=grant_id)
            grant.status = GrantStatus.REVOKED
            grant.revoked_at = datetime.datetime.now()
            grant.revoked_by = request.user
            grant.save()

            grant.judge_submission.is_locked = True
            grant.judge_submission.save()

            messages.info(request, f"Correction access revoked for Grant #{grant.id}.")
            return redirect('cd_corrections')

        elif action == 'cd_finalise':
            perf_id = request.POST.get('performance_id')
            perf = get_object_or_404(Performance, id=perf_id)
            perf.status = PerformanceStatus.CD_FINALIZED
            perf.cd_finalized_by = request.user
            perf.cd_finalized_at = datetime.datetime.now()
            perf.save()
            messages.success(request, f"Performance for Chest {perf.chest_number} officially FINALIZED and locked by CD.")
            return redirect(f"/cd/corrections/?chest={perf.chest_number}")

    return render(request, 'corrections/cd_corrections.html', {
        'championship': championship,
        'chest_query': chest_query,
        'selected_perf': selected_perf,
        'active_grants': grants,
    })

@login_required
def jury_view(request):
    """
    Implements 25 jury.png:
    Read-only score audit desk for Jury:
    Search by name OR chest number OR district, with event/round filters,
    per-judge breakdown, sequence marks, and revision history.
    Cannot edit scores, unlock judges, or finalise results.
    """
    championship = Championship.objects.first()
    query = request.GET.get('q', '').strip()
    district = request.GET.get('district', '').strip()

    performances = Performance.objects.filter(
        event_entry__registration__championship=championship
    ).select_related('event_entry__registration__athlete', 'batch')

    if query:
        performances = performances.filter(
            Q(event_entry__chest_number__iexact=query) |
            Q(event_entry__registration__athlete__first_name__icontains=query) |
            Q(event_entry__registration__athlete__last_name__icontains=query)
        )
    if district:
        performances = performances.filter(event_entry__registration__athlete__district=district)

    selected_perf_id = request.GET.get('select_id')
    selected_perf = performances.filter(id=selected_perf_id).first() if selected_perf_id else performances.first()

    submissions = selected_perf.submissions.prefetch_related('items').all() if selected_perf else []

    return render(request, 'corrections/jury.html', {
        'championship': championship,
        'query': query,
        'district': district,
        'performances': performances[:20],
        'selected_perf': selected_perf,
        'submissions': submissions,
    })
