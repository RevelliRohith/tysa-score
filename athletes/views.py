import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import JsonResponse
from .models import Athlete, AthleteDocument, generate_private_key, generate_athlete_id
from championships.models import Championship, EventConfiguration, AgeCategory, EventType
from registrations.models import ChampionshipRegistration, EventEntry, RegistrationStatus, AsanaSequenceItem
from catalogue.models import Asana
from accounts.permissions import get_active_championship

def athlete_register_step1(request):
    """
    Implements 05_Athlete_Personal_Details.png:
    Step 1: Personal Details, DOB, Gender, District, Photo, Guardian Name.
    """
    championship = get_active_championship(request)
    districts = [
        'Hyderabad', 'Rangareddy', 'Medchal-Malkajgiri', 'Warangal', 
        'Karimnagar', 'Nizamabad', 'Khammam', 'Nalgonda', 'Mahabubnagar', 'Adilabad'
    ]

    # Check if athlete is in an active session
    athlete_id = request.session.get('athlete_id')
    athlete = Athlete.objects.filter(id=athlete_id).first() if athlete_id else None

    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        dob_str = request.POST.get('dob')
        gender = request.POST.get('gender')
        guardian_name = request.POST.get('guardian_name', '').strip()
        district = request.POST.get('district')
        mobile = request.POST.get('mobile', '').strip()
        email = request.POST.get('email', '').strip()
        academy = request.POST.get('academy', '').strip()
        
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
            raw_key = athlete.set_private_key()
            athlete.save()
            request.session['athlete_id'] = athlete.id
            request.session['athlete_private_key'] = raw_key

        return redirect('athlete_register_step2')

    return render(request, 'athletes/step1_personal.html', {
        'athlete': athlete,
        'districts': districts,
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
        if not selected_event_ids:
            messages.error(request, "Please select at least one event to participate.")
            return redirect('athlete_register_step2')

        # Create or get registration
        reg_num = f"TYSA-REG-{championship.year}-{athlete.id:04d}"
        reg, _ = ChampionshipRegistration.objects.get_or_create(
            athlete=athlete,
            championship=championship,
            defaults={'registration_number': reg_num, 'status': RegistrationStatus.DRAFT}
        )

        for ev_id in selected_event_ids:
            ev_cfg = EventConfiguration.objects.get(id=ev_id)
            partner_id_str = request.POST.get(f'partner_id_{ev_id}', '').strip()
            partner = Athlete.objects.filter(public_athlete_id=partner_id_str).first() if partner_id_str else None

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
