from django.shortcuts import render
from scoring.models import Performance, PerformanceStatus
from competition.models import StageBatch
from championships.models import Championship

def public_live_display(request):
    """
    Implements 27_Public_Live_Score_Display.png:
    1. Pinned Current Performer / Current Batch at the top ("score awaiting release" until approved).
    2. Pinned Top 3 Scores immediately below with rank and tie handling.
    3. All completed released performance scores in a cumulative rotating table.
    """
    championship = Championship.objects.first()
    
    # Current performer on stage
    active_batch = StageBatch.objects.filter(status='ACTIVE').first()
    current_entry = active_batch.entries.first() if active_batch else None
    current_perf = Performance.objects.filter(event_entry=current_entry).first() if current_entry else None

    # Released performances
    released_qs = Performance.objects.filter(
        status__in=[PerformanceStatus.PROVISIONAL_RELEASED, PerformanceStatus.CD_FINALIZED]
    ).select_related('event_entry__registration__athlete').order_by('-final_score', '-provisional_score')

    top_scores = list(released_qs[:3])
    cumulative_table = list(released_qs)

    return render(request, 'results/live_display.html', {
        'hide_navbar': True,
        'championship': championship,
        'active_batch': active_batch,
        'current_perf': current_perf,
        'top_scores': top_scores,
        'cumulative_table': cumulative_table,
        'total_count': 32,
        'performed_count': released_qs.count(),
    })

def scoreboard_view(request):
    """
    Implements 28_Certificates_and_Printing.png:
    (Identified as a cumulative scoreboard mode despite its misnamed file label).
    """
    championship = Championship.objects.first()
    released_qs = Performance.objects.filter(
        status__in=[PerformanceStatus.PROVISIONAL_RELEASED, PerformanceStatus.CD_FINALIZED]
    ).select_related('event_entry__registration__athlete').order_by('-final_score', '-provisional_score')

    return render(request, 'results/scoreboard.html', {
        'championship': championship,
        'results': released_qs,
    })
