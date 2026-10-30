from decimal import Decimal
from typing import List, Dict, Any
from championships.models import Championship, EventConfiguration, AgeCategory
from scoring.models import Performance, PerformanceStatus

class TiePolicy:
    STANDARD = 'STANDARD'  # Standard competition ranking: tied athletes get same rank, skip next (1, 2, 2, 4)
    DENSE = 'DENSE'        # Dense ranking: tied athletes get same rank, no skip (1, 2, 2, 3)
    SHARED = 'SHARED'      # Shared medals (e.g. joint Gold)

def calculate_event_division_standings(
    championship: Championship,
    event_config: EventConfiguration = None,
    age_category: AgeCategory = None,
    gender: str = None,
    round_type: str = 'FINAL',
    tie_policy: str = TiePolicy.STANDARD
) -> List[Dict[str, Any]]:
    """
    Calculates official standings strictly scoped to:
    - Championship
    - Event Configuration
    - Age Category
    - Gender Division
    - Round Type (defaults to FINAL, falls back to PRELIMINARY if no finals)

    Only considers CD_FINALIZED (or PROVISIONAL_RELEASED if cd_finalized=False) performances.
    Applies deterministic tie resolution and computes medal/rank labels.
    """
    qs = Performance.objects.filter(
        event_entry__registration__championship=championship,
        status__in=[PerformanceStatus.CD_FINALIZED, PerformanceStatus.PROVISIONAL_RELEASED]
    )

    if event_config:
        qs = qs.filter(event_entry__event_config=event_config)
    if age_category:
        qs = qs.filter(event_entry__age_category=age_category)
    if gender:
        qs = qs.filter(event_entry__gender=gender)
    if round_type:
        qs = qs.filter(round_type=round_type)

    # Order primarily by CD finalized score descending, then provisional
    performances = list(qs.select_related(
        'event_entry__registration__athlete',
        'event_entry__partner_athlete',
        'event_entry__event_config',
        'event_entry__age_category'
    ).order_by('-final_score', '-provisional_score', 'id'))

    standings = []
    current_rank = 0
    items_processed = 0
    last_score = None

    for p in performances:
        items_processed += 1
        score = p.final_score if p.final_score is not None else p.provisional_score
        
        if score != last_score:
            if tie_policy == TiePolicy.DENSE:
                current_rank += 1
            else: # STANDARD
                current_rank = items_processed
            last_score = score

        # Medal determination based on current rank
        if current_rank == 1:
            rank_label = "Rank #1 — Gold Medal"
            medal = "GOLD"
        elif current_rank == 2:
            rank_label = "Rank #2 — Silver Medal"
            medal = "SILVER"
        elif current_rank == 3:
            rank_label = "Rank #3 — Bronze Medal"
            medal = "BRONZE"
        else:
            rank_label = f"Rank #{current_rank} — Merit Position"
            medal = None

        standings.append({
            'performance': p,
            'event_entry': p.event_entry,
            'athlete': p.event_entry.registration.athlete,
            'partner': p.event_entry.partner_athlete if p.event_entry.is_pair else None,
            'score': score,
            'rank': current_rank,
            'rank_label': rank_label,
            'medal': medal,
            'is_pair': p.event_entry.is_pair,
            'event_name': p.event_entry.event_config.get_event_type_display(),
            'age_category_name': p.event_entry.age_category.name,
            'gender': p.event_entry.get_gender_display(),
            'district': p.event_entry.district
        })

    return standings
