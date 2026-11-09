"""
Authoritative Scoring Calculation Engine for TYSA Score.
Unified server-side implementation conforming to Yogasana Bharat COP rules,
State Yogasana Complete Requirements, and India Amendments.

Handles:
- Decimal arithmetic (no binary float drift)
- Traditional Yogasana (7 asanas, round configurations, 5/4/3 panel trimming, compulsory-only tie-break)
- Artistic Single (10 items, D*BV, 4 D judges trimmed mean)
- Artistic Pair (10 items, (P1*BV1 + P2*BV2)/2, formation, 4 D judges trimmed mean)
- Rhythmic Pair (10 items, ((P1+P2)/2)*BV, 4 D judges trimmed mean)
- Artistry (2 judges, 5 criteria max 20, net average)
- Timing (2 judges, TW brackets, TA 5s holds, reconciliation, overtime)
- Invalidity propagation (zeros complete affected D item for all D judges)
- Evaluator penalties & sequence tracking
- Full performance composite score
- Tie breaks (Traditional: compulsory score; Music: Artistry score; Joint medals)
- Round progression (QF->SF, SF->Final)
- Championship trophy standings (Medal-priority vs 5/3/2 points profile; pair counted once)
"""

from decimal import Decimal, ROUND_HALF_UP
from typing import List, Dict, Any, Tuple, Optional
from datetime import datetime
from django.utils import timezone
from django.db import transaction

def make_json_serializable(obj: Any) -> Any:
    """Recursively converts Decimals and sets to strings/lists for JSON storage."""
    if isinstance(obj, Decimal):
        return str(obj)
    elif isinstance(obj, dict):
        return {k: make_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple, set)):
        return [make_json_serializable(i) for i in obj]
    return obj


# Constants
DEFAULT_ROUNDING_PLACES = Decimal('0.001')

def quantize_score(val: Any, places: int = 3) -> Decimal:
    """Rounds a value to specified decimal places (default 3) using ROUND_HALF_UP."""
    if val is None:
        return Decimal('0.' + '0' * places)
    if not isinstance(val, Decimal):
        val = Decimal(str(val))
    step = Decimal('10') ** -places
    return val.quantize(step, rounding=ROUND_HALF_UP)

def to_decimal(val: Any, default: str = '0.00') -> Decimal:
    """Safe decimal converter preserving exact representation."""
    if val is None:
        return Decimal(default)
    if isinstance(val, Decimal):
        return val
    try:
        return Decimal(str(val))
    except Exception:
        return Decimal(default)


# ============================================================================
# UNRESOLVED RULES REGISTER
# ============================================================================
UNRESOLVED_RULES_REGISTER = {
    'SEQUENCE_PENALTY_POLICY': {
        'rule': 'Evaluator sequence change penalty scaling',
        'options': ['REPLACEMENT_2_4_6', 'ACCUMULATED_2_6_12'],
        'default': 'REPLACEMENT_2_4_6',
        'status': 'UNRESOLVED_COP_POLICY',
        'description': 'COP specifies 1st change 2, 2nd 4, 3rd 6, >3 DQ. Whether marks represent replacement totals or accumulated additions requires formal policy adoption.'
    },
    'TRADITIONAL_EXACT_FOUR_MISSING_CATEGORIES': {
        'rule': 'Traditional category deficit at exactly four',
        'options': ['PENALTY_20_MARKS', 'DISQUALIFIED'],
        'default': 'DISQUALIFIED',
        'status': 'UNRESOLVED_COP_POLICY',
        'description': 'COP table specifies 5/10/15 for 1/2/3 missing categories and DQ for >4. Outcome for exactly 4 missing categories is unstated in source.'
    },
    'TRADITIONAL_TIMING_ANNEXURE_TABLES': {
        'rule': 'Traditional hold duration under-75% micro-marking',
        'options': ['ANNEXURE_TABLE_LOOKUP', 'UNRESOLVED_FLAG'],
        'default': 'UNRESOLVED_FLAG',
        'status': 'UNRESOLVED_COP_POLICY',
        'description': 'Annexure A/B timing deduction tables and failed 2nd attainment deductions require official YB publication confirmation.'
    },
    'MUSIC_TIMING_DISAGREEMENT_GENERAL': {
        'rule': 'General Time Judge reconciliation beyond explicit 4/5 and 150s rules',
        'options': ['AVERAGE_BOTH', 'FLAG_FOR_TECHNICAL_REVIEW'],
        'default': 'AVERAGE_BOTH',
        'status': 'UNRESOLVED_COP_POLICY',
        'description': 'Source provides explicit rules for (4,5)->5, (4,4)->invalid, and 3-second 150s margin. General continuous disagreements default to arithmetic mean with review flag.'
    },
    'MUSIC_OVERTIME_UPPER_BOUNDARY': {
        'rule': 'TW marks above 180s and exact 190s DQ cutoff',
        'options': ['DQ_AT_190', 'DQ_AFTER_190'],
        'default': 'DQ_AFTER_190',
        'status': 'UNRESOLVED_COP_POLICY',
        'description': 'Source notes overtime penalty 0.5/sec after 180s and disqualification after 10s. Whether DQ occurs at exactly 190.0s or strictly >190.0s requires technical confirmation.'
    },
    'PROGRESSION_CUTOFF_TIES': {
        'rule': 'Progression cutoff ties for 20th/10th qualifiers',
        'options': ['ADVANCE_ALL_TIED', 'STRICT_TIE_BREAK_ONLY'],
        'default': 'ADVANCE_ALL_TIED',
        'status': 'UNRESOLVED_COP_POLICY',
        'description': 'When multiple athletes tie at the qualification cutoff position, whether all tied advance or tie-break strictly caps field.'
    }
}


# ============================================================================
# COP CHAPTER 4 ANNEXURE A & B TIMING TABLES
# ============================================================================
# Annexure A: Reference table with marks and time in seconds (75%) for asana in 1st Attempt (Max 2.0)
ANNEXURE_A_TABLE = {
    60: {
        60: Decimal('2.000000'), 59: Decimal('1.966667'), 58: Decimal('1.933333'), 57: Decimal('1.900000'),
        56: Decimal('1.866667'), 55: Decimal('1.833333'), 54: Decimal('1.800000'), 53: Decimal('1.766667'),
        52: Decimal('1.733333'), 51: Decimal('1.700000'), 50: Decimal('1.666667'), 49: Decimal('1.633333'),
        48: Decimal('1.600000'), 47: Decimal('1.566667'), 46: Decimal('1.533333'), 45: Decimal('1.500000'),
    },
    45: {
        45: Decimal('2.000000'), 44: Decimal('1.955556'), 43: Decimal('1.911111'), 42: Decimal('1.866667'),
        41: Decimal('1.822222'), 40: Decimal('1.777778'), 39: Decimal('1.733333'), 38: Decimal('1.688889'),
        37: Decimal('1.644444'), 36: Decimal('1.600000'), 35: Decimal('1.555556'), 34: Decimal('1.511111'),
        33: Decimal('1.466667'),
    },
    30: {
        30: Decimal('2.000000'), 29: Decimal('1.933333'), 28: Decimal('1.866667'), 27: Decimal('1.800000'),
        26: Decimal('1.733333'), 25: Decimal('1.666667'), 24: Decimal('1.600000'), 23: Decimal('1.533333'),
        22: Decimal('1.466667'),
    },
    15: {
        15: Decimal('2.000000'), 14: Decimal('1.866667'), 13: Decimal('1.733333'), 12: Decimal('1.600000'),
        11: Decimal('1.466667'),
    }
}

