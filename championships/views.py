import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.urls import reverse
from .models import (
    Championship, EventConfiguration, AgeCategory, EventType,
    DistrictQuota, ChampionshipStatus, ChampionshipLogo, BrandingLogoType
)
from competition.models import JudgePanel
from registrations.models import EventEntry
from accounts.permissions import (
    admin_required, get_authorized_championships, get_user_landing_url,
    is_safe_redirect_url, is_user_authorized_for_url
)

DEFAULT_DISTRICTS = [
    'Hyderabad', 'Rangareddy', 'Medchal-Malkajgiri', 'Warangal', 
    'Karimnagar', 'Nizamabad', 'Khammam', 'Nalgonda', 'Mahabubnagar', 'Adilabad'
]

def setup_default_events_for_championship(championship):
    """
    Initializes official sporting formats with COP-compliant age brackets:
    - Traditional Yogasana: Sub-Junior, Junior, Senior, Senior A, Senior B (35–45), Senior C (45–55)
    - Artistic & Rhythmic: Sub-Junior, Junior, Senior
    """
    all_cats = {c.code: c for c in AgeCategory.objects.all()}
    traditional_cats = [all_cats[c] for c in ['SUB_JR', 'JR', 'SR', 'SR_A', 'SR_B', 'SR_C'] if c in all_cats]
    other_cats = [all_cats[c] for c in ['SUB_JR', 'JR', 'SR'] if c in all_cats]

    ev_trad, _ = EventConfiguration.objects.get_or_create(
        championship=championship,
        event_type=EventType.TRADITIONAL,
        defaults={'total_asanas': 7, 'max_batch_size': 5, 'is_enabled': True, 'allow_male': True, 'allow_female': True}
    )
    if traditional_cats and not ev_trad.age_categories.exists():
        ev_trad.age_categories.set(traditional_cats)

    for ev_type in [EventType.ARTISTIC_SINGLE, EventType.ARTISTIC_PAIR, EventType.RHYTHMIC_PAIR]:
        ev, _ = EventConfiguration.objects.get_or_create(
            championship=championship,
            event_type=ev_type,
            defaults={'total_asanas': 10, 'max_batch_size': 1, 'is_enabled': True, 'allow_male': True, 'allow_female': True}
        )
        if other_cats and not ev.age_categories.exists():
            ev.age_categories.set(other_cats)

@admin_required
def championship_list(request):
    """
    Multi-Championship Hub:
    Lists all active, upcoming, and completed championships.
    Allows creating new championships or switching the active context.
    Protected strictly for System Admin.
    """
    championships = Championship.objects.prefetch_related('registrations', 'event_configurations').all().order_by('-year', '-start_date')
    active_id = request.session.get('active_championship_id')
    
    return render(request, 'championships/list.html', {
        'championships': championships,
        'active_champ_id': active_id,
    })

@login_required
def championship_switch(request, champ_id):
    """
    Switches active championship context across the entire application.
    Enforces championship isolation: users may only switch to championships
    for which they have an active authorized role or global admin rights.
    """
    champ = get_object_or_404(Championship, id=champ_id)
    authorized = get_authorized_championships(request.user)
    if not authorized.filter(id=champ.id).exists():
        messages.error(request, f"Access denied: You are not authorized for championship '{champ.name}'.")
        return redirect(get_user_landing_url(request))

    request.session['active_championship_id'] = champ.id
    messages.success(request, f"Switched active championship to: {champ.name}")
    
    next_url = request.META.get('HTTP_REFERER')
    if next_url and is_safe_redirect_url(next_url, request.get_host()):
        if is_user_authorized_for_url(request.user, next_url, champ):
            return redirect(next_url)
    return redirect(get_user_landing_url(request))

