from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.db.models import Q
from config.districts import TELANGANA_DISTRICTS
from .models import UniversalJudge, CertificationLevel, ApprovalStatus
from accounts.models import ScopedRole, RoleChoices
from accounts.permissions import admin_required, get_user_roles_for_request, get_active_championship
from competition.models import JudgePanel

User = get_user_model()

def get_judge_assignment_info(panel, user):
    """
    Returns (seat_label, scoring_url_name) for a user in a JudgePanel.
    Maps judge's role and event type to the appropriate scoring desk.
    """
    ev_type = panel.event_config.event_type if panel.event_config else 'TRADITIONAL'
    round_type = panel.round_type
    
    seat = None
    url_name = None
    
    if panel.chief_judge_id == user.id:
        seat = 'Chief Judge'
        url_name = 'scoring_chief_judge'
    elif panel.evaluator_id == user.id or panel.evaluator_2_id == user.id:
        seat = 'Evaluator'
        url_name = 'scoring_evaluator'
    elif panel.artistry_judge_id == user.id or panel.artistry_judge_2_id == user.id:
        seat = 'Artistry Judge'
        url_name = 'scoring_artistry'
    elif panel.time_judge_id == user.id or panel.time_judge_2_id == user.id:
        seat = 'Time Judge'
        url_name = 'scoring_traditional_time' if ev_type == 'TRADITIONAL' else 'scoring_music_time'
    elif panel.d1_judge_id == user.id:
        seat = 'D1 Judge'
    elif panel.d2_judge_id == user.id:
        seat = 'D2 Judge'
    elif panel.d3_judge_id == user.id:
        seat = 'D3 Judge'
    elif panel.d4_judge_id == user.id:
        seat = 'D4 Judge'
    else:
        # Check PanelSeatAssignment
        seat_assign = panel.seat_assignments.filter(judge=user, is_active=True).first()
        if seat_assign:
            seat = seat_assign.get_seat_display()
            if 'TIME' in seat_assign.seat:
                url_name = 'scoring_traditional_time' if ev_type == 'TRADITIONAL' else 'scoring_music_time'
            elif 'ARTISTRY' in seat_assign.seat:
                url_name = 'scoring_artistry'
            elif 'EVALUATOR' in seat_assign.seat:
                url_name = 'scoring_evaluator'
            elif 'CHIEF' in seat_assign.seat:
                url_name = 'scoring_chief_judge'
                
    if seat and not url_name:
        # D Judge
        if ev_type == 'TRADITIONAL':
            url_name = 'scoring_traditional_d_final' if round_type == 'FINAL' else 'scoring_traditional_d'
        elif ev_type == 'ARTISTIC_SINGLE':
            url_name = 'scoring_artistic_single_d'
        elif ev_type == 'ARTISTIC_PAIR':
            url_name = 'scoring_artistic_pair_d'
        elif ev_type == 'RHYTHMIC_PAIR':
            url_name = 'scoring_rhythmic_pair_d'
        else:
            url_name = 'scoring_traditional_d'
            
    return seat, url_name

def models_q_for_judge(user):
    return (
        Q(d1_judge=user) | Q(d2_judge=user) | Q(d3_judge=user) | Q(d4_judge=user) |
        Q(time_judge=user) | Q(time_judge_2=user) |
        Q(artistry_judge=user) | Q(artistry_judge_2=user) |
        Q(evaluator=user) | Q(evaluator_2=user) |
        Q(chief_judge=user) |
        Q(seat_assignments__judge=user, seat_assignments__is_active=True)
    )

def universal_judge_register(request):
    """
    Implements 09_Universal_Judge_Registration.png:
    Championship-independent judge application with certification level,
    experience, district, and credentials.
    """
    if request.method == 'POST':
        full_name = request.POST.get('full_name', '').strip()
        email = request.POST.get('email', '').strip()
        mobile = request.POST.get('mobile', '').strip()
        district = request.POST.get('district', '').strip()
        cert_level = request.POST.get('certification_level')
        cert_authority = request.POST.get('certification_authority', 'Yogasana Bharat / State Assoc')
        cert_number = request.POST.get('certificate_number', '').strip()
        exp_years = int(request.POST.get('experience_years', 1))
        password = request.POST.get('password')

        if User.objects.filter(email=email).exists():
            messages.error(request, "An account with this email already exists. Please sign in or contact Admin.")
            return redirect('universal_judge_register')

        user = User.objects.create_user(
            email=email,
            password=password,
            full_name=full_name,
            mobile=mobile,
            is_staff=True
        )

        UniversalJudge.objects.create(
            user=user,
            certification_level=cert_level,
            certification_authority=cert_authority,
            certificate_number=cert_number,
            experience_years=exp_years,
            district=district,
            approval_status=ApprovalStatus.PENDING
        )

        messages.success(request, "Judge application submitted successfully! Pending System Admin review and approval before scoring desk access.")
        return redirect('login')

    return render(request, 'judges/register.html', {
        'cert_levels': CertificationLevel.choices,
    })

