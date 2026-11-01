import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q
from .models import ChampionshipRegistration, EventEntry, RegistrationStatus
from championships.models import Championship, DistrictQuota
from championships.services import ChampionshipService
from django.utils import timezone
from config.districts import TELANGANA_DISTRICTS
from athletes.models import AthleteDocument
from accounts.models import RoleChoices, ScopedRole
from accounts.permissions import get_active_championship, role_required, get_user_roles_for_request

@role_required(RoleChoices.ADMIN, RoleChoices.DISTRICT_USER)
def district_approvals(request):
    """
    Implements 12_District_Athlete_Approval.png:
    District queue, quota tracker, document inspection, approve/request correction/reject.
    Enforces district scope: District users only see and manage their own assigned district.
    Supports Event and Age Category filtering, and Batch Selection & Approval ("Submit All").
    """
    championship = get_active_championship(request)
    user_roles = get_user_roles_for_request(request, championship)
    is_admin = request.user.is_superuser or (RoleChoices.ADMIN in user_roles)

    # Resolve assigned district
    user_scoped = ScopedRole.objects.filter(user=request.user, role=RoleChoices.DISTRICT_USER).first()
    assigned_district = user_scoped.district if user_scoped else getattr(request.user, 'district', '')

    if is_admin:
        selected_district = request.GET.get('district', '').strip()
        districts = DistrictQuota.objects.filter(championship=championship) if championship else DistrictQuota.objects.none()
    else:
        # Strict enforcement: district user can ONLY view their assigned district
        selected_district = assigned_district
        districts = DistrictQuota.objects.filter(championship=championship, district_name__iexact=assigned_district) if championship else DistrictQuota.objects.none()

    tab = request.GET.get('tab', 'queue').strip().lower()
    if tab not in ['queue', 'approved', 'venue_clearance', 'consolidated']:
        tab = 'queue'

    selected_event = request.GET.get('event', '').strip()
    selected_cat = request.GET.get('cat', '').strip()

    base_registrations = ChampionshipRegistration.objects.filter(championship=championship).select_related('athlete', 'approved_by', 'venue_cleared_by').prefetch_related('event_entries__event_config', 'event_entries__age_category', 'athlete__documents') if championship else ChampionshipRegistration.objects.none()
    if selected_district:
        base_registrations = base_registrations.filter(athlete__district__iexact=selected_district)
    elif not is_admin and assigned_district:
        base_registrations = base_registrations.filter(athlete__district__iexact=assigned_district)
    
    if selected_event:
        base_registrations = base_registrations.filter(event_entries__event_config__event_type=selected_event).distinct()
    if selected_cat:
        base_registrations = base_registrations.filter(event_entries__age_category_id=selected_cat).distinct()

    # Exact Counts for District Pipeline Awareness
    pending_count = base_registrations.filter(status=RegistrationStatus.SUBMITTED).count()
    dist_approved_count = base_regs_approved = base_registrations.filter(status=RegistrationStatus.DISTRICT_APPROVED).count()
    venue_cleared_count = base_registrations.filter(Q(status=RegistrationStatus.VENUE_CLEARED) | Q(venue_cleared=True)).count()
    approved_count = base_registrations.filter(status__in=[RegistrationStatus.DISTRICT_APPROVED, RegistrationStatus.VENUE_CLEARED]).count()
    corrections_count = base_registrations.filter(Q(status=RegistrationStatus.NEEDS_CORRECTION) | Q(status=RegistrationStatus.DRAFT)).count()
    exception_count = base_registrations.filter(has_venue_exception=True).count()
    total_count = base_registrations.count()

    # Apply Tab Filter
    if tab == 'queue':
        # Applications waiting for district review
        registrations = base_registrations.filter(status=RegistrationStatus.SUBMITTED).order_by('-created_at')
    elif tab == 'approved':
        # Approved by district -> Forwarded & waiting for venue check-in
        registrations = base_registrations.filter(status=RegistrationStatus.DISTRICT_APPROVED).order_by('-approved_at', '-id')
    elif tab == 'venue_clearance':
        # Separate Venue Clearance Module: shows both awaiting check-in and cleared candidates
        registrations = base_registrations.filter(status__in=[RegistrationStatus.DISTRICT_APPROVED, RegistrationStatus.VENUE_CLEARED]).order_by('-venue_cleared', '-id')
    elif tab == 'consolidated':
        # Master register of all approved athletes
        registrations = base_registrations.filter(status__in=[RegistrationStatus.DISTRICT_APPROVED, RegistrationStatus.VENUE_CLEARED]).order_by('id')
    else:
        registrations = base_registrations.filter(status=RegistrationStatus.SUBMITTED).order_by('-created_at')

    quota_info = None
    remaining_quota = 30
    if selected_district and championship:
        quota_info = DistrictQuota.objects.filter(championship=championship, district_name__iexact=selected_district).first()
        if quota_info:
            remaining_quota = max(0, quota_info.quota_limit - quota_info.quota_used)
        else:
            remaining_quota = max(0, 30 - dist_approved_count)
    else:
        remaining_quota = max(0, 30 - dist_approved_count)

    if request.method == 'POST':
        action = request.POST.get('action')
        decision = request.POST.get('decision')
        
        # Batch approval / Submit All
        if action == 'batch_approve' or decision == 'submit_all':
            selected_ids = request.POST.getlist('selected_reg_ids')
            approved_count = 0
            for r_id in selected_ids:
                reg_obj = ChampionshipRegistration.objects.filter(id=r_id, championship=championship).first()
                if reg_obj and (is_admin or reg_obj.athlete.district == assigned_district):
                    success = ChampionshipService.approve_district_registration(reg_obj.id, request.user)
                    if success:
                        approved_count += 1
            messages.success(request, f"Successfully approved {approved_count} candidate(s) for district quota!")
            return redirect(f"/district/approvals/?tab={tab}&district={selected_district}&event={selected_event}&cat={selected_cat}")

        reg_id = request.POST.get('reg_id')
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
                messages.success(request, f"Registration {reg.registration_number} for {reg.athlete.full_name} approved and forwarded to Venue Clearance!")
        elif decision == 'reject':
            reg.status = RegistrationStatus.DISTRICT_REJECTED
            reg.district_approval_notes = notes
            reg.save()
            messages.info(request, f"Registration {reg.registration_number} rejected: {notes}")
        elif decision == 'needs_correction':
            reg.status = RegistrationStatus.NEEDS_CORRECTION
            reg.district_approval_notes = f"CORRECTION REQUIRED: {notes}"
            reg.save()
            messages.warning(request, f"Registration {reg.registration_number} returned for correction: {notes}")
        
        return redirect(f"/district/approvals/?tab={tab}&district={selected_district}&event={selected_event}&cat={selected_cat}")

    from championships.models import AgeCategory, EventType
    age_categories = AgeCategory.objects.all().order_by('display_order')

    return render(request, 'registrations/district_approvals.html', {
        'championship': championship,
        'tab': tab,
        'approved_count': approved_count,
        'pending_count': pending_count,
        'corrections_count': corrections_count,
        'dist_approved_count': dist_approved_count,
        'venue_cleared_count': venue_cleared_count,
        'exception_count': exception_count,
        'total_count': total_count,
        'remaining_quota': remaining_quota,
        'districts': districts,
        'selected_district': selected_district,
        'assigned_district': assigned_district,
        'is_admin': is_admin,
        'registrations': registrations,
        'quota_info': quota_info,
        'selected_event': selected_event,
        'selected_cat': selected_cat,
        'event_types': EventType.choices,
        'age_categories': age_categories,
    })

