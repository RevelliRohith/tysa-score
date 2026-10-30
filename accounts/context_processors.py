from .models import RoleChoices, ScopedRole
from .permissions import (
    is_system_admin, get_authorized_championships, get_active_championship,
    get_user_roles_for_request, get_role_navigation_items
)
from championships.models import Championship

def user_roles(request):
    """
    Context processor providing active role status, assigned district,
    and role-specific navigation scoped strictly to the active championship.
    """
    if not hasattr(request, 'user') or not request.user.is_authenticated:
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
            'user_district': '',
            'nav_items': [],
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
    is_judge = any(r in roles for r in [
        RoleChoices.D_JUDGE, RoleChoices.TIME_JUDGE, RoleChoices.ARTISTRY_JUDGE,
        RoleChoices.EVALUATOR, RoleChoices.CHIEF_JUDGE, 'APPROVED_JUDGE'
    ]) or hasattr(user, 'judge_profile')

    is_judge_only = (not is_admin and not is_cd and not is_stage and not is_jury and not is_district and not is_venue and is_judge)

    user_district = ''
    if is_district and champ:
        from django.db.models import Q
        dist_role = ScopedRole.objects.filter(user=user, role=RoleChoices.DISTRICT_USER).filter(Q(championship=champ) | Q(championship__isnull=True)).first()
        if dist_role and dist_role.district:
            user_district = dist_role.district

    # Current URL name for navigation highlighting
    current_url_name = ''
    if hasattr(request, 'resolver_match') and request.resolver_match:
        current_url_name = request.resolver_match.url_name

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
        'user_district': user_district,
        'nav_items': nav_items,
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
        }

    user = request.user
    all_champs = get_authorized_championships(user)
    active_champ = get_active_championship(request, user=user)

    return {
        'active_championship': active_champ,
        'all_championships': all_champs,
    }
