from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .models import Championship, EventConfiguration, AgeCategory, EventType, DistrictQuota, ChampionshipStatus

@login_required
def championship_create(request):
    """
    Implements 34_Championship_Basic_Details.png:
    Championship basics, dates, venue, registration period, and event enablement.
    """
    championship = Championship.objects.first()
    if request.method == 'POST':
        name = request.POST.get('name')
        code = request.POST.get('code')
        year = request.POST.get('year', 2026)
        venue_name = request.POST.get('venue_name')
        venue_city = request.POST.get('venue_city', 'Hyderabad')
        start_date = request.POST.get('start_date')
        end_date = request.POST.get('end_date')
        reg_start = request.POST.get('registration_start')
        reg_end = request.POST.get('registration_end')
        cutoff_date = request.POST.get('age_cutoff_date')
        rules_edition = request.POST.get('rules_edition', 'Yogasana Bharat COP 2024–2026')

        if championship:
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
            messages.success(request, "Championship basic configuration updated.")
        else:
            championship = Championship.objects.create(
                name=name, code=code, year=year, venue_name=venue_name, venue_city=venue_city,
                start_date=start_date, end_date=end_date, registration_start=reg_start,
                registration_end=reg_end, age_cutoff_date=cutoff_date, rules_edition=rules_edition,
                status=ChampionshipStatus.IN_PROGRESS
            )
            messages.success(request, f"Championship {name} created successfully.")
        return redirect('championship_setup', champ_id=championship.id)

    return render(request, 'championships/basic_details.html', {
        'championship': championship,
    })

@login_required
def championship_setup(request, champ_id):
    """
    Implements 03_Championship_Setup.png:
    Category and gender configuration applied separately for EACH of the 4 events:
    Traditional, Artistic Single, Artistic Pair, Rhythmic Pair.
    """
    championship = get_object_or_404(Championship, id=champ_id)
    event_configs = championship.event_configurations.prefetch_related('age_categories').all()
    all_categories = AgeCategory.objects.all()
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
            
            messages.success(request, f"Configuration saved for {ev_cfg.get_event_type_display()}.")
            return redirect('championship_setup', champ_id=championship.id)
        
        elif action == 'update_quota':
            dist_id = request.POST.get('quota_id')
            quota_limit = request.POST.get('quota_limit')
            dq = get_object_or_404(DistrictQuota, id=dist_id, championship=championship)
            dq.quota_limit = int(quota_limit)
            dq.save()
            messages.success(request, f"District quota for {dq.district_name} set to {dq.quota_limit}.")
            return redirect('championship_setup', champ_id=championship.id)

    return render(request, 'championships/setup.html', {
        'championship': championship,
        'event_configs': event_configs,
        'all_categories': all_categories,
        'quotas': quotas,
    })
