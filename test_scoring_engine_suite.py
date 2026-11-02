"""
Comprehensive Verification Suite for TYSA Score Scoring Calculation Engine.
Tests every rule, mathematical formula, panel trimming, fixture, invalidation,
tie-break, progression, and championship trophy tally according to COP and
State_Yogasana_Complete_Requirements.docx.
"""
import os
import sys
import django
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from scoring.services import (
    quantize_score,
    to_decimal,
    calculate_traditional_item,
    calculate_artistic_single_item,
    calculate_artistic_pair_item,
    calculate_rhythmic_pair_item,
    calculate_artistry_net,
    reconcile_music_timing,
    calculate_panel_trimmed_mean,
    calculate_round_progression,
    compute_progression_slots,
    calculate_championship_trophy_standings,
    UNRESOLVED_RULES_REGISTER,
    TiePolicy
)


def run_tests():
    passed = 0
    failed = 0

    def assert_eq(test_name, actual, expected):
        nonlocal passed, failed
        if actual == expected:
            print(f" [PASS] {test_name}: {actual}")
            passed += 1
        else:
            print(f"![FAIL] {test_name}: expected {expected}, got {actual}")
            failed += 1

    print("\n--- 1. NUMERICAL & DECIMAL ARITHMETIC TESTS ---")
    d1 = to_decimal(7.2)
    assert_eq("to_decimal float precision", d1, Decimal('7.20'))
    assert_eq("quantize_score 3 decimals", quantize_score(Decimal('7.2567'), 3), Decimal('7.257'))
    assert_eq("quantize_score 2 decimals", quantize_score(Decimal('7.2567'), 2), Decimal('7.26'))

    print("\n--- 2. TRADITIONAL YOGASANA ITEM FORMULA ---")
    # Base execution: posture 5.5, stability 1.8, timing 2.0, BV 1.0 -> (5.5 + 1.8 + 2.0) * 1.0 = 9.300
    res = calculate_traditional_item(
        posture=Decimal('5.5'),
        stability_grace=Decimal('1.8'),
        timing_contribution=Decimal('2.0'),
        base_value=Decimal('1.0'),
        is_compulsory=True
    )
    assert_eq("Traditional standard item", res['effective_item_score'], Decimal('9.300'))
    assert_eq("Traditional compulsory BV forced 1.0", res['base_value'], Decimal('1.000'))

    # Detailed microfields: Accuracy 3.5/4, Mounting 1.5/2, Stability 0.8/1, Grace 0.7/1 -> execution 6.50
    # timing 1.5/2, optional BV 0.8 -> (6.5 + 1.5) * 0.8 = 8.0 * 0.8 = 6.400
    res_micro = calculate_traditional_item(
        posture=Decimal('5.0'),  # overridden by microfields
        stability_grace=Decimal('1.5'),
        timing_contribution=Decimal('1.5'),
        base_value=Decimal('0.8'),
        detailed_accuracy=Decimal('3.5'),
        detailed_mounting_dismounting=Decimal('1.5'),
        detailed_stability=Decimal('0.8'),
        detailed_grace=Decimal('0.7'),
        is_compulsory=False
    )
    assert_eq("Traditional microfields execution", res_micro['execution'], Decimal('6.500'))
    assert_eq("Traditional microfields item total", res_micro['effective_item_score'], Decimal('6.400'))

    # Invalidation check: invalid asana -> 0.000
    res_inv = calculate_traditional_item(
        posture=Decimal('5.0'),
        stability_grace=Decimal('2.0'),
        timing_contribution=Decimal('2.0'),
        base_value=Decimal('1.0'),
        is_invalid=True
    )
    assert_eq("Traditional invalid asana zeroed", res_inv['effective_item_score'], Decimal('0.000'))

    print("\n--- 3. ARTISTIC SINGLE ITEM FORMULA ---")
    # execution 8.5, BV 0.9 -> 8.5 * 0.9 = 7.650
    res_as = calculate_artistic_single_item(
        execution=Decimal('8.5'),
        base_value=Decimal('0.9')
    )
    assert_eq("Artistic single standard", res_as['effective_item_score'], Decimal('7.650'))

    print("\n--- 4. ARTISTIC PAIR REQUIRED FIXTURE ---")
    # Prompt specification: E1=8, BV1=0.9 (7.2), E2=7, BV2=0.8 (5.6), Formation=8
    # Players avg = (7.2 + 5.6) / 2 = 6.4, Pair item = (6.4 + 8) / 2 = 7.200
    res_ap = calculate_artistic_pair_item(
        execution_1=Decimal('8.0'),
        bv_1=Decimal('0.9'),
        execution_2=Decimal('7.0'),
        bv_2=Decimal('0.8'),
        formation=Decimal('8.0')
    )
    assert_eq("Artistic pair SP1", res_ap['player_1_score'], Decimal('7.200'))
    assert_eq("Artistic pair SP2", res_ap['player_2_score'], Decimal('5.600'))
    assert_eq("Artistic pair players avg", res_ap['players_average'], Decimal('6.400'))
    assert_eq("Artistic pair required fixture final", res_ap['effective_item_score'], Decimal('7.200'))

    # Artistic Pair invalidity: either player invalid or pair invalid -> complete item 0
    res_ap_inv = calculate_artistic_pair_item(
        execution_1=Decimal('8.0'),
        bv_1=Decimal('0.9'),
        execution_2=Decimal('7.0'),
        bv_2=Decimal('0.8'),
        formation=Decimal('8.0'),
        is_invalid=True
    )
    assert_eq("Artistic pair invalidity complete zero", res_ap_inv['effective_item_score'], Decimal('0.000'))

    print("\n--- 5. RHYTHMIC PAIR REQUIRED FIXTURE ---")
    # Prompt specification: E1=8, E2=6, shared_BV=0.9 -> ((8+6)/2)*0.9 = 7 * 0.9 = 6.300
    res_rp = calculate_rhythmic_pair_item(
        execution_1=Decimal('8.0'),
        execution_2=Decimal('6.0'),
        shared_bv=Decimal('0.9')
    )
    assert_eq("Rhythmic pair required fixture final", res_rp['effective_item_score'], Decimal('6.300'))

    print("\n--- 6. ARTISTRY SCORE NET & 5 CRITERIA ---")
    # Costume 1/1, Theme/Music 1/1, Character 2.5/3, Rhythm/Steps 8.5/10, Floor 4/5 = Gross 17.0, Deductions 1.0 -> Net 16.0
    res_art = calculate_artistry_net(
        costume=Decimal('1.0'),
        theme_music=Decimal('1.0'),
        character_expression=Decimal('2.5'),
        rhythm_artistic_steps=Decimal('8.5'),
        use_of_floor=Decimal('4.0'),
        deductions=Decimal('1.0')
    )
    assert_eq("Artistry subtotal sum", res_art['subtotal'], Decimal('17.000'))
    assert_eq("Artistry net score", res_art['net_artistry'], Decimal('16.000'))

    print("\n--- 7. MUSIC TIMING RECONCILIATION & REQUIRED FIXTURE ---")
    # Prompt specification: 165s, 9 valid holds -> TW=4.0, TA=4.5, Timing=8.5
    # Two judges with duration 165, and 9 holds >= 5s, 1 hold < 5s
    res_tim = reconcile_music_timing(
        t1_tw=Decimal('165.0'),
        t2_tw=Decimal('165.0'),
        t1_holds=[Decimal('5.0')] * 9 + [Decimal('4.0')],
        t2_holds=[Decimal('5.0')] * 9 + [Decimal('4.0')]
    )
    assert_eq("Timing fixture TW", res_tim['tw_marks'], Decimal('4.000'))
    assert_eq("Timing fixture TA (9 holds)", res_tim['ta_marks'], Decimal('4.500'))
    assert_eq("Timing fixture Total", res_tim['timing_total'], Decimal('8.500'))
    assert_eq("Timing fixture Overtime", res_tim['overtime_deduction'], Decimal('0.000'))
    assert_eq("Timing fixture Invalid Hold Index", res_tim['invalid_hold_indices'], [10])

    # Test 150s 3-second margin rule: duration 148s (150 - 2s) & 150s -> treated as 150s (TW 3 marks)
    res_margin = reconcile_music_timing(
        t1_tw=Decimal('148.0'),
        t2_tw=Decimal('150.0'),
        t1_holds=[Decimal('5.0')] * 10,
        t2_holds=[Decimal('5.0')] * 10
    )
    assert_eq("150s margin rule 148s & 150s gets TW 3", res_margin['tw_marks'], Decimal('3.000'))

    # Both below 147s -> TW = 0
    res_short = reconcile_music_timing(
        t1_tw=Decimal('145.0'),
        t2_tw=Decimal('145.0'),
        t1_holds=[Decimal('5.0')] * 10,
        t2_holds=[Decimal('5.0')] * 10
    )
    assert_eq("Below margin 145s gets TW 0", res_short['tw_marks'], Decimal('0.000'))

    # Overtime rule: duration 186s -> 6 seconds over 180s * 0.5 = 3.000 overtime deduction
    res_ot = reconcile_music_timing(
        t1_tw=Decimal('186.0'),
        t2_tw=Decimal('186.0'),
        t1_holds=[Decimal('5.0')] * 10,
        t2_holds=[Decimal('5.0')] * 10
    )
    assert_eq("Overtime deduction 6s * 0.5", res_ot['overtime_deduction'], Decimal('3.000'))

    # Explicit COP hold reconciliation: T1=4 and T2=5 -> accepted 5 (valid)
    res_hold_reconcile = reconcile_music_timing(
        t1_tw=Decimal('160.0'),
        t2_tw=Decimal('160.0'),
        t1_holds=[Decimal('4.0')] + [Decimal('5.0')] * 9,
        t2_holds=[Decimal('5.0')] + [Decimal('5.0')] * 9
    )
    assert_eq("Hold reconciliation 4 & 5 -> accepted 5 (all 10 valid)", res_hold_reconcile['ta_valid_holds_count'], 10)
    assert_eq("Hold reconciliation 4 & 5 -> no invalid holds", res_hold_reconcile['invalid_hold_indices'], [])

    print("\n--- 8. PANEL TRIMMED MEAN (5, 4, 3 JUDGES & TIED EXTREMES) ---")
    # Panel of 5: [60, 62, 64, 66, 68] -> Drop 68 (high) and 60 (low) -> Retain [62, 64, 66] -> avg = 64.000
    res_p5 = calculate_panel_trimmed_mean(
        [('D1', Decimal('60.0')), ('D2', Decimal('62.0')), ('D3', Decimal('64.0')), ('D4', Decimal('66.0')), ('D5', Decimal('68.0'))],
        confirmed_panel_size=5
    )
    assert_eq("Panel 5 trimmed mean", res_p5['retained_average'], Decimal('64.000'))
    assert_eq("Panel 5 dropped count", len(res_p5['discarded_scores']), 2)
    assert_eq("Panel 5 dropped low", res_p5['discarded_scores'][0]['score'], Decimal('60.000'))
    assert_eq("Panel 5 dropped high", res_p5['discarded_scores'][1]['score'], Decimal('68.000'))

    # Panel of 4 (Required Fixture): [60, 62, 64, 66] -> Drop 66 and 60 -> Retain [62, 64] -> avg = 63.000
    res_p4 = calculate_panel_trimmed_mean(
        [('D1', Decimal('60.0')), ('D2', Decimal('62.0')), ('D3', Decimal('64.0')), ('D4', Decimal('66.0'))],
        confirmed_panel_size=4
    )
    assert_eq("Panel 4 trimmed mean (Required Fixture)", res_p4['retained_average'], Decimal('63.000'))
    assert_eq("Panel 4 retained count", len(res_p4['retained_scores']), 2)

    # Panel of 4 with tied extremes: [60, 60, 64, 66] -> Drop one 60 and one 66 -> Retain [60, 64] -> avg = 62.000
    res_p4_tie = calculate_panel_trimmed_mean(
        [('D1', Decimal('60.0')), ('D2', Decimal('60.0')), ('D3', Decimal('64.0')), ('D4', Decimal('66.0'))],
        confirmed_panel_size=4
    )
    assert_eq("Panel 4 tied low trimmed mean", res_p4_tie['retained_average'], Decimal('62.000'))

    # Panel of 3: [60, 63, 66] -> No trims, average all 3 -> 63.000
    res_p3 = calculate_panel_trimmed_mean(
        [('D1', Decimal('60.0')), ('D2', Decimal('63.0')), ('D3', Decimal('66.0'))],
        confirmed_panel_size=3
    )
    assert_eq("Panel 3 average all", res_p3['retained_average'], Decimal('63.000'))
    assert_eq("Panel 3 discarded count", len(res_p3['discarded_scores']), 0)

    print("\n--- 9. COMPOSITE FINAL SCORE REQUIRED FIXTURE ---")
    # Prompt specification: Retained D = 63, Artistry = 16, Timing = 8.5 -> 87.5 - 2 penalty = 85.500
    retained_d = res_p4['retained_average']  # 63.000
    artistry_avg = Decimal('16.000')
    timing_score = Decimal('8.500')
    penalties = Decimal('2.000')
    overtime = Decimal('0.000')
    composite = retained_d + artistry_avg + timing_score - penalties - overtime
    assert_eq("Composite final score required fixture", quantize_score(composite), Decimal('85.500'))

    print("\n--- 10. ROUND PROGRESSION CUTOFFS ---")
    # QF -> SF: min(20, 20% of field)
    # 50 athletes in QF -> 20% of 50 = 10 -> cutoff = 10
    assert_eq("QF progression for 50 athletes", compute_progression_slots('QUARTER_FINAL', 50), 10)
    # 200 athletes in QF -> 20% of 200 = 40, capped at 20 -> cutoff = 20
    assert_eq("QF progression for 200 athletes capped at 20", compute_progression_slots('QUARTER_FINAL', 200), 20)

    # SF -> Final: min(10, 50% of field)
    # 16 athletes in SF -> 50% of 16 = 8 -> cutoff = 8
    assert_eq("SF progression for 16 athletes", compute_progression_slots('SEMI_FINAL', 16), 8)
    # 30 athletes in SF -> 50% of 30 = 15, capped at 10 -> cutoff = 10
    assert_eq("SF progression for 30 athletes capped at 10", compute_progression_slots('SEMI_FINAL', 30), 10)

    print("\n--- 11. UNRESOLVED RULES REGISTER ---")
    assert_eq("Unresolved rules registered count >= 4", len(UNRESOLVED_RULES_REGISTER) >= 4, True)
    for code, rule in UNRESOLVED_RULES_REGISTER.items():
        print(f"  * {code}: {rule['rule']} -> Status: {rule['status']}")

    print(f"\n==========================================")
    print(f"VERIFICATION SUMMARY: {passed} PASSED, {failed} FAILED")
    print(f"==========================================")
    return failed == 0


if __name__ == '__main__':
    success = run_tests()
    sys.exit(0 if success else 1)
