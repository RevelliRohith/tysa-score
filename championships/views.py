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
    Implements 34_Championship_Basic_Details.png:
    Creates a brand new independent championship without overwriting existing ones.
    Configures basic details, dates, venue, registration period, and events.
    Protected strictly for System Admin.
    """
    edit_id = request.GET.get('edit_id')
    championship = None
    if edit_id:
        championship = get_object_or_404(Championship, id=edit_id)

    all_categories = AgeCategory.objects.all().order_by('display_order')

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        code = request.POST.get('code', '').strip()
        year = int(request.POST.get('year', datetime.date.today().year))
        venue_name = request.POST.get('venue_name', '').strip()
        venue_city = request.POST.get('venue_city', 'Hyderabad').strip()
        start_date = request.POST.get('start_date')
        end_date = request.POST.get('end_date')
        reg_start = request.POST.get('registration_start')
        reg_end = request.POST.get('registration_end')
        cutoff_date = request.POST.get('age_cutoff_date')
        rules_edition = request.POST.get('rules_edition', 'Yogasana Bharat COP & Circular 2024–2026')

        if not code:
            code = f"TYSA-{year}-{int(datetime.datetime.now().timestamp()) % 10000}"

        if championship:
            # Edit existing championship
            championship.name = name
            championship.code = code
            championship.year = year
            championship.venue_name = venue_name
            championship.venue_city = venue_city
            championship.start_date = start_date
            championship.end_date = end_date
            championship.registration_start = reg_start
            championship.registration_end = reg_end
            championship.age_cutoff_date = cutoff_date
            championship.rules_edition = rules_edition
            championship.save()
            messages.success(request, f"Championship '{championship.name}' updated successfully.")
        else:
            # Create a brand new championship record!
            championship = Championship.objects.create(
                name=name,
                code=code,
                year=year,
                venue_name=venue_name,
                venue_city=venue_city,
                start_date=start_date,
                end_date=end_date,
                registration_start=reg_start,
                registration_end=reg_end,
                age_cutoff_date=cutoff_date,
                rules_edition=rules_edition,
                status=ChampionshipStatus.IN_PROGRESS
            )

            # Auto-initialize 4 standard events
            default_events = [
                (EventType.TRADITIONAL, 7, 5),
                (EventType.ARTISTIC_SINGLE, 10, 1),
                (EventType.ARTISTIC_PAIR, 10, 1),
                (EventType.RHYTHMIC_PAIR, 10, 1),
            ]
            for ev_type, total_a, max_b in default_events:
                ev_cfg, _ = EventConfiguration.objects.get_or_create(
                    championship=championship,
                    event_type=ev_type,
                    defaults={
                        'total_asanas': total_a,
                        'max_batch_size': max_b,
                        'is_enabled': True,
                        'allow_male': True,
                        'allow_female': True,
                    }
                )
                if all_categories.exists():
                    ev_cfg.age_categories.set(all_categories)

            # Auto-initialize district quotas
            for dist in DEFAULT_DISTRICTS:
                DistrictQuota.objects.get_or_create(
                    championship=championship,
                    district_name=dist,
                    defaults={'quota_limit': 15, 'quota_used': 0}
                )

            messages.success(request, f"New championship '{name}' created successfully with standard events and quotas!")

        # Set as active championship in session
        request.session['active_championship_id'] = championship.id
        return redirect('championship_setup', champ_id=championship.id)

    return render(request, 'championships/basic_details.html', {
        'championship': championship,
        'all_categories': all_categories,
    })

@admin_required
def championship_setup(request, champ_id):
    """
    Complete 5-Step Championship Setup Wizard:
    Step 1: Basics (Dates, Venue, Cutoff Date) -> championship_create
    Step 2: Categories & Gender (Add/remove age categories, divisions, quotas)
    Step 3: Events (Configure 4 official sporting formats, asanas, batch size)
    Step 4: Schedule (Configure stages, sessions, and time slots)
    Step 5: Review & Publish (Verify COP compliance and publish championship)
    """
    championship = get_object_or_404(Championship, id=champ_id)
    step = request.GET.get('step', 'categories')
    if step not in ['categories', 'events', 'schedule', 'review']:
        step = 'categories'

    # Ensure 4 default events exist
    default_events = [
        (EventType.TRADITIONAL, 7, 5),
        (EventType.ARTISTIC_SINGLE, 10, 1),
        (EventType.ARTISTIC_PAIR, 10, 1),
        (EventType.RHYTHMIC_PAIR, 10, 1),
    ]
    all_categories = AgeCategory.objects.all().order_by('display_order', 'min_age')
    for ev_type, total_a, max_b in default_events:
        ev_cfg, created = EventConfiguration.objects.get_or_create(
            championship=championship,
            event_type=ev_type,
            defaults={
                'total_asanas': total_a,
                'max_batch_size': max_b,
                'is_enabled': True,
                'allow_male': True,
                'allow_female': True,
            }
        )
        if created and all_categories.exists():
            ev_cfg.age_categories.set(all_categories)

    # Ensure default quotas exist
    for dist in DEFAULT_DISTRICTS:
        DistrictQuota.objects.get_or_create(
            championship=championship,
            district_name=dist,
            defaults={'quota_limit': 15, 'quota_used': 0}
        )

    event_configs = championship.event_configurations.prefetch_related('age_categories').all()
    quotas = championship.district_quotas.all()
    scheduled_panels = championship.panels.select_related('event_config', 'age_category').order_by('stage_name', 'scheduled_start')

    if request.method == 'POST':
        action = request.POST.get('action')

        # 1. Add Age Category
        if action == 'add_category':
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
                    # Automatically attach new category to all championship events
                    for ev in event_configs:
                        ev.age_categories.add(cat)
                    messages.success(request, f"Age Category '{cat.name}' ({cat.min_age}–{cat.max_age} yrs) added successfully.")
                except Exception as e:
                    messages.error(request, f"Could not create age category: {e}")
            else:
                messages.error(request, "Please provide Name, Min Age, and Max Age.")
            return redirect(f"{reverse('championship_setup', kwargs={'champ_id': championship.id})}?step=categories")

        # 2. Delete / Remove Age Category
        elif action == 'delete_category':
            cat_id = request.POST.get('category_id')
            cat = get_object_or_404(AgeCategory, id=cat_id)
            if EventEntry.objects.filter(age_category=cat).exists():
                messages.error(request, f"Cannot remove '{cat.name}': Athletes are already registered in this category.")
            else:
                cat_name = cat.name
                cat.delete()
                messages.success(request, f"Age Category '{cat_name}' removed successfully.")
            return redirect(f"{reverse('championship_setup', kwargs={'champ_id': championship.id})}?step=categories")

        # 3. Update Categories & Rules Header
        elif action == 'update_categories_settings':
            cutoff = request.POST.get('age_cutoff_date')
            rules_ed = request.POST.get('rules_edition')
            if cutoff:
                championship.age_cutoff_date = cutoff
            if rules_ed:
                championship.rules_edition = rules_ed
            championship.save()
            messages.success(request, "Updated Age Cutoff Date & Rules Edition.")
            return redirect(f"{reverse('championship_setup', kwargs={'champ_id': championship.id})}?step=categories")

        # 4. Update Event Configuration (Step 3: Events)
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

            selected_cat_ids = request.POST.getlist('age_categories')
            ev_cfg.age_categories.set(selected_cat_ids)
            ev_cfg.save()
            
            messages.success(request, f"Updated rounds and divisions for {ev_cfg.get_event_type_display()}.")
            return redirect(f"{reverse('championship_setup', kwargs={'champ_id': championship.id})}?step=events")

        # 5. Add Schedule Slot (Step 4: Schedule)
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

        # 6. Delete Schedule Slot
        elif action == 'delete_schedule_slot':
            panel_id = request.POST.get('panel_id')
            JudgePanel.objects.filter(id=panel_id, championship=championship).delete()
            messages.success(request, "Schedule slot removed.")
            return redirect(f"{reverse('championship_setup', kwargs={'champ_id': championship.id})}?step=schedule")

        # 7. Publish / Verify Championship (Step 5: Review)
        elif action == 'publish_championship':
            is_verified = ('is_rules_preset_verified' in request.POST)
            championship.is_rules_preset_verified = is_verified
            target_status = request.POST.get('status', ChampionshipStatus.PUBLISHED)
            championship.status = target_status
            championship.save()
            messages.success(request, f"Championship status updated to: {championship.get_status_display()}!")
            return redirect(f"{reverse('championship_setup', kwargs={'champ_id': championship.id})}?step=review")

        # 8. Update District Quota
        elif action == 'update_quota':
            dist_id = request.POST.get('quota_id')
            quota_limit = request.POST.get('quota_limit')
            dq = get_object_or_404(DistrictQuota, id=dist_id, championship=championship)
            dq.quota_limit = int(quota_limit)
            dq.save()
            messages.success(request, f"Quota for {dq.district_name} set to {dq.quota_limit}.")
            return redirect(f"{reverse('championship_setup', kwargs={'champ_id': championship.id})}?step=categories")

    stages_list = ['Stage A', 'Stage B', 'Stage C']
    sessions_list = ['Morning Session (09:00 - 13:00)', 'Afternoon Session (14:00 - 18:00)', 'Evening Finals (18:30 - 21:00)']

    return render(request, 'championships/setup.html', {
        'championship': championship,
        'step': step,
        'event_configs': event_configs,
        'all_categories': all_categories,
        'quotas': quotas,
        'scheduled_panels': scheduled_panels,
        'stages_list': stages_list,
        'sessions_list': sessions_list,
    })

@admin_required
def championship_branding(request, champ_id):
    """
    Dedicated Championship Branding & Multi-Logo Management:
    Allows adding 3 to 10 logos:
    - State Association Logo (TYSA)
    - Organizing District Association Logo (e.g. Mancherial Dist)
    - Sponsor, Federation & Partner Logos (3 to 8 logos)
    These logos are rendered on printable master rolls, live results, scoreboards, and certificates.
    """
    championship = get_object_or_404(Championship, id=champ_id)
    
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'update_branding':
            if 'logo' in request.FILES:
                championship.logo = request.FILES['logo']
            
            name = request.POST.get('name', '').strip()
            if name:
                championship.name = name
            venue_name = request.POST.get('venue_name', '').strip()
            if venue_name:
                championship.venue_name = venue_name
            venue_city = request.POST.get('venue_city', '').strip()
            if venue_city:
                championship.venue_city = venue_city
            venue_address = request.POST.get('venue_address', '').strip()
            championship.venue_address = venue_address
            
            championship.save()
            messages.success(request, f"Branding assets and visual identity updated for {championship.name}!")
            return redirect('championship_branding', champ_id=championship.id)

        elif action == 'add_logo':
            logo_type = request.POST.get('logo_type', BrandingLogoType.SPONSOR)
            title = request.POST.get('title', '').strip() or "Brand Logo"
            show_on_prints = ('show_on_prints' in request.POST)
            img = request.FILES.get('image')

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
                        display_order=logo_count + 1
                    )
                    messages.success(request, f"Added {title} ({logo_type}) to championship branding!")
            else:
                messages.error(request, "Please choose an image file for the logo.")
            return redirect('championship_branding', champ_id=championship.id)

        elif action == 'delete_logo':
            logo_id = request.POST.get('logo_id')
            logo_obj = get_object_or_404(ChampionshipLogo, id=logo_id, championship=championship)
            title = logo_obj.title
            logo_obj.delete()
            messages.info(request, f"Removed logo '{title}'.")
            return redirect('championship_branding', champ_id=championship.id)

        elif action == 'move_logo':
            logo_id = request.POST.get('logo_id')
            direction = request.POST.get('direction')
            logo_obj = get_object_or_404(ChampionshipLogo, id=logo_id, championship=championship)
            logos_list = list(championship.branding_logos.all().order_by('display_order', 'id'))
            idx = next((i for i, l in enumerate(logos_list) if l.id == logo_obj.id), -1)
            if direction == 'up' and idx > 0:
                logos_list[idx], logos_list[idx - 1] = logos_list[idx - 1], logos_list[idx]
            elif direction == 'down' and idx < len(logos_list) - 1:
                logos_list[idx], logos_list[idx + 1] = logos_list[idx + 1], logos_list[idx]
            for order, l in enumerate(logos_list, start=1):
                if l.display_order != order:
                    l.display_order = order
                    l.save(update_fields=['display_order'])
            messages.success(request, f"Reordered logo '{logo_obj.title}'.")
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
