from .models import RoleChoices

def user_roles(request):
    if not hasattr(request, 'user') or not request.user.is_authenticated:
        return {'user_roles': [], 'is_admin': False, 'is_cd': False}
    
    roles = list(request.user.scoped_roles.values_list('role', flat=True))
    is_admin = request.user.is_superuser or (RoleChoices.ADMIN in roles)
    is_cd = is_admin or (RoleChoices.CD in roles)
    return {
        'user_roles': roles,
        'is_admin': is_admin,
        'is_cd': is_cd,
    }
