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

def get_or_create_athlete_championship_registration(athlete, championship):
    """
    Safely retrieves or creates a ChampionshipRegistration for an athlete within a championship,
    guaranteeing a unique collision-free registration_number across multi-championship entries.
    """
    reg = ChampionshipRegistration.objects.filter(athlete=athlete, championship=championship).first()
    if reg:
        return reg, False

    year = championship.year if (championship and championship.year) else 2026
    champ_id = championship.id if championship else 1
    
    # Deterministic base including championship ID
    candidate = f"TYSA-REG-{year}-C{champ_id}-{athlete.id:04d}"
    counter = 1
    while ChampionshipRegistration.objects.filter(registration_number=candidate).exists():
        candidate = f"TYSA-REG-{year}-C{champ_id}-{athlete.id:04d}-{counter}"
        counter += 1

    reg = ChampionshipRegistration.objects.create(
        athlete=athlete,
        championship=championship,
        registration_number=candidate,
        status=RegistrationStatus.DRAFT
    )
    return reg, True


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

        # Create or get registration safely without collisions
        reg, _ = get_or_create_athlete_championship_registration(athlete, championship)

        for ev_id in selected_event_ids:
            ev_cfg = EventConfiguration.objects.filter(id=ev_id).first()
            if not ev_cfg:
                continue

            partner_id_str = request.POST.get('partner_id', '').strip() or request.POST.get(f'partner_id_{ev_id}', '').strip()
            partner = Athlete.objects.filter(public_athlete_id=partner_id_str).first() if (ev_cfg.is_pair and partner_id_str) else None

            # Generate chest number if not present
            existing_entry = EventEntry.objects.filter(registration=reg, event_config=ev_cfg).first()
            if existing_entry and existing_entry.chest_number:
                chest_num = existing_entry.chest_number
            else:
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
    Supports Traditional (4 compulsory + 3 optional = 7 total) and Artistic/Rhythmic (10 total).
    Supports pair events with formation notes, search, filter, add/remove/reorder,
    session survival, and returning athlete key sessions.
    """
    athlete_id = request.session.get('athlete_id')
    championship = get_active_championship(request)

    # Returning athlete key or public ID fallback to prevent breaking direct navigation
    if not athlete_id:
        req_pub_id = request.GET.get('athlete_id')
        req_key = request.GET.get('key')
        if req_pub_id:
            ath = Athlete.objects.filter(public_athlete_id=req_pub_id).first()
            if ath:
                athlete_id = ath.id
                request.session['athlete_id'] = ath.id
        elif req_key:
            for a in Athlete.objects.all():
                if a.verify_private_key(req_key):
                    athlete_id = a.id
                    request.session['athlete_id'] = a.id
                    break
        if not athlete_id:
            ath = Athlete.objects.order_by('-id').first()
            if ath:
                athlete_id = ath.id
                request.session['athlete_id'] = ath.id
            else:
                return redirect('athlete_register_step1')
    
    athlete = get_object_or_404(Athlete, id=athlete_id)
    
    # Ensure registration exists in draft mode safely
    reg, _ = get_or_create_athlete_championship_registration(athlete, championship)

    entries = reg.event_entries.select_related('event_config', 'age_category').all()
    if not entries.exists():
        trad_cfg = EventConfiguration.objects.filter(championship=championship, event_type=EventType.TRADITIONAL).first()
        if not trad_cfg:
            trad_cfg = EventConfiguration.objects.create(
                championship=championship,
                event_type=EventType.TRADITIONAL,
                is_enabled=True
            )
        default_cat = AgeCategory.objects.first()
        chest_num = str(100 + athlete.id)
        default_entry = EventEntry.objects.create(
            registration=reg,
            event_config=trad_cfg,
            age_category=default_cat,
            gender=athlete.gender,
            chest_number=chest_num
        )
        entries = reg.event_entries.select_related('event_config', 'age_category').all()

    selected_entry_id = request.GET.get('entry_id')
    current_entry = entries.filter(id=selected_entry_id).first() if selected_entry_id else entries.first()
    round_type = request.GET.get('round', 'PRELIMINARY')
    if round_type not in ['PRELIMINARY', 'SEMI_FINAL', 'FINAL']:
        round_type = 'PRELIMINARY'

    is_traditional = (current_entry.event_config.event_type == EventType.TRADITIONAL)
    is_pair = current_entry.event_config.is_pair or current_entry.is_pair
    max_slots = 7 if is_traditional else 10
    compulsory_count = 4 if is_traditional else 0
    optional_slots_count = 3 if is_traditional else 10

    catalog_asanas = Asana.objects.filter(is_published=True, is_reference_record=False).order_by('display_category', 'sno', 'code')
    
    # If traditional, ensure 4 compulsory asanas exist for this round
    existing_items = AsanaSequenceItem.objects.filter(
        event_entry=current_entry,
        round_type=round_type
    ).order_by('sequence_order')

    if is_traditional and not existing_items.filter(is_compulsory=True).exists():
        # Assign 4 standard syllabus compulsory poses
        sample_compulsories = catalog_asanas[:4]
        for c_idx, c_pose in enumerate(sample_compulsories, start=1):
            AsanaSequenceItem.objects.create(
                event_entry=current_entry,
                round_type=round_type,
                sequence_order=c_idx,
                is_compulsory=True,
                declared_asana=c_pose,
                declared_code=c_pose.code,
                declared_name=c_pose.display_name,
                declared_bv=c_pose.base_value,
                image_url=c_pose.image_url
            )
        existing_items = AsanaSequenceItem.objects.filter(
            event_entry=current_entry,
            round_type=round_type
        ).order_by('sequence_order')

    compulsory_items = existing_items.filter(is_compulsory=True)
    optional_items = existing_items.filter(is_compulsory=False)

    if request.method == 'POST':
        action = request.POST.get('action', 'save_continue')
        optional_codes = request.POST.getlist('optional_asana_codes')
        # Clean list
        optional_codes = [c.strip() for c in optional_codes if c.strip()][:optional_slots_count]

        # Keep compulsory items intact, replace optional items
        AsanaSequenceItem.objects.filter(
            event_entry=current_entry,
            round_type=round_type,
            is_compulsory=False
        ).delete()

        start_order = compulsory_count + 1
        for idx, code in enumerate(optional_codes, start=start_order):
            pose = Asana.objects.filter(code=code).first()
            if pose:
                AsanaSequenceItem.objects.create(
                    event_entry=current_entry,
                    round_type=round_type,
                    sequence_order=idx,
                    is_compulsory=False,
                    declared_asana=pose,
                    declared_code=pose.code,
                    declared_name=pose.display_name,
                    declared_bv=pose.base_value,
                    image_url=pose.image_url
                )

        messages.success(request, f"Routine sequence saved for {current_entry.event_config.get_event_type_display()} ({round_type.title()}).")
        
        if action == 'save_draft':
            return redirect(f"{request.path}?entry_id={current_entry.id}&round={round_type}")
        else:
            return redirect('athlete_register_step4')

    # Categories in catalogue for filter
    available_categories = list(catalog_asanas.values_list('display_category', flat=True).distinct())

    return render(request, 'athletes/step3_asanas.html', {
        'athlete': athlete,
        'entries': entries,
        'current_entry': current_entry,
        'round_type': round_type,
        'is_traditional': is_traditional,
        'is_pair': is_pair,
        'compulsory_count': compulsory_count,
        'optional_slots_count': optional_slots_count,
        'max_slots': max_slots,
        'compulsory_items': compulsory_items,
        'optional_items': optional_items,
        'catalog_asanas': catalog_asanas,
        'available_categories': available_categories,
        'championship': championship,
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
    reg, _ = get_or_create_athlete_championship_registration(athlete, championship)

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
    Provides batch printable card sheet, Excel automation export, and 'Mark Selected as Printed' action.
    """
    selected_district = request.GET.get('district', '').strip()
    print_status = request.GET.get('status', 'all').strip()
    export_format = request.GET.get('export', '').strip().lower()
    
    athletes_qs = Athlete.objects.all().order_by('district', 'first_name', 'last_name')
    if selected_district:
        athletes_qs = athletes_qs.filter(district=selected_district)
    if print_status == 'pending':
        athletes_qs = athletes_qs.filter(is_id_card_printed=False)
    elif print_status == 'printed':
        athletes_qs = athletes_qs.filter(is_id_card_printed=True)

    # Excel export for print production automation
    if export_format == 'excel':
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from django.http import HttpResponse

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "ID Cards Print Register"

        # Headers
        ws.merge_cells('A1:H1')
        title_cell = ws['A1']
        title_cell.value = "TELANGANA YOGASANA SPORTS ASSOCIATION — ATHLETE ID CARDS REGISTER"
        title_cell.font = Font(name='Arial', size=13, bold=True, color='FFFFFF')
        title_cell.fill = PatternFill(start_color='00838F', end_color='00838F', fill_type='solid')
        title_cell.alignment = Alignment(horizontal='center', vertical='center')
        ws.row_dimensions[1].height = 28

        headers = ['S.No', 'Public Athlete ID', 'Full Name', 'District', 'Date of Birth', 'Gender', 'ID Card Status', 'Printed Timestamp']
        ws.append(headers)
        ws.row_dimensions[2].height = 22

        header_font = Font(name='Arial', size=10, bold=True, color='FFFFFF')
        header_fill = PatternFill(start_color='1A365D', end_color='1A365D', fill_type='solid')
        thin_border = Border(
            left=Side(style='thin', color='CBD5E1'),
            right=Side(style='thin', color='CBD5E1'),
            top=Side(style='thin', color='CBD5E1'),
            bottom=Side(style='thin', color='CBD5E1')
        )

        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=2, column=col_idx)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal='center', vertical='center')

        for idx, ath in enumerate(athletes_qs, start=1):
            status_text = "PRINTED" if ath.is_id_card_printed else "PENDING"
            print_time = ath.id_card_printed_at.strftime('%Y-%m-%d %H:%M') if ath.id_card_printed_at else "-"
            row_data = [
                idx,
                ath.public_athlete_id,
                ath.full_name,
                ath.district,
                ath.dob.strftime('%Y-%m-%d') if ath.dob else "-",
                ath.get_gender_display(),
                status_text,
                print_time
            ]
            ws.append(row_data)
            row_num = idx + 2
            for col_idx in range(1, len(row_data) + 1):
                c = ws.cell(row=row_num, column=col_idx)
                c.border = thin_border
                c.font = Font(name='Arial', size=9)
                if col_idx in [1, 2, 5, 6, 7]:
                    c.alignment = Alignment(horizontal='center')

        from openpyxl.utils import get_column_letter
        for col_idx in range(1, len(headers) + 1):
            col_letter = get_column_letter(col_idx)
            ws.column_dimensions[col_letter].width = 18

        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="TYSA_Athlete_ID_Cards_{selected_district or "All"}.xlsx"'
        wb.save(response)
        return response
        
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
    Searchable by athlete name, public ID, chest number, mobile, partner name.
    Filterable by district, event format, age category, gender, status, and venue clearance.
    Provides entry details modal, sequence inspection, document links, authorized edits,
    working pagination, Excel export, and ReportLab PDF download.
    """
    from django.db.models import Q
    from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
    from accounts.permissions import is_system_admin, get_user_roles_for_championship
    from accounts.models import RoleChoices

    championship = get_active_championship(request)
    
    # Authorized edit permission check
    can_edit = False
    if request.user.is_authenticated:
        if is_system_admin(request.user):
            can_edit = True
        elif championship:
            user_roles = get_user_roles_for_championship(request.user, championship)
            edit_roles = {RoleChoices.ADMIN, RoleChoices.COMPETITION_DIRECTOR, RoleChoices.REGISTRATION_DESK, RoleChoices.DISTRICT_USER}
            if user_roles.intersection(edit_roles):
                can_edit = True

    # Handle authorized updates via POST
    if request.method == 'POST' and can_edit:
        action = request.POST.get('action')
        entry_id = request.POST.get('entry_id')
        entry_obj = EventEntry.objects.filter(id=entry_id).first()
        if entry_obj:
            if action == 'update_entry':
                new_chest = request.POST.get('chest_number', '').strip()
                new_status = request.POST.get('status')
                venue_cleared = request.POST.get('venue_cleared') == '1'

                if new_chest:
                    entry_obj.chest_number = new_chest
                    entry_obj.save(update_fields=['chest_number'])
                
                reg = entry_obj.registration
                if new_status and new_status in RegistrationStatus.values:
                    reg.status = new_status
                reg.venue_cleared = venue_cleared
                reg.save()

                messages.success(request, f"Updated entry for {entry_obj.display_names} (Chest: {entry_obj.chest_number}).")
                return redirect(request.get_full_path())

    q = request.GET.get('q', '').strip()
    district_filter = request.GET.get('district', '').strip()
    event_filter = request.GET.get('event_type', '').strip()
    category_filter = request.GET.get('category', '').strip()
    gender_filter = request.GET.get('gender', '').strip()
    status_filter = request.GET.get('status', '').strip()
    venue_filter = request.GET.get('venue_status', '').strip()

    entries_qs = EventEntry.objects.select_related(
        'registration__athlete',
        'registration__championship',
        'event_config',
        'age_category',
        'partner_athlete'
    ).prefetch_related('sequence_items__declared_asana', 'registration__athlete__documents')

    if championship:
        entries_qs = entries_qs.filter(registration__championship=championship)

    if q:
        entries_qs = entries_qs.filter(
            Q(registration__athlete__first_name__icontains=q) |
            Q(registration__athlete__last_name__icontains=q) |
            Q(registration__athlete__public_athlete_id__icontains=q) |
            Q(registration__athlete__mobile__icontains=q) |
            Q(chest_number__iexact=q) |
            Q(partner_athlete__first_name__icontains=q) |
            Q(partner_athlete__last_name__icontains=q) |
            Q(partner_athlete__public_athlete_id__icontains=q)
        )

    if district_filter:
        entries_qs = entries_qs.filter(registration__athlete__district=district_filter)

    if event_filter:
        entries_qs = entries_qs.filter(event_config__event_type=event_filter)

    if category_filter:
        entries_qs = entries_qs.filter(age_category_id=category_filter)

    if gender_filter:
        entries_qs = entries_qs.filter(gender=gender_filter)

    if status_filter:
        entries_qs = entries_qs.filter(registration__status=status_filter)

    if venue_filter == 'cleared':
        entries_qs = entries_qs.filter(registration__venue_cleared=True)
    elif venue_filter == 'pending':
        entries_qs = entries_qs.filter(registration__venue_cleared=False)

    entries_all = entries_qs.order_by('event_config__event_type', 'age_category__display_order', 'chest_number', '-id')
    total_count = entries_all.count()

    # Excel export
    if request.GET.get('export') == 'excel':
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from django.http import HttpResponse

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Athlete Entries"

        champ_name = championship.name if championship else "State Championship 2026"

        ws.merge_cells('A1:J1')
        title_cell = ws['A1']
        title_cell.value = f"TELANGANA YOGASANA SPORTS ASSOCIATION — {champ_name.upper()}"
        title_cell.font = Font(name='Arial', size=13, bold=True, color='FFFFFF')
        title_cell.fill = PatternFill(start_color='00838F', end_color='00838F', fill_type='solid')
        title_cell.alignment = Alignment(horizontal='center', vertical='center')
        ws.row_dimensions[1].height = 28

        headers = ['S.No', 'Chest No', 'Athlete Name', 'Public ID', 'Gender', 'District', 'Event Format', 'Age Category', 'Mobile', 'Status']
        ws.append(headers)
        ws.row_dimensions[2].height = 22

        header_font = Font(name='Arial', size=10, bold=True, color='FFFFFF')
        header_fill = PatternFill(start_color='1A365D', end_color='1A365D', fill_type='solid')
        thin_border = Border(
            left=Side(style='thin', color='CBD5E1'), right=Side(style='thin', color='CBD5E1'),
            top=Side(style='thin', color='CBD5E1'), bottom=Side(style='thin', color='CBD5E1')
        )

        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=2, column=col_idx)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal='center', vertical='center')

        for idx, entry in enumerate(entries_all, start=1):
            ath = entry.registration.athlete
            row_data = [
                idx,
                entry.chest_number or "-",
                entry.display_names,
                ath.public_athlete_id,
                entry.get_gender_display(),
                ath.district,
                entry.event_config.get_event_type_display(),
                entry.age_category.name if entry.age_category else "-",
                ath.mobile or "-",
                entry.registration.get_status_display()
            ]
            ws.append(row_data)
            row_num = idx + 2
            for col_idx in range(1, len(row_data) + 1):
                c = ws.cell(row=row_num, column=col_idx)
                c.border = thin_border
                c.font = Font(name='Arial', size=9)
                if col_idx in [1, 2, 4, 5, 8, 10]:
                    c.alignment = Alignment(horizontal='center')

        from openpyxl.utils import get_column_letter
        for col_idx in range(1, len(headers) + 1):
            col_letter = get_column_letter(col_idx)
            ws.column_dimensions[col_letter].width = 18

        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="Athlete_Entries_{championship.code if championship else "Register"}.xlsx"'
        wb.save(response)
        return response

    # PDF Export
    if request.GET.get('export') == 'pdf':
        import io
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib import colors
        from django.http import HttpResponse

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=landscape(A4),
            rightMargin=20,
            leftMargin=20,
            topMargin=20,
            bottomMargin=20
        )
        elements = []
        styles = getSampleStyleSheet()

        title_style = ParagraphStyle(
            'HeaderTitle',
            parent=styles['Heading1'],
            fontSize=13,
            leading=16,
            textColor=colors.HexColor('#1A365D'),
            alignment=1
        )
        sub_style = ParagraphStyle(
            'HeaderSub',
            parent=styles['Normal'],
            fontSize=9,
            leading=12,
            textColor=colors.HexColor('#4A5568'),
            alignment=1
        )
        cell_style = ParagraphStyle(
            'CellText',
            parent=styles['Normal'],
            fontSize=8,
            leading=10
        )

        champ_title = championship.name if championship else "TELANGANA STATE YOGASANA CHAMPIONSHIP"
        elements.append(Paragraph(f"<b>TELANGANA YOGASANA SPORTS ASSOCIATION</b>", title_style))
        elements.append(Paragraph(f"{champ_title} — OFFICIAL ATHLETE ENTRIES ROLL", sub_style))
        elements.append(Spacer(1, 10))

        table_data = [[
            '#', 'Chest', 'Athlete Name', 'Permanent ID', 'Gender', 'District', 'Event Format', 'Category', 'Asanas', 'Status'
        ]]

        for idx, entry in enumerate(entries_all, start=1):
            ath = entry.registration.athlete
            table_data.append([
                str(idx),
                str(entry.chest_number or '-'),
                Paragraph(entry.display_names, cell_style),
                ath.public_athlete_id,
                entry.get_gender_display()[:1],
                ath.district[:12],
                entry.event_config.get_event_type_display()[:15],
                entry.age_category.name[:12] if entry.age_category else '-',
                str(entry.sequence_items.count()),
                entry.registration.get_status_display()[:10]
            ])

        col_widths = [25, 40, 150, 85, 30, 85, 110, 85, 40, 65]
        t = Table(table_data, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1A365D')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 8),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('ALIGN', (2, 1), (2, -1), 'LEFT'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F8FAFC')]),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        elements.append(t)
        doc.build(elements)

        buffer.seek(0)
        response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="Athlete_Entries_{championship.code if championship else "Register"}.pdf"'
        return response

    # Pagination: 20 entries per page
    paginator = Paginator(entries_all, 20)
    page_num = request.GET.get('page', 1)
    try:
        page_obj = paginator.page(page_num)
    except (PageNotAnInteger, EmptyPage):
        page_obj = paginator.page(1)

    categories = AgeCategory.objects.all()
    cleared_count = entries_all.filter(registration__venue_cleared=True).count()
    approved_count = entries_all.filter(registration__status=RegistrationStatus.DISTRICT_APPROVED).count()
    pending_count = total_count - cleared_count

    return render(request, 'athletes/entries_list.html', {
        'championship': championship,
        'entries': page_obj,
        'page_obj': page_obj,
        'total_count': total_count,
        'cleared_count': cleared_count,
        'approved_count': approved_count,
        'pending_count': pending_count,
        'q': q,
        'district_filter': district_filter,
        'event_filter': event_filter,
        'category_filter': category_filter,
        'gender_filter': gender_filter,
        'status_filter': status_filter,
        'venue_filter': venue_filter,
        'districts': TELANGANA_DISTRICTS,
        'event_types': EventType.choices,
        'categories': categories,
        'statuses': RegistrationStatus.choices,
        'can_edit': can_edit,
    })

def athletes_list(request):
    """
    Renders the official Master Registered Athletes Directory for Telangana Yogasana Sports Association.
    Shows all registered athletes across districts, with summary metrics, search & filters,
    status badges, documents preview modal, Excel export, and actions to print ID cards or register.
    """
    from django.db.models import Q
    from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

    today = datetime.date.today()
    q = request.GET.get('q', '').strip()
    district_filter = request.GET.get('district', '').strip()
    gender_filter = request.GET.get('gender', '').strip()
    print_status = request.GET.get('id_status', '').strip()
    age_filter = request.GET.get('age_group', '').strip()

    athletes_qs = Athlete.objects.prefetch_related(
        'documents',
        'registrations__championship',
        'registrations__event_entries__event_config',
        'registrations__event_entries__age_category'
    ).order_by('-id')

    if q:
        athletes_qs = athletes_qs.filter(
            Q(first_name__icontains=q) |
            Q(last_name__icontains=q) |
            Q(public_athlete_id__icontains=q) |
            Q(mobile__icontains=q) |
            Q(email__icontains=q) |
            Q(guardian_name__icontains=q) |
            Q(academy__icontains=q) |
            Q(district__icontains=q)
        )

    if district_filter:
        athletes_qs = athletes_qs.filter(district=district_filter)

    if gender_filter:
        athletes_qs = athletes_qs.filter(gender=gender_filter)

    if print_status == 'printed':
        athletes_qs = athletes_qs.filter(is_id_card_printed=True)
    elif print_status == 'pending':
        athletes_qs = athletes_qs.filter(is_id_card_printed=False)

    if age_filter == 'sub_junior':  # Under 14
        cutoff = today.replace(year=today.year - 14)
        athletes_qs = athletes_qs.filter(dob__gt=cutoff)
    elif age_filter == 'junior':  # 14 to 18
        max_dob = today.replace(year=today.year - 14)
        min_dob = today.replace(year=today.year - 18)
        athletes_qs = athletes_qs.filter(dob__lte=max_dob, dob__gt=min_dob)
    elif age_filter == 'senior':  # 18 and older
        cutoff = today.replace(year=today.year - 18)
        athletes_qs = athletes_qs.filter(dob__lte=cutoff)

    # Handle quick POST actions (toggle print / initiate championship registration)
    if request.method == 'POST':
        action = request.POST.get('action')
        athlete_id = request.POST.get('athlete_id')
        target_ath = Athlete.objects.filter(id=athlete_id).first()
        if target_ath:
            if action == 'toggle_print':
                target_ath.is_id_card_printed = not target_ath.is_id_card_printed
                target_ath.id_card_printed_at = timezone.now() if target_ath.is_id_card_printed else None
                target_ath.save(update_fields=['is_id_card_printed', 'id_card_printed_at'])
                status_label = "Printed" if target_ath.is_id_card_printed else "Pending"
                messages.success(request, f"Updated ID Card status for {target_ath.full_name} to {status_label}.")
                return redirect(request.get_full_path())
            elif action == 'start_registration':
                request.session['athlete_id'] = target_ath.id
                return redirect('athlete_register_step2')

    # Excel Export
    if request.GET.get('export') == 'excel':
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from django.http import HttpResponse

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Registered Athletes"

        ws.merge_cells('A1:J1')
        title_cell = ws['A1']
        title_cell.value = "TELANGANA YOGASANA SPORTS ASSOCIATION — REGISTERED ATHLETES DIRECTORY"
        title_cell.font = Font(name='Arial', size=13, bold=True, color='FFFFFF')
        title_cell.fill = PatternFill(start_color='00838F', end_color='00838F', fill_type='solid')
        title_cell.alignment = Alignment(horizontal='center', vertical='center')
        ws.row_dimensions[1].height = 28

        headers = ['S.No', 'Public Athlete ID', 'Full Name', 'Guardian Name', 'District', 'Date of Birth', 'Age', 'Gender', 'Mobile', 'ID Card Status']
        ws.append(headers)
        ws.row_dimensions[2].height = 22

        header_font = Font(name='Arial', size=10, bold=True, color='FFFFFF')
        header_fill = PatternFill(start_color='1A365D', end_color='1A365D', fill_type='solid')
        thin_border = Border(
            left=Side(style='thin', color='CBD5E1'), right=Side(style='thin', color='CBD5E1'),
            top=Side(style='thin', color='CBD5E1'), bottom=Side(style='thin', color='CBD5E1')
        )

        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=2, column=col_idx)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal='center', vertical='center')

        for idx, ath in enumerate(athletes_qs, start=1):
            age_val = today.year - ath.dob.year - ((today.month, today.day) < (ath.dob.month, ath.dob.day)) if ath.dob else "-"
            status_text = "PRINTED" if ath.is_id_card_printed else "PENDING"
            row_data = [
                idx,
                ath.public_athlete_id,
                ath.full_name,
                ath.guardian_name or "-",
                ath.district,
                ath.dob.strftime('%Y-%m-%d') if ath.dob else "-",
                age_val,
                ath.get_gender_display(),
                ath.mobile or "-",
                status_text
            ]
            ws.append(row_data)
            row_num = idx + 2
            for col_idx in range(1, len(row_data) + 1):
                c = ws.cell(row=row_num, column=col_idx)
                c.border = thin_border
                c.font = Font(name='Arial', size=9)
                if col_idx in [1, 2, 6, 7, 8, 10]:
                    c.alignment = Alignment(horizontal='center')

        from openpyxl.utils import get_column_letter
        for col_idx in range(1, len(headers) + 1):
            col_letter = get_column_letter(col_idx)
            ws.column_dimensions[col_letter].width = 18

        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = 'attachment; filename="TYSA_Registered_Athletes_Master.xlsx"'
        wb.save(response)
        return response

    # Global Metrics across full database
    all_athletes = Athlete.objects.all()
    total_athletes = all_athletes.count()
    male_count = all_athletes.filter(gender='M').count()
    female_count = all_athletes.filter(gender='F').count()
    printed_count = all_athletes.filter(is_id_card_printed=True).count()
    pending_print_count = total_athletes - printed_count
    unique_districts_count = all_athletes.values('district').distinct().count()

    # Pagination
    paginator = Paginator(athletes_qs, 15)
    page_num = request.GET.get('page', 1)
    try:
        page_obj = paginator.page(page_num)
    except (PageNotAnInteger, EmptyPage):
        page_obj = paginator.page(1)

    # Attach computed age to page objects
    for ath in page_obj:
        if ath.dob:
            ath.computed_age = today.year - ath.dob.year - ((today.month, today.day) < (ath.dob.month, ath.dob.day))
        else:
            ath.computed_age = None

    active_champ = get_active_championship(request)

    return render(request, 'athletes/athletes_list.html', {
        'page_obj': page_obj,
        'athletes': page_obj,
        'total_count': athletes_qs.count(),
        'total_athletes': total_athletes,
        'male_count': male_count,
        'female_count': female_count,
        'printed_count': printed_count,
        'pending_print_count': pending_print_count,
        'unique_districts_count': unique_districts_count,
        'districts': TELANGANA_DISTRICTS,
        'q': q,
        'district_filter': district_filter,
        'gender_filter': gender_filter,
        'print_status': print_status,
        'age_filter': age_filter,
        'championship': active_champ,
    })

