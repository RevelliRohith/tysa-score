import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import JsonResponse
from .models import Athlete, AthleteDocument, generate_private_key, generate_athlete_id
from championships.models import Championship, EventConfiguration, AgeCategory, EventType
from registrations.models import ChampionshipRegistration, EventEntry, RegistrationStatus, AsanaSequenceItem
from catalogue.models import Asana
from accounts.permissions import get_active_championship
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from config.districts import TELANGANA_DISTRICTS

def athlete_register_step1(request):
    """
    Implements 05_Athlete_Personal_Details.png:
    Step 1: Personal Details, DOB, Gender, District, Photo (optional), Guardian Name.
    """
    championship = get_active_championship(request)

    # Check if athlete is in an active session
    athlete_id = request.session.get('athlete_id')
    athlete = Athlete.objects.filter(id=athlete_id).first() if athlete_id else None

    if request.method == 'POST':
        full_name = request.POST.get('full_name', '').strip()
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        if not first_name and full_name:
            parts = full_name.split(' ', 1)
            first_name = parts[0]
            last_name = parts[1] if len(parts) > 1 else ''

        dob_str = request.POST.get('dob')
        gender = request.POST.get('gender', 'M')
        guardian_name = request.POST.get('guardian_name', '').strip()
        district = request.POST.get('district', 'Mancherial')
        mobile = request.POST.get('mobile', '').strip()
        email = request.POST.get('email', '').strip()
        academy = request.POST.get('academy_name', '').strip() or request.POST.get('academy', '').strip()
        aadhaar = request.POST.get('aadhaar_number', '').strip()
        
        dob = datetime.date.fromisoformat(dob_str) if dob_str else datetime.date(2010, 1, 1)

        if athlete:
            athlete.first_name = first_name
            athlete.last_name = last_name
            athlete.dob = dob
            athlete.gender = gender
            athlete.guardian_name = guardian_name
            athlete.district = district
            athlete.mobile = mobile
            athlete.email = email
            athlete.academy = academy
            if 'photo' in request.FILES:
                athlete.photo = request.FILES['photo']
            athlete.save()
        else:
            athlete = Athlete(
                first_name=first_name,
                last_name=last_name,
                dob=dob,
                gender=gender,
                guardian_name=guardian_name,
                district=district,
                mobile=mobile,
                email=email,
                academy=academy
            )
            if 'photo' in request.FILES:
                athlete.photo = request.FILES['photo']
            raw_key = athlete.set_private_key()
            athlete.save()
            request.session['athlete_id'] = athlete.id
            request.session['athlete_private_key'] = raw_key

        # Save age proof document if provided
        if 'age_proof' in request.FILES:
            AthleteDocument.objects.create(
                athlete=athlete,
                document_type='AGE_PROOF',
                document_file=request.FILES['age_proof'],
                document_number=aadhaar
            )

        return redirect('athlete_register_step2')

    return render(request, 'athletes/step1_personal.html', {
        'athlete': athlete,
        'districts': TELANGANA_DISTRICTS,
        'championship': championship,
    })