@login_required
def judge_portal(request):
    """
    Landing portal for logged-in judges (10_Judge_Profile_and_Assigned_Panels.png):
    Loads the authenticated judge's profile and assigned stage panels.
    - Pending judge -> profile and approval status; no scoring access.
    - Approved judge without a panel -> Profile and 'No Panel Assigned'.
    - Does NOT auto-create approved judge accounts.
    """
    judge = UniversalJudge.objects.filter(user=request.user).first()
    if not judge:
        roles = get_user_roles_for_request(request)
        is_judge_role = any(r in roles for r in [
            RoleChoices.D_JUDGE, RoleChoices.TIME_JUDGE, RoleChoices.ARTISTRY_JUDGE,
            RoleChoices.EVALUATOR, RoleChoices.CHIEF_JUDGE
        ])
        if is_judge_role or request.user.is_staff:
            judge, _ = UniversalJudge.objects.get_or_create(
                user=request.user,
                defaults={
                    'district': getattr(request.user, 'district', 'Hyderabad') or 'Hyderabad',
                    'approval_status': ApprovalStatus.PENDING,
                    'certification_level': CertificationLevel.STATE_LEVEL,
                }
            )
        else:
            messages.info(request, "No judge profile found. You can submit a judge application below.")
            return redirect('universal_judge_register')

    champ = get_active_championship(request)
    assigned_panels = []
    
    if judge.approval_status == ApprovalStatus.APPROVED:
        panels_qs = JudgePanel.objects.filter(
            models_q_for_judge(request.user)
        ).select_related('championship', 'event_config', 'age_category').distinct()
        if champ:
            panels_qs = panels_qs.filter(championship=champ)

        for p in panels_qs:
            seat_label, scoring_url_name = get_judge_assignment_info(p, request.user)
            assigned_panels.append({
                'panel': p,
                'seat_label': seat_label or 'Assigned Seat',
                'scoring_url_name': scoring_url_name or 'scoring_traditional_d',
            })

    return render(request, 'judges/profile.html', {
        'judge': judge,
        'assigned_panels': assigned_panels,
        'is_approved': (judge.approval_status == ApprovalStatus.APPROVED),
        'is_pending': (judge.approval_status == ApprovalStatus.PENDING),
        'is_rejected': (judge.approval_status == ApprovalStatus.REJECTED),
        'is_reviewer': False,
    })

@login_required
def judge_profile(request, judge_id):
    """
    Implements 10_Judge_Profile_and_Assigned_Panels.png:
    Scoped view: Judges may only view their own profile.
    System Admin and CD may inspect any judge's profile.
    """
    judge = get_object_or_404(UniversalJudge, id=judge_id)
    
    # Permission check: own profile OR Admin OR CD
    user_roles = get_user_roles_for_request(request)
    is_privileged = request.user.is_superuser or (RoleChoices.ADMIN in user_roles) or (RoleChoices.CD in user_roles)
    if not is_privileged and judge.user != request.user:
        messages.warning(request, "You can only view your own judge profile and assigned panels.")
        return redirect('judge_portal')

    champ = get_active_championship(request)
    assigned_panels = []
    if judge.approval_status == ApprovalStatus.APPROVED:
        panels_qs = JudgePanel.objects.filter(
            models_q_for_judge(judge.user)
        ).select_related('championship', 'event_config', 'age_category').distinct()
        if champ and not is_privileged:
            panels_qs = panels_qs.filter(championship=champ)

        for p in panels_qs:
            seat_label, scoring_url_name = get_judge_assignment_info(p, judge.user)
            assigned_panels.append({
                'panel': p,
                'seat_label': seat_label or 'Assigned Seat',
                'scoring_url_name': scoring_url_name or 'scoring_traditional_d',
            })

    return render(request, 'judges/profile.html', {
        'judge': judge,
        'assigned_panels': assigned_panels,
        'is_approved': (judge.approval_status == ApprovalStatus.APPROVED),
        'is_pending': (judge.approval_status == ApprovalStatus.PENDING),
        'is_rejected': (judge.approval_status == ApprovalStatus.REJECTED),
        'is_reviewer': is_privileged and (judge.user != request.user),
    })

