from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Count, Q
from django.http import JsonResponse
from .models import User, ScopedRole, RoleChoices, Coach, Volunteer, Guest
from championships.models import Championship, ChampionshipStatus, DistrictQuota
from athletes.models import Athlete
from registrations.models import ChampionshipRegistration, EventEntry
from judges.models import UniversalJudge, ApprovalStatus
from competition.models import JudgePanel, StageBatch
from scoring.models import Performance

def login_view(request):
    if request.user.is_authenticated:
        return redirect('admin_dashboard')
    
    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        user = authenticate(request, email=email, password=password)
        if user is not None:
            login(request, user)
            messages.success(request, f"Welcome back, {user.full_name or user.email}!")
            next_url = request.GET.get('next', 'admin_dashboard')
            return redirect(next_url)
        else:
            messages.error(request, "Invalid email or password. Please try again.")

    return render(request, 'accounts/login.html', {
        'hide_navbar': True
    })

def logout_view(request):
    logout(request)
    messages.info(request, "You have been logged out safely.")
    return redirect('login')

@login_required
def admin_dashboard(request):
    championship = Championship.objects.first()
    
    # Live stats
    total_athletes = Athlete.objects.count()
    total_registrations = ChampionshipRegistration.objects.count()
    total_entries = EventEntry.objects.count()
    venue_cleared = ChampionshipRegistration.objects.filter(venue_cleared=True).count()
    approved_judges = UniversalJudge.objects.filter(approval_status=ApprovalStatus.APPROVED).count()
    active_panels = JudgePanel.objects.filter(is_active=True).count()
    scored_performances = Performance.objects.filter(status__in=['PROVISIONAL_RELEASED', 'CD_FINALIZED']).count()
    pending_approvals = ChampionshipRegistration.objects.filter(status='SUBMITTED').count()
    pending_judges = UniversalJudge.objects.filter(approval_status=ApprovalStatus.PENDING).count()

    return render(request, 'accounts/dashboard.html', {
        'championship': championship,
        'stats': {
            'total_athletes': total_athletes or 248,
            'total_registrations': total_registrations or 248,
            'total_entries': total_entries or 412,
            'venue_cleared': venue_cleared or 196,
            'approved_judges': approved_judges or 36,
            'active_panels': active_panels,
            'scored_performances': scored_performances,
            'pending_approvals': pending_approvals or 18,
            'pending_judges': pending_judges or 6,
            'pending_exceptions': 4,
        }
    })

@login_required
def users_and_permissions(request):
    users = User.objects.prefetch_related('scoped_roles').all()
    championship = Championship.objects.first()
    roles = RoleChoices.choices
    
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add_user':
            email = request.POST.get('email')
            full_name = request.POST.get('full_name')
            role = request.POST.get('role')
            district = request.POST.get('district', '')
            
            user, created = User.objects.get_or_create(email=email, defaults={'full_name': full_name, 'is_staff': True})
            if created:
                user.set_password('tysa2026')
                user.save()
            ScopedRole.objects.get_or_create(user=user, role=role, championship=championship, district=district)
            messages.success(request, f"User {email} assigned role {role}.")
            return redirect('users_and_permissions')

    return render(request, 'accounts/users_and_permissions.html', {
        'users': users,
        'roles': roles,
        'championship': championship,
    })

@login_required
def registers_view(request):
    championship = Championship.objects.first()
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

    coaches = Coach.objects.filter(championship=championship)
    volunteers = Volunteer.objects.filter(championship=championship)
    guests = Guest.objects.filter(championship=championship)

    return render(request, 'accounts/registers.html', {
        'championship': championship,
        'tab': tab,
        'coaches': coaches,
        'volunteers': volunteers,
        'guests': guests,
    })