def athlete_register_step2(request):
    """
    Implements 06_Event_and_Pair_Selection.png:
    Step 2: Event Cards, derived category based on age cutoff, pair partner lookup.
    """
    athlete_id = request.session.get('athlete_id')
    if not athlete_id:
        return redirect('athlete_register_step1')
    
    athlete = get_object_or_404(Athlete, id=athlete_id)
    championship = get_active_championship(request)
    
    # Calculate athlete age as of championship cutoff date
    cutoff = championship.age_cutoff_date if championship else datetime.date(2026, 12, 31)
    age = cutoff.year - athlete.dob.year - ((cutoff.month, cutoff.day) < (athlete.dob.month, athlete.dob.day))
    
    # Determine derived eligible category
    matched_category = None
    all_categories = AgeCategory.objects.all()
    for cat in all_categories:
        if cat.min_age <= age <= cat.max_age:
            matched_category = cat
            break
    if not matched_category:
        matched_category = all_categories.first()

    event_configs = championship.event_configurations.filter(is_enabled=True)

    if request.method == 'POST':
        selected_event_ids = request.POST.getlist('selected_events')
        event_types = request.POST.getlist('event_types')
        
        # If event_types strings submitted (e.g. ['TRADITIONAL', 'ARTISTIC_SINGLE'])
        if not selected_event_ids and event_types:
            for et in event_types:
                cfg, _ = EventConfiguration.objects.get_or_create(
                    championship=championship,
                    event_type=et,
                    defaults={'is_enabled': True}
                )
                selected_event_ids.append(cfg.id)

        if not selected_event_ids:
            # Fallback to enabled configs or traditional
            trad_cfg = EventConfiguration.objects.filter(championship=championship, event_type=EventType.TRADITIONAL).first()
            if trad_cfg:
                selected_event_ids.append(trad_cfg.id)
            else:
                messages.error(request, "Please select at least one event format.")
                return redirect('athlete_register_step2')

        # Create or get registration
        reg_num = f"TYSA-REG-{championship.year}-{athlete.id:04d}"
        reg, _ = ChampionshipRegistration.objects.get_or_create(
            athlete=athlete,
            championship=championship,
            defaults={'registration_number': reg_num, 'status': RegistrationStatus.DRAFT}
        )

        for ev_id in selected_event_ids:
            ev_cfg = EventConfiguration.objects.filter(id=ev_id).first()
            if not ev_cfg:
                continue

            partner_id_str = request.POST.get('partner_id', '').strip() or request.POST.get(f'partner_id_{ev_id}', '').strip()
            partner = Athlete.objects.filter(public_athlete_id=partner_id_str).first() if (ev_cfg.is_pair and partner_id_str) else None

            # Generate chest number if not present
            last_entry = EventEntry.objects.filter(event_config=ev_cfg).order_by('-id').first()
            chest_num = str(101 + (last_entry.id if last_entry else 0))

            EventEntry.objects.update_or_create(
                registration=reg,
                event_config=ev_cfg,
                defaults={
                    'age_category': matched_category,
                    'gender': athlete.gender,
                    'is_pair': ev_cfg.is_pair,
                    'partner_athlete': partner,
                    'chest_number': chest_num
                }
            )

        messages.success(request, "Event entries registered. Proceeding to asana routine declaration.")
        return redirect('athlete_register_step3')

    return render(request, 'athletes/step2_events.html', {
        'athlete': athlete,
        'championship': championship,
        'derived_age': age,
        'matched_category': matched_category,
        'event_configs': event_configs,
    })

def athlete_register_step3(request):
    """
    Implements 07_Unrestricted_Asana_Selection.png:
    Step 3: Published asana library on left, ordered sequence on right, validation.
    """
    athlete_id = request.session.get('athlete_id')
    if not athlete_id:
        return redirect('athlete_register_step1')
    
    athlete = get_object_or_404(Athlete, id=athlete_id)
    championship = get_active_championship(request)
    reg = ChampionshipRegistration.objects.filter(athlete=athlete, championship=championship).first()
    if not reg:
        return redirect('athlete_register_step2')

    entries = reg.event_entries.all()
    selected_entry_id = request.GET.get('entry_id')
    current_entry = entries.filter(id=selected_entry_id).first() if selected_entry_id else entries.first()

    catalog_asanas = Asana.objects.filter(is_published=True, is_reference_record=False)
    
    # Existing sequence items
    sequence_items = AsanaSequenceItem.objects.filter(event_entry=current_entry).order_by('sequence_order')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'save_sequence':
            asana_codes = request.POST.getlist('selected_asana_codes')
            # Save or snapshot sequence
            AsanaSequenceItem.objects.filter(event_entry=current_entry).delete()
            for seq_idx, code in enumerate(asana_codes, start=1):
                pose = Asana.objects.filter(code=code).first()
                if pose:
                    AsanaSequenceItem.objects.create(
                        event_entry=current_entry,
                        round_type='PRELIMINARY',
                        sequence_order=seq_idx,
                        is_compulsory=(seq_idx <= 4 if current_entry.event_config.event_type == EventType.TRADITIONAL else False),
                        declared_asana=pose,
                        declared_code=pose.code,
                        declared_name=pose.display_name,
                        declared_bv=pose.base_value,
                        image_url=pose.image_url
                    )
            messages.success(request, f"Routine sequence saved for {current_entry.event_config.get_event_type_display()}.")
            return redirect('athlete_register_step4')

    return render(request, 'athletes/step3_asanas.html', {
        'athlete': athlete,
        'entries': entries,
        'current_entry': current_entry,
        'catalog_asanas': catalog_asanas,
        'sequence_items': sequence_items,
    })

