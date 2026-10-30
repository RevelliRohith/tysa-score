from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .models import UniversalJudge, CertificationLevel, ApprovalStatus
from accounts.models import ScopedRole, RoleChoices
from competition.models import JudgePanel

User = get_user_model()

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
def judge_profile(request, judge_id):
    """
    Implements 10_Judge_Profile_and_Assigned_Panels.png:
    Universal judge profile, Admin approval status badge, credentials,
    and assigned stage panel schedule.
    """
    judge = get_object_or_404(UniversalJudge, id=judge_id)
    
    # Panels assigned to this judge
    assigned_panels = JudgePanel.objects.filter(
        models_q_for_judge(judge.user)
    ).select_related('championship', 'event_config')

    return render(request, 'judges/profile.html', {
        'judge': judge,
        'assigned_panels': assigned_panels,
    })

@login_required
def judge_approval_queue(request):
    """
    Admin approval queue for universal judges:
    Admin reviews pending applications and approves or rejects.
    Pending judges cannot access scoring desks.
    """
    pending_judges = UniversalJudge.objects.filter(approval_status=ApprovalStatus.PENDING)
    approved_judges = UniversalJudge.objects.filter(approval_status=ApprovalStatus.APPROVED)

    if request.method == 'POST':
        judge_id = request.POST.get('judge_id')
        decision = request.POST.get('decision')
        judge = get_object_or_404(UniversalJudge, id=judge_id)

        if decision == 'approve':
            judge.approval_status = ApprovalStatus.APPROVED
            judge.approved_by = request.user
            judge.save()
            # Assign default D_JUDGE scoped role
            ScopedRole.objects.get_or_create(user=judge.user, role=RoleChoices.D_JUDGE)
            messages.success(request, f"Judge {judge.user.full_name} officially approved.")
        elif decision == 'reject':
            judge.approval_status = ApprovalStatus.REJECTED
            judge.save()
            messages.info(request, f"Judge {judge.user.full_name} application rejected.")
        return redirect('judge_approval_queue')

    return render(request, 'judges/approval_queue.html', {
        'pending_judges': pending_judges,
        'approved_judges': approved_judges,
    })

def models_q_for_judge(user):
    from django.db.models import Q
    return (
        Q(d1_judge=user) | Q(d2_judge=user) | Q(d3_judge=user) | Q(d4_judge=user) |
        Q(time_judge=user) | Q(evaluator=user) | Q(artistry_judge=user) | Q(chief_judge=user)
    )