# Annexure B: Reference table with marks and time in seconds (75%) for asana in 2nd Attempt (Max 1.0)
ANNEXURE_B_TABLE = {
    60: {
        60: Decimal('1.000000'), 59: Decimal('0.983333'), 58: Decimal('0.966667'), 57: Decimal('0.950000'),
        56: Decimal('0.933333'), 55: Decimal('0.916667'), 54: Decimal('0.900000'), 53: Decimal('0.883333'),
        52: Decimal('0.866667'), 51: Decimal('0.850000'), 50: Decimal('0.833333'), 49: Decimal('0.816667'),
        48: Decimal('0.800000'), 47: Decimal('0.783333'), 46: Decimal('0.766667'), 45: Decimal('0.750000'),
    },
    45: {
        45: Decimal('1.000000'), 44: Decimal('0.977778'), 43: Decimal('0.955556'), 42: Decimal('0.933333'),
        41: Decimal('0.911111'), 40: Decimal('0.888889'), 39: Decimal('0.866667'), 38: Decimal('0.844444'),
        37: Decimal('0.822222'), 36: Decimal('0.800000'), 35: Decimal('0.777778'), 34: Decimal('0.755556'),
        33: Decimal('0.733333'),
    },
    30: {
        30: Decimal('1.000000'), 29: Decimal('0.966667'), 28: Decimal('0.933333'), 27: Decimal('0.900000'),
        26: Decimal('0.866667'), 25: Decimal('0.833333'), 24: Decimal('0.800000'), 23: Decimal('0.766667'),
        22: Decimal('0.733333'),
    },
    15: {
        15: Decimal('1.000000'), 14: Decimal('0.933333'), 13: Decimal('0.866667'), 12: Decimal('0.800000'),
        11: Decimal('0.733333'),
    }
}


def calculate_traditional_timing_contribution(
    hold_seconds: Decimal,
    target_hold: Decimal,
    attempt: int = 1
) -> Decimal:
    """
    COP Chapter 4 Annexure A & B Timing calculation:
    - Attempt 1: Annexure A (Target hold = 2.0 marks, down to 75% cutoff; below cutoff = 0).
    - Attempt 2: Annexure B (Target hold = 1.0 mark, down to 75% cutoff; below cutoff = 0).
    - When hold is 0 or contribution is 0, D scores for that asana become zero.
    """
    hold_seconds = to_decimal(hold_seconds)
    target_hold = to_decimal(target_hold)
    if hold_seconds <= Decimal('0.00') or target_hold <= Decimal('0.00'):
        return Decimal('0.000')

    # Match target table (60, 45, 30, 15)
    target_int = int(round(float(target_hold)))
    table_family = ANNEXURE_B_TABLE if attempt == 2 else ANNEXURE_A_TABLE
    table = table_family.get(target_int)

    # Whole seconds rounded with ROUND_HALF_UP per COP standard
    sec_int = int(hold_seconds.quantize(Decimal('1'), rounding=ROUND_HALF_UP))

    if table:
        if sec_int >= target_int:
            return table[target_int]
        if sec_int in table:
            return table[sec_int]
        return Decimal('0.000')

    # Fallback formula if custom non-standard target:
    max_marks = Decimal('1.00') if attempt == 2 else Decimal('2.00')
    cutoff_75 = target_hold * Decimal('0.75')
    if hold_seconds >= target_hold:
        return max_marks
    elif hold_seconds >= cutoff_75:
        return quantize_score(max_marks * (hold_seconds / target_hold), 3)
    else:
        return Decimal('0.000')


# ============================================================================
# 1. TRADITIONAL YOGASANA CALCULATIONS
# ============================================================================
def calculate_traditional_item(
    posture: Decimal,
    stability_grace: Decimal,
    timing_contribution: Decimal,
    base_value: Decimal = Decimal('1.00'),
    detailed_accuracy: Optional[Decimal] = None,
    detailed_mounting_dismounting: Optional[Decimal] = None,
    detailed_stability: Optional[Decimal] = None,
    detailed_grace: Optional[Decimal] = None,
    is_invalid: bool = False,
    is_compulsory: bool = True
) -> Dict[str, Any]:
    """
    Computes a single Traditional Yogasana item score.
    Compulsory BV = 1.0. Optional BV = from catalogue.
    Execution = Posture (/6) + Stability/Grace (/2) = /8.
    If detailed microfields provided, they form an alternative breakdown of execution /8:
    (Accuracy /4 + Mounting/Dismounting /2 + Stability /1 + Grace /1).
    item_score = (execution + approved_time_marks /2) * BV.
    If is_invalid is True OR timing_contribution <= 0, effective_item_score is strictly 0.000.
    """
    posture = to_decimal(posture)
    stability_grace = to_decimal(stability_grace)
    timing_contribution = to_decimal(timing_contribution)
    base_value = Decimal('1.00') if is_compulsory else to_decimal(base_value, '1.00')

    # Detailed fields handling (alternative breakdown of execution out of 8)
    has_detailed = all(v is not None for v in [detailed_accuracy, detailed_mounting_dismounting, detailed_stability, detailed_grace])
    if has_detailed:
        acc = min(Decimal('4.00'), max(Decimal('0.00'), to_decimal(detailed_accuracy)))
        mnt = min(Decimal('2.00'), max(Decimal('0.00'), to_decimal(detailed_mounting_dismounting)))
        stb = min(Decimal('1.00'), max(Decimal('0.00'), to_decimal(detailed_stability)))
        grc = min(Decimal('1.00'), max(Decimal('0.00'), to_decimal(detailed_grace)))
        execution = acc + mnt + stb + grc
    else:
        p_clamped = min(Decimal('6.00'), max(Decimal('0.00'), posture))
        sg_clamped = min(Decimal('2.00'), max(Decimal('0.00'), stability_grace))
        execution = p_clamped + sg_clamped

    execution = min(Decimal('8.00'), max(Decimal('0.00'), execution))
    time_clamped = min(Decimal('2.00'), max(Decimal('0.00'), timing_contribution))

    raw_item = (execution + time_clamped) * base_value
    # Evaluator is the authoritative decider of asana validity, and timing hold < 75% zeroes the score
    if is_invalid or time_clamped <= Decimal('0.00'):
        effective_item = Decimal('0.000')
    else:
        effective_item = raw_item

    return {
        'execution': execution,
        'timing_contribution': time_clamped,
        'base_value': base_value,
        'is_compulsory': is_compulsory,
        'is_invalid': is_invalid,
        'raw_item_score': quantize_score(raw_item),
        'effective_item_score': quantize_score(effective_item),
    }


# ============================================================================
# 2. ARTISTIC SINGLE CALCULATIONS
# ============================================================================
def calculate_artistic_single_item(
    execution: Decimal,
    base_value: Decimal,
    is_invalid: bool = False
) -> Dict[str, Any]:
    """
    Artistic Single: 10 items.
    item_score = execution_out_of_10 * declared_BV.
    """
    e = min(Decimal('10.00'), max(Decimal('0.00'), to_decimal(execution)))
    bv = to_decimal(base_value, '0.80')

    raw_item = e * bv
    effective_item = Decimal('0.000') if is_invalid else raw_item

    return {
        'execution': e,
        'base_value': bv,
        'is_invalid': is_invalid,
        'raw_item_score': quantize_score(raw_item),
        'effective_item_score': quantize_score(effective_item),
    }


# ============================================================================
# 3. ARTISTIC PAIR CALCULATIONS
# ============================================================================
def calculate_artistic_pair_item(
    execution_1: Decimal,
    bv_1: Decimal,
    execution_2: Decimal,
    bv_2: Decimal,
    formation: Decimal,
    is_invalid: bool = False
) -> Dict[str, Any]:
    """
    Artistic Pair: 10 pair items.
    player_1_score = execution_1 * bv_1
    player_2_score = execution_2 * bv_2
    players_average = (player_1_score + player_2_score) / 2.0
    pair_item_score = (players_average + formation_score) / 2.0

    Required fixture:
    E1=8, BV1=0.9 -> SP1=7.2
    E2=7, BV2=0.8 -> SP2=5.6
    Formation=8
    players_average = 6.4
    pair_item_score = 7.2
    """
    e1 = min(Decimal('10.00'), max(Decimal('0.00'), to_decimal(execution_1)))
    b1 = to_decimal(bv_1, '1.00')
    e2 = min(Decimal('10.00'), max(Decimal('0.00'), to_decimal(execution_2)))
    b2 = to_decimal(bv_2, '1.00')
    fmt = min(Decimal('10.00'), max(Decimal('0.00'), to_decimal(formation)))

    sp1 = e1 * b1
    sp2 = e2 * b2
    players_avg = (sp1 + sp2) / Decimal('2.0')
    raw_item = (players_avg + fmt) / Decimal('2.0')

    # If invalid, complete pair item including formation is zeroed
    effective_item = Decimal('0.000') if is_invalid else raw_item

    return {
        'execution_1': e1,
        'bv_1': b1,
        'player_1_score': quantize_score(sp1),
        'execution_2': e2,
        'bv_2': b2,
        'player_2_score': quantize_score(sp2),
        'players_average': quantize_score(players_avg),
        'formation': fmt,
        'is_invalid': is_invalid,
        'raw_item_score': quantize_score(raw_item),
        'effective_item_score': quantize_score(effective_item),
    }