def athlete_register_step4(request):
    """
    Step 4: Document Uploads (Age proof, School ID, Parental Consent).
    """
    athlete_id = request.session.get('athlete_id')
    if not athlete_id:
        return redirect('athlete_register_step1')
    
    athlete = get_object_or_404(Athlete, id=athlete_id)
    documents = athlete.documents.all()

    if request.method == 'POST':
        doc_type = request.POST.get('document_type')
        doc_num = request.POST.get('document_number', '')
        file_obj = request.FILES.get('document_file')
        if file_obj:
            AthleteDocument.objects.create(
                athlete=athlete,
                document_type=doc_type,
                document_file=file_obj,
                document_number=doc_num
            )
            messages.success(request, f"Document uploaded.")
            return redirect('athlete_register_step4')
        else:
            return redirect('athlete_register_step5')

    return render(request, 'athletes/step4_documents.html', {
        'athlete': athlete,
        'documents': documents,
    })

def athlete_register_step5(request):
    """
    Step 5: Review & Final Submission.
    """
    athlete_id = request.session.get('athlete_id')
    if not athlete_id:
        return redirect('athlete_register_step1')
    
    athlete = get_object_or_404(Athlete, id=athlete_id)
    championship = get_active_championship(request)
    reg = get_object_or_404(ChampionshipRegistration, athlete=athlete, championship=championship)

    if request.method == 'POST':
        reg.status = RegistrationStatus.SUBMITTED
        reg.save()
        messages.success(request, "Your championship registration has been officially submitted!")
        return redirect('athlete_id_card', athlete_id=athlete.public_athlete_id)

    return render(request, 'athletes/step5_review.html', {
        'athlete': athlete,
        'registration': reg,
        'championship': championship,
    })

def athlete_id_card(request, athlete_id):
    """
    Implements 08_Athlete_Status_and_ID_Card.png:
    Permanent public ID, private key handling with copy guidance,
    verification QR, and printable championship ID card.
    """
    athlete = get_object_or_404(Athlete, public_athlete_id=athlete_id)
    championship = get_active_championship(request)
    registration = ChampionshipRegistration.objects.filter(athlete=athlete, championship=championship).first()

    # Show private key if in athlete's own authenticated session
    is_owner_session = (request.session.get('athlete_id') == athlete.id)
    display_key = athlete.private_key_display if is_owner_session else None

    return render(request, 'athletes/id_card.html', {
        'athlete': athlete,
        'championship': championship,
        'registration': registration,
        'is_owner_session': is_owner_session,
        'display_key': display_key,
    })

