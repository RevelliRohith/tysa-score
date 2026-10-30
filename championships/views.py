import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .models import Championship, EventConfiguration, AgeCategory, EventType, DistrictQuota, ChampionshipStatus
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
    Implements 03_Championship_Setup.png:
    5-Step Wizard (Step 2: Categories & Gender Divisions).
    Configure age categories, gender divisions, rules edition, and quotas.
    """
    championship = get_object_or_404(Championship, id=champ_id)
    event_configs = championship.event_configurations.prefetch_related('age_categories').all()
    all_categories = AgeCategory.objects.all().order_by('display_order')
    quotas = championship.district_quotas.all()

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'update_event':
            event_id = request.POST.get('event_config_id')
            ev_cfg = get_object_or_404(EventConfiguration, id=event_id, championship=championship)
            
            ev_cfg.allow_male = ('allow_male' in request.POST)
            ev_cfg.allow_female = ('allow_female' in request.POST)
            ev_cfg.is_enabled = ('is_enabled' in request.POST)
            
            selected_cat_ids = request.POST.getlist('age_categories')
            ev_cfg.age_categories.set(selected_cat_ids)
            ev_cfg.save()
            
            messages.success(request, f"Saved settings for {ev_cfg.get_event_type_display()}.")
            return redirect('championship_setup', champ_id=championship.id)
        
        elif action == 'update_quota':
            dist_id = request.POST.get('quota_id')
            quota_limit = request.POST.get('quota_limit')
            dq = get_object_or_404(DistrictQuota, id=dist_id, championship=championship)
            dq.quota_limit = int(quota_limit)
            dq.save()
            messages.success(request, f"Quota for {dq.district_name} set to {dq.quota_limit}.")
            return redirect('championship_setup', champ_id=championship.id)

    return render(request, 'championships/setup.html', {
        'championship': championship,
        'event_configs': event_configs,
        'all_categories': all_categories,
        'quotas': quotas,
    })
