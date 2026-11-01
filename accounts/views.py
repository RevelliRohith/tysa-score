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
from config.districts import TELANGANA_DISTRICTS
from audit.models import AuditLog
from django.views.decorators.csrf import ensure_csrf_cookie

@ensure_csrf_cookie
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
    Operational Admin Dashboard:
    Displays running active championships, live competition metrics,
    direct operational control links, and recent audit activity.
    Redundant static module cards removed as requested.
    """
    from registrations.models import RegistrationStatus
    championship = get_active_championship(request)
    running_championships = Championship.objects.all().order_by('-year', '-start_date')

    if championship:
        base_regs = ChampionshipRegistration.objects.filter(championship=championship)
        total_athletes = Athlete.objects.filter(registrations__championship=championship).distinct().count()
        total_registrations = base_regs.count()
        total_entries = EventEntry.objects.filter(registration__championship=championship).count()
        
        # Unique athlete counts across pipeline
        dist_approved_athletes = Athlete.objects.filter(
            registrations__championship=championship,
            registrations__status__in=[RegistrationStatus.DISTRICT_APPROVED, RegistrationStatus.VENUE_CLEARED]
        ).distinct().count()

        pending_athletes = Athlete.objects.filter(
            registrations__championship=championship,
            registrations__status=RegistrationStatus.SUBMITTED
        ).distinct().count()

        awaiting_venue_clearance = Athlete.objects.filter(
            registrations__championship=championship,
            registrations__status=RegistrationStatus.DISTRICT_APPROVED,
            registrations__venue_cleared=False
        ).distinct().count()

        venue_cleared = Athlete.objects.filter(
            registrations__championship=championship
        ).filter(
            Q(registrations__status=RegistrationStatus.VENUE_CLEARED) | Q(registrations__venue_cleared=True)
        ).distinct().count()

        corrections_count = base_regs.filter(status=RegistrationStatus.DRAFT).count()
        rejected_count = base_regs.filter(status=RegistrationStatus.DISTRICT_REJECTED).count()

        active_panels = JudgePanel.objects.filter(championship=championship, is_active=True).count()
        scored_performances = Performance.objects.filter(
            event_entry__registration__championship=championship,
            status__in=['PROVISIONAL_RELEASED', 'CD_FINALIZED']
        ).count()

        dist_approved_list = list(base_regs.filter(
            status=RegistrationStatus.DISTRICT_APPROVED
        ).select_related('athlete', 'approved_by').prefetch_related('event_entries__event_config', 'event_entries__age_category').order_by('-approved_at', '-id')[:12])

        pending_athletes_list = list(base_regs.filter(
            status=RegistrationStatus.SUBMITTED
        ).select_related('athlete').prefetch_related('event_entries__event_config', 'event_entries__age_category').order_by('-created_at', '-id')[:12])
    else:
        base_regs = ChampionshipRegistration.objects.all()
        total_athletes = Athlete.objects.distinct().count()
        total_registrations = base_regs.count()
        total_entries = EventEntry.objects.count()
        dist_approved_athletes = Athlete.objects.filter(registrations__status__in=[RegistrationStatus.DISTRICT_APPROVED, RegistrationStatus.VENUE_CLEARED]).distinct().count()
        pending_athletes = Athlete.objects.filter(registrations__status=RegistrationStatus.SUBMITTED).distinct().count()
        awaiting_venue_clearance = Athlete.objects.filter(registrations__status=RegistrationStatus.DISTRICT_APPROVED, registrations__venue_cleared=False).distinct().count()
        venue_cleared = Athlete.objects.filter(Q(registrations__status=RegistrationStatus.VENUE_CLEARED) | Q(registrations__venue_cleared=True)).distinct().count()
        corrections_count = base_regs.filter(status=RegistrationStatus.DRAFT).count()
        rejected_count = base_regs.filter(status=RegistrationStatus.DISTRICT_REJECTED).count()
        active_panels = JudgePanel.objects.filter(is_active=True).count()
        scored_performances = Performance.objects.filter(status__in=['PROVISIONAL_RELEASED', 'CD_FINALIZED']).count()

        dist_approved_list = list(base_regs.filter(
            status=RegistrationStatus.DISTRICT_APPROVED
        ).select_related('athlete', 'approved_by').prefetch_related('event_entries__event_config', 'event_entries__age_category').order_by('-approved_at', '-id')[:12])

        pending_athletes_list = list(base_regs.filter(
            status=RegistrationStatus.SUBMITTED
        ).select_related('athlete').prefetch_related('event_entries__event_config', 'event_entries__age_category').order_by('-created_at', '-id')[:12])

    approved_judges = UniversalJudge.objects.filter(approval_status=ApprovalStatus.APPROVED).count()
    pending_judges = UniversalJudge.objects.filter(approval_status=ApprovalStatus.PENDING).count()
    
    from audit.models import AuditLog
    recent_logs = AuditLog.objects.select_related('user').order_by('-timestamp')[:8]

    return render(request, 'accounts/dashboard.html', {
        'championship': championship,
        'running_championships': running_championships,
        'recent_logs': recent_logs,
        'dist_approved_list': dist_approved_list,
        'pending_athletes_list': pending_athletes_list,
        'stats': {
            'total_athletes': total_athletes,
            'total_registrations': total_registrations,
            'total_entries': total_entries,
            'dist_approved_athletes': dist_approved_athletes,
            'pending_athletes': pending_athletes,
            'awaiting_venue_clearance': awaiting_venue_clearance,
            'venue_cleared': venue_cleared,
            'corrections_count': corrections_count,
            'rejected_count': rejected_count,
            'approved_judges': approved_judges,
            'active_panels': active_panels,
            'scored_performances': scored_performances,
            'pending_approvals': pending_athletes,
            'pending_judges': pending_judges,
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
    ]
    roles = [
        (RoleChoices.ADMIN, 'System Admin (Global)'),
        (RoleChoices.CD, 'Competition Director (CD)'),
        (RoleChoices.DISTRICT_USER, 'District User (Universal across Championships)'),
        (RoleChoices.VENUE_DESK, 'Venue Desk (Scoped)'),
    ]

    # Only show management staff (or superusers). Do not list pure judges in management screen.
    users = User.objects.filter(
        Q(is_superuser=True) | Q(scoped_roles__role__in=MANAGEMENT_ROLE_KEYS)
    ).distinct().prefetch_related('scoped_roles__championship').order_by('id')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add_user':
            staff_user_id = request.POST.get('staff_user_id', '').strip()
            email = request.POST.get('email', '').strip()
            full_name = request.POST.get('full_name', '').strip()
            role = request.POST.get('role')
            district = request.POST.get('district', '').strip()
            champ_scope_id = request.POST.get('championship_id')

            if role not in MANAGEMENT_ROLE_KEYS:
                messages.error(request, "Invalid role. Judge duties, Jury and Stage Managers cannot be assigned from Users & Permissions.")
                return redirect('users_and_permissions')

            target_champ = None
            if role in [RoleChoices.ADMIN, RoleChoices.DISTRICT_USER]:
                target_champ = None
            elif champ_scope_id:
                target_champ = Championship.objects.filter(id=champ_scope_id).first()
            else:
                target_champ = championship

            if staff_user_id:
                user = get_object_or_404(User, id=staff_user_id)
            else:
                if not email:
                    messages.error(request, "Email address is required for new staff member.")
                    return redirect('users_and_permissions')
                user, created = User.objects.get_or_create(
                    email=email,
                    defaults={'full_name': full_name, 'is_staff': True}
                )
                if created:
                    user.set_password('tysa2026')
                    user.save()
                elif full_name and not user.full_name:
                    user.full_name = full_name
                    user.save(update_fields=['full_name'])

            role_obj, _ = ScopedRole.objects.get_or_create(
                user=user,
                role=role,
                championship=target_champ,
                district=district
            )

            # Audit logging
            AuditLog.log(
                action='ROLE_ASSIGNED',
                resource_type='ScopedRole',
                resource_id=role_obj.id,
                details={
                    'email': user.email,
                    'role': role_obj.get_role_display(),
                    'championship': target_champ.name if target_champ else 'Global/Universal',
                    'district': district or 'All Districts'
                },
                user=request.user,
                ip_address=request.META.get('REMOTE_ADDR')
            )

            messages.success(request, f"Operational staff user {user.email} assigned role {role_obj.get_role_display()}.")
            return redirect('/accounts/users-and-permissions/?tab=assignments')

        elif action == 'revoke_role':
            role_id = request.POST.get('role_id')
            role_obj = get_object_or_404(ScopedRole, id=role_id)
            user_email = role_obj.user.email
            role_name = role_obj.get_role_display()
            
            AuditLog.log(
                action='ROLE_REVOKED',
                resource_type='ScopedRole',
                resource_id=role_obj.id,
                details={'email': user_email, 'role': role_name},
                user=request.user,
                ip_address=request.META.get('REMOTE_ADDR')
            )

            role_obj.delete()
            messages.info(request, f"Role {role_name} revoked from {user_email}.")
            return redirect('/accounts/users-and-permissions/?tab=assignments')

    tab = request.GET.get('tab', 'staff')
    all_scoped_roles = ScopedRole.objects.select_related('user', 'championship').all().order_by('-id')
    audit_logs = AuditLog.objects.select_related('user').all().order_by('-timestamp')[:100]
    all_staff_users = User.objects.all().order_by('full_name', 'email')
    all_championships = Championship.objects.all().order_by('-year')

    return render(request, 'accounts/users_and_permissions.html', {
        'users': users,
        'roles': roles,
        'tab': tab,
        'all_scoped_roles': all_scoped_roles,
        'audit_logs': audit_logs,
        'all_staff_users': all_staff_users,
        'championship': championship,
        'all_championships': all_championships,
        'districts': TELANGANA_DISTRICTS,
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

@role_required(RoleChoices.ADMIN, RoleChoices.CD)
def attendance_and_duties(request):
    """
    Dedicated Attendance & Duty Management Module:
    Manages operational duties, ceremony staffing, stage coordinators, volunteers, and officials.
    Provides attendance marking, duty completion tracking, service certificate eligibility,
    filters, search, pagination, and Excel export.
    """
    from .models import DutyAssignment, PersonType, DutyStatus
    from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

    championship = get_active_championship(request)
    if not championship:
        messages.error(request, "No active championship selected.")
        return redirect('admin_dashboard')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'create_assignment':
            person_name = request.POST.get('person_name', '').strip()
            person_type = request.POST.get('person_type', PersonType.VOLUNTEER)
            contact = request.POST.get('contact_number', '').strip() or request.POST.get('mobile', '').strip()
            duty_title = request.POST.get('duty_title', '').strip()
            stage = request.POST.get('stage_assigned', '').strip() or request.POST.get('assigned_stage', '').strip()
            ceremony = request.POST.get('ceremony_assigned', '').strip() or request.POST.get('assigned_ceremony', '').strip()
            supervisor = request.POST.get('supervisor_name', '').strip() or request.POST.get('supervisor', '').strip()
            duty_date = request.POST.get('duty_date')
            start_time = request.POST.get('start_time') or None
            end_time = request.POST.get('end_time') or None

            DutyAssignment.objects.create(
                championship=championship,
                person_name=person_name,
                person_type=person_type,
                mobile=contact,
                duty_title=duty_title,
                assigned_stage=stage,
                assigned_ceremony=ceremony,
                supervisor=supervisor,
                duty_date=duty_date or datetime.date.today(),
                start_time=start_time,
                end_time=end_time
            )
            messages.success(request, f"Duty assignment created for {person_name} ({duty_title}).")
            return redirect('attendance_and_duties')

        elif action == 'update_status':
            duty_id = request.POST.get('duty_id')
            duty = get_object_or_404(DutyAssignment, id=duty_id, championship=championship)
            att_status = request.POST.get('status') or request.POST.get('attendance_status')
            cert_ok = request.POST.get('is_certificate_eligible') == '1' or request.POST.get('service_certificate_eligible') == '1'

            if att_status in DutyStatus.values:
                duty.status = att_status
            duty.service_certificate_eligible = cert_ok
            duty.save()

            messages.success(request, f"Updated attendance and duty status for {duty.person_name}.")
            return redirect(request.get_full_path())

    q = request.GET.get('q', '').strip()
    type_filter = request.GET.get('person_type', '').strip()
    status_filter = request.GET.get('status', '').strip()
    cert_filter = request.GET.get('cert_eligible', '').strip()

    duties_qs = DutyAssignment.objects.filter(championship=championship).order_by('-duty_date', 'person_type', 'person_name')

    if q:
        duties_qs = duties_qs.filter(
            Q(person_name__icontains=q) |
            Q(mobile__icontains=q) |
            Q(duty_title__icontains=q) |
            Q(supervisor__icontains=q) |
            Q(assigned_stage__icontains=q) |
            Q(assigned_ceremony__icontains=q)
        )

    if type_filter:
        duties_qs = duties_qs.filter(person_type=type_filter)

    if status_filter:
        duties_qs = duties_qs.filter(status=status_filter)

    if cert_filter == 'yes':
        duties_qs = duties_qs.filter(service_certificate_eligible=True)
    elif cert_filter == 'no':
        duties_qs = duties_qs.filter(service_certificate_eligible=False)

    total_count = duties_qs.count()
    present_count = DutyAssignment.objects.filter(championship=championship, status__in=[DutyStatus.PRESENT, DutyStatus.COMPLETED]).count()
    completed_count = DutyAssignment.objects.filter(championship=championship, status=DutyStatus.COMPLETED).count()
    eligible_count = DutyAssignment.objects.filter(championship=championship, service_certificate_eligible=True).count()

    # Excel export
    if request.GET.get('export') == 'excel':
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from django.http import HttpResponse

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Attendance & Duties"

        ws.merge_cells('A1:J1')
        title_cell = ws['A1']
        title_cell.value = f"TELANGANA YOGASANA SPORTS ASSOCIATION — {championship.name.upper()} ATTENDANCE REGISTER"
        title_cell.font = Font(name='Arial', size=13, bold=True, color='FFFFFF')
        title_cell.fill = PatternFill(start_color='00838F', end_color='00838F', fill_type='solid')
        title_cell.alignment = Alignment(horizontal='center', vertical='center')
        ws.row_dimensions[1].height = 28

        headers = ['S.No', 'Person Name', 'Role Type', 'Contact', 'Duty Title', 'Stage / Ceremony', 'Supervisor', 'Attendance Status', 'Certificate Eligible']
        ws.append(headers)
        ws.row_dimensions[2].height = 22

        header_font = Font(name='Arial', size=10, bold=True, color='FFFFFF')
        header_fill = PatternFill(start_color='1A365D', end_color='1A365D', fill_type='solid')
        for col_idx in range(1, len(headers) + 1):
            c = ws.cell(row=2, column=col_idx)
            c.font = header_font
            c.fill = header_fill
            c.alignment = Alignment(horizontal='center', vertical='center')

        for idx, d in enumerate(duties_qs, start=1):
            row_data = [
                idx,
                d.person_name,
                d.get_person_type_display(),
                d.mobile or "—",
                d.duty_title,
                f"{d.assigned_stage or ''} {d.assigned_ceremony or ''}".strip() or "General",
                d.supervisor or "—",
                d.get_status_display(),
                "Eligible" if d.service_certificate_eligible else "Not Eligible"
            ]
            ws.append(row_data)

        from openpyxl.utils import get_column_letter
        for col_idx in range(1, len(headers) + 1):
            ws.column_dimensions[get_column_letter(col_idx)].width = 18

        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="Attendance_and_Duties_{championship.code}.xlsx"'
        wb.save(response)
        return response

    paginator = Paginator(duties_qs, 20)
    page_num = request.GET.get('page', 1)
    try:
        duties = paginator.page(page_num)
    except (EmptyPage, PageNotAnInteger):
        duties = paginator.page(1)

    return render(request, 'accounts/attendance_duties.html', {
        'championship': championship,
        'duties': duties,
        'total_count': total_count,
        'present_count': present_count,
        'completed_count': completed_count,
        'eligible_count': eligible_count,
        'q': q,
        'type_filter': type_filter,
        'status_filter': status_filter,
        'cert_filter': cert_filter,
        'person_types': PersonType.choices,
        'duty_statuses': DutyStatus.choices,
    })

@login_required
def no_role_assigned(request):
    """Clear fallback landing screen for users with no active role in the selected championship."""
    return render(request, 'accounts/no_role.html')