@admin_required
def judge_approval_queue(request):
    """
    Implements 09_Universal_Judge_Registration.png, approval, and qualifications:
    Admin management for Universal Judges with 3 distinct dedicated views:
    - 'universal': Complete statewide directory of all Universal Judges.
    - 'applications': Dedicated approval queue with Approve / Reject actions.
    - 'qualifications': Certification details, authorities, license numbers, and profile inspection.
    """
    tab = request.GET.get('tab', 'universal')
    if tab not in ['universal', 'applications', 'qualifications']:
        tab = 'universal'

    all_judges = UniversalJudge.objects.select_related('user', 'approved_by').all().order_by('-id')
    pending_judges = all_judges.filter(approval_status=ApprovalStatus.PENDING)
    approved_judges = all_judges.filter(approval_status=ApprovalStatus.APPROVED)
    rejected_judges = all_judges.filter(approval_status=ApprovalStatus.REJECTED)

    if request.method == 'POST':
        judge_id = request.POST.get('judge_id')
        decision = request.POST.get('decision')
        judge = get_object_or_404(UniversalJudge, id=judge_id)

        if decision == 'approve':
            judge.approval_status = ApprovalStatus.APPROVED
            judge.approved_by = request.user
            judge.save()
            messages.success(request, f"Universal Judge {judge.user.full_name or judge.user.email} officially approved and accredited.")
        elif decision == 'reject':
            judge.approval_status = ApprovalStatus.REJECTED
            judge.save()
            messages.info(request, f"Judge application for {judge.user.full_name or judge.user.email} marked rejected.")
        return redirect(f"/judges/approval-queue/?tab={tab}")

    return render(request, 'judges/approval_queue.html', {
        'tab': tab,
        'all_judges': all_judges,
        'pending_judges': pending_judges,
        'approved_judges': approved_judges,
        'rejected_judges': rejected_judges,
    })

def judge_id_card(request, judge_id):
    """
    Universal Judge Accreditation ID Card:
    Championship-independent official pass.
    Includes association details, judge credentials, QR code, and General Secretary signature.
    """
    judge = get_object_or_404(UniversalJudge, id=judge_id)
    return render(request, 'judges/id_card.html', {
        'judge': judge,
    })

@admin_required
def judge_id_cards_batch_print(request):
    """
    Universal Judge ID Cards - District & Batch Printing:
    Filters judges by district, certification level, and print status.
    Provides batch printable card sheet and 'Mark Selected as Printed' action.
    """
    selected_district = request.GET.get('district', '').strip()
    selected_cert = request.GET.get('cert', '').strip()
    print_status = request.GET.get('status', 'all').strip()

    judges_qs = UniversalJudge.objects.filter(approval_status=ApprovalStatus.APPROVED).select_related('user').order_by('district', 'user__full_name')
    if selected_district:
        judges_qs = judges_qs.filter(district=selected_district)
    if selected_cert:
        judges_qs = judges_qs.filter(certification_level=selected_cert)
    if print_status == 'pending':
        judges_qs = judges_qs.filter(is_id_card_printed=False)
    elif print_status == 'printed':
        judges_qs = judges_qs.filter(is_id_card_printed=True)

    if request.method == 'POST':
        action = request.POST.get('action')
        selected_ids = request.POST.getlist('selected_judge_ids')
        if action == 'mark_printed':
            updated = UniversalJudge.objects.filter(id__in=selected_ids).update(
                is_id_card_printed=True,
                id_card_printed_at=timezone.now()
            )
            messages.success(request, f"Marked {updated} Judge ID cards as printed.")
        elif action == 'mark_unprinted':
            updated = UniversalJudge.objects.filter(id__in=selected_ids).update(
                is_id_card_printed=False,
                id_card_printed_at=None
            )
            messages.info(request, f"Reset print status for {updated} judges.")
        return redirect(f"/judges/id-cards/batch/?district={selected_district}&cert={selected_cert}&status={print_status}")

    return render(request, 'judges/id_cards_batch.html', {
        'judges': judges_qs,
        'districts': TELANGANA_DISTRICTS,
        'cert_choices': CertificationLevel.choices,
        'selected_district': selected_district,
        'selected_cert': selected_cert,
        'print_status': print_status,
        'total_count': judges_qs.count(),
    })
