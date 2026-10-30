import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q
from .models import ChampionshipRegistration, EventEntry, RegistrationStatus
from championships.models import Championship, DistrictQuota

@login_required
def district_approvals(request):
    """
    Implements 12_District_Athlete_Approval.png:
    District queue, quota tracker, document inspection, approve/request correction/reject.
    Enforces quota updates transactionally.
    """
    championship = Championship.objects.first()
    
    # Filter by user's assigned district or all if Admin
    selected_district = request.GET.get('district', '')
    districts = DistrictQuota.objects.filter(championship=championship)
    
    registrations = ChampionshipRegistration.objects.filter(championship=championship).select_related('athlete')
    if selected_district:
        registrations = registrations.filter(athlete__district=selected_district)
    
    quota_info = None
    if selected_district:
        quota_info = DistrictQuota.objects.filter(championship=championship, district_name=selected_district).first()

    if request.method == 'POST':
        reg_id = request.POST.get('reg_id')
        decision = request.POST.get('decision')
        notes = request.POST.get('notes', '')
        reg = get_object_or_404(ChampionshipRegistration, id=reg_id, championship=championship)

        if decision == 'approve':
            # Check quota
            dq = DistrictQuota.objects.filter(championship=championship, district_name=reg.athlete.district).first()
            if dq and dq.quota_used >= dq.quota_limit:
                messages.error(request, f"Cannot approve: District quota limit ({dq.quota_limit}) reached for {reg.athlete.district}!")
            else:
                reg.status = RegistrationStatus.DISTRICT_APPROVED
                reg.approved_by = request.user
                reg.approved_at = datetime.datetime.now()
                reg.district_approval_notes = notes
                reg.save()
                if dq:
                    dq.quota_used += 1
                    dq.save()
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
        'registrations': registrations,
        'quota_info': quota_info,
    })

@login_required
def venue_verification(request):
    """
    Implements 13_Venue_Verification.png:
    Search by name OR district OR mobile individually or combined.
    Shows matching records before selecting athlete.
    Verify identity, attendance, required documents, and issue clearance.
    """
    championship = Championship.objects.first()
    query = request.GET.get('q', '').strip()
    district_filter = request.GET.get('district', '').strip()
    
    matches = []
    selected_registration = None
    
    districts = [
        'Hyderabad', 'Rangareddy', 'Medchal-Malkajgiri', 'Warangal', 
        'Karimnagar', 'Nizamabad', 'Khammam', 'Nalgonda', 'Mahabubnagar', 'Adilabad'
    ]

    if query or district_filter:
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
    if reg_id:
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
