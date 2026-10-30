import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Count, Q
from django.http import JsonResponse
from .models import User, ScopedRole, RoleChoices, Coach, Volunteer, Guest
from .permissions import (
    is_system_admin, get_authorized_championships, get_active_championship,
    get_user_roles_for_request, get_user_workspaces, get_user_landing_url,
    is_safe_redirect_url, is_user_authorized_for_url,
    admin_required, role_required
)
from championships.models import Championship, ChampionshipStatus, DistrictQuota
from athletes.models import Athlete
from registrations.models import ChampionshipRegistration, EventEntry
from judges.models import UniversalJudge, ApprovalStatus
from competition.models import JudgePanel, StageBatch
from scoring.models import Performance

def login_view(request):
    """
    Role-aware Authentication & Destination Routing:
    - System Admin -> All Modules dashboard (/dashboard/)
    - CD -> assigned championship CD dashboard (/cd/dashboard/)
    - District User -> own-district dashboard (/district/approvals/)
    - Venue Desk -> Venue Verification (/venue/verification/)
    - Stage Manager -> assigned stage console (/competition/stage/)
    - D/Time/Artistry/Evaluator/Chief -> Judge Portal (/judges/portal/)
    - Jury -> Jury Review (/jury/)
    - Multiple valid roles -> authorized role/championship selection (/accounts/role-select/)
    - No assignment -> clear 'No active assignment' screen (/accounts/no-role/)
    - Pending judge -> own profile and approval status; no scoring access (/judges/portal/)
    - Approved judge without a panel -> Profile and Assigned Panels with 'No Panel Assigned' (/judges/portal/)

    Preserves safe internal next-page redirects ONLY when the destination is authorized.
    Rejects external redirect destinations.
    """
    if request.user.is_authenticated:
        landing = get_user_landing_url(request)
        return redirect(landing)

    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        user = authenticate(request, email=email, password=password)
        if user is not None:
            if not user.is_active:
                messages.error(request, "This account has been deactivated. Please contact the administrator.")
                return redirect('login')

            login(request, user)
            messages.success(request, f"Welcome back, {user.full_name or user.email}!")

            # Safe Next-page redirect check
            next_url = request.GET.get('next') or request.POST.get('next')
            if next_url and is_safe_redirect_url(next_url, request.get_host()):
                champ = get_active_championship(request, user=user)
                if is_user_authorized_for_url(user, next_url, champ):
                    return redirect(next_url)
                else:
                    messages.warning(
                        request,
                        "The requested page is outside your authorized role scope. Redirected to your workspace."
                    )

            landing = get_user_landing_url(request)
            return redirect(landing)
        else:
            messages.error(request, "Invalid email or password. Please try again.")

    return render(request, 'accounts/login.html', {
        'hide_navbar': True
    })

def logout_view(request):
    """Safe user logout."""
    logout(request)
    messages.info(request, "You have been safely signed out.")
    return redirect('login')

@login_required
def role_select_view(request):
    """
    Workspace selector for users who hold multiple valid roles across championships.
    Allows user to explicitly select which role and championship to activate.
    """
    workspaces = get_user_workspaces(request.user)

    if request.method == 'POST':
        ws_idx = request.POST.get('workspace_index')
        try:
            ws_idx = int(ws_idx)
            selected_ws = workspaces[ws_idx]
            if selected_ws['championship']:
                request.session['active_championship_id'] = selected_ws['championship'].id
            messages.success(request, f"Activated workspace: {selected_ws['role_display']} ({selected_ws['championship_name']})")
            return redirect(selected_ws['url'])
        except (ValueError, IndexError, KeyError):
            messages.error(request, "Invalid workspace selection.")

    return render(request, 'accounts/role_select.html', {
        'workspaces': workspaces,
    })

@admin_required
def admin_dashboard(request):
    """
    System Admin All-Modules Dashboard:
    Strictly restricted to System Admin.
    Calculates REAL, scoped metrics for the active championship without mock fallbacks.
    Returns real zero counts if no records exist.
    """
    championship = get_active_championship(request)

    if championship:
        total_athletes = Athlete.objects.filter(registrations__championship=championship).distinct().count()
        total_registrations = ChampionshipRegistration.objects.filter(championship=championship).count()
        total_entries = EventEntry.objects.filter(registration__championship=championship).count()
        venue_cleared = ChampionshipRegistration.objects.filter(championship=championship, venue_cleared=True).count()
        pending_approvals = ChampionshipRegistration.objects.filter(championship=championship, status='SUBMITTED').count()
        active_panels = JudgePanel.objects.filter(championship=championship, is_active=True).count()
        scored_performances = Performance.objects.filter(
            event_entry__registration__championship=championship,
            status__in=['PROVISIONAL_RELEASED', 'CD_FINALIZED']
        ).count()
    else:
        total_athletes = 0
        total_registrations = 0
        total_entries = 0
        venue_cleared = 0
        pending_approvals = 0
        active_panels = 0
        scored_performances = 0

    approved_judges = UniversalJudge.objects.filter(approval_status=ApprovalStatus.APPROVED).count()
    pending_judges = UniversalJudge.objects.filter(approval_status=ApprovalStatus.PENDING).count()

    return render(request, 'accounts/dashboard.html', {
        'championship': championship,
        'stats': {
            'total_athletes': total_athletes,
            'total_registrations': total_registrations,
            'total_entries': total_entries,
            'venue_cleared': venue_cleared,
            'approved_judges': approved_judges,
            'active_panels': active_panels,
            'scored_performances': scored_performances,
            'pending_approvals': pending_approvals,
            'pending_judges': pending_judges,
            'pending_exceptions': 0,
        }
    })