@admin_required
def championship_create(request):
    """
    Unified Championship Creation & Setup:
    Creates a brand new championship and launches directly into the 5-step setup wizard.
    Step 1: Basics, Dates, Venue, Cutoff Date & Header Snippet.
    Rules version input is removed per specification.
    """
    all_categories = AgeCategory.objects.all().order_by('display_order', 'min_age')

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        code = request.POST.get('code', '').strip()
        year = int(request.POST.get('year', datetime.date.today().year))
        venue_name = request.POST.get('venue_name', '').strip()
        venue_city = request.POST.get('venue_city', 'Hyderabad').strip()
        venue_address = request.POST.get('venue_address', '').strip()
        start_date = request.POST.get('start_date')
        end_date = request.POST.get('end_date')
        reg_start = request.POST.get('registration_start')
        reg_end = request.POST.get('registration_end')
        cutoff_date = request.POST.get('age_cutoff_date')

        if not code:
            code = f"TYSA-{year}-{int(datetime.datetime.now().timestamp()) % 10000}"

        championship = Championship.objects.create(
            name=name,
            code=code,
            year=year,
            venue_name=venue_name,
            venue_city=venue_city,
            venue_address=venue_address,
            start_date=start_date,
            end_date=end_date,
            registration_start=reg_start,
            registration_end=reg_end,
            age_cutoff_date=cutoff_date,
            status=ChampionshipStatus.IN_PROGRESS
        )

        if 'header_snippet' in request.FILES:
            championship.header_snippet = request.FILES['header_snippet']
            championship.save(update_fields=['header_snippet'])

        # Auto-initialize 4 standard events with official sporting age brackets
        setup_default_events_for_championship(championship)

        # Auto-initialize district quotas
        for dist in DEFAULT_DISTRICTS:
            DistrictQuota.objects.get_or_create(
                championship=championship,
                district_name=dist,
                defaults={'quota_limit': 15, 'quota_used': 0}
            )

        request.session['active_championship_id'] = championship.id
        messages.success(request, f"Championship '{championship.name}' created! Proceeding to Events & Divisions setup.")
        return redirect(f"{reverse('championship_setup', kwargs={'champ_id': championship.id})}?step=events")

    return render(request, 'championships/setup.html', {
        'championship': None,
        'is_create': True,
        'setup_url_base': reverse('championship_create'),
        'step': 'basics',
        'all_categories': all_categories,
        'event_configs': [],
        'quotas': [],
        'scheduled_panels': [],
        'stages_list': ['Stage A', 'Stage B', 'Stage C'],
        'sessions_list': ['Morning Session (09:00 - 13:00)', 'Afternoon Session (14:00 - 18:00)', 'Evening Finals (18:30 - 21:00)'],
    })

