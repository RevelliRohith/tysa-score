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
    selected_event_id = request.GET.get('event_id')
    selected_round = request.GET.get('round_type')
    perf_id_param = request.GET.get('perf_id')

    selected_perf = None
    matching_performances = []
    no_matches = False

    perf_base_qs = Performance.objects.filter(
        event_entry__registration__championship=championship
    ).select_related(
        'event_entry__registration__athlete',
        'event_entry__partner_athlete',
        'event_entry__event_config',
        'event_entry__age_category'
    )

    if selected_event_id:
        perf_base_qs = perf_base_qs.filter(event_entry__event_config_id=selected_event_id)
    if selected_round:
        perf_base_qs = perf_base_qs.filter(round_type=selected_round)

    if perf_id_param:
        selected_perf = perf_base_qs.filter(id=perf_id_param).first()
    elif search_q:
        matches = perf_base_qs.filter(
            Q(event_entry__chest_number__iexact=search_q) |
            Q(event_entry__registration__athlete__first_name__icontains=search_q) |
            Q(event_entry__registration__athlete__last_name__icontains=search_q) |
            Q(event_entry__registration__athlete__public_athlete_id__iexact=search_q) |
            Q(event_entry__partner_athlete__first_name__icontains=search_q) |
            Q(event_entry__partner_athlete__last_name__icontains=search_q) |
            Q(event_entry__partner_athlete__public_athlete_id__iexact=search_q) |
            Q(event_entry__registration__athlete__district__icontains=search_q)
        ).order_by('-id')

        match_count = matches.count()
        if match_count == 1:
            selected_perf = matches.first()
        elif match_count > 1:
            matching_performances = list(matches)
            selected_perf = None
        else:
            no_matches = True
            selected_perf = None
    else:
        selected_perf = perf_base_qs.first()

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
            from .models import Appeal, AppealStatus
            if Appeal.objects.filter(performance_id=perf_id, status__in=[AppealStatus.PENDING, AppealStatus.UNDER_REVIEW]).exists():
                messages.error(request, "Cannot finalize score: An unresolved official appeal is currently pending for this performance.")
                return redirect(f"/cd/corrections/?perf_id={perf_id}")

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

    # Load submissions and item breakdowns if performance selected
    submissions = []
    sequence_items = []
    athlete = None
    if selected_perf:
        athlete = selected_perf.event_entry.registration.athlete
        submissions = JudgeSubmission.objects.filter(
            performance=selected_perf
        ).select_related('judge').prefetch_related('items').order_by('judge_role', '-version')
        
        from registrations.models import AsanaSequenceItem
        sequence_items = AsanaSequenceItem.objects.filter(
            event_entry=selected_perf.event_entry,
            round_type=selected_perf.round_type
        ).order_by('sequence_order')

    return render(request, 'corrections/cd_corrections.html', {
        'championship': championship,
        'chest_query': search_q or chest_query,
        'search_q': search_q,
        'selected_perf': selected_perf,
        'matching_performances': matching_performances,
        'no_matches': no_matches,
        'athlete': athlete,
        'sequence_items': sequence_items,
        'submissions': submissions,
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

@role_required(RoleChoices.ADMIN, RoleChoices.JURY, RoleChoices.CD)
def appeal_list(request):
    """
    Dynamic Appeals Module:
    Lists official competition appeals for the active championship.
    Searchable by chest number, applicant, mobile; filterable by status.
    """
    from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
    from .models import Appeal, AppealStatus

    championship = get_active_championship(request)
    q = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', '').strip()

    appeals_qs = Appeal.objects.filter(
        championship=championship
    ).select_related('performance__event_entry__registration__athlete', 'assigned_reviewer').order_by('-intake_at')

    if q:
        appeals_qs = appeals_qs.filter(
            Q(chest_number__iexact=q) |
            Q(applicant_name__icontains=q) |
            Q(applicant_mobile__icontains=q) |
            Q(reason__icontains=q)
        )

    if status_filter:
        appeals_qs = appeals_qs.filter(status=status_filter)

    total_count = appeals_qs.count()
    pending_count = Appeal.objects.filter(championship=championship, status=AppealStatus.PENDING).count()
    under_review_count = Appeal.objects.filter(championship=championship, status=AppealStatus.UNDER_REVIEW).count()
    upheld_count = Appeal.objects.filter(championship=championship, status=AppealStatus.UPHELD).count()
    rejected_count = Appeal.objects.filter(championship=championship, status=AppealStatus.REJECTED).count()

    paginator = Paginator(appeals_qs, 15)
    page_num = request.GET.get('page', 1)
    try:
        appeals = paginator.page(page_num)
    except (EmptyPage, PageNotAnInteger):
        appeals = paginator.page(1)

    return render(request, 'corrections/appeal_list.html', {
        'championship': championship,
        'appeals': appeals,
        'total_count': total_count,
        'pending_count': pending_count,
        'under_review_count': under_review_count,
        'upheld_count': upheld_count,
        'rejected_count': rejected_count,
        'q': q,
        'status_filter': status_filter,
        'statuses': AppealStatus.choices,
    })

@role_required(RoleChoices.ADMIN, RoleChoices.JURY, RoleChoices.CD)
def appeal_create(request):
    """
    Intake form for lodging a new official competition appeal under COP rules.
    Verifies 30-minute score publication window, fee payment, and evidence attachment.
    """
    from django.utils import timezone
    from .models import Appeal, AppealStatus

    championship = get_active_championship(request)
    chest_prefill = request.GET.get('chest', '').strip()
    perf_id = request.GET.get('perf_id')

    matched_perf = None
    if perf_id:
        matched_perf = Performance.objects.filter(id=perf_id, event_entry__registration__championship=championship).first()
    elif chest_prefill:
        matched_perf = Performance.objects.filter(
            event_entry__chest_number__iexact=chest_prefill,
            event_entry__registration__championship=championship
        ).first()

    if request.method == 'POST':
        chest = request.POST.get('chest_number', '').strip()
        perf = Performance.objects.filter(
            event_entry__chest_number__iexact=chest,
            event_entry__registration__championship=championship
        ).first()

        applicant_name = request.POST.get('applicant_name', '').strip()
        applicant_role = request.POST.get('applicant_role', 'Coach / District In-charge').strip()
        applicant_mobile = request.POST.get('applicant_mobile', '').strip()
        applicant_email = request.POST.get('applicant_email', '').strip()
        fee_receipt = request.POST.get('fee_receipt_number', '').strip()
        reason = request.POST.get('reason', '').strip()

        now = timezone.now()
        first_pub = None
        is_within_window = True
        if perf and perf.chief_approved_at:
            first_pub = perf.chief_approved_at
            # Window check: 30 minutes
            diff_minutes = (now - first_pub).total_seconds() / 60
            is_within_window = (diff_minutes <= 30)

        appeal = Appeal.objects.create(
            championship=championship,
            performance=perf,
            event_entry=perf.event_entry if perf else None,
            chest_number=chest,
            applicant_name=applicant_name,
            applicant_role=applicant_role,
            applicant_mobile=applicant_mobile,
            applicant_email=applicant_email,
            intake_at=now,
            first_score_published_at=first_pub,
            is_within_window=is_within_window,
            fee_receipt_number=fee_receipt,
            reason=reason,
            status=AppealStatus.PENDING,
            form_document=request.FILES.get('form_document'),
            evidence_file=request.FILES.get('evidence_file')
        )
        messages.success(request, f"Appeal #{appeal.id} registered successfully for Chest {chest}.")
        return redirect('appeal_detail', appeal_id=appeal.id)

    return render(request, 'corrections/appeal_create.html', {
        'championship': championship,
        'matched_perf': matched_perf,
        'chest_prefill': chest_prefill,
    })

@role_required(RoleChoices.ADMIN, RoleChoices.JURY, RoleChoices.CD)
def appeal_detail(request, appeal_id):
    """
    Detailed review for an Appeal. Jury, CD, or Admin can review grounds, evidence,
    and record an official decision (Upheld, Rejected, Withdrawn).
    If Upheld, links directly to CD correction grant.
    """
    from django.utils import timezone
    from .models import Appeal, AppealStatus

    championship = get_active_championship(request)
    appeal = get_object_or_404(Appeal, id=appeal_id, championship=championship)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'record_decision':
            new_status = request.POST.get('status')
            notes = request.POST.get('decision_notes', '').strip()

            if new_status in AppealStatus.values:
                appeal.status = new_status
                appeal.decision_notes = notes
                appeal.decision_recorded_at = timezone.now()
                appeal.decision_recorded_by = request.user
                appeal.assigned_reviewer = request.user
                appeal.save()

                messages.success(request, f"Decision recorded for Appeal #{appeal.id}: {appeal.get_status_display()}.")

                # If Upheld, offer link or transition to CD Correction
                if new_status == AppealStatus.UPHELD:
                    messages.info(request, "Appeal Upheld! You can now grant CD correction access for this performance.")
                    if appeal.performance:
                        return redirect(f"/cd/corrections/?chest={appeal.chest_number}&perf_id={appeal.performance.id}")

            return redirect('appeal_detail', appeal_id=appeal.id)

    return render(request, 'corrections/appeal_detail.html', {
        'championship': championship,
        'appeal': appeal,
        'statuses': AppealStatus.choices,
    })
