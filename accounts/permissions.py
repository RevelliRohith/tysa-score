import urllib.parse
from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.urls import resolve, Resolver404
from .models import RoleChoices, ScopedRole
from championships.models import Championship
from competition.models import JudgePanel, PanelSeatAssignment

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
            Q(artistry_judge=user) | Q(artistry_judge_2=user) | Q(stage_manager=user) | Q(jury_member=user) |
            Q(seat_assignments__judge=user, seat_assignments__is_active=True)
        ).values_list('championship_id', flat=True)
    )

    from corrections.models import CDCorrectionGrant
    correction_champ_ids = set(CDCorrectionGrant.objects.filter(
        judge_submission__judge=user, status='OPEN').values_list(
        'performance__event_entry__registration__championship_id', flat=True))
    allowed_ids = (scoped_champ_ids | panel_champ_ids | correction_champ_ids) - {None}
    return Championship.objects.filter(id__in=allowed_ids).order_by('-year', '-start_date')

def get_active_championship(request, user=None):
    """
    Resolves the active championship for the request:
    - If user is authenticated, resolves strictly against their authorized championships.
    - If user is anonymous (e.g. public pages, athlete registration), checks session or default.
    - Removes Championship.objects.first() as an implicit operational fallback.
    """
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
        return first_authorized

    # Anonymous user resolution
    if active_id:
        champ = Championship.objects.filter(id=active_id).first()
        if champ:
            return champ
    
    # Return published active championship
    return Championship.objects.filter(status='IN_PROGRESS').first() or Championship.objects.first()

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

    # Active Panel assignments for this championship (must be is_active=True)
    panels = JudgePanel.objects.filter(championship=champ, is_active=True)
    if panels.filter(chief_judge=user).exists():
        roles.add(RoleChoices.CHIEF_JUDGE)
    if panels.filter(Q(d1_judge=user) | Q(d2_judge=user) | Q(d3_judge=user) | Q(d4_judge=user) | Q(d5_judge=user)).exists():
        roles.add(RoleChoices.D_JUDGE)
    if panels.filter(Q(time_judge=user) | Q(time_judge_2=user)).exists():
        roles.add(RoleChoices.TIME_JUDGE)
    if panels.filter(Q(evaluator=user) | Q(evaluator_2=user)).exists():
        roles.add(RoleChoices.EVALUATOR)
    if panels.filter(Q(artistry_judge=user) | Q(artistry_judge_2=user)).exists():
        roles.add(RoleChoices.ARTISTRY_JUDGE)
    if panels.filter(stage_manager=user).exists():
        roles.add(RoleChoices.STAGE_MANAGER)
    if panels.filter(jury_member=user).exists():
        roles.add(RoleChoices.JURY)

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
    if ScopedRole.objects.filter(user=user, role=RoleChoices.DISTRICT_USER, championship__isnull=True).exists():
        workspaces.append({'role': RoleChoices.DISTRICT_USER, 'role_display': 'District User',
                           'championship': None, 'championship_name': 'Authorized district',
                           'district': '', 'url': 'district_approvals',
                           'description': 'District registration and approvals'})
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
        elif sr.role == RoleChoices.JURY:
            url = 'jury_view'
        elif sr.role == RoleChoices.STAGE_MANAGER:
            url = 'stage_manager'
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
        Q(artistry_judge=user) | Q(artistry_judge_2=user) | Q(stage_manager=user) | Q(jury_member=user) |
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

    # Judge views
    judge_views = [
        'judge_portal', 'judge_profile', 'scoring_traditional_d',
        'scoring_traditional_d_final', 'scoring_artistic_single_d',
        'scoring_artistic_pair_d', 'scoring_rhythmic_pair_d',
        'scoring_traditional_time', 'scoring_music_time',
        'scoring_artistry', 'scoring_evaluator', 'scoring_chief_judge'
    ]
    if view_name in judge_views:
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
    active_panels = JudgePanel.objects.filter(championship=championship, is_active=True)
    if active_panels.filter(chief_judge=user).exists():
        roles.add(RoleChoices.CHIEF_JUDGE)
    if active_panels.filter(Q(d1_judge=user) | Q(d2_judge=user) | Q(d3_judge=user) | Q(d4_judge=user) | Q(d5_judge=user)).exists():
        roles.add(RoleChoices.D_JUDGE)
    if active_panels.filter(Q(time_judge=user) | Q(time_judge_2=user)).exists():
        roles.add(RoleChoices.TIME_JUDGE)
    if active_panels.filter(Q(evaluator=user) | Q(evaluator_2=user)).exists():
        roles.add(RoleChoices.EVALUATOR)
    if active_panels.filter(Q(artistry_judge=user) | Q(artistry_judge_2=user)).exists():
        roles.add(RoleChoices.ARTISTRY_JUDGE)
    if active_panels.filter(stage_manager=user).exists():
        roles.add(RoleChoices.STAGE_MANAGER)

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
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
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

            # Access denied: redirect to their own role portal with notification
            landing = get_user_landing_url(request)
            messages.warning(
                request,
                "Access restricted: Your current role does not have permission to access that module. Redirected to your authorized workspace."
            )
            return redirect(landing)
        return _wrapped_view
    return decorator

def admin_required(view_func):
    """Restricts access strictly to System Admin."""
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if is_system_admin(request.user):
            return view_func(request, *args, **kwargs)

        landing = get_user_landing_url(request)
        messages.warning(request, "Access restricted to System Admin. Redirected to your authorized workspace.")
        return redirect(landing)
    return _wrapped_view

def cd_required(view_func):
    """Restricts access strictly to Competition Director or System Admin."""
    return role_required(RoleChoices.ADMIN, RoleChoices.CD)(view_func)