# ============================================================================
# 4. RHYTHMIC PAIR CALCULATIONS
# ============================================================================
def calculate_rhythmic_pair_item(
    execution_1: Decimal,
    execution_2: Decimal,
    shared_bv: Decimal,
    is_invalid: bool = False
) -> Dict[str, Any]:
    """
    Rhythmic Pair: 10 shared items. No separate formation score.
    item_score = ((execution_1 + execution_2) / 2.0) * shared_bv.

    Required fixture:
    E1=8, E2=6, shared_bv=0.9 -> ((8 + 6) / 2) * 0.9 = 6.3.
    """
    e1 = min(Decimal('10.00'), max(Decimal('0.00'), to_decimal(execution_1)))
    e2 = min(Decimal('10.00'), max(Decimal('0.00'), to_decimal(execution_2)))
    bv = to_decimal(shared_bv, '1.00')

    exec_avg = (e1 + e2) / Decimal('2.0')
    raw_item = exec_avg * bv
    effective_item = Decimal('0.000') if is_invalid else raw_item

    return {
        'execution_1': e1,
        'execution_2': e2,
        'execution_average': quantize_score(exec_avg),
        'shared_bv': bv,
        'is_invalid': is_invalid,
        'raw_item_score': quantize_score(raw_item),
        'effective_item_score': quantize_score(effective_item),
    }


# ============================================================================
# 5. ARTISTRY CALCULATION (MUSIC EVENTS)
# ============================================================================
def calculate_artistry_net(
    costume: Decimal,
    theme_music: Decimal,
    character_expression: Decimal,
    rhythm_artistic_steps: Decimal,
    use_of_floor: Decimal,
    deductions: Decimal = Decimal('0.00')
) -> Dict[str, Any]:
    """
    Artistry: 5 official criteria (max 20.0).
    Costume: max 1.0
    Theme & Music: max 1.0
    Character & Expression: max 3.0
    Rhythm & Artistic Steps (incl. Synchronization): max 10.0
    Use of Floor: max 5.0
    Net = sum(components) - deductions.
    """
    c = min(Decimal('1.00'), max(Decimal('0.00'), to_decimal(costume)))
    tm = min(Decimal('1.00'), max(Decimal('0.00'), to_decimal(theme_music)))
    ce = min(Decimal('3.00'), max(Decimal('0.00'), to_decimal(character_expression)))
    ras = min(Decimal('10.00'), max(Decimal('0.00'), to_decimal(rhythm_artistic_steps)))
    uf = min(Decimal('5.00'), max(Decimal('0.00'), to_decimal(use_of_floor)))
    ded = max(Decimal('0.00'), to_decimal(deductions))

    subtotal = c + tm + ce + ras + uf
    net = max(Decimal('0.000'), subtotal - ded)

    return {
        'costume': c,
        'theme_music': tm,
        'character_expression': ce,
        'rhythm_artistic_steps': ras,
        'use_of_floor': uf,
        'subtotal': quantize_score(subtotal),
        'deductions': quantize_score(ded),
        'net_artistry': quantize_score(net),
    }


# ============================================================================
# 6. TIME JUDGES & RECONCILIATION (MUSIC EVENTS)
# ============================================================================
def calculate_tw_marks(duration_seconds: Decimal) -> Decimal:
    """
    TW Marks:
    Below 150 seconds = 0.
    150–160 seconds = 3.
    161–170 seconds = 4.
    171–180 seconds = 5.
    """
    d = int(round(duration_seconds))
    if d < 150:
        return Decimal('0.000')
    elif 150 <= d <= 160:
        return Decimal('3.000')
    elif 161 <= d <= 170:
        return Decimal('4.000')
    else: # 171 <= d <= 180 (and above 180 base TW marks capped at 5)
        return Decimal('5.000')

