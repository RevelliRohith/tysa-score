from django.shortcuts import render, get_object_or_404
from django.db.models import Q
from scoring.models import Performance, PerformanceStatus
from competition.models import JudgePanel, StageBatch
from championships.models import Championship, EventConfiguration, AgeCategory
from accounts.permissions import get_active_championship

def public_live_display(request, championship_id=None, panel_id=None, event_id=None, cat_id=None):
    """
    Implements Big Screen / Arena Live Display:
    - Multi-arena & multi-event scoped (Stage 1, Stage 2, etc.)
    - Dedicated event and age category separation:
        URL pattern: /display/live/<int:championship_id>/<int:panel_id>/<int:event_id>/<int:cat_id>/
        Query params fallback: /display/live/?panel_id=...&event_id=...&cat_id=...&gender=...&round_type=...
    - Full-screen 100% viewport scaling
    - Active Performer / Batch on stage strictly for the scoped panel/event/category
    - Top 3 Podium with Medal Badges (🥇 Gold, 🥈 Silver, 🥉 Bronze)
    - Full cumulative released standings strictly filtered to this division
    """
    if championship_id:
        championship = Championship.objects.filter(id=championship_id).first()
    else:
        championship = get_active_championship(request)
    
    event_configs = championship.event_configurations.filter(is_enabled=True) if championship else EventConfiguration.objects.none()
    panels = JudgePanel.objects.filter(championship=championship, is_active=True).select_related('event_config', 'age_category') if championship else JudgePanel.objects.none()

    # Resolution from URL kwargs or query parameters
    target_panel_id = panel_id or request.GET.get('panel_id')
    target_event_id = event_id or request.GET.get('event_id')
    target_cat_id = cat_id or request.GET.get('cat_id')
    selected_stage = request.GET.get('stage')
    gender = request.GET.get('gender')
    round_type = request.GET.get('round_type')

    target_panel = None
    if target_panel_id:
        target_panel = panels.filter(id=target_panel_id).first()
    elif selected_stage:
        target_panel = panels.filter(stage_name=selected_stage).first()
    elif target_event_id:
        panel_qs = panels.filter(event_config_id=target_event_id)
        if target_cat_id:
            panel_qs = panel_qs.filter(age_category_id=target_cat_id)
        if gender:
            panel_qs = panel_qs.filter(gender=gender)
        if round_type:
            panel_qs = panel_qs.filter(round_type=round_type)
        target_panel = panel_qs.first()

    # Fallback default panel if not specified
    if not target_panel and panels.exists():
        target_panel = panels.first()

    current_event = target_panel.event_config if target_panel else (event_configs.filter(id=target_event_id).first() if target_event_id else event_configs.first())
    current_cat = target_panel.age_category if target_panel else (AgeCategory.objects.filter(id=target_cat_id).first() if target_cat_id else None)
    gender = target_panel.gender if target_panel else (gender or 'M')
    round_type = target_panel.round_type if target_panel else (round_type or 'PRELIMINARY')
    stage_name = target_panel.stage_name if target_panel else (selected_stage or 'Main Arena / Stage 1')

    # Current Performer / Active Batch strictly on this targeted panel
    active_batch = None
    if target_panel:
        active_batch = StageBatch.objects.filter(panel=target_panel, status__in=['ACTIVE', 'SUBMITTED']).first()

    current_entry = active_batch.entries.first() if active_batch else None
    current_perf = Performance.objects.filter(event_entry=current_entry).first() if current_entry else None

    # Released performances strictly for this Championship, Event, Age Category, Gender, and Round
    perf_qs = Performance.objects.filter(
        status__in=[PerformanceStatus.PROVISIONAL_RELEASED, PerformanceStatus.CD_FINALIZED]
    ).select_related('event_entry__registration__athlete', 'event_entry__partner_athlete', 'event_entry__event_config', 'event_entry__age_category')

    if championship:
        perf_qs = perf_qs.filter(event_entry__registration__championship=championship)
    if current_event:
        perf_qs = perf_qs.filter(event_entry__event_config=current_event)
    if current_cat:
        perf_qs = perf_qs.filter(event_entry__age_category=current_cat)
    if gender:
        perf_qs = perf_qs.filter(event_entry__gender=gender)
    if round_type:
        perf_qs = perf_qs.filter(round_type=round_type)

    perf_qs = perf_qs.order_by('-final_score', '-provisional_score')
    cumulative_list = list(perf_qs)

    # Attach rank and medal symbols
    ranked_entries = []
    for idx, p in enumerate(cumulative_list, start=1):
        medal = None
        if idx == 1:
            medal = '🥇'
        elif idx == 2:
            medal = '🥈'
        elif idx == 3:
            medal = '🥉'
        
        ranked_entries.append({
            'rank': idx,
            'medal': medal,
            'performance': p,
            'athlete': p.event_entry.registration.athlete,
            'partner': p.event_entry.partner_athlete,
            'display_names': p.event_entry.display_names,
            'chest_number': p.event_entry.chest_number or f"{idx:03d}",
            'district': p.event_entry.district,
            'score': p.final_score or p.provisional_score or 0.0,
            'state_status': p.get_status_display()
        })

    top_scores = ranked_entries[:3]

    # Total registered competitors for this specific division
    from registrations.models import EventEntry
    division_total_count = EventEntry.objects.filter(
        registration__championship=championship,
        event_config=current_event,
        age_category=current_cat,
        gender=gender
    ).count() if (championship and current_event and current_cat) else len(ranked_entries)

    return render(request, 'results/live_display.html', {
        'hide_navbar': True,
        'championship': championship,
        'stage_name': stage_name,
        'current_event': current_event,
        'current_cat': current_cat,
        'gender': gender,
        'round_type': round_type,
        'active_batch': active_batch,
        'current_perf': current_perf,
        'current_entry': current_entry,
        'top_scores': top_scores,
        'ranked_entries': ranked_entries,
        'panels': panels,
        'target_panel': target_panel,
        'event_configs': event_configs,
        'total_count': max(division_total_count, len(ranked_entries) + (1 if active_batch else 0)),
        'performed_count': len(ranked_entries),
    })

def scoreboard_view(request):
    """
    Implements cumulative scoreboard view with official logos.
    """
    championship = get_active_championship(request)
    released_qs = Performance.objects.filter(
        status__in=[PerformanceStatus.PROVISIONAL_RELEASED, PerformanceStatus.CD_FINALIZED]
    ).select_related('event_entry__registration__athlete').order_by('-final_score', '-provisional_score')

    state_logo = championship.branding_logos.filter(logo_type='STATE').first() if championship else None
    district_logo = championship.branding_logos.filter(logo_type='DISTRICT').first() if championship else None
    other_logos = championship.branding_logos.exclude(logo_type__in=['STATE', 'DISTRICT']) if championship else None

    return render(request, 'results/scoreboard.html', {
        'championship': championship,
        'results': released_qs,
        'state_logo': state_logo,
        'district_logo': district_logo,
        'other_logos': other_logos,
    })
