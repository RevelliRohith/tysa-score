import datetime
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q
from django.core.exceptions import PermissionDenied, ValidationError
from .models import CDCorrectionGrant, GrantStatus
from .services import (
    grant_correction_access,
    revoke_correction_access,
    resubmit_judge_correction,
    finalize_performance_by_cd
)
from scoring.models import Performance, PerformanceStatus, JudgeSubmission, AsanaScoreItem
from championships.models import Championship, EventConfiguration
from accounts.models import RoleChoices
from accounts.permissions import (
    get_active_championship,
    role_required,
    user_can_grant_correction,
    get_user_roles_for_request,
    get_user_landing_url
)

@login_required
def cd_corrections(request):
    """
    Implements 26_CD_Correction_and_Finalisation.png:
    CD searches performance by chest number, inspects marks,
    grants access to 1 judge's entire submission for 1 performance with reason,
    revokes access or finalises score.

    Strict Championship Isolation & Transactional Governance:
    - Every performance, submission, and grant lookup is strictly scoped to the active championship.
    - Only an assigned CD may grant/revoke correction access or finalize scores.
    - Only the original authorized judge may resubmit their corrected performance.
    - All mutations route through transactional production services.
    """
    championship = get_active_championship(request)
    if not championship:
        messages.error(request, "No active championship selected or assigned.")
        return redirect('no_role_assigned')

    can_unlock = user_can_grant_correction(request.user, championship)

    # For GET requests, ensure user is Admin or CD on this championship
    if request.method == 'GET':
        user_roles = get_user_roles_for_request(request, championship=championship)
        is_cd_or_admin = (RoleChoices.ADMIN in user_roles or RoleChoices.CD in user_roles)
        if not is_cd_or_admin:
            messages.warning(request, "Access restricted: Only Competition Director or Admin can access the CD Correction Desk.")
            return redirect(get_user_landing_url(request))

    chest_query = request.GET.get('chest', '').strip()
    search_q = request.GET.get('q', '').strip() or chest_query
    selected_perf = None
    if search_q:
        selected_perf = Performance.objects.filter(
            event_entry__registration__championship=championship
        ).filter(
            Q(event_entry__chest_number__iexact=search_q) |
            Q(event_entry__registration__athlete__first_name__icontains=search_q) |
            Q(event_entry__registration__athlete__last_name__icontains=search_q) |
            Q(event_entry__district__icontains=search_q)
        ).select_related('event_entry__registration__athlete', 'event_entry__event_config', 'event_entry__age_category').first()

    if not selected_perf:
        selected_perf = Performance.objects.filter(
            event_entry__registration__championship=championship
        ).select_related('event_entry__registration__athlete', 'event_entry__event_config', 'event_entry__age_category').first()

    grants = CDCorrectionGrant.objects.filter(
        performance__event_entry__registration__championship=championship,
        status=GrantStatus.OPEN
    ).select_related('performance__event_entry', 'judge_submission__judge')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'grant_correction':
            perf_id = request.POST.get('performance_id')
            sub_id = request.POST.get('submission_id')
            reason = request.POST.get('reason', '').strip()

            try:
                grant = grant_correction_access(
                    user=request.user,
                    performance_id=perf_id,
                    submission_id=sub_id,
                    reason=reason,
                    championship=championship
                )
                messages.success(
                    request,
                    f"Correction access granted to {grant.judge_submission.get_judge_role_display()} "
                    f"for Chest {grant.performance.chest_number}. Reason recorded: {grant.reason}"
                )
                return redirect(f"/cd/corrections/?chest={grant.performance.chest_number}")
            except (ValidationError, PermissionDenied) as e:
                err_msg = e.message if hasattr(e, 'message') else str(e)
                messages.error(request, err_msg)
                return redirect(f"/cd/corrections/?chest={chest_query}")

        elif action == 'resubmit_correction':
            grant_id = request.POST.get('grant_id')
            try:
                grant, sub = resubmit_judge_correction(
                    user=request.user,
                    grant_id=grant_id,
                    championship=championship
                )
                messages.success(
                    request,
                    f"Corrected submission v{sub.version} for {sub.get_judge_role_display()} "
                    f"officially resubmitted and relocked."
                )
                return redirect(f"/cd/corrections/?chest={grant.performance.chest_number}")
            except (ValidationError, PermissionDenied) as e:
                err_msg = e.message if hasattr(e, 'message') else str(e)
                messages.error(request, err_msg)
                return redirect(f"/cd/corrections/?chest={chest_query}")

        elif action == 'revoke_grant':
            grant_id = request.POST.get('grant_id')
            try:
                grant = revoke_correction_access(
                    user=request.user,
                    grant_id=grant_id,
                    championship=championship
                )
                messages.info(request, f"Correction access revoked for Grant #{grant.id}.")
            except (ValidationError, PermissionDenied) as e:
                err_msg = e.message if hasattr(e, 'message') else str(e)
                messages.error(request, err_msg)
            return redirect('cd_corrections')

        elif action == 'cd_finalise':
            perf_id = request.POST.get('performance_id')
            try:
                perf = finalize_performance_by_cd(
                    user=request.user,
                    performance_id=perf_id,
                    championship=championship
                )
                messages.success(request, f"Performance for Chest {perf.chest_number} officially FINALIZED and locked by CD.")
                return redirect(f"/cd/corrections/?chest={perf.chest_number}")
            except (ValidationError, PermissionDenied) as e:
                err_msg = e.message if hasattr(e, 'message') else str(e)
                messages.error(request, err_msg)
                return redirect(f"/cd/corrections/?chest={chest_query}")

    return render(request, 'corrections/cd_corrections.html', {
        'championship': championship,
        'chest_query': search_q or chest_query,
        'search_q': search_q,
        'selected_perf': selected_perf,
        'active_grants': grants,
        'can_unlock': can_unlock,
    })

@role_required(RoleChoices.ADMIN, RoleChoices.JURY, RoleChoices.CD)
def jury_view(request):
    """
    Implements 25 jury.png:
    Read-only score audit desk for Jury:
    Search by name OR chest number OR district, with event/round filters,
    per-judge breakdown, sequence marks, and revision history.
    Cannot edit scores, unlock judges, or finalise results.
    """
    championship = get_active_championship(request)
    query = request.GET.get('q', '').strip()
    district = request.GET.get('district', '').strip()
    event_id = request.GET.get('event_id')
    round_type = request.GET.get('round_type')

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
    if event_id:
        performances = performances.filter(event_entry__event_config_id=event_id)
    if round_type:
        performances = performances.filter(round_type=round_type)

    selected_perf_id = request.GET.get('select_id')
    selected_perf = performances.filter(id=selected_perf_id).first() if selected_perf_id else performances.first()

    submissions = selected_perf.submissions.prefetch_related('items').all() if selected_perf else []

    event_configs = championship.event_configurations.filter(is_enabled=True) if championship else []

    return render(request, 'corrections/jury.html', {
        'championship': championship,
        'query': query,
        'district': district,
        'event_id': event_id,
        'round_type': round_type,
        'event_configs': event_configs,
        'performances': performances[:25],
        'selected_perf': selected_perf,
        'submissions': submissions,
    })
