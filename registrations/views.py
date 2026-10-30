import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q
from .models import ChampionshipRegistration, EventEntry, RegistrationStatus
from championships.models import Championship, DistrictQuota
from championships.services import ChampionshipService
from accounts.models import RoleChoices, ScopedRole
from accounts.permissions import get_active_championship, role_required, get_user_roles_for_request

@role_required(RoleChoices.ADMIN, RoleChoices.DISTRICT_USER)
def district_approvals(request):
    """
    Implements 12_District_Athlete_Approval.png:
    District queue, quota tracker, document inspection, approve/request correction/reject.
    Enforces district scope: District users only see and manage their own assigned district.
    """
    championship = get_active_championship(request)
    user_roles = get_user_roles_for_request(request, championship)
    is_admin = request.user.is_superuser or (RoleChoices.ADMIN in user_roles)

    # Resolve assigned district
    user_scoped = ScopedRole.objects.filter(user=request.user, role=RoleChoices.DISTRICT_USER).first()
    assigned_district = user_scoped.district if user_scoped else ''

    if is_admin:
        selected_district = request.GET.get('district', '')
    else:
        # Strict enforcement: district user can ONLY view their assigned district
        selected_district = assigned_district

    districts = DistrictQuota.objects.filter(championship=championship) if championship else DistrictQuota.objects.none()
    
    registrations = ChampionshipRegistration.objects.filter(championship=championship).select_related('athlete') if championship else ChampionshipRegistration.objects.none()
    if selected_district:
        registrations = registrations.filter(athlete__district=selected_district)
    
    quota_info = None
    if selected_district and championship:
        quota_info = DistrictQuota.objects.filter(championship=championship, district_name=selected_district).first()

    if request.method == 'POST':
        reg_id = request.POST.get('reg_id')
        decision = request.POST.get('decision')
        notes = request.POST.get('notes', '')
        reg = get_object_or_404(ChampionshipRegistration, id=reg_id, championship=championship)

        # Ensure district user cannot modify another district's athlete
        if not is_admin and reg.athlete.district != assigned_district:
            messages.error(request, "Permission denied: You can only approve athletes from your assigned district.")
            return redirect('district_approvals')

        if decision == 'approve':
            success = ChampionshipService.approve_district_registration(reg.id, request.user)
            if not success:
                messages.error(request, f"Cannot approve: District quota limit reached for {reg.athlete.district}!")
            else:
                if notes:
                    reg.refresh_from_db()
                    reg.district_approval_notes = notes
                    reg.save(update_fields=['district_approval_notes'])
                messages.success(request, f"Registration {reg.registration_number} for {reg.athlete.full_name} approved!")
        elif decision == 'reject':
            reg.status = RegistrationStatus.DISTRICT_REJECTED
            reg.district_approval_notes = notes
            reg.save()
            messages.info(request, f"Registration {reg.registration_number} rejected: {notes}")
        
        return redirect(f"/district/approvals/?district={selected_district}")

    return render(request, 'registrations/district_approvals.html', {
        'championship': championship,
        'districts': districts,
        'selected_district': selected_district,
        'assigned_district': assigned_district,
        'is_admin': is_admin,
        'registrations': registrations,
        'quota_info': quota_info,
    })

@role_required(RoleChoices.ADMIN, RoleChoices.VENUE_DESK)
def venue_verification(request):
    """
    Implements 13_Venue_Verification.png:
    Search by name OR district OR mobile individually or combined.
    Shows matching records before selecting athlete.
    Verify identity, attendance, required documents, and issue clearance.
    """
    championship = get_active_championship(request)
    query = request.GET.get('q', '').strip()
    district_filter = request.GET.get('district', '').strip()
    
    matches = []
    selected_registration = None
    
    districts = [
        'Hyderabad', 'Rangareddy', 'Medchal-Malkajgiri', 'Warangal', 
        'Karimnagar', 'Nizamabad', 'Khammam', 'Nalgonda', 'Mahabubnagar', 'Adilabad'
    ]

    if (query or district_filter) and championship:
        qs = ChampionshipRegistration.objects.filter(championship=championship).select_related('athlete')
        if query:
            qs = qs.filter(
                Q(athlete__first_name__icontains=query) |
                Q(athlete__last_name__icontains=query) |
                Q(athlete__mobile__icontains=query) |
                Q(athlete__public_athlete_id__icontains=query) |
                Q(event_entries__chest_number__iexact=query)
            ).distinct()
        if district_filter:
            qs = qs.filter(athlete__district=district_filter)
        matches = list(qs[:25])

    reg_id = request.GET.get('select_id')
    if reg_id and championship:
        selected_registration = ChampionshipRegistration.objects.filter(id=reg_id, championship=championship).select_related('athlete').first()

    if request.method == 'POST':
        action = request.POST.get('action')
        target_id = request.POST.get('registration_id')
        reg = get_object_or_404(ChampionshipRegistration, id=target_id, championship=championship)

        if action == 'clear_venue':
            notes = request.POST.get('venue_notes', '')
            reg.status = RegistrationStatus.VENUE_CLEARED
            reg.venue_cleared = True
            reg.venue_cleared_by = request.user
            reg.venue_cleared_at = datetime.datetime.now()
            reg.venue_notes = notes
            reg.save()
            messages.success(request, f"Athlete {reg.athlete.full_name} cleared at venue desk! Status marked VENUE CLEARED.")
            return redirect(f"/venue/verification/?select_id={reg.id}")

    return render(request, 'registrations/venue_verification.html', {
        'championship': championship,
        'query': query,
        'district_filter': district_filter,
        'districts': districts,
        'matches': matches,
        'selected_registration': selected_registration,
    })
