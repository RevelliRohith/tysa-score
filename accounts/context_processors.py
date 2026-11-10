from .models import RoleChoices, ScopedRole
from .permissions import (
    is_system_admin, get_authorized_championships, get_active_championship,
    get_user_roles_for_request, get_role_navigation_items, get_user_workspaces
)
from championships.models import Championship
from athletes.models import Athlete

def user_roles(request):
    """
    Context processor providing active role status, assigned district,
    multi-role workspaces, user initials, and role-specific navigation scoped strictly to the active championship.
    """
    # 1. Athlete key session check (when staff user is not logged in)
    athlete_id = request.session.get('athlete_id') if hasattr(request, 'session') else None
    athlete_obj = None
    is_athlete_session = False
    if athlete_id and (not hasattr(request, 'user') or not request.user.is_authenticated):
        athlete_obj = Athlete.objects.filter(id=athlete_id).first()
        if athlete_obj:
            is_athlete_session = True

    if not hasattr(request, 'user') or not request.user.is_authenticated:
        if is_athlete_session:
            initials = athlete_obj.full_name[:2].upper() if athlete_obj.full_name else 'AT'
            return {
                'user_roles': ['ATHLETE'],
                'is_admin': False,
                'is_cd': False,
                'is_stage': False,
                'is_jury': False,
                'is_district': False,
                'is_venue': False,
                'is_judge': False,
                'is_judge_only': False,
                'is_chief_judge': False,
                'is_athlete_session': True,
                'athlete_obj': athlete_obj,
                'user_district': athlete_obj.district,
                'active_role': 'Registered Athlete',
                'active_role_code': 'ATHLETE',
                'user_initials': initials,
                'user_display_name': athlete_obj.full_name,
                'nav_items': [],
                'workspaces': [],
                'has_multiple_roles': False,
                'show_sidebar': True,
                'is_scoring_desk': False,
                'current_url_name': getattr(getattr(request, 'resolver_match', None), 'url_name', ''),
            }
        return {
            'user_roles': [],
            'is_admin': False,
            'is_cd': False,
            'is_stage': False,
            'is_jury': False,
            'is_district': False,
            'is_venue': False,
            'is_judge': False,
            'is_judge_only': False,
            'is_chief_judge': False,
            'is_athlete_session': False,
            'athlete_obj': None,
            'user_district': '',
            'active_role': '',
            'active_role_code': '',
            'user_initials': '',
            'user_display_name': '',
            'nav_items': [],
            'workspaces': [],
            'has_multiple_roles': False,
            'show_sidebar': False,
            'is_scoring_desk': False,
            'current_url_name': getattr(getattr(request, 'resolver_match', None), 'url_name', ''),
        }

    user = request.user
    champ = get_active_championship(request)
    roles = get_user_roles_for_request(request, championship=champ)

    is_admin = is_system_admin(user)
    is_cd = (RoleChoices.CD in roles)
    is_stage = (RoleChoices.STAGE_MANAGER in roles)
    is_jury = (RoleChoices.JURY in roles)
    is_district = (RoleChoices.DISTRICT_USER in roles)
    is_venue = (RoleChoices.VENUE_DESK in roles)
    is_chief_judge = (RoleChoices.CHIEF_JUDGE in roles)
    is_judge = any(r in roles for r in [
        RoleChoices.D_JUDGE, RoleChoices.TIME_JUDGE, RoleChoices.ARTISTRY_JUDGE,
        RoleChoices.EVALUATOR, RoleChoices.CHIEF_JUDGE, RoleChoices.STAGE_MANAGER, 'APPROVED_JUDGE'
    ]) or hasattr(user, 'judge_profile')

    is_judge_only = (not is_admin and not is_cd and not is_jury and not is_district and not is_venue and is_judge)

    user_district = ''
    if is_district and champ:
        from django.db.models import Q
        dist_role = ScopedRole.objects.filter(user=user, role=RoleChoices.DISTRICT_USER).filter(Q(championship=champ) | Q(championship__isnull=True)).first()
        if dist_role and dist_role.district:
            user_district = dist_role.district
    elif hasattr(user, 'judge_profile') and user.judge_profile.district:
        user_district = user.judge_profile.district

    # Determine user initials
    if user.full_name:
        parts = [p for p in user.full_name.strip().split() if p]
        if len(parts) >= 2:
            user_initials = (parts[0][0] + parts[1][0]).upper()
        elif len(parts) == 1:
            user_initials = parts[0][:2].upper()
        else:
            user_initials = user.email[:2].upper()
    else:
        user_initials = user.email[:2].upper()

    user_display_name = user.full_name if user.full_name else user.email

    # Active role determination
    if is_admin:
        active_role = "System Admin"
        active_role_code = "ADMIN"
    elif is_cd:
        active_role = "Competition Director"
        active_role_code = "CD"
    elif is_district:
        active_role = f"District Officer ({user_district or 'Statewide'})"
        active_role_code = "DISTRICT_USER"
    elif is_venue:
        active_role = "Venue Clearance Desk"
        active_role_code = "VENUE_DESK"
    elif is_stage:
        active_role = "Stage Manager (Assigned Judge)"
        active_role_code = "STAGE_MANAGER"
    elif is_chief_judge:
        active_role = "Chief Judge"
        active_role_code = "CHIEF_JUDGE"
    elif any(r in roles for r in [RoleChoices.D_JUDGE, RoleChoices.TIME_JUDGE, RoleChoices.ARTISTRY_JUDGE, RoleChoices.EVALUATOR]):
        active_role = "Judge"
        active_role_code = "JUDGE"
    elif is_jury:
        active_role = "Jury Official"
        active_role_code = "JURY"
    elif is_judge:
        if hasattr(user, 'judge_profile') and not user.judge_profile.is_approved:
            active_role = "Universal Judge (Pending Approval)"
            active_role_code = "PENDING_JUDGE"
        else:
            active_role = "Universal Judge"
            active_role_code = "JUDGE"
    else:
        active_role = "Official Staff"
        active_role_code = "STAFF"

    workspaces = get_user_workspaces(user)
    has_multiple_roles = len(workspaces) > 1

    # Current URL name for navigation highlighting
    current_url_name = ''
    if hasattr(request, 'resolver_match') and request.resolver_match:
        current_url_name = request.resolver_match.url_name

    is_scoring_desk = current_url_name.startswith('scoring_') if current_url_name else False

    nav_items = get_role_navigation_items(user, champ, current_url_name)

    return {
        'user_roles': list(roles),
        'is_admin': is_admin,
        'is_cd': is_cd,
        'is_stage': is_stage,
        'is_jury': is_jury,
        'is_district': is_district,
        'is_venue': is_venue,
        'is_judge': is_judge,
        'is_judge_only': is_judge_only,
        'is_chief_judge': is_chief_judge,
        'is_athlete_session': False,
        'athlete_obj': None,
        'user_district': user_district,
        'active_role': active_role,
        'active_role_code': active_role_code,
        'user_initials': user_initials,
        'user_display_name': user_display_name,
        'nav_items': nav_items,
        'workspaces': workspaces,
        'has_multiple_roles': has_multiple_roles,
        'show_sidebar': True,
        'is_scoring_desk': is_scoring_desk,
        'current_url_name': current_url_name,
    }

def active_championship_context(request):
    """
    Context processor providing active championship and authorized championships list:
    - Admins see all championships.
    - Other staff/judges ONLY see championships they are assigned to.
    """
    if not hasattr(request, 'user') or not request.user.is_authenticated:
        active_champ = get_active_championship(request)
        return {
            'active_championship': active_champ,
            'all_championships': Championship.objects.filter(status='IN_PROGRESS'),
            'authorized_championships_count': 1,
            'can_switch_championships': False,
        }

    user = request.user
    all_champs = get_authorized_championships(user)
    active_champ = get_active_championship(request, user=user)

    can_switch = is_system_admin(user) or all_champs.count() > 1

    return {
        'active_championship': active_champ,
        'all_championships': all_champs,
        'authorized_championships_count': all_champs.count(),
        'can_switch_championships': can_switch,
    }