@admin_required
def championship_setup(request, champ_id):
    """
    Unified 6-Step Championship Setup Wizard:
    Step 1: Basics (Name, Dates, Venue, Cutoff Date, Header Snippet)
    Step 2: Events & Divisions (Configure 4 official formats, male/female division, rounds, quotas)
    Step 3: Rules & Quotas (Rules edition, age categories management, district quotas)
    Step 4: Venue/Stages & Schedule (Stages, sessions, and time slots)
    Step 5: Branding/Templates (Logo positioning, layout coordinates, print preview)
    Step 6: Review & Signatories (Verify COP, configure authorized signatories for Master Rolls & Results)
    """
    championship = get_object_or_404(Championship, id=champ_id)
    step = request.GET.get('step', 'basics')
    if step == 'categories':
        step = 'rules'
    if step not in ['basics', 'events', 'rules', 'schedule', 'branding', 'review']:
        step = 'basics'

    # Ensure 4 default events exist with proper COP age categories
    setup_default_events_for_championship(championship)

    # Ensure default quotas exist
    for dist in DEFAULT_DISTRICTS:
        DistrictQuota.objects.get_or_create(
            championship=championship,
            district_name=dist,
            defaults={'quota_limit': 15, 'quota_used': 0}
        )

    event_configs = championship.event_configurations.prefetch_related('age_categories').all()
    all_categories = AgeCategory.objects.all().order_by('display_order')
    quotas = championship.district_quotas.all()
    scheduled_panels = championship.panels.select_related('event_config', 'age_category').order_by('stage_name', 'scheduled_start')

    if request.method == 'POST':
        action = request.POST.get('action')

        # 0. Update Basic Details & Header Snippet (Step 1: Basics)
        if action == 'update_basics':
            championship.name = request.POST.get('name', championship.name).strip()
            championship.code = request.POST.get('code', championship.code).strip()
            championship.year = int(request.POST.get('year', championship.year))
            championship.venue_name = request.POST.get('venue_name', championship.venue_name).strip()
            championship.venue_city = request.POST.get('venue_city', championship.venue_city).strip()
            championship.venue_address = request.POST.get('venue_address', championship.venue_address).strip()
            
            s_date = request.POST.get('start_date')
            e_date = request.POST.get('end_date')
            r_start = request.POST.get('registration_start')
            r_end = request.POST.get('registration_end')
            c_date = request.POST.get('age_cutoff_date')
            
            if s_date: championship.start_date = s_date
            if e_date: championship.end_date = e_date
            if r_start: championship.registration_start = r_start
            if r_end: championship.registration_end = r_end
            if c_date: championship.age_cutoff_date = c_date

            if 'header_snippet' in request.FILES:
                championship.header_snippet = request.FILES['header_snippet']
            elif request.POST.get('clear_header_snippet') == '1':
                championship.header_snippet = None

            championship.save()
            messages.success(request, f"Basic details and header updated for '{championship.name}'.")
            return redirect(f"{reverse('championship_setup', kwargs={'champ_id': championship.id})}?step=events")

        # 1. Update Event Configuration (Step 2: Events & Divisions)
        elif action == 'update_event':
            event_id = request.POST.get('event_config_id')
            ev_cfg = get_object_or_404(EventConfiguration, id=event_id, championship=championship)
            
            ev_cfg.is_enabled = ('is_enabled' in request.POST)
            ev_cfg.allow_male = ('allow_male' in request.POST)
            ev_cfg.allow_female = ('allow_female' in request.POST)
            
            # Competition rounds enabled for this event
            ev_cfg.has_preliminary = ('has_preliminary' in request.POST)
            ev_cfg.has_semi_final = ('has_semi_final' in request.POST)
            ev_cfg.has_final = ('has_final' in request.POST)

            # Rules per event
            if 'total_asanas' in request.POST:
                ev_cfg.total_asanas = int(request.POST.get('total_asanas', 7))
            if 'max_batch_size' in request.POST:
                # Artistic Single, Artistic Pair, and Rhythmic Pair are strictly 1 athlete/pair at a time
                if ev_cfg.event_type in (EventType.ARTISTIC_SINGLE, EventType.ARTISTIC_PAIR, EventType.RHYTHMIC_PAIR):
                    ev_cfg.max_batch_size = 1
                else:
                    ev_cfg.max_batch_size = int(request.POST.get('max_batch_size', 5))
            if 'prelim_compulsory_count' in request.POST:
                ev_cfg.prelim_compulsory_count = int(request.POST.get('prelim_compulsory_count', 4))
            if 'prelim_optional_count' in request.POST:
                ev_cfg.prelim_optional_count = int(request.POST.get('prelim_optional_count', 3))
            if 'final_compulsory_count' in request.POST:
                ev_cfg.final_compulsory_count = int(request.POST.get('final_compulsory_count', 2))
            if 'final_optional_count' in request.POST:
                ev_cfg.final_optional_count = int(request.POST.get('final_optional_count', 5))

            selected_cat_ids = request.POST.getlist('age_categories')
            ev_cfg.age_categories.set(selected_cat_ids)
            ev_cfg.save()
            
            messages.success(request, f"Updated divisions, rounds, and rules for {ev_cfg.get_event_type_display()}.")
            return redirect(f"{reverse('championship_setup', kwargs={'champ_id': championship.id})}?step=events")

        # 2. Add Age Category (Step 3: Rules & Quotas)
        elif action == 'add_category':
            name = request.POST.get('name', '').strip()
            code = request.POST.get('code', '').strip().upper()
            min_age = request.POST.get('min_age')
            max_age = request.POST.get('max_age')
            display_order = request.POST.get('display_order', 1)

            if not code:
                code = name.upper().replace(' ', '_')[:20]

            if name and min_age and max_age:
                try:
                    cat = AgeCategory.objects.create(
                        name=name,
                        code=code,
                        min_age=int(min_age),
                        max_age=int(max_age),
                        display_order=int(display_order) if display_order else 1
                    )
                    for ev in event_configs:
                        ev.age_categories.add(cat)
                    messages.success(request, f"Age Category '{cat.name}' ({cat.min_age}–{cat.max_age} yrs) added successfully.")
                except Exception as e:
                    messages.error(request, f"Could not create age category: {e}")
            else:
                messages.error(request, "Please provide Name, Min Age, and Max Age.")
            return redirect(f"{reverse('championship_setup', kwargs={'champ_id': championship.id})}?step=rules")

        # 3. Delete / Remove Age Category
        elif action == 'delete_category':
            cat_id = request.POST.get('category_id')
            cat = get_object_or_404(AgeCategory, id=cat_id)
            if EventEntry.objects.filter(age_category=cat).exists():
                messages.error(request, f"Cannot remove '{cat.name}': Athletes are already registered in this category.")
            else:
                cat_name = cat.name
                cat.delete()
                messages.success(request, f"Age Category '{cat_name}' removed successfully.")
            return redirect(f"{reverse('championship_setup', kwargs={'champ_id': championship.id})}?step=rules")

        # 4. Update Categories & Rules Header
        elif action == 'update_categories_settings':
            cutoff = request.POST.get('age_cutoff_date')
            rules_ed = request.POST.get('rules_edition')
            is_verified = ('is_rules_preset_verified' in request.POST)
            if cutoff:
                championship.age_cutoff_date = cutoff
            if rules_ed:
                championship.rules_edition = rules_ed
            championship.is_rules_preset_verified = is_verified
            championship.save()
            messages.success(request, "Updated Age Cutoff Date, Rules Edition & Verification Status.")
            return redirect(f"{reverse('championship_setup', kwargs={'champ_id': championship.id})}?step=rules")

        # 5. Update District Quota
        elif action == 'update_quota':
            dist_id = request.POST.get('quota_id')
            quota_limit = request.POST.get('quota_limit')
            dq = get_object_or_404(DistrictQuota, id=dist_id, championship=championship)
            dq.quota_limit = int(quota_limit)
            dq.save()
            messages.success(request, f"Quota for {dq.district_name} set to {dq.quota_limit}.")
            return redirect(f"{reverse('championship_setup', kwargs={'champ_id': championship.id})}?step=rules")

        # 6. Add Schedule Slot (Step 4: Schedule)
        elif action == 'add_schedule_slot':
            ev_id = request.POST.get('event_config_id')
            ev_cfg = get_object_or_404(EventConfiguration, id=ev_id, championship=championship)
            stage_name = request.POST.get('stage_name', 'Stage A').strip()
            session_name = request.POST.get('session_name', 'Morning Session').strip()
            round_type = request.POST.get('round_type', 'PRELIMINARY')
            cat_id = request.POST.get('age_category_id')
            gender = request.POST.get('gender', 'M')
            sched_start = request.POST.get('scheduled_start') or None
            sched_end = request.POST.get('scheduled_end') or None

            age_cat = AgeCategory.objects.filter(id=cat_id).first() if cat_id else None

            panel = JudgePanel.objects.create(
                championship=championship,
                event_config=ev_cfg,
                stage_name=stage_name,
                session_name=session_name,
                round_type=round_type,
                age_category=age_cat,
                gender=gender,
                scheduled_start=sched_start,
                scheduled_end=sched_end,
                is_active=True
            )
            messages.success(request, f"Added schedule slot for {ev_cfg.get_event_type_display()} on {stage_name} ({session_name}).")
            return redirect(f"{reverse('championship_setup', kwargs={'champ_id': championship.id})}?step=schedule")

        # 7. Delete Schedule Slot
        elif action == 'delete_schedule_slot':
            panel_id = request.POST.get('panel_id')
            JudgePanel.objects.filter(id=panel_id, championship=championship).delete()
            messages.success(request, "Schedule slot removed.")
            return redirect(f"{reverse('championship_setup', kwargs={'champ_id': championship.id})}?step=schedule")

        # 8. Publish / Verify Championship (Step 6: Review)
        elif action == 'publish_championship':
            is_verified = ('is_rules_preset_verified' in request.POST)
            championship.is_rules_preset_verified = is_verified
            target_status = request.POST.get('status', ChampionshipStatus.PUBLISHED)
            championship.status = target_status
            championship.save()
            messages.success(request, f"Championship status updated to: {championship.get_status_display()}!")
            return redirect(f"{reverse('championship_setup', kwargs={'champ_id': championship.id})}?step=review")

        # 8b. Update Authorized Signatories for Master Rolls & Results
        elif action == 'update_signatories':
            championship.master_roll_signatory_1 = request.POST.get('master_roll_signatory_1', '').strip()
            championship.master_roll_signatory_2 = request.POST.get('master_roll_signatory_2', '').strip()
            championship.master_roll_signatory_3 = request.POST.get('master_roll_signatory_3', '').strip()
            championship.master_roll_signatory_4 = request.POST.get('master_roll_signatory_4', '').strip()
            championship.results_signatory_1 = request.POST.get('results_signatory_1', '').strip()
            championship.results_signatory_2 = request.POST.get('results_signatory_2', '').strip()
            championship.results_signatory_3 = request.POST.get('results_signatory_3', '').strip()
            championship.results_signatory_4 = request.POST.get('results_signatory_4', '').strip()
            championship.save()
            messages.success(request, "Authorized signatories for Master Rolls and Results saved successfully.")
            return redirect(f"{reverse('championship_setup', kwargs={'champ_id': championship.id})}?step=review")

    stages_list = ['Stage A', 'Stage B', 'Stage C']
    sessions_list = ['Morning Session (09:00 - 13:00)', 'Afternoon Session (14:00 - 18:00)', 'Evening Finals (18:30 - 21:00)']
    setup_url_base = reverse('championship_setup', kwargs={'champ_id': championship.id})
    branding_logos = championship.branding_logos.all().order_by('display_order', 'id')

    return render(request, 'championships/setup.html', {
        'championship': championship,
        'setup_url_base': setup_url_base,
        'is_create': False,
        'step': step,
        'event_configs': event_configs,
        'all_categories': all_categories,
        'quotas': quotas,
        'scheduled_panels': scheduled_panels,
        'stages_list': stages_list,
        'sessions_list': sessions_list,
        'branding_logos': branding_logos,
    })