@admin_required
def users_and_permissions(request):
    """
    Users & Scoped Permissions Management:
    Strictly restricted to System Admin.
    Manages management/operational staff only:
    - System Admin (Global)
    - Competition Director (CD) (Championship scope)
    - District User (Championship + District scope)
    - Venue Desk (Championship scope)
    - Stage Manager / Announcer (Championship + Stage scope)
    - Jury (Championship scope read-only reviewer)

    Judges are NOT listed or assigned duties here:
    Judges undergo separate Universal Registration and Admin Approval,
    and their scoring duties are derived exclusively from confirmed CD Judge Panel Assignments.
    """
    championship = get_active_championship(request)

    MANAGEMENT_ROLE_KEYS = [
        RoleChoices.ADMIN,
        RoleChoices.CD,
        RoleChoices.DISTRICT_USER,
        RoleChoices.VENUE_DESK,
        RoleChoices.STAGE_MANAGER,
        RoleChoices.JURY,
    ]
    roles = [
        (RoleChoices.ADMIN, 'System Admin (Global)'),
        (RoleChoices.CD, 'Competition Director (CD)'),
        (RoleChoices.DISTRICT_USER, 'District User (Scoped)'),
        (RoleChoices.VENUE_DESK, 'Venue Desk (Scoped)'),
        (RoleChoices.STAGE_MANAGER, 'Stage Manager / Announcer'),
        (RoleChoices.JURY, 'Jury (Read-only Review)'),
    ]

    # Only show management staff (or superusers). Do not list pure judges in management screen.
    users = User.objects.filter(
        Q(is_superuser=True) | Q(scoped_roles__role__in=MANAGEMENT_ROLE_KEYS)
    ).distinct().prefetch_related('scoped_roles__championship').order_by('id')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add_user':
            email = request.POST.get('email', '').strip()
            full_name = request.POST.get('full_name', '').strip()
            role = request.POST.get('role')
            district = request.POST.get('district', '').strip()
            champ_scope_id = request.POST.get('championship_id')

            if role not in MANAGEMENT_ROLE_KEYS:
                messages.error(request, "Invalid role. Judge duties cannot be assigned from Users & Permissions.")
                return redirect('users_and_permissions')

            target_champ = None
            if role == RoleChoices.ADMIN:
                target_champ = None
            elif champ_scope_id:
                target_champ = Championship.objects.filter(id=champ_scope_id).first()
            else:
                target_champ = championship

            user, created = User.objects.get_or_create(
                email=email,
                defaults={'full_name': full_name, 'is_staff': True}
            )
            if created:
                user.set_password('tysa2026')
                user.save()

            ScopedRole.objects.get_or_create(
                user=user,
                role=role,
                championship=target_champ,
                district=district
            )
            messages.success(request, f"Operational staff user {email} assigned role {role}.")
            return redirect('users_and_permissions')

        elif action == 'revoke_role':
            role_id = request.POST.get('role_id')
            role_obj = get_object_or_404(ScopedRole, id=role_id)
            user_email = role_obj.user.email
            role_name = role_obj.get_role_display()
            role_obj.delete()
            messages.info(request, f"Role {role_name} revoked from {user_email}.")
            return redirect('users_and_permissions')

    all_championships = Championship.objects.all().order_by('-year')

    return render(request, 'accounts/users_and_permissions.html', {
        'users': users,
        'roles': roles,
        'championship': championship,
        'all_championships': all_championships,
    })

@role_required(RoleChoices.ADMIN, RoleChoices.CD)
def registers_view(request):
    """
    Volunteers, Coaches & Guests Registry:
    Admin-maintained records only. A registry record does not create
    an account or system permissions. No coach-to-athlete access.
    """
    championship = get_active_championship(request)
    tab = request.GET.get('tab', 'coaches')

    if request.method == 'POST':
        entity_type = request.POST.get('entity_type')
        name = request.POST.get('name')
        mobile = request.POST.get('mobile')
        district = request.POST.get('district')

        if entity_type == 'coach':
            academy = request.POST.get('academy_org', '')
            qualification = request.POST.get('qualification', '')
            Coach.objects.create(
                championship=championship,
                name=name,
                mobile=mobile,
                district=district,
                academy_org=academy,
                qualification=qualification
            )
            messages.success(request, f"Coach {name} added to official registry.")
            return redirect('/registers/?tab=coaches')
        elif entity_type == 'volunteer':
            role_assigned = request.POST.get('role_assigned', 'Logistics')
            Volunteer.objects.create(
                championship=championship,
                name=name,
                mobile=mobile,
                district=district,
                role_assigned=role_assigned
            )
            messages.success(request, f"Volunteer {name} added to official registry.")
            return redirect('/registers/?tab=volunteers')
        elif entity_type == 'guest':
            designation = request.POST.get('designation', 'Guest')
            org = request.POST.get('organization', '')
            Guest.objects.create(
                championship=championship,
                name=name,
                designation=designation,
                organization=org
            )
            messages.success(request, f"Guest {name} added to official registry.")
            return redirect('/registers/?tab=guests')

    coaches = Coach.objects.filter(championship=championship) if championship else Coach.objects.none()
    volunteers = Volunteer.objects.filter(championship=championship) if championship else Volunteer.objects.none()
    guests = Guest.objects.filter(championship=championship) if championship else Guest.objects.none()

    return render(request, 'accounts/registers.html', {
        'championship': championship,
        'tab': tab,
        'coaches': coaches,
        'volunteers': volunteers,
        'guests': guests,
    })

@login_required
def no_role_assigned(request):
    """Clear fallback landing screen for users with no active role in the selected championship."""
    return render(request, 'accounts/no_role.html')
