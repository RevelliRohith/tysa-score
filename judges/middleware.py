from django.shortcuts import redirect
from django.contrib import messages
from django.contrib.auth import logout

class JudgeMustChangePasswordMiddleware:
    """
    Enforces mandatory first-login password replacement for Universal Judges.
    If a judge has must_change_password=True, they cannot navigate anywhere
    in the application until they set their permanent confidential password.
    """
    EXEMPT_PREFIXES = (
        '/judges/set-password/',
        '/accounts/logout/',
        '/static/',
        '/media/',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            # Check if user has an associated judge profile with active credential
            if hasattr(request.user, 'judge_profile'):
                judge = request.user.judge_profile
                if hasattr(judge, 'credential') and judge.credential:
                    cred = judge.credential
                    if cred.must_change_password:
                        path = request.path
                        if not any(path.startswith(prefix) for prefix in self.EXEMPT_PREFIXES):
                            if cred.is_expired:
                                logout(request)
                                messages.error(
                                    request,
                                    "Your temporary password has expired. Please contact the System Administrator to receive a fresh credential."
                                )
                                return redirect('login')
                            return redirect('judge_change_password')

        return self.get_response(request)
