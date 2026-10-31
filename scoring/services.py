from decimal import Decimal
from typing import List, Dict, Any
from championships.models import Championship, EventConfiguration, AgeCategory
from scoring.models import Performance, PerformanceStatus, JudgeSubmission, AsanaScoreItem, PerformanceArtistryScore
from registrations.models import AsanaSequenceItem

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


def calculate_performance_score(performance: Performance) -> Dict[str, Any]:
    """
    Computes official composite score for a Performance under Yogasana Bharat COP rules.
    - Compares submissions against every required panel seat.
    - Explicitly distinguishes 'MISSING' from intentional zero (0.000).
    - Applies Olympic trimmed mean to D-judges when 4 judges are present (drops high & low, averages middle 2).
    - Accumulates Artistry score, Time judge contributions/penalties, and Evaluator invalidations.
    - Returns full audit dictionary with seat status, breakdown, and provisional total.
    """
    event_entry = performance.event_entry
    championship = event_entry.registration.championship
    event_config = event_entry.event_config
    event_type = event_config.event_type if event_config else 'TRADITIONAL'

    # Determine required panel seats based on event type
    # Traditional: D1, D2, D3, D4, TIME, EVALUATOR
    # Artistic / Rhythmic: D1, D2, D3, D4, TIME, EVALUATOR, ARTISTRY
    is_traditional = 'TRADITIONAL' in event_type.upper()

    required_roles = [
        ('D1_JUDGE', 'D1 Judge', 'D'),
        ('D2_JUDGE', 'D2 Judge', 'D'),
        ('D3_JUDGE', 'D3 Judge', 'D'),
        ('D4_JUDGE', 'D4 Judge', 'D'),
        ('TIME_JUDGE', 'Time Judge', 'TIME'),
        ('EVALUATOR', 'Evaluator', 'EVALUATOR'),
    ]
    if not is_traditional:
        required_roles.append(('ARTISTRY_JUDGE', 'Artistry Judge', 'ARTISTRY'))

    submissions = JudgeSubmission.objects.filter(performance=performance).select_related('judge')
    sub_by_role = {sub.judge_role: sub for sub in submissions}

    seat_status = []
    missing_seats = []
    d_effective_scores = []
    artistry_total = Decimal('0.000')
    timing_score = Decimal('0.000')
    timing_penalties = Decimal('0.000')
    evaluator_deductions = Decimal('0.000')
    invalid_items = []

    for role_code, role_label, role_category in required_roles:
        sub = sub_by_role.get(role_code)
        
        if not sub or not sub.is_submitted:
            seat_status.append({
                'role_code': role_code,
                'role_label': role_label,
                'category': role_category,
                'status': 'MISSING',
                'is_submitted': False,
                'is_zero': False,
                'score': None,
                'judge_name': sub.judge.get_full_name() if (sub and sub.judge) else 'Unassigned',
                'badge_class': 'bg-rose-50 text-rose-700 border-rose-200',
                'display_text': 'Missing / Pending'
            })
            missing_seats.append(role_label)
        else:
            # Judge submitted their score! Check if it is intentional 0.00
            score_val = sub.effective_total
            is_zero = (score_val == Decimal('0.000') or score_val == Decimal('0.00') or score_val == Decimal('0'))
            
            seat_status.append({
                'role_code': role_code,
                'role_label': role_label,
                'category': role_category,
                'status': 'SUBMITTED',
                'is_submitted': True,
                'is_zero': is_zero,
                'score': score_val,
                'judge_name': sub.judge.get_full_name() or sub.judge.email,
                'badge_class': 'bg-emerald-50 text-emerald-700 border-emerald-200' if not is_zero else 'bg-amber-50 text-amber-700 border-amber-200',
                'display_text': f"{score_val:.3f} (Intentional Zero)" if is_zero else f"{score_val:.3f}"
            })

            if role_category == 'D':
                d_effective_scores.append(score_val)
            elif role_category == 'TIME':
                timing_score = score_val
            elif role_category == 'ARTISTRY':
                artistry_total = score_val
            elif role_category == 'EVALUATOR':
                # Check invalid items from evaluator
                eval_items = sub.items.filter(is_valid=False)
                for item in eval_items:
                    pose_seq = AsanaSequenceItem.objects.filter(
                        event_entry=event_entry, 
                        sequence_order=item.sequence_order
                    ).first()
                    invalid_items.append({
                        'sequence_order': item.sequence_order,
                        'performed_asana': item.observed_asana.display_name if item.observed_asana else (pose_seq.declared_name if pose_seq else f"Item #{item.sequence_order}"),
                        'declared_code': pose_seq.declared_code if pose_seq else f"SEQ-{item.sequence_order}",
                        'reason': item.invalid_reason or "Form / hold criteria not met",
                        'action': "Penalty deduction applied"
                    })
                    evaluator_deductions += Decimal('2.000')

    # Calculate Retained D Marks
    d_trimmed_scores = list(d_effective_scores)
    if len(d_effective_scores) == 4:
        # Olympic trimmed mean: discard min and max, average middle 2
        sorted_d = sorted(d_effective_scores)
        retained_d = (sorted_d[1] + sorted_d[2]) / Decimal('2.0')
        d_trimmed_scores = [sorted_d[1], sorted_d[2]]
    elif len(d_effective_scores) > 0:
        retained_d = sum(d_effective_scores) / Decimal(str(len(d_effective_scores)))
    else:
        retained_d = Decimal('0.000')

    # Round all to 3 decimal places
    retained_d = round(retained_d, 3)
    artistry_total = round(artistry_total, 3)
    timing_score = round(timing_score, 3)
    evaluator_deductions = round(evaluator_deductions, 3)
    penalties = round(evaluator_deductions + timing_penalties, 3)

    if is_traditional:
        # In Traditional, Timing is integrated into the 10-point item D-mark, evaluator invalid items deduct
        provisional_total = max(Decimal('0.000'), retained_d - penalties)
    else:
        # In Artistic / Rhythmic: Retained D + Artistry + Timing - Penalties
        provisional_total = max(Decimal('0.000'), retained_d + artistry_total + timing_score - penalties)

    provisional_total = round(provisional_total, 3)

    # Check whether provisional score release is allowed
    block_reasons = []
    if missing_seats:
        block_reasons.append(f"Missing official scores from: {', '.join(missing_seats)}")
    if not championship.is_rules_preset_verified:
        block_reasons.append(f"Championship rule preset '{championship.rules_edition}' is unverified against official COP.")

    can_release = (len(block_reasons) == 0)

    return {
        'performance': performance,
        'event_entry': event_entry,
        'championship': championship,
        'is_traditional': is_traditional,
        'seat_status': seat_status,
        'missing_seats': missing_seats,
        'd_effective_scores': d_effective_scores,
        'd_trimmed_scores': d_trimmed_scores,
        'retained_d': retained_d,
        'artistry_total': artistry_total,
        'timing_score': timing_score,
        'penalties': penalties,
        'evaluator_deductions': evaluator_deductions,
        'invalid_items': invalid_items,
        'provisional_total': provisional_total,
        'can_release': can_release,
        'block_reasons': block_reasons,
    }