@admin_required
def championship_branding(request, champ_id):
    """
    Dedicated Championship Branding & Interactive Multi-Logo Editor:
    - Association, National Body (NYSF), Organizing District, and Sponsor logos.
    - Drag & drop repositioning with percentage coordinates (pos_x, pos_y).
    - Aspect-ratio locked proportional resizing (width, height).
    - Numeric input controls for exact positioning & dimensions.
    - Alignment (left, center, right), layering (z-index), and ordering.
    - Default master-roll layout: State Assoc (left), NYSF (centre), Dist (right), Sponsors (gaps).
    - Versioned layout persistence in championship.branding_layout & branding_version.
    - Live print dimension preview (A4 Portrait & Landscape).
    """
    import json
    championship = get_object_or_404(Championship, id=champ_id)
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'save_layout':
            # Save layout coordinates and versioning
            layout_data_raw = request.POST.get('layout_data', '{}')
            try:
                layout_data = json.loads(layout_data_raw)
            except Exception:
                layout_data = {}

            # Update individual logos
            for logo in championship.branding_logos.all():
                logo_str_id = str(logo.id)
                if logo_str_id in layout_data:
                    c = layout_data[logo_str_id]
                    logo.pos_x = float(c.get('x', logo.pos_x))
                    logo.pos_y = float(c.get('y', logo.pos_y))
                    logo.width = float(c.get('width', logo.width))
                    logo.height = float(c.get('height', logo.height))
                    logo.z_index = int(c.get('z_index', logo.z_index))
                    logo.lock_aspect_ratio = bool(c.get('lock_aspect_ratio', logo.lock_aspect_ratio))
                    logo.alignment = str(c.get('alignment', logo.alignment))
                    logo.save(update_fields=['pos_x', 'pos_y', 'width', 'height', 'z_index', 'lock_aspect_ratio', 'alignment'])

            championship.branding_layout = layout_data
            championship.branding_version += 1
            championship.save(update_fields=['branding_layout', 'branding_version'])
            messages.success(request, f"Branding layout version {championship.branding_version} saved successfully!")
            return redirect('championship_branding', champ_id=championship.id)

        elif action == 'reset_default_layout':
            # Apply default master-roll layout arrangement:
            # State Association at left, NYSF National Body in centre, Organizing District at right, Sponsors in gaps
            logos = list(championship.branding_logos.all())
            for logo in logos:
                if logo.logo_type == BrandingLogoType.STATE_ASSOCIATION:
                    logo.pos_x, logo.pos_y, logo.width, logo.height, logo.z_index, logo.alignment = 2.0, 10.0, 110.0, 75.0, 2, 'left'
                elif logo.logo_type == BrandingLogoType.FEDERATION:
                    logo.pos_x, logo.pos_y, logo.width, logo.height, logo.z_index, logo.alignment = 43.0, 5.0, 130.0, 80.0, 3, 'center'
                elif logo.logo_type == BrandingLogoType.ORGANIZING_DISTRICT:
                    logo.pos_x, logo.pos_y, logo.width, logo.height, logo.z_index, logo.alignment = 86.0, 10.0, 110.0, 75.0, 2, 'right'
                elif logo.logo_type == BrandingLogoType.SPONSOR:
                    logo.pos_x, logo.pos_y, logo.width, logo.height, logo.z_index, logo.alignment = 24.0, 15.0, 85.0, 55.0, 1, 'center'
                else:
                    logo.pos_x, logo.pos_y, logo.width, logo.height, logo.z_index, logo.alignment = 65.0, 15.0, 85.0, 55.0, 1, 'center'
                logo.save()

            championship.branding_version += 1
            championship.save(update_fields=['branding_version'])
            messages.success(request, f"Reset branding to official Master-Roll default arrangement (v{championship.branding_version})!")
            return redirect('championship_branding', champ_id=championship.id)

        elif action == 'update_branding':
            if 'logo' in request.FILES:
                championship.logo = request.FILES['logo']
            if 'header_snippet' in request.FILES:
                championship.header_snippet = request.FILES['header_snippet']
            elif request.POST.get('clear_header_snippet') == '1':
                championship.header_snippet = None
            
            name = request.POST.get('name', '').strip()
            if name: championship.name = name
            venue_name = request.POST.get('venue_name', '').strip()
            if venue_name: championship.venue_name = venue_name
            venue_city = request.POST.get('venue_city', '').strip()
            if venue_city: championship.venue_city = venue_city
            venue_address = request.POST.get('venue_address', '').strip()
            championship.venue_address = venue_address
            
            championship.save()
            messages.success(request, f"Championship branding assets updated for {championship.name}!")
            return redirect('championship_branding', champ_id=championship.id)

        elif action == 'add_logo':
            logo_type = request.POST.get('logo_type', BrandingLogoType.SPONSOR)
            title = request.POST.get('title', '').strip() or "Brand Logo"
            show_on_prints = ('show_on_prints' in request.POST)
            img = request.FILES.get('image')

            pos_x = float(request.POST.get('pos_x', 50.0))
            pos_y = float(request.POST.get('pos_y', 10.0))
            width = float(request.POST.get('width', 110.0))
            height = float(request.POST.get('height', 75.0))
            alignment = request.POST.get('alignment', 'center')

            if img:
                logo_count = championship.branding_logos.count()
                if logo_count >= 12:
                    messages.error(request, "Maximum of 12 branding logos reached for this championship.")
                else:
                    ChampionshipLogo.objects.create(
                        championship=championship,
                        logo_type=logo_type,
                        title=title,
                        image=img,
                        show_on_prints=show_on_prints,
                        display_order=logo_count + 1,
                        pos_x=pos_x,
                        pos_y=pos_y,
                        width=width,
                        height=height,
                        alignment=alignment
                    )
                    championship.branding_version += 1
                    championship.save(update_fields=['branding_version'])
                    messages.success(request, f"Added {title} ({logo_type}) to championship branding!")
            else:
                messages.error(request, "Please choose an image file for the logo.")
            return redirect('championship_branding', champ_id=championship.id)

        elif action == 'delete_logo':
            logo_id = request.POST.get('logo_id')
            logo_obj = get_object_or_404(ChampionshipLogo, id=logo_id, championship=championship)
            title = logo_obj.title
            logo_obj.delete()
            championship.branding_version += 1
            championship.save(update_fields=['branding_version'])
            messages.info(request, f"Removed logo '{title}'.")
            return redirect('championship_branding', champ_id=championship.id)

    branding_logos = championship.branding_logos.all().order_by('display_order', 'id')
    state_logo = branding_logos.filter(logo_type=BrandingLogoType.STATE_ASSOCIATION).first()
    district_logo = branding_logos.filter(logo_type=BrandingLogoType.ORGANIZING_DISTRICT).first()
    fed_logo = branding_logos.filter(logo_type=BrandingLogoType.FEDERATION).first()
    other_logos = branding_logos.exclude(logo_type__in=[
        BrandingLogoType.STATE_ASSOCIATION, 
        BrandingLogoType.ORGANIZING_DISTRICT,
        BrandingLogoType.FEDERATION
    ])

    return render(request, 'championships/branding.html', {
        'championship': championship,
        'branding_logos': branding_logos,
        'state_logo': state_logo,
        'district_logo': district_logo,
        'fed_logo': fed_logo,
        'other_logos': other_logos,
        'logo_types': BrandingLogoType.choices,
    })