@role_required(RoleChoices.ADMIN, RoleChoices.VENUE_DESK)
def venue_verification(request):
    """
    Implements 13_Venue_Verification.png:
    - Mode 1: Individual Search by name OR district OR mobile, full comprehensive athlete data upon selection.
    - Mode 2: District Bulk Verification: select whole district, select all candidates, and complete one-click verification and venue clearance.
    """
    championship = get_active_championship(request)
    mode = request.GET.get('mode', 'individual')
    query = request.GET.get('q', '').strip()
    district_filter = request.GET.get('district', '').strip()
    bulk_district = request.GET.get('bulk_district', '').strip() or TELANGANA_DISTRICTS[0]
    
    matches = []
    selected_registration = None
    athlete_age = None
    bulk_registrations = []

    if championship:
        if mode == 'bulk_district':
            bulk_registrations = ChampionshipRegistration.objects.filter(
                championship=championship,
                athlete__district__iexact=bulk_district
            ).select_related('athlete').prefetch_related('event_entries__event_config', 'athlete__documents').order_by('id')
        else:
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
            
            matches = list(qs.order_by('-id')[:35])

    reg_id = request.GET.get('select_id')
    if not reg_id and matches:
        reg_id = matches[0].id

    if reg_id and championship and mode != 'bulk_district':
        selected_registration = ChampionshipRegistration.objects.filter(
            id=reg_id, championship=championship
        ).select_related('athlete', 'approved_by', 'venue_cleared_by').prefetch_related(
            'athlete__documents',
            'event_entries__event_config',
            'event_entries__age_category',
            'event_entries__partner_athlete',
            'event_entries__sequence_items__declared_asana'
        ).first()

        if selected_registration and selected_registration.athlete and selected_registration.athlete.dob:
            cutoff = championship.age_cutoff_date or datetime.date(2026, 12, 31)
            dob = selected_registration.athlete.dob
            athlete_age = cutoff.year - dob.year - ((cutoff.month, cutoff.day) < (dob.month, dob.day))

    if request.method == 'POST':
        action = request.POST.get('action')
        
        # Whole District Bulk Verification
        if action == 'bulk_verify_district':
            selected_ids = request.POST.getlist('selected_reg_ids')
            if not selected_ids:
                messages.warning(request, "No athletes were selected for bulk clearance.")
                return redirect(f"/venue/verification/?mode=bulk_district&bulk_district={bulk_district}")

            updated_count = ChampionshipRegistration.objects.filter(
                id__in=selected_ids,
                championship=championship
            ).update(
                status=RegistrationStatus.VENUE_CLEARED,
                venue_cleared=True,
                venue_cleared_by=request.user,
                venue_cleared_at=timezone.now()
            )

            # Auto-verify documents for cleared athletes
            AthleteDocument.objects.filter(
                athlete__registrations__id__in=selected_ids
            ).update(
                is_verified=True,
                verified_by=request.user,
                verified_at=timezone.now()
            )

            messages.success(request, f"Successfully verified and cleared {updated_count} athletes from {bulk_district} at Venue Desk!")
            return redirect(f"/venue/verification/?mode=bulk_district&bulk_district={bulk_district}")

        target_id = request.POST.get('registration_id')
        reg = get_object_or_404(ChampionshipRegistration, id=target_id, championship=championship)

        if action == 'clear_venue':
            notes = request.POST.get('venue_notes', '').strip()
            reg.status = RegistrationStatus.VENUE_CLEARED
            reg.venue_cleared = True
            reg.venue_cleared_by = request.user
            reg.venue_cleared_at = timezone.now()
            if notes:
                reg.venue_notes = notes
            reg.save()
            messages.success(request, f"Athlete {reg.athlete.full_name} ({reg.athlete.public_athlete_id}) officially cleared at Venue Desk!")
            return redirect(f"/venue/verification/?select_id={reg.id}&mode={mode}")

        elif action == 'revoke_clearance':
            reg.status = RegistrationStatus.DISTRICT_APPROVED
            reg.venue_cleared = False
            reg.venue_cleared_by = None
            reg.venue_cleared_at = None
            reg.save()
            messages.info(request, f"Venue clearance revoked for {reg.athlete.full_name}.")
            return redirect(f"/venue/verification/?select_id={reg.id}&mode={mode}")

        elif action == 'verify_document':
            doc_id = request.POST.get('doc_id')
            doc = get_object_or_404(AthleteDocument, id=doc_id, athlete=reg.athlete)
            doc.is_verified = True
            doc.verified_at = timezone.now()
            doc.verified_by = request.user
            doc.save()
            messages.success(request, f"Document '{doc.get_document_type_display()}' verified successfully.")
            return redirect(f"/venue/verification/?select_id={reg.id}&mode={mode}")

        elif action == 'log_exception':
            reason = request.POST.get('exception_reason', '').strip()
            accepted_by_role = request.POST.get('exception_accepted_by_role', 'Competition Director').strip()
            authorizer_name = request.POST.get('exception_authorizer_name', '').strip()
            authorizer_mobile = request.POST.get('exception_authorizer_mobile', '').strip()
            clear_venue_too = (request.POST.get('clear_venue_too') == '1')

            if reason:
                reg.has_venue_exception = True
                reg.exception_reason = reason
                reg.exception_accepted_by_role = accepted_by_role
                reg.exception_authorizer_name = authorizer_name
                reg.exception_authorizer_mobile = authorizer_mobile
                reg.exception_granted_at = timezone.now()

                timestamp_str = timezone.now().strftime("%d-%m-%Y %H:%M")
                auth_info = f"{authorizer_name} ({accepted_by_role}, {authorizer_mobile})" if authorizer_name else accepted_by_role
                existing = reg.venue_notes or ""
                reg.venue_notes = f"{existing}\n[{timestamp_str}] EXCEPTION AUTHORIZED by {auth_info}: {reason}".strip()

                if clear_venue_too:
                    reg.status = RegistrationStatus.VENUE_CLEARED
                    reg.venue_cleared = True
                    reg.venue_cleared_by = request.user
                    reg.venue_cleared_at = timezone.now()

                reg.save()
                messages.warning(
                    request, 
                    f"Official exception accepted for {reg.athlete.full_name}! Authorized by {authorizer_name or accepted_by_role} ({authorizer_mobile})."
                )
            else:
                messages.error(request, "Please provide a clear reason for the venue exception.")
            return redirect(f"/venue/verification/?select_id={reg.id}&mode={mode}")

    return render(request, 'registrations/venue_verification.html', {
        'championship': championship,
        'mode': mode,
        'query': query,
        'district_filter': district_filter,
        'bulk_district': bulk_district,
        'districts': TELANGANA_DISTRICTS,
        'matches': matches,
        'selected_registration': selected_registration,
        'athlete_age': athlete_age,
        'bulk_registrations': bulk_registrations,
    })
