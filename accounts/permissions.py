import urllib.parse
from functools import wraps
from django.shortcuts import redirect, render
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.urls import resolve, Resolver404
from .models import RoleChoices, ScopedRole
from championships.models import Championship
from competition.models import JudgePanel, PanelSeatAssignment
from audit.models import AuditLog

def is_system_admin(user):
    """Returns True if the user is superuser or holds the global ADMIN ScopedRole."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.scoped_roles.filter(role=RoleChoices.ADMIN).exists()

def get_authorized_championships(user):
    """
    Returns QuerySet of championships the user is authorized to access:
    - System Admin has global access to all championships.
    - Other staff/judges ONLY have access to championships where they hold
      an explicit active ScopedRole or confirmed JudgePanel assignment.
    - Operational roles without a championship MUST NOT become global.
    """
    if not user or not user.is_authenticated:
        return Championship.objects.none()

    if is_system_admin(user):
        return Championship.objects.all().order_by('-year', '-start_date')

    # Universal District Users have access across all championships for their district
    if ScopedRole.objects.filter(user=user, role=RoleChoices.DISTRICT_USER, championship__isnull=True).exists():
        return Championship.objects.all().order_by('-year', '-start_date')

    # Find championships where user has an explicit ScopedRole
    scoped_champ_ids = set(
        ScopedRole.objects.filter(user=user, championship__isnull=False)
        .values_list('championship_id', flat=True)
    )

    # Find championships where user is assigned on an ACTIVE JudgePanel or ACTIVE PanelSeatAssignment
    panel_champ_ids = set(
        JudgePanel.objects.filter(is_active=True).filter(
            Q(chief_judge=user) | Q(d1_judge=user) | Q(d2_judge=user) |
            Q(d3_judge=user) | Q(d4_judge=user) | Q(d5_judge=user) | Q(time_judge=user) |
            Q(time_judge_2=user) | Q(evaluator=user) | Q(evaluator_2=user) |
            Q(artistry_judge=user) | Q(artistry_judge_2=user) | Q(stage_manager=user) |
            Q(seat_assignments__judge=user, seat_assignments__is_active=True)
        ).values_list('championship_id', flat=True)
    )

    allowed_ids = (scoped_champ_ids | panel_champ_ids) - {None}
    return Championship.objects.filter(id__in=allowed_ids).order_by('-year', '-start_date')

def get_active_championship(request, user=None):
    """
    Resolves the active championship for the request:
    - If user is authenticated, resolves strictly against their authorized championships.
    - If user is anonymous (e.g. public pages, athlete registration), checks session or default.
    - Removes Championship.objects.first() as an implicit operational fallback.
    """
    if request is not None and user is None and hasattr(request, '_cached_active_championship'):
        return request._cached_active_championship

    effective_user = user or (request.user if hasattr(request, 'user') and request.user.is_authenticated else None)

    active_id = None
    if hasattr(request, 'session'):
        active_id = request.session.get('active_championship_id')

    if effective_user:
        authorized_champs = get_authorized_championships(effective_user)
        if active_id and authorized_champs.filter(id=active_id).exists():
            return authorized_champs.filter(id=active_id).first()
        
        # Default to first authorized championship
        first_authorized = authorized_champs.first()
        if first_authorized and hasattr(request, 'session'):
            request.session['active_championship_id'] = first_authorized.id
        if request is not None and user is None:
            request._cached_active_championship = first_authorized
        return first_authorized

    # Anonymous user resolution
    if active_id:
        champ = Championship.objects.filter(id=active_id).first()
        if champ:
            return champ
    
    # Return published active championship
    return Championship.objects.filter(status='IN_PROGRESS').first() or Championship.objects.first()

def get_user_assigned_seats(user, championship=None, is_active_only=True):
    """
    Returns list of seat assignment dictionaries for user:
    [{'panel': JudgePanel, 'seat_code': str, 'seat_label': str, 'role_group': str}, ...]
    Accurately supports judges serving on multiple panels or different seats across events.
    """
    if not user or not user.is_authenticated:
        return []
    
    qs = JudgePanel.objects.all().select_related('event_config', 'age_category', 'championship')
    if is_active_only:
        qs = qs.filter(is_active=True)
    if championship:
        qs = qs.filter(championship=championship)

    seats = []
    uid = user.id
    for p in qs:
        if p.chief_judge_id == uid:
            seats.append({'panel': p, 'seat_code': 'CHIEF_JUDGE', 'seat_label': 'Chief Judge', 'role_group': 'CHIEF_JUDGE'})
        if p.stage_manager_id == uid:
            seats.append({'panel': p, 'seat_code': 'STAGE_MANAGER', 'seat_label': 'Stage Manager', 'role_group': 'STAGE_MANAGER'})
        if p.d1_judge_id == uid:
            seats.append({'panel': p, 'seat_code': 'D1_JUDGE', 'seat_label': 'D1 Judge', 'role_group': 'D_JUDGE'})
        if p.d2_judge_id == uid:
            seats.append({'panel': p, 'seat_code': 'D2_JUDGE', 'seat_label': 'D2 Judge', 'role_group': 'D_JUDGE'})
        if p.d3_judge_id == uid:
            seats.append({'panel': p, 'seat_code': 'D3_JUDGE', 'seat_label': 'D3 Judge', 'role_group': 'D_JUDGE'})
        if p.d4_judge_id == uid:
            seats.append({'panel': p, 'seat_code': 'D4_JUDGE', 'seat_label': 'D4 Judge', 'role_group': 'D_JUDGE'})
        if p.d5_judge_id == uid:
            seats.append({'panel': p, 'seat_code': 'D5_JUDGE', 'seat_label': 'D5 Judge', 'role_group': 'D_JUDGE'})
        if p.time_judge_id == uid:
            seats.append({'panel': p, 'seat_code': 'TIME_JUDGE', 'seat_label': 'Time Judge 1', 'role_group': 'TIME_JUDGE'})
        if p.time_judge_2_id == uid:
            seats.append({'panel': p, 'seat_code': 'TIME_JUDGE_2', 'seat_label': 'Time Judge 2', 'role_group': 'TIME_JUDGE'})
        if p.artistry_judge_id == uid:
            seats.append({'panel': p, 'seat_code': 'ARTISTRY_JUDGE', 'seat_label': 'Artistry Judge 1', 'role_group': 'ARTISTRY_JUDGE'})
        if p.artistry_judge_2_id == uid:
            seats.append({'panel': p, 'seat_code': 'ARTISTRY_JUDGE_2', 'seat_label': 'Artistry Judge 2', 'role_group': 'ARTISTRY_JUDGE'})
        if p.evaluator_id == uid:
            seats.append({'panel': p, 'seat_code': 'EVALUATOR', 'seat_label': 'Evaluator 1', 'role_group': 'EVALUATOR'})
        if p.evaluator_2_id == uid:
            seats.append({'panel': p, 'seat_code': 'EVALUATOR_2', 'seat_label': 'Evaluator 2', 'role_group': 'EVALUATOR'})
        if p.jury_member_id == uid:
            seats.append({'panel': p, 'seat_code': 'JURY', 'seat_label': 'Jury Member', 'role_group': 'JURY'})

    return seats


def get_user_roles_for_request(request, championship=None):
    """
    Returns the set of role strings for the logged-in user within the given championship context.
    - System Admin has ADMIN everywhere.
    - Other roles are evaluated strictly within the active championship.
    """
    if not hasattr(request, 'user') or not request.user.is_authenticated:
        return set()

    user = request.user
    if is_system_admin(user):
        return {RoleChoices.ADMIN}

    champ = championship or get_active_championship(request)
    if not champ:
        # Check if user has a universal judge profile
        if hasattr(user, 'judge_profile'):
            if user.judge_profile.is_approved:
                return {'APPROVED_JUDGE'}
            return {'PENDING_JUDGE'}
        return set()

    roles = set()
    # Explicit ScopedRole for this championship (or universal district user across all championships)
    scoped_roles = ScopedRole.objects.filter(user=user).filter(
        Q(championship=champ) | Q(championship__isnull=True)
    ).values_list('role', flat=True)
    roles.update(scoped_roles)

    # All active panel seat assignments for this user in this championship
    assigned_seats = get_user_assigned_seats(user, championship=champ, is_active_only=True)
    for seat in assigned_seats:
        rg = seat['role_group']
        if rg == 'CHIEF_JUDGE': roles.add(RoleChoices.CHIEF_JUDGE)
        elif rg == 'STAGE_MANAGER': roles.add(RoleChoices.STAGE_MANAGER)
        elif rg == 'D_JUDGE': roles.add(RoleChoices.D_JUDGE)
        elif rg == 'TIME_JUDGE': roles.add(RoleChoices.TIME_JUDGE)
        elif rg == 'ARTISTRY_JUDGE': roles.add(RoleChoices.ARTISTRY_JUDGE)
        elif rg == 'EVALUATOR': roles.add(RoleChoices.EVALUATOR)
        elif rg == 'JURY': roles.add(RoleChoices.JURY)

    # Active PanelSeatAssignment check (handles seat assignment only users)
    from competition.models import PanelSeatAssignment
    seat_assignments = PanelSeatAssignment.objects.filter(
        panel__championship=champ,
        panel__is_active=True,
        judge=user,
        is_active=True
    ).select_related('panel')
    for sa in seat_assignments:
        s_code = sa.seat_name
        if any(d in s_code for d in ['D1', 'D2', 'D3', 'D4', 'D5', 'D_JUDGE']):
            roles.add(RoleChoices.D_JUDGE)
        elif 'TIME' in s_code:
            roles.add(RoleChoices.TIME_JUDGE)
        elif 'ARTISTRY' in s_code:
            roles.add(RoleChoices.ARTISTRY_JUDGE)
        elif 'EVALUATOR' in s_code:
            roles.add(RoleChoices.EVALUATOR)
        elif 'CHIEF' in s_code:
            roles.add(RoleChoices.CHIEF_JUDGE)
        elif 'STAGE' in s_code:
            roles.add(RoleChoices.STAGE_MANAGER)

    # Judge profile status
    if hasattr(user, 'judge_profile'):
        if user.judge_profile.is_approved:
            roles.add('APPROVED_JUDGE')
        else:
            roles.add('PENDING_JUDGE')

    return roles

def get_user_workspaces(user):
    """
    Enumerates all distinct authorized workspaces across championships for a user.
    Used for routing and multi-role selection.
    """
    if not user or not user.is_authenticated:
        return []

    if is_system_admin(user):
        return [{
            'role': RoleChoices.ADMIN,
            'role_display': 'System Admin',
            'championship': None,
            'championship_name': 'All Championships (Global)',
            'district': '',
            'url': 'admin_dashboard',
            'description': 'Full System Access & All Modules'
        }]

    workspaces = []
    # Scoped roles
    scoped = ScopedRole.objects.filter(user=user, championship__isnull=False).select_related('championship')
    for sr in scoped:
        url = 'no_role_assigned'
        if sr.role == RoleChoices.CD:
            url = 'cd_dashboard'
        elif sr.role == RoleChoices.DISTRICT_USER:
            url = 'district_approvals'
        elif sr.role == RoleChoices.VENUE_DESK:
            url = 'venue_verification'
        elif sr.role == RoleChoices.STAGE_MANAGER:
            url = 'stage_manager'
        elif sr.role == RoleChoices.JURY:
            url = 'jury_view'
        elif sr.role in [RoleChoices.D_JUDGE, RoleChoices.TIME_JUDGE, RoleChoices.ARTISTRY_JUDGE, RoleChoices.EVALUATOR, RoleChoices.CHIEF_JUDGE]:
            url = 'judge_portal'

        workspaces.append({
            'role': sr.role,
            'role_display': sr.get_role_display(),
            'championship': sr.championship,
            'championship_name': sr.championship.name,
            'district': sr.district,
            'url': url,
            'description': f"{sr.get_role_display()} for {sr.championship.name}" + (f" ({sr.district})" if sr.district else "")
        })

    # Panel check: direct active assignments OR active PanelSeatAssignment
    from competition.models import PanelSeatAssignment
    active_panels = JudgePanel.objects.filter(is_active=True).filter(
        Q(chief_judge=user) | Q(d1_judge=user) | Q(d2_judge=user) |
        Q(d3_judge=user) | Q(d4_judge=user) | Q(d5_judge=user) | Q(time_judge=user) |
        Q(time_judge_2=user) | Q(evaluator=user) | Q(evaluator_2=user) |
        Q(artistry_judge=user) | Q(artistry_judge_2=user) | Q(stage_manager=user) |
        Q(seat_assignments__judge=user, seat_assignments__is_active=True)
    ).select_related('championship').distinct()
    
    # If active panels exist, ensure each championship has an entry
    champ_ids_in_workspaces = {w['championship'].id for w in workspaces if w['championship']}
    for p in active_panels:
        if p.championship_id not in champ_ids_in_workspaces:
            workspaces.append({
                'role': 'JUDGE',
                'role_display': 'Official Judge',
                'championship': p.championship,
                'championship_name': p.championship.name,
                'district': getattr(getattr(user, 'judge_profile', None), 'district', ''),
                'url': 'judge_portal',
                'description': f"Assigned Judge on {p.stage_name} ({p.championship.name})"
            })
            champ_ids_in_workspaces.add(p.championship_id)

    # If user has judge profile or is an accredited judge without specific workspace yet
    if hasattr(user, 'judge_profile') and not workspaces:
        judge = user.judge_profile
        workspaces.append({
            'role': 'PENDING_JUDGE' if not judge.is_approved else 'APPROVED_JUDGE',
            'role_display': 'Universal Judge Application' if not judge.is_approved else 'Approved Universal Judge',
            'championship': None,
            'championship_name': 'Pending Panel Assignment',
            'district': judge.district,
            'url': 'judge_portal',
            'description': 'Universal Judge Profile & Panel Assignments'
        })

    return workspaces

def get_user_landing_url(request):
    """
    Determines the correct landing URL for an authenticated user:
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
    """
    user = request.user
    if not user.is_authenticated:
        return 'login'

    if is_system_admin(user):
        return 'admin_dashboard'

    workspaces = get_user_workspaces(user)
    if not workspaces:
        return 'no_role_assigned'

    # If user has multiple distinct roles/championships, route to role selection
    if len(workspaces) > 1:
        return 'role_select'

    # Single workspace: activate championship in session and route directly
    ws = workspaces[0]
    if ws['championship'] and hasattr(request, 'session'):
        request.session['active_championship_id'] = ws['championship'].id

    return ws['url']

def is_safe_redirect_url(url, host):
    """Verifies that a redirect target is an internal relative URL and safe."""
    if not url:
        return False
    if url.startswith('//') or url.startswith('http://') or url.startswith('https://'):
        return False
    return url.startswith('/')

def is_user_authorized_for_url(user, url_path, championship):
    """
    Checks whether the user is authorized to access the given internal path
    under the given championship context.
    """
    if not user.is_authenticated:
        return False
    if is_system_admin(user):
        return True

    try:
        match = resolve(url_path.split('?')[0])
    except Resolver404:
        return False

    view_name = match.url_name
    roles = get_user_roles_for_championship(user, championship)

    # Admin only views
    admin_views = [
        'admin_dashboard', 'users_and_permissions', 'championship_create',
        'championship_setup', 'judge_approval_queue', 'certificate_templates',
        'certificate_template_edit', 'report_complete'
    ]
    if view_name in admin_views:
        return False

    # CD views
    cd_views = [
        'cd_dashboard', 'district_draw', 'panel_assignments',
        'cd_corrections', 'report_trophies', 'registers_view'
    ]
    if view_name in cd_views:
        return RoleChoices.CD in roles

    # District views
    if view_name == 'district_approvals':
        return RoleChoices.DISTRICT_USER in roles

    # Venue views
    if view_name == 'venue_verification':
        return RoleChoices.VENUE_DESK in roles

    # Stage views
    if view_name in ['stage_manager', 'stage_manager_alt', 'second_screen']:
        return RoleChoices.STAGE_MANAGER in roles or RoleChoices.CD in roles

    # Jury views
    if view_name in ['jury_view', 'scoreboard_view']:
        return RoleChoices.JURY in roles or RoleChoices.CD in roles

    # Strict Desk Isolation for Scoring Views
    # D Judge desks
    if view_name in ['scoring_traditional_d', 'scoring_traditional_d_final', 'scoring_artistic_single_d', 'scoring_artistic_pair_d', 'scoring_rhythmic_pair_d']:
        return RoleChoices.D_JUDGE in roles or RoleChoices.CD in roles

    # Time Judge desks
    if view_name in ['scoring_traditional_time', 'scoring_music_time']:
        return RoleChoices.TIME_JUDGE in roles or RoleChoices.CD in roles

    # Artistry Judge desk
    if view_name == 'scoring_artistry':
        return RoleChoices.ARTISTRY_JUDGE in roles or RoleChoices.CD in roles

    # Evaluator desk
    if view_name == 'scoring_evaluator':
        return RoleChoices.EVALUATOR in roles or RoleChoices.CD in roles

    # Chief Judge desk
    if view_name == 'scoring_chief_judge':
        return RoleChoices.CHIEF_JUDGE in roles or RoleChoices.CD in roles

    # Judge portal & general profile
    if view_name in ['judge_portal', 'judge_profile', 'judge_my_scores']:
        return any(r in roles for r in [
            RoleChoices.D_JUDGE, RoleChoices.TIME_JUDGE, RoleChoices.ARTISTRY_JUDGE,
            RoleChoices.EVALUATOR, RoleChoices.CHIEF_JUDGE, 'APPROVED_JUDGE'
        ]) or hasattr(user, 'judge_profile')

    return True

def get_user_roles_for_championship(user, championship):
    """Helper returning set of role strings for user in given championship."""
    if is_system_admin(user):
        return {RoleChoices.ADMIN}
    if not championship:
        if hasattr(user, 'judge_profile'):
            return {'APPROVED_JUDGE'} if user.judge_profile.is_approved else {'PENDING_JUDGE'}
        return set()

    roles = set(ScopedRole.objects.filter(user=user, championship=championship).values_list('role', flat=True))
    assigned_seats = get_user_assigned_seats(user, championship=championship, is_active_only=True)
    for seat in assigned_seats:
        rg = seat['role_group']
        if rg == 'CHIEF_JUDGE': roles.add(RoleChoices.CHIEF_JUDGE)
        elif rg == 'STAGE_MANAGER': roles.add(RoleChoices.STAGE_MANAGER)
        elif rg == 'D_JUDGE': roles.add(RoleChoices.D_JUDGE)
        elif rg == 'TIME_JUDGE': roles.add(RoleChoices.TIME_JUDGE)
        elif rg == 'ARTISTRY_JUDGE': roles.add(RoleChoices.ARTISTRY_JUDGE)
        elif rg == 'EVALUATOR': roles.add(RoleChoices.EVALUATOR)
        elif rg == 'JURY': roles.add(RoleChoices.JURY)

    # Active PanelSeatAssignment check
    from competition.models import PanelSeatAssignment
    seat_assignments = PanelSeatAssignment.objects.filter(
        panel__championship=championship,
        panel__is_active=True,
        judge=user,
        is_active=True
    ).select_related('panel')
    for sa in seat_assignments:
        s_code = sa.seat_name
        if any(d in s_code for d in ['D1', 'D2', 'D3', 'D4', 'D5', 'D_JUDGE']):
            roles.add(RoleChoices.D_JUDGE)
        elif 'TIME' in s_code:
            roles.add(RoleChoices.TIME_JUDGE)
        elif 'ARTISTRY' in s_code:
            roles.add(RoleChoices.ARTISTRY_JUDGE)
        elif 'EVALUATOR' in s_code:
            roles.add(RoleChoices.EVALUATOR)
        elif 'CHIEF' in s_code:
            roles.add(RoleChoices.CHIEF_JUDGE)
        elif 'STAGE' in s_code:
            roles.add(RoleChoices.STAGE_MANAGER)

    if hasattr(user, 'judge_profile'):
        roles.add('APPROVED_JUDGE' if user.judge_profile.is_approved else 'PENDING_JUDGE')

    return roles

def user_can_grant_correction(user, championship):
    """
    Requirement 6: Submitted-score unlock authority:
    - ONLY a valid assigned CD role may unlock.
    - System Admin can inspect all modules but cannot silently bypass the CD requirement.
    - An Admin who also holds the assigned CD role may act through that audited assignment.
    """
    if not user or not user.is_authenticated or not championship:
        return False
    return ScopedRole.objects.filter(user=user, championship=championship, role=RoleChoices.CD).exists()

def get_role_navigation_items(user, championship, current_url_name=''):
    """
    Central permission-aware menu definition:
    Ensures visible top-level and secondary navigation stays 100% consistent
    with server-side permissions for every role.
    """
    if not user or not user.is_authenticated:
        return []

    roles = get_user_roles_for_championship(user, championship)

    # 1. JUDGES (D, Time, Artistry, Evaluator, Chief, Stage Manager):
    # Stage Manager is also a Judge and assigned per event panel
    is_judge = not is_system_admin(user) and not any(
        r in roles for r in [RoleChoices.CD, RoleChoices.DISTRICT_USER, RoleChoices.VENUE_DESK, RoleChoices.JURY]
    ) and (
        any(r in roles for r in [
            RoleChoices.D_JUDGE, RoleChoices.TIME_JUDGE, RoleChoices.ARTISTRY_JUDGE,
            RoleChoices.EVALUATOR, RoleChoices.CHIEF_JUDGE, RoleChoices.STAGE_MANAGER
        ]) or hasattr(user, 'judge_profile')
    )

    if is_judge:
        items = [
            {'label': 'Profile', 'url': '/judges/portal/#profile', 'url_name': 'judge_portal', 'icon': 'user'},
            {'label': 'Assigned Panels', 'url': '/judges/portal/#panels', 'url_name': 'judge_portal', 'icon': 'layout-grid'},
        ]
        # If this judge is actively assigned as Stage Manager for an open event panel:
        if RoleChoices.STAGE_MANAGER in roles:
            items.append({'label': 'Stage Control', 'url': '/competition/stage/', 'url_name': 'stage_manager', 'icon': 'sliders'})
            items.append({'label': '2nd Screen Display', 'url': '/display/second-screen/', 'url_name': 'second_screen', 'icon': 'monitor'})
        return items

    # 2. SYSTEM ADMIN
    if is_system_admin(user):
        return [
            {'label': 'Dashboard', 'url': '/dashboard/', 'url_name': 'admin_dashboard', 'icon': 'home'},
            {'label': 'Championships', 'url': '/championships/', 'url_name': 'championship_list', 'icon': 'trophy'},
            {'label': 'Users & Roles', 'url': '/accounts/users/', 'url_name': 'users_and_permissions', 'icon': 'users'},
            {'label': 'Asana Library', 'url': '/catalogue/', 'url_name': 'catalogue_view', 'icon': 'book-open'},
            {'label': 'Judge Approvals', 'url': '/judges/approval-queue/', 'url_name': 'judge_approval_queue', 'icon': 'user-check'},
            {'label': 'Registers', 'url': '/registers/', 'url_name': 'registers_view', 'icon': 'clipboard-list'},
            {'label': 'Draw & Roll', 'url': '/competition/draw/', 'url_name': 'district_draw', 'icon': 'shuffle'},
            {'label': 'Panel Setup', 'url': '/competition/panels/', 'url_name': 'panel_assignments', 'icon': 'users-round'},
            {'label': 'Stage Manager', 'url': '/competition/stage/', 'url_name': 'stage_manager', 'icon': 'sliders'},
            {'label': 'Corrections', 'url': '/cd/corrections/', 'url_name': 'cd_corrections', 'icon': 'file-edit'},
            {'label': 'Scoreboard', 'url': '/results/scoreboard/', 'url_name': 'scoreboard_view', 'icon': 'bar-chart-2'},
            {'label': 'Certificates', 'url': '/certificates/templates/', 'url_name': 'certificate_templates', 'icon': 'award'},
            {'label': 'Reports', 'url': '/reports/statistics/', 'url_name': 'report_statistics', 'icon': 'file-text'},
        ]

    # 3. COMPETITION DIRECTOR
    if RoleChoices.CD in roles:
        return [
            {'label': 'CD Dashboard', 'url': '/cd/dashboard/', 'url_name': 'cd_dashboard', 'icon': 'home'},
            {'label': 'Draw & Master Roll', 'url': '/competition/draw/', 'url_name': 'district_draw', 'icon': 'shuffle'},
            {'label': 'Panel Assignments', 'url': '/competition/panels/', 'url_name': 'panel_assignments', 'icon': 'users-round'},
            {'label': 'Judge Credentials Desk', 'url': '/judges/credentials/', 'url_name': 'judge_credentials_admin', 'icon': 'key'},
            {'label': 'Stage Progress', 'url': '/competition/stage/', 'url_name': 'stage_manager', 'icon': 'sliders'},
            {'label': 'Correction Access', 'url': '/cd/corrections/', 'url_name': 'cd_corrections', 'icon': 'file-edit'},
            {'label': 'Trophies & Standings', 'url': '/reports/trophies/', 'url_name': 'report_trophies', 'icon': 'trophy'},
            {'label': 'Complete Report', 'url': '/reports/championship/', 'url_name': 'report_complete', 'icon': 'file-text'},
        ]

    # 4. DISTRICT USER
    if RoleChoices.DISTRICT_USER in roles:
        return [
            {'label': 'District Dashboard', 'url': '/district/approvals/', 'url_name': 'district_approvals', 'icon': 'building-2'},
            {'label': 'Applications Queue', 'url': '/district/approvals/?tab=queue', 'url_name': 'district_approvals', 'icon': 'inbox'},
            {'label': 'District Approved', 'url': '/district/approvals/?tab=approved', 'url_name': 'district_approvals', 'icon': 'user-check'},
            {'label': 'Venue Clearance', 'url': '/district/approvals/?tab=venue_clearance', 'url_name': 'district_approvals', 'icon': 'shield-check'},
            {'label': 'Master Register', 'url': '/district/approvals/?tab=consolidated', 'url_name': 'district_approvals', 'icon': 'clipboard-list'},
        ]

    # 5. VENUE DESK
    if RoleChoices.VENUE_DESK in roles:
        return [
            {'label': 'Venue Verification', 'url': '/venue/verification/', 'url_name': 'venue_verification', 'icon': 'shield-check'},
            {'label': 'Attendance Checks', 'url': '/venue/verification/#search', 'url_name': 'venue_verification', 'icon': 'search'},
        ]

    # 6. JURY
    if RoleChoices.JURY in roles:
        return [
            {'label': 'Jury Review', 'url': '/jury/', 'url_name': 'jury_view', 'icon': 'scale'},
            {'label': 'Event Results', 'url': '/results/scoreboard/', 'url_name': 'scoreboard_view', 'icon': 'bar-chart-2'},
        ]

    return []

# Decorators
def role_required(*allowed_roles):
    """
    Decorator that checks whether the user has at least one of the allowed roles
    within the active championship context.
    System Admin bypasses unless explicitly checked.
    Logs unauthorized attempts and renders a clean 403 Forbidden page or JSON 403.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.path.startswith('/api/'):
                    from django.http import JsonResponse
                    return JsonResponse({'error': 'Authentication required'}, status=401)
                return redirect('login')

            user = request.user
            if is_system_admin(user):
                return view_func(request, *args, **kwargs)

            champ = get_active_championship(request)
            if not champ:
                # User has no authorized championship
                messages.error(request, "You do not have an active championship assignment.")
                return redirect('no_role_assigned')

            user_roles = get_user_roles_for_request(request, championship=champ)
            matched = any(r in user_roles for r in allowed_roles)
            if matched:
                return view_func(request, *args, **kwargs)

            # Access Denied: Log to AuditLog for security tracking
            try:
                AuditLog.log(
                    action='SECURITY_UNAUTHORIZED_ACCESS',
                    resource_type='View',
                    resource_id=request.path,
                    details={
                        'path': request.path,
                        'required_roles': list(allowed_roles),
                        'user_roles': list(user_roles),
                        'user_id': user.id,
                        'email': user.email,
                    },
                    user=user,
                    ip_address=request.META.get('REMOTE_ADDR')
                )
            except Exception:
                pass

            if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.path.startswith('/api/'):
                from django.http import JsonResponse
                return JsonResponse({
                    'error': 'Access Denied: Your authenticated account does not have permission for this desk or action.',
                    'required_roles': list(allowed_roles)
                }, status=403)

            return render(request, '403.html', {
                'message': f"Access restricted: Your current role(s) ({', '.join(user_roles) or 'None'}) do not have permission to access {request.path}.",
                'current_role': ', '.join(user_roles) if user_roles else 'No Role',
                'active_championship': champ,
            }, status=403)
        return _wrapped_view
    return decorator

def admin_required(view_func):
    """Restricts access strictly to System Admin."""
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.path.startswith('/api/'):
                from django.http import JsonResponse
                return JsonResponse({'error': 'Authentication required'}, status=401)
            return redirect('login')
        if is_system_admin(request.user):
            return view_func(request, *args, **kwargs)

        try:
            AuditLog.log(
                action='SECURITY_UNAUTHORIZED_ACCESS',
                resource_type='View',
                resource_id=request.path,
                details={'path': request.path, 'required_roles': ['ADMIN'], 'user_id': request.user.id},
                user=request.user,
                ip_address=request.META.get('REMOTE_ADDR')
            )
        except Exception:
            pass

        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.path.startswith('/api/'):
            from django.http import JsonResponse
            return JsonResponse({'error': 'Access Denied: System Admin required.'}, status=403)

        return render(request, '403.html', {
            'message': "Access restricted to System Admin. Your account does not have administrator privileges.",
            'current_role': 'Non-Admin',
        }, status=403)
    return _wrapped_view

def cd_required(view_func):
    """Restricts access strictly to Competition Director or System Admin."""
    return role_required(RoleChoices.ADMIN, RoleChoices.CD)(view_func)