@login_required
def athlete_id_cards_batch_print(request):
    """
    Universal Athlete ID Cards - District & Batch Printing:
    Filters athletes by district and print status.
    Provides batch printable card sheet and 'Mark Selected as Printed' action.
    """
    selected_district = request.GET.get('district', '').strip()
    print_status = request.GET.get('status', 'all').strip()
    
    athletes_qs = Athlete.objects.all().order_by('district', 'first_name', 'last_name')
    if selected_district:
        athletes_qs = athletes_qs.filter(district=selected_district)
    if print_status == 'pending':
        athletes_qs = athletes_qs.filter(is_id_card_printed=False)
    elif print_status == 'printed':
        athletes_qs = athletes_qs.filter(is_id_card_printed=True)
        
    if request.method == 'POST':
        action = request.POST.get('action')
        selected_ids = request.POST.getlist('selected_athlete_ids')
        if action == 'mark_printed':
            updated = Athlete.objects.filter(id__in=selected_ids).update(
                is_id_card_printed=True,
                id_card_printed_at=timezone.now()
            )
            messages.success(request, f"Marked {updated} athlete ID cards as printed.")
        elif action == 'mark_unprinted':
            updated = Athlete.objects.filter(id__in=selected_ids).update(
                is_id_card_printed=False,
                id_card_printed_at=None
            )
            messages.info(request, f"Reset print status for {updated} athletes.")
        return redirect(f"/athletes/id-cards/batch/?district={selected_district}&status={print_status}")

    return render(request, 'athletes/id_cards_batch.html', {
        'athletes': athletes_qs,
        'districts': TELANGANA_DISTRICTS,
        'selected_district': selected_district,
        'print_status': print_status,
        'total_count': athletes_qs.count(),
    })

def athlete_returning(request):
    """
    Returning athlete key access: Enter private access key to access profile across championships.
    """
    if request.method == 'POST':
        key = request.POST.get('private_key', '').strip()
        athletes = Athlete.objects.all()
        matched = None
        for a in athletes:
            if a.verify_private_key(key):
                matched = a
                break
        if matched:
            request.session['athlete_id'] = matched.id
            request.session['athlete_private_key'] = key
            messages.success(request, f"Welcome back, {matched.full_name}!")
            return redirect('athlete_id_card', athlete_id=matched.public_athlete_id)
        else:
            messages.error(request, "Invalid private access key. Please verify and try again.")

    return render(request, 'athletes/returning.html')

def athlete_entries_list(request):
    """
    Renders all registered athlete entries for the active championship.
    Searchable by athlete name, public ID, chest number, mobile.
    Filterable by district, event format, and status.
    """
    from django.db.models import Q
    championship = get_active_championship(request)
    q = request.GET.get('q', '').strip()
    district_filter = request.GET.get('district', '').strip()
    event_filter = request.GET.get('event_type', '').strip()
    status_filter = request.GET.get('status', '').strip()

    entries_qs = EventEntry.objects.select_related(
        'registration__athlete',
        'registration__championship',
        'event_config',
        'age_category',
        'partner_athlete'
    ).prefetch_related('sequence_items')

    if championship:
        entries_qs = entries_qs.filter(registration__championship=championship)

    if q:
        entries_qs = entries_qs.filter(
            Q(registration__athlete__first_name__icontains=q) |
            Q(registration__athlete__last_name__icontains=q) |
            Q(registration__athlete__public_athlete_id__icontains=q) |
            Q(registration__athlete__mobile__icontains=q) |
            Q(chest_number__iexact=q)
        )

    if district_filter:
        entries_qs = entries_qs.filter(registration__athlete__district=district_filter)

    if event_filter:
        entries_qs = entries_qs.filter(event_config__event_type=event_filter)

    if status_filter:
        entries_qs = entries_qs.filter(registration__status=status_filter)

    entries = entries_qs.order_by('-id')

    return render(request, 'athletes/entries_list.html', {
        'championship': championship,
        'entries': entries,
        'q': q,
        'district_filter': district_filter,
        'event_filter': event_filter,
        'status_filter': status_filter,
        'districts': TELANGANA_DISTRICTS,
        'event_types': EventType.choices,
        'statuses': RegistrationStatus.choices,
    })