def reconcile_music_timing(
    t1_tw: Decimal,
    t2_tw: Decimal,
    t1_holds: List[Decimal],
    t2_holds: List[Decimal]
) -> Dict[str, Any]:
    """
    Reconciles two independent Time Judges' readings for Music events.
    - TW whole seconds rounded with 0.5 rounded up.
    - 150-second margin rule: if one is below 150 and the other is >= 150 within 3 seconds, treated as 150s.
    - Holds:
      - T1=4 and T2=5: accepted = 5 (valid).
      - T1=4 and T2=4: accepted = 4 (invalid).
      - Accepted hold < 5s: TA is 0.0 for that item and marks item invalid for D judges!
    - Overtime: 0.5 marks per extra second after 180s, applied once.
    """
    # Round raw readings (0.5 rounded up)
    t1_sec = int(Decimal(str(t1_tw)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    t2_sec = int(Decimal(str(t2_tw)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))

    # Reconcile TW
    # 150-second margin check: e.g. 148 and 150 -> 150
    if (t1_sec < 150 and t2_sec >= 150 and (t2_sec - t1_sec) <= 3):
        reconciled_tw = Decimal('150')
    elif (t2_sec < 150 and t1_sec >= 150 and (t1_sec - t2_sec) <= 3):
        reconciled_tw = Decimal('150')
    else:
        reconciled_tw = Decimal(str(int(round((t1_sec + t2_sec) / 2.0))))

    tw_marks = calculate_tw_marks(reconciled_tw)

    # Overtime calculation: 0.5 marks per extra second after 180
    overtime_seconds = max(0, int(reconciled_tw) - 180)
    overtime_deduction = Decimal(str(overtime_seconds)) * Decimal('0.500')

    # Reconcile 10 asana holds
    hold_count = max(len(t1_holds), len(t2_holds), 10)
    reconciled_holds = []
    invalid_hold_indices = []
    ta_valid_count = 0

    for i in range(hold_count):
        h1 = int(Decimal(str(t1_holds[i] if i < len(t1_holds) else 0)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        h2 = int(Decimal(str(t2_holds[i] if i < len(t2_holds) else 0)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))

        # Explicit COP reconciliation:
        # One judge 4, other 5: accepted = 5
        if (h1 == 4 and h2 == 5) or (h1 == 5 and h2 == 4):
            accepted_h = 5
        elif h1 < 5 and h2 < 5:
            accepted_h = min(h1, h2)
        else:
            # Average or max
            accepted_h = max(h1, h2) if (h1 >= 5 or h2 >= 5) else min(h1, h2)

        is_valid_hold = (accepted_h >= 5)
        if is_valid_hold:
            ta_valid_count += 1
        else:
            invalid_hold_indices.append(i + 1) # 1-based sequence order

        reconciled_holds.append({
            'sequence_order': i + 1,
            't1_raw': h1,
            't2_raw': h2,
            'accepted_hold': accepted_h,
            'is_valid': is_valid_hold
        })

    # Each valid hold of at least 5s = 0.5 marks. 10 valid = max 5.0
    ta_marks = min(Decimal('5.000'), Decimal(str(ta_valid_count)) * Decimal('0.500'))
    timing_total = tw_marks + ta_marks

    return {
        't1_tw_seconds': t1_sec,
        't2_tw_seconds': t2_sec,
        'reconciled_tw_seconds': int(reconciled_tw),
        'tw_marks': quantize_score(tw_marks),
        'ta_valid_holds_count': ta_valid_count,
        'ta_marks': quantize_score(ta_marks),
        'reconciled_holds': reconciled_holds,
        'invalid_hold_indices': invalid_hold_indices,
        'overtime_seconds': overtime_seconds,
        'overtime_deduction': quantize_score(overtime_deduction),
        'timing_total': quantize_score(timing_total),
    }


# ============================================================================
# 7. PANEL AVERAGING (5/4/3 D JUDGES TRIMMED MEAN)
# ============================================================================
def calculate_panel_trimmed_mean(
    judge_totals: List[Tuple[str, Decimal]],
    confirmed_panel_size: int = 4
) -> Dict[str, Any]:
    """
    Applies confirmed Yogasana panel averaging to FULL performance totals:
    - Confirmed panel of 5: remove exactly 1 highest and 1 lowest full judge total; average remaining 3.
    - Confirmed panel of 4: remove exactly 1 highest and 1 lowest full judge total; average remaining 2.
    - Confirmed panel of 3: average all 3 without removing extremes.
    - When extremes are tied, remove exactly ONE occurrence at each end using a stable documented method.
    """
    if not judge_totals:
        return {
            'retained_average': Decimal('0.000'),
            'retained_scores': [],
            'discarded_scores': [],
            'all_scores': [],
            'formula_display': 'No submissions',
        }

    # Sort stably: score ascending, break ties by judge role name
    sorted_entries = sorted(judge_totals, key=lambda x: (x[1], x[0]))
    n = len(sorted_entries)

    # Missing submissions block completion; if less than confirmed panel size, cannot trim
    if n < confirmed_panel_size:
        return {
            'retained_average': Decimal('0.000'),
            'retained_scores': [{'role': x[0], 'score': quantize_score(x[1])} for x in sorted_entries],
            'discarded_scores': [],
            'all_scores': [{'role': x[0], 'score': quantize_score(x[1])} for x in sorted_entries],
            'is_incomplete': True,
            'formula_display': f'Incomplete Panel ({n}/{confirmed_panel_size} submitted)',
        }

    if confirmed_panel_size == 5:
        # Drop lowest (index 0) and highest (index -1), retain middle 3 (indices 1, 2, 3)
        discarded_low = sorted_entries[0]
        discarded_high = sorted_entries[-1]
        retained = sorted_entries[1:-1]
        divisor = Decimal('3.0')
        retained_sum = sum(x[1] for x in retained)
        retained_avg = retained_sum / divisor
        formula = f"({retained[0][1]} + {retained[1][1]} + {retained[2][1]}) / 3 = {quantize_score(retained_avg)}"
        discarded = [
            {'role': discarded_low[0], 'score': discarded_low[1], 'reason': 'Dropped Lowest Extreme'},
            {'role': discarded_high[0], 'score': discarded_high[1], 'reason': 'Dropped Highest Extreme'}
        ]

    elif confirmed_panel_size == 4:
        # Drop lowest (index 0) and highest (index -1), retain middle 2 (indices 1, 2)
        discarded_low = sorted_entries[0]
        discarded_high = sorted_entries[-1]
        retained = sorted_entries[1:-1]
        divisor = Decimal('2.0')
        retained_sum = sum(x[1] for x in retained)
        retained_avg = retained_sum / divisor
        formula = f"({retained[0][1]} + {retained[1][1]}) / 2 = {quantize_score(retained_avg)}"
        discarded = [
            {'role': discarded_low[0], 'score': discarded_low[1], 'reason': 'Dropped Lowest Extreme'},
            {'role': discarded_high[0], 'score': discarded_high[1], 'reason': 'Dropped Highest Extreme'}
        ]

    else: # Panel of 3 (or fallback)
        retained = sorted_entries
        divisor = Decimal(str(len(retained)))
        retained_sum = sum(x[1] for x in retained)
        retained_avg = retained_sum / divisor if divisor > 0 else Decimal('0.000')
        formula = f"({' + '.join(str(x[1]) for x in retained)}) / {len(retained)} = {quantize_score(retained_avg)}"
        discarded = []

    return {
        'retained_average': quantize_score(retained_avg),
        'retained_sum': quantize_score(retained_sum),
        'retained_scores': [{'role': x[0], 'score': quantize_score(x[1])} for x in retained],
        'discarded_scores': discarded,
        'all_scores': [{'role': x[0], 'score': quantize_score(x[1])} for x in sorted_entries],
        'is_incomplete': False,
        'formula_display': formula,
    }


# ============================================================================
# 8. MASTER CALCULATION SERVICE FOR A PERFORMANCE
# ============================================================================
def calculate_performance_score(performance, persist: bool = True) -> Dict[str, Any]:
    """
    Authoritative server-side scoring calculation for a performance.
    Runs on server for all screens, exports, live scoreboard, and CD review.

    Order of operations:
    1. Resolve confirmed panel & required seats.
    2. Check for missing submissions (Missing != 0).
    3. Process Evaluator decisions and Time Judge holds for invalid asanas.
    4. Compute raw item scores, apply confirmed invalidity (0.000 for affected items).
    5. Compute full judge totals for each D Judge.
    6. Apply panel trimmed mean (drop 1 high, 1 low on full totals; average remaining).
    7. Compute net Artistry average.
    8. Compute reconciled Timing average & overtime deduction.
    9. Apply approved separate evaluator penalties (deducted once).
    10. Compute final composite score and record audit metadata.
    """
    event_entry = performance.event_entry
    championship = event_entry.registration.championship
    event_config = event_entry.event_config
    event_type = (event_config.event_type if event_config else 'TRADITIONAL').upper()
    round_type = performance.round_type

    is_traditional = 'TRADITIONAL' in event_type
    is_artistic_single = 'ARTISTIC_SINGLE' in event_type or 'SINGLE' in event_type
    is_artistic_pair = 'ARTISTIC_PAIR' in event_type
    is_rhythmic_pair = 'RHYTHMIC_PAIR' in event_type or 'RHYTHMIC' in event_type

    # Determine confirmed panel size and required roles
    # Check if a confirmed JudgePanel exists for this division
    from competition.models import JudgePanel, PanelSeatAssignment
    from scoring.models import JudgeSubmission, PerformanceStatus, AsanaScoreItem
    from registrations.models import AsanaSequenceItem

    panel = JudgePanel.objects.filter(
        championship=championship,
        event_config=event_config,
        age_category=event_entry.age_category,
        gender=event_entry.gender,
        round_type=round_type,
        is_active=True
    ).first()

    # Confirmed D count from panel or default
    if panel and panel.d_judges_count in [3, 4, 5]:
        confirmed_d_count = panel.d_judges_count
    else:
        confirmed_d_count = 5 if is_traditional else 4

    # Build list of required roles
    required_d_roles = [f"D{i}_JUDGE" for i in range(1, confirmed_d_count + 1)]
    required_roles = list(required_d_roles)
    required_roles.append('TIME_JUDGE')
    if not is_traditional:
        required_roles.append('TIME_JUDGE_2')
        required_roles.append('ARTISTRY_JUDGE')
        required_roles.append('ARTISTRY_JUDGE_2')

    required_roles.append('EVALUATOR')
    if is_artistic_pair:
        required_roles.append('EVALUATOR_2')

    # Fetch submissions (both submitted and live in-progress/draft so matrix & preview sync live)
    all_submissions = list(JudgeSubmission.objects.filter(
        performance=performance
    ).select_related('judge').prefetch_related('items'))
    all_sub_by_role = {s.judge_role: s for s in all_submissions}

    # Only finalized/submitted submissions count toward official panel completeness
    submissions = [s for s in all_submissions if s.is_submitted]
    sub_by_role = {s.judge_role: s for s in submissions}

    # Seat status tracking
    seat_status = []
    missing_seats = []
    for r in required_roles:
        sub = sub_by_role.get(r)
        if not sub:
            missing_seats.append(r)
            seat_status.append({
                'role_code': r,
                'status': 'MISSING',
                'is_submitted': False,
                'score': None,
                'display_text': 'Missing / Pending'
            })
        else:
            seat_status.append({
                'role_code': r,
                'status': 'SUBMITTED',
                'is_submitted': True,
                'is_zero': sub.is_zero,
                'score': quantize_score(sub.effective_total),
                'display_text': f"{quantize_score(sub.effective_total):.3f}"
            })

    # Step 3: Determine Invalid Items across Evaluators and Time Judges
    invalid_item_sequences = set()
    invalid_reasons = {}

    # Check Evaluator submissions (respects invalid decisions across both live draft and submitted sheets)
    eval_subs = [s for s in all_submissions if s.judge_role in ['EVALUATOR', 'EVALUATOR_1', 'EVALUATOR_2']]
    eval1 = next((s for s in eval_subs if s.judge_role in ['EVALUATOR', 'EVALUATOR_1']), None)
    eval2 = next((s for s in eval_subs if s.judge_role == 'EVALUATOR_2'), None)
    for ev in eval_subs:
        for itm in ev.items.all():
            if not itm.is_valid:
                invalid_item_sequences.add(itm.sequence_order)
                invalid_reasons[itm.sequence_order] = itm.invalid_reason or "Evaluator Confirmed Invalid Asana"

    # Check Time Judges holds
    reconciled_timing_data = {}
    time_judge_1 = all_sub_by_role.get('TIME_JUDGE')
    time_judge_2 = all_sub_by_role.get('TIME_JUDGE_2')

    if not is_traditional and time_judge_1 and time_judge_2:
        t1_holds = [itm.hold_duration_seconds for itm in time_judge_1.items.all().order_by('sequence_order')]
        t2_holds = [itm.hold_duration_seconds for itm in time_judge_2.items.all().order_by('sequence_order')]
        t1_tw = time_judge_1.raw_total
        t2_tw = time_judge_2.raw_total

        reconciled_timing_data = reconcile_music_timing(t1_tw, t2_tw, t1_holds, t2_holds)
        # Propagate hold < 5s invalidations
        for inv_seq in reconciled_timing_data.get('invalid_hold_indices', []):
            invalid_item_sequences.add(inv_seq)
            invalid_reasons[inv_seq] = "Accepted Hold Duration Below 5 Seconds"

    # Step 4: Calculate D Judges items and totals
    from registrations.models import AsanaSequenceItem
    declared_seqs = {
        asi.sequence_order: asi
        for asi in AsanaSequenceItem.objects.filter(event_entry=event_entry, round_type=round_type)
    }
    if not declared_seqs and event_entry:
        declared_seqs = {
            asi.sequence_order: asi
            for asi in AsanaSequenceItem.objects.filter(event_entry=event_entry)
        }

    d_judge_totals = []
    compulsory_d_judge_totals = [] # For Traditional tie-break
    d_matrix = []
    sequence_count = 7 if is_traditional else 10

    # Number of compulsory asanas in Traditional:
    # Quarter-Final / Preliminary: 5 Compulsory, 2 Optional
    # Semi-Final: 5 Compulsory, 2 Optional
    # Final: 4 Compulsory, 3 Optional
    if is_traditional:
        rt = (round_type or '').upper()
        if 'FINAL' in rt and 'SEMI' not in rt:
            compulsory_count = 4
        else:
            compulsory_count = 5
    else:
        compulsory_count = 0

    for d_role in required_d_roles:
        d_sub = sub_by_role.get(d_role)
        if not d_sub:
            continue

        judge_effective_total = Decimal('0.000')
        judge_raw_total = Decimal('0.000')
        judge_compulsory_total = Decimal('0.000')

        items = sorted(list(d_sub.items.all()), key=lambda x: x.sequence_order)
        for itm in items:
            seq = itm.sequence_order
            is_inv = (seq in invalid_item_sequences)

            if is_traditional:
                decl = declared_seqs.get(seq)
                if decl and hasattr(decl, 'is_compulsory') and decl.is_compulsory is not None:
                    is_comp = decl.is_compulsory
                else:
                    is_comp = (seq <= compulsory_count)

                if is_comp:
                    resolved_bv = Decimal('1.00')
                elif decl and decl.declared_bv:
                    resolved_bv = decl.declared_bv
                elif itm.base_value and itm.base_value > Decimal('0.00'):
                    resolved_bv = itm.base_value
                else:
                    resolved_bv = Decimal('0.80')

                itm.base_value = resolved_bv

                time_contrib = itm.timing_contribution
                if time_judge_1:
                    tj_itm = next((ti for ti in time_judge_1.items.all() if ti.sequence_order == seq), None)
                    if tj_itm is not None:
                        time_contrib = tj_itm.timing_contribution

                calc_res = calculate_traditional_item(
                    posture=itm.posture_score,
                    stability_grace=itm.stability_score,
                    timing_contribution=time_contrib,
                    base_value=resolved_bv,
                    detailed_accuracy=itm.detailed_accuracy,
                    detailed_mounting_dismounting=itm.detailed_mounting_dismounting,
                    detailed_stability=itm.detailed_stability,
                    detailed_grace=itm.detailed_grace,
                    is_invalid=is_inv,
                    is_compulsory=is_comp
                )
            elif is_artistic_pair:
                calc_res = calculate_artistic_pair_item(
                    execution_1=itm.chest_a_execution,
                    bv_1=itm.chest_a_bv,
                    execution_2=itm.chest_b_execution,
                    bv_2=itm.chest_b_bv,
                    formation=itm.formation_score,
                    is_invalid=is_inv
                )
            elif is_rhythmic_pair:
                calc_res = calculate_rhythmic_pair_item(
                    execution_1=itm.chest_a_execution,
                    execution_2=itm.chest_b_execution,
                    shared_bv=itm.base_value,
                    is_invalid=is_inv
                )
            else: # Artistic Single
                calc_res = calculate_artistic_single_item(
                    execution=itm.execution_score,
                    base_value=itm.base_value,
                    is_invalid=is_inv
                )

            new_raw = calc_res['raw_item_score']
            new_eff = calc_res['effective_item_score']
            new_valid = not is_inv
            new_reason = invalid_reasons.get(seq, 'Invalid Asana') if is_inv else ''

            if persist and (
                itm.raw_item_score != new_raw or
                itm.effective_item_score != new_eff or
                itm.is_valid != new_valid or
                itm.invalid_reason != new_reason or
                itm.base_value != itm.base_value
            ):
                itm.raw_item_score = new_raw
                itm.effective_item_score = new_eff
                itm.is_valid = new_valid
                itm.invalid_reason = new_reason
                itm.save(update_fields=['base_value', 'raw_item_score', 'effective_item_score', 'is_valid', 'invalid_reason'])
            else:
                itm.raw_item_score = new_raw
                itm.effective_item_score = new_eff
                itm.is_valid = new_valid
                itm.invalid_reason = new_reason

            judge_raw_total += calc_res['raw_item_score']
            judge_effective_total += calc_res['effective_item_score']

            if is_traditional and seq <= compulsory_count:
                judge_compulsory_total += calc_res['effective_item_score']

        # Update submission totals
        new_sub_raw = quantize_score(judge_raw_total)
        new_sub_eff = quantize_score(judge_effective_total)
        if persist and (d_sub.raw_total != new_sub_raw or d_sub.effective_total != new_sub_eff):
            d_sub.raw_total = new_sub_raw
            d_sub.effective_total = new_sub_eff
            d_sub.save(update_fields=['raw_total', 'effective_total'])
        else:
            d_sub.raw_total = new_sub_raw
            d_sub.effective_total = new_sub_eff
        d_judge_totals.append((d_role, quantize_score(judge_effective_total)))
        compulsory_d_judge_totals.append((d_role, quantize_score(judge_compulsory_total)))

    # Assemble Asana-by-Asana matrix across all active judges (D1-D5, Time, Evaluator) live
    items_by_role_seq = {}
    for role_name, sub_obj in all_sub_by_role.items():
        if sub_obj:
            for itm in sub_obj.items.all():
                items_by_role_seq[(role_name, itm.sequence_order)] = itm

    for seq in range(1, sequence_count + 1):
        decl = declared_seqs.get(seq)
        first_itm = None
        for d_role in required_d_roles:
            first_itm = items_by_role_seq.get((d_role, seq))
            if first_itm:
                break

        code = decl.declared_code if decl and decl.declared_code else f"A{seq}"
        name = decl.declared_name if decl and decl.declared_name else f"Item {seq}"
        if is_traditional:
            is_seq_comp = decl.is_compulsory if (decl and hasattr(decl, 'is_compulsory') and decl.is_compulsory is not None) else (seq <= compulsory_count)
            if is_seq_comp:
                bv = Decimal('1.00')
            elif decl and decl.declared_bv:
                bv = decl.declared_bv
            elif first_itm and first_itm.base_value:
                bv = first_itm.base_value
            else:
                bv = Decimal('0.80')
        else:
            bv = first_itm.base_value if (first_itm and first_itm.base_value) else (decl.declared_bv if decl else Decimal('0.80'))

        def _get_item_marks(itm):
            if not itm:
                return Decimal('0.000'), Decimal('0.000')
            # Given raw score (posture + stability + time) * bv
            raw = Decimal('0.000')
            if itm.raw_item_score > Decimal('0.000'):
                raw = itm.raw_item_score
            elif itm.posture_score > Decimal('0.00') or itm.stability_score > Decimal('0.00'):
                raw = quantize_score(itm.posture_score + itm.stability_score + (itm.timing_contribution or Decimal('0.00')))
            elif itm.execution_score > Decimal('0.00'):
                raw = quantize_score(itm.execution_score * itm.base_value)
            else:
                raw = itm.effective_item_score
            eff = itm.effective_item_score
            return quantize_score(raw), quantize_score(eff)

        d1_itm = items_by_role_seq.get(('D1_JUDGE', seq))
        d2_itm = items_by_role_seq.get(('D2_JUDGE', seq))
        d3_itm = items_by_role_seq.get(('D3_JUDGE', seq))
        d4_itm = items_by_role_seq.get(('D4_JUDGE', seq))
        d5_itm = items_by_role_seq.get(('D5_JUDGE', seq))

        d1_raw, d1_val = _get_item_marks(d1_itm)
        d2_raw, d2_val = _get_item_marks(d2_itm)
        d3_raw, d3_val = _get_item_marks(d3_itm)
        d4_raw, d4_val = _get_item_marks(d4_itm)
        d5_raw, d5_val = _get_item_marks(d5_itm)

        # Time judge & Evaluator details for this sequence (checked across draft and submitted sheets)
        time_itm = items_by_role_seq.get(('TIME_JUDGE', seq))
        eval_itm = items_by_role_seq.get(('EVALUATOR', seq)) or items_by_role_seq.get(('EVALUATOR_1', seq))

        time_hold = time_itm.hold_duration_seconds if time_itm else None
        time_contrib = time_itm.timing_contribution if time_itm else None
        is_eval_valid = eval_itm.is_valid if eval_itm else True
        eval_reason = eval_itm.invalid_reason if (eval_itm and not eval_itm.is_valid) else ""

        all_d_at_seq = [d1_val, d2_val, d3_val, d4_val]
        if confirmed_d_count >= 5:
            all_d_at_seq.append(d5_val)

        item_mean = quantize_score(sum(all_d_at_seq) / Decimal(str(len(all_d_at_seq)))) if all_d_at_seq else Decimal('0.000')
        is_seq_invalid = (seq in invalid_item_sequences) or (not is_eval_valid) or (time_contrib is not None and time_contrib <= Decimal('0.00'))

        effective_reason = invalid_reasons.get(seq, '') or eval_reason
        if not effective_reason and time_contrib is not None and time_contrib <= Decimal('0.00'):
            effective_reason = "Hold < 75% (0.0s): Score is 0.000"

        d_matrix.append({
            'seq': seq,
            'code': code,
            'name': name,
            'bv': bv,
            'd1': d1_val,
            'd1_raw': d1_raw,
            'd2': d2_val,
            'd2_raw': d2_raw,
            'd3': d3_val,
            'd3_raw': d3_raw,
            'd4': d4_val,
            'd4_raw': d4_raw,
            'd5': d5_val,
            'd5_raw': d5_raw,
            'time_hold': time_hold,
            'time_contrib': time_contrib,
            'is_eval_valid': is_eval_valid,
            'eval_reason': eval_reason,
            'trimmed_mean': item_mean,
            'deduction': item_mean if is_seq_invalid else Decimal('0.00'),
            'net_item_score': Decimal('0.000') if is_seq_invalid else item_mean,
            'is_invalid': is_seq_invalid,
            'invalid_reason': effective_reason
        })

    d_dict = dict(d_judge_totals)
    d1_total = d_dict.get('D1_JUDGE', Decimal('0.000'))
    d2_total = d_dict.get('D2_JUDGE', Decimal('0.000'))
    d3_total = d_dict.get('D3_JUDGE', Decimal('0.000'))
    d4_total = d_dict.get('D4_JUDGE', Decimal('0.000'))
    d5_total = d_dict.get('D5_JUDGE', Decimal('0.000'))

    # Step 6: Apply Panel Trimmed Mean to D totals
    panel_result = calculate_panel_trimmed_mean(d_judge_totals, confirmed_panel_size=confirmed_d_count)
    retained_d_avg = panel_result['retained_average']

    # For Traditional tie-break: compulsory trimmed mean
    compulsory_panel_result = calculate_panel_trimmed_mean(compulsory_d_judge_totals, confirmed_panel_size=confirmed_d_count)
    compulsory_retained_avg = compulsory_panel_result['retained_average']

    # Step 7: Artistry Calculation (Music events)
    artistry_avg = Decimal('0.000')
    artistry_breakdown_data = []
    if not is_traditional:
        a1_sub = sub_by_role.get('ARTISTRY_JUDGE')
        a2_sub = sub_by_role.get('ARTISTRY_JUDGE_2')
        a_nets = []
        for a_sub in [a1_sub, a2_sub]:
            if a_sub:
                art_bd = getattr(a_sub, 'artistry_breakdown', None)
                if art_bd:
                    net_val = art_bd.calculate_total()
                    a_nets.append(net_val)
                    artistry_breakdown_data.append({
                        'judge_role': a_sub.judge_role,
                        'judge_name': a_sub.judge.get_full_name() or a_sub.judge.email,
                        'costume': art_bd.costume,
                        'theme_music': art_bd.theme_music,
                        'character_expression': art_bd.character_expression,
                        'rhythm_artistic_steps': art_bd.rhythm_artistic_steps,
                        'use_of_floor': art_bd.use_of_floor,
                        'deductions': art_bd.artistry_deductions or art_bd.deductions,
                        'net_score': quantize_score(net_val)
                    })
                else:
                    a_nets.append(a_sub.effective_total)
        if a_nets:
            artistry_avg = quantize_score(sum(a_nets) / Decimal(str(len(a_nets))))

    # Step 8: Timing Score (Music events)
    timing_score = Decimal('0.000')
    overtime_deduction = Decimal('0.000')
    if not is_traditional:
        if reconciled_timing_data:
            timing_score = reconciled_timing_data.get('timing_total', Decimal('0.000'))
            overtime_deduction = reconciled_timing_data.get('overtime_deduction', Decimal('0.000'))
        elif time_judge_1:
            timing_score = time_judge_1.effective_total

    # Step 9: Evaluator & T-Box Penalties (Applied Once, Separate from Invalidity)
    evaluator_penalties = Decimal('0.000')
    evaluator_incidents = []

    # 1. From EvaluatorIncident model (Page 17)
    from scoring.models import EvaluatorIncident
    db_incidents = EvaluatorIncident.objects.filter(performance=performance)
    seen_incidents = set()
    for inc in db_incidents:
        if inc.zeroes_item and inc.affected_item_numbers:
            for itm_str in inc.affected_item_numbers.split(','):
                try:
                    invalid_item_sequences.add(int(itm_str.strip()))
                    invalid_reasons[int(itm_str.strip())] = f"Evaluator: {inc.get_incident_type_display()}"
                except ValueError:
                    pass

        # Deduplicate shared incidents between E1 and E2 (Page 17: Shared incidents are not charged twice)
        inc_key = (inc.incident_type, inc.affected_item_numbers, inc.declared_code)
        # Evaluator penalties apply automatically without requiring Chief Judge confirmation unless rejected
        if inc_key not in seen_incidents and inc.chief_decision != 'REJECTED':
            seen_incidents.add(inc_key)
            evaluator_penalties += inc.penalty_marks
            evaluator_incidents.append({
                'id': inc.id,
                'type': inc.get_incident_type_display(),
                'penalty': str(inc.penalty_marks),
                'player': inc.player_target,
                'evaluator': inc.evaluator_seat,
                'reason': inc.evidence_notes or inc.get_incident_type_display(),
                'status': inc.chief_decision or 'APPROVED'
            })

    # 2. From metadata on submissions (backward compatible)
    for ev in [eval1, eval2]:
        if ev and hasattr(ev, 'calculation_metadata'):
            incidents = ev.calculation_metadata.get('penalties', [])
            for inc in incidents:
                inc_id = inc.get('id')
                if not any(x.get('id') == inc_id for x in evaluator_incidents):
                    pts = to_decimal(inc.get('points', 0))
                    evaluator_penalties += pts
                    evaluator_incidents.append(inc)

    # 3. Traditional T Penalties from Time Judge (Page 14)
    if is_traditional and time_judge_1:
        tj_meta = time_judge_1.calculation_metadata or {}
        for tp in tj_meta.get('traditional_t_penalties', []):
            p_val = to_decimal(tp.get('penalty_amount', 0))
            if p_val > Decimal('0.00'):
                evaluator_penalties += p_val
                evaluator_incidents.append({
                    'type': 'Traditional T Penalty Box',
                    'penalty': str(p_val),
                    'player': 'SOLO',
                    'evaluator': 'TIME_JUDGE',
                    'reason': tp.get('reason', 'Time judge deduction'),
                    'status': 'CHIEF_REVIEW'
                })

    # Step 10: Final Composite Score
    # Required fixture verification:
    # Retained D = 63.0, Artistry = 16.0, Timing = 8.5 -> Before penalties = 87.5
    # With penalty of 2.0 -> Final = 85.5.
    if is_traditional:
        final_composite = max(Decimal('0.000'), retained_d_avg - evaluator_penalties)
    else:
        before_penalties = retained_d_avg + artistry_avg + timing_score
        final_composite = max(Decimal('0.000'), before_penalties - evaluator_penalties - overtime_deduction)

    final_composite = quantize_score(final_composite)

    # Check completion readiness
    block_reasons = []
    if missing_seats:
        block_reasons.append(f"Missing required judge submissions: {', '.join(missing_seats)}")

    can_release = (len(block_reasons) == 0 and not panel_result.get('is_incomplete', False))

    audit_payload = {
        'calculation_rule_version': 'Yogasana Bharat COP 2025-26 India Amendments v1.5',
        'calculated_at': timezone.now().isoformat(),
        'event_type': event_type,
        'round_type': round_type,
        'confirmed_d_count': confirmed_d_count,
        'retained_d_average': str(retained_d_avg),
        'panel_trim_details': panel_result,
        'compulsory_retained_d_average': str(compulsory_retained_avg),
        'artistry_average': str(artistry_avg),
        'artistry_breakdown': artistry_breakdown_data,
        'timing_score': str(timing_score),
        'reconciled_timing_data': {k: str(v) if isinstance(v, Decimal) else v for k, v in reconciled_timing_data.items()},
        'overtime_deduction': str(overtime_deduction),
        'evaluator_penalties': str(evaluator_penalties),
        'evaluator_incidents': evaluator_incidents,
        'invalid_item_sequences': list(invalid_item_sequences),
        'final_composite_score': str(final_composite),
        'can_release': can_release,
        'block_reasons': block_reasons,
    }

    # Store calculation payload on performance
    serializable_payload = make_json_serializable(audit_payload)
    performance.calculation_data = serializable_payload
    if persist:
        try:
            performance.save(update_fields=['calculation_data'])
        except Exception:
            pass

    return {
        'performance': performance,
        'event_entry': event_entry,
        'championship': championship,
        'event_type': event_type,
        'is_traditional': is_traditional,
        'confirmed_d_count': confirmed_d_count,
        'seat_status': seat_status,
        'missing_seats': missing_seats,
        'd_judge_totals': d_judge_totals,
        'panel_trim_details': panel_result,
        'retained_d': retained_d_avg,
        'compulsory_retained_d': compulsory_retained_avg,
        'artistry_total': artistry_avg,
        'artistry_breakdown': artistry_breakdown_data,
        'timing_score': timing_score,
        'reconciled_timing': reconciled_timing_data,
        'overtime_deduction': overtime_deduction,
        'evaluator_penalties': evaluator_penalties,
        'evaluator_incidents': evaluator_incidents,
        'invalid_items': list(invalid_item_sequences),
        'd_matrix': d_matrix,
        'd1_total': d1_total,
        'd2_total': d2_total,
        'd3_total': d3_total,
        'd4_total': d4_total,
        'd5_total': d5_total,
        'penalties': evaluator_penalties,
        'd_trimmed_scores': [x['score'] if isinstance(x, dict) else quantize_score(x[1]) for x in panel_result.get('retained_scores', [])],
        'provisional_total': final_composite,
        'final_total': final_composite,
        'can_release': can_release,
        'block_reasons': block_reasons,
        'audit_payload': audit_payload,
    }


class TiePolicy:
    STANDARD = 'STANDARD'
    DENSE = 'DENSE'
    JOINT = 'JOINT'


# ============================================================================
# 9. TIE RESOLUTION & DIVISION STANDINGS
# ============================================================================
def calculate_event_division_standings(
    championship,
    event_config=None,
    age_category=None,
    gender: str = None,
    round_type: str = 'FINAL',
    tie_policy: str = 'STANDARD'
) -> List[Dict[str, Any]]:
    """
    Computes official standings for a division with confirmed COP tie-breaks:
    - Traditional: If final score is tied, compare compulsory-only score (5/4/3 panel average).
      If still tied, joint medal/rank supported.
    - Music Events: If final score is tied, compare average Artistry score.
      If still tied, joint medal/rank supported.
    """
    from scoring.models import Performance, PerformanceStatus

    qs = Performance.objects.filter(
        event_entry__registration__championship=championship,
        status__in=[PerformanceStatus.CD_FINALIZED, PerformanceStatus.PROVISIONAL_RELEASED, PerformanceStatus.DISQUALIFIED]
    )
    if event_config:
        qs = qs.filter(event_entry__event_config=event_config)
    if age_category:
        qs = qs.filter(event_entry__age_category=age_category)
    if gender:
        qs = qs.filter(event_entry__gender=gender)
    if round_type:
        qs = qs.filter(round_type=round_type)

    performances = list(qs.select_related(
        'event_entry__registration__athlete',
        'event_entry__partner_athlete',
        'event_entry__event_config',
        'event_entry__age_category'
    ))

    # Recalculate/ensure calculation_data is active
    scored_list = []
    for p in performances:
        if p.status == PerformanceStatus.DISQUALIFIED:
            scored_list.append({
                'performance': p,
                'final_score': Decimal('0.000'),
                'tie_break_score': Decimal('0.000'),
                'is_dq': True
            })
            continue

        calc = p.calculation_data or {}
        score = p.final_score if p.final_score is not None else (p.provisional_score or Decimal('0.000'))

        # Tie break score
        is_trad = 'TRADITIONAL' in (p.event_entry.event_config.event_type.upper() if p.event_entry.event_config else '')
        if is_trad:
            tie_break_score = to_decimal(calc.get('compulsory_retained_d_average', '0.000'))
        else:
            tie_break_score = to_decimal(calc.get('artistry_average', '0.000'))

        scored_list.append({
            'performance': p,
            'final_score': score,
            'tie_break_score': tie_break_score,
            'is_dq': False
        })

    # Sort: non-DQ first, then final_score descending, then tie_break_score descending, then stable ID
    sorted_performances = sorted(
        scored_list,
        key=lambda x: (not x['is_dq'], x['final_score'], x['tie_break_score'], -x['performance'].id),
        reverse=True
    )

    standings = []
    current_rank = 0
    items_processed = 0
    last_key = None

    for item in sorted_performances:
        p = item['performance']
        if item['is_dq']:
            standings.append({
                'performance': p,
                'event_entry': p.event_entry,
                'athlete': p.event_entry.registration.athlete,
                'partner': p.event_entry.partner_athlete if p.event_entry.is_pair else None,
                'score': Decimal('0.000'),
                'rank': None,
                'rank_label': "Disqualified (DQ)",
                'medal': None,
                'is_dq': True,
                'dq_reason': p.disqualification_reason or "Technical Rule Disqualification",
                'is_pair': p.event_entry.is_pair,
                'district': p.event_entry.district
            })
            continue

        items_processed += 1
        sort_key = (item['final_score'], item['tie_break_score'])
        if sort_key != last_key:
            current_rank = items_processed
            last_key = sort_key

        medal = None
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

        standings.append({
            'performance': p,
            'event_entry': p.event_entry,
            'athlete': p.event_entry.registration.athlete,
            'partner': p.event_entry.partner_athlete if p.event_entry.is_pair else None,
            'score': item['final_score'],
            'tie_break_score': item['tie_break_score'],
            'rank': current_rank,
            'rank_label': rank_label,
            'medal': medal,
            'is_dq': False,
            'is_pair': p.event_entry.is_pair,
            'district': p.event_entry.district
        })

    return standings


# ============================================================================
# 10. ROUND PROGRESSION (QF -> SF -> FINAL)
# ============================================================================
def compute_progression_slots(round_type: str, total_field: int) -> int:
    """
    Computes advancing qualifier slot count under confirmed COP progression rules:
    - QF to SF = min(20, 20% of the applicable field)
    - SF to Final = min(10, 50% of the applicable field)
    """
    if total_field <= 0:
        return 0
    if round_type in ('PRELIMINARY', 'QUARTER_FINAL', 'QF'):
        twenty_percent = int(round(total_field * 0.20))
        return min(20, max(1, twenty_percent))
    else:  # SEMI_FINAL to FINAL
        fifty_percent = int(round(total_field * 0.50))
        return min(10, max(1, fifty_percent))


def calculate_round_progression(
    championship,
    event_config,
    age_category,
    gender: str,
    from_round: str = 'PRELIMINARY'
) -> Dict[str, Any]:
    """
    Computes advancing qualifiers under confirmed COP progression rules:
    - QF to SF = min(20, 20% of the applicable field)
    - SF to Final = min(10, 50% of the applicable field)
    Unresolved details (cutoff ties, minimum cutoff) handled with explicit policy.
    """
    standings = calculate_event_division_standings(
        championship=championship,
        event_config=event_config,
        age_category=age_category,
        gender=gender,
        round_type=from_round
    )

    non_dq_standings = [s for s in standings if not s.get('is_dq')]
    total_field = len(non_dq_standings)

    target_qualifiers = compute_progression_slots(from_round, total_field)
    target_round = 'SEMI_FINAL' if from_round in ('PRELIMINARY', 'QUARTER_FINAL', 'QF') else 'FINAL'

    qualifiers = []
    eliminated = []

    for idx, s in enumerate(non_dq_standings):
        if idx < target_qualifiers:
            qualifiers.append(s)
        else:
            eliminated.append(s)

    return {
        'from_round': from_round,
        'target_round': target_round,
        'total_field': total_field,
        'target_qualifier_count': target_qualifiers,
        'qualifiers': qualifiers,
        'eliminated': eliminated,
        'unresolved_policy': 'PROGRESSION_CUTOFF_TIES',
    }


# ============================================================================
# 11. CHAMPIONSHIP TROPHY STANDINGS
# ============================================================================
def calculate_championship_trophy_standings(
    championship,
    ranking_method: str = 'POINTS_5_3_2',
    gold_pts: int = 5,
    silver_pts: int = 3,
    bronze_pts: int = 2
) -> List[Dict[str, Any]]:
    """
    Computes overall championship trophy standings using CD-finalized results.
    - Ranking profiles:
      - 'MEDAL_PRIORITY': Gold count descending, then Silver descending, then Bronze descending.
      - 'POINTS_5_3_2': gold*5 + silver*3 + bronze*2 points descending.
    - Pair rule: A pair medal counts ONCE for the district tally.
    """
    from scoring.models import Performance, PerformanceStatus
    from championships.models import TELANGANA_DISTRICTS

    # Find all Final CD_FINALIZED performances with medals
    final_perfs = Performance.objects.filter(
        event_entry__registration__championship=championship,
        round_type='FINAL',
        status=PerformanceStatus.CD_FINALIZED
    ).select_related(
        'event_entry__registration__athlete',
        'event_entry__event_config',
        'event_entry__age_category'
    )

    # Group by division and compute standings
    divisions = {}
    for p in final_perfs:
        ee = p.event_entry
        key = (ee.event_config_id, ee.age_category_id, ee.gender)
        if key not in divisions:
            divisions[key] = []
        divisions[key].append(p)

    district_tallies = {d: {'district': d, 'gold': 0, 'silver': 0, 'bronze': 0, 'points': 0, 'total_medals': 0} for d in TELANGANA_DISTRICTS}

    # Evaluate each division standings to assign medals
    for (e_id, cat_id, g), perfs in divisions.items():
        if not perfs:
            continue
        first_perf = perfs[0]
        div_standings = calculate_event_division_standings(
            championship=championship,
            event_config=first_perf.event_entry.event_config,
            age_category=first_perf.event_entry.age_category,
            gender=g,
            round_type='FINAL'
        )

        for s in div_standings:
            medal = s.get('medal')
            if medal in ['GOLD', 'SILVER', 'BRONZE']:
                dist = s['district']
                if dist in district_tallies:
                    if medal == 'GOLD':
                        district_tallies[dist]['gold'] += 1
                    elif medal == 'SILVER':
                        district_tallies[dist]['silver'] += 1
                    elif medal == 'BRONZE':
                        district_tallies[dist]['bronze'] += 1
                    district_tallies[dist]['total_medals'] += 1

    # Calculate points
    for dist, data in district_tallies.items():
        data['points'] = (data['gold'] * gold_pts) + (data['silver'] * silver_pts) + (data['bronze'] * bronze_pts)

    # Filter districts with at least one medal or point
    active_districts = [d for d in district_tallies.values() if d['total_medals'] > 0 or d['points'] > 0]

    if ranking_method == 'MEDAL_PRIORITY':
        sorted_tallies = sorted(active_districts, key=lambda x: (x['gold'], x['silver'], x['bronze'], x['points']), reverse=True)
    else: # POINTS_5_3_2
        sorted_tallies = sorted(active_districts, key=lambda x: (x['points'], x['gold'], x['silver'], x['bronze']), reverse=True)

    for rank, d in enumerate(sorted_tallies, start=1):
        d['rank'] = rank

    return sorted_tallies


# ============================================================================
# 12. RECALCULATION & CORRECTION PROPAGATION
# ============================================================================
@transaction.atomic
def recalculate_performance_after_correction(performance) -> Dict[str, Any]:
    """
    Executes authoritative recalculation after an authorized CD correction:
    1. Recalculates affected item scores and invalidity propagation.
    2. Recalculates full judge totals for each judge submission.
    3. Reselects retained/discarded extreme full totals.
    4. Recalculates final composite scores.
    5. Updates division standings and rankings.
    6. Returns updated calculation audit payload.
    """
    res = calculate_performance_score(performance)
    performance.provisional_score = res['provisional_total']
    if performance.final_score is not None:
        performance.final_score = res['final_total']
    performance.save()

    return res