import pytest
from decimal import Decimal
from scoring.services import (
    calculate_tw_marks,
    reconcile_music_timing,
    calculate_panel_trimmed_mean,
    quantize_score,
    calculate_performance_score,
)
from scoring.models import Performance, JudgeSubmission, AsanaScoreItem, PerformanceStatus
from registrations.models import EventEntry, AsanaSequenceItem
from championships.models import EventConfiguration, EventType
from accounts.models import User

@pytest.mark.django_db
def test_tw_scoring_bands():
    # Below 150s = 0 marks
    assert calculate_tw_marks(Decimal('149')) == Decimal('0.000')
    assert calculate_tw_marks(Decimal('120')) == Decimal('0.000')
    assert calculate_tw_marks(Decimal('0')) == Decimal('0.000')

    # 150-160s = 3 marks
    assert calculate_tw_marks(Decimal('150')) == Decimal('3.000')
    assert calculate_tw_marks(Decimal('155')) == Decimal('3.000')
    assert calculate_tw_marks(Decimal('160')) == Decimal('3.000')

    # 161-170s = 4 marks
    assert calculate_tw_marks(Decimal('161')) == Decimal('4.000')
    assert calculate_tw_marks(Decimal('165')) == Decimal('4.000')
    assert calculate_tw_marks(Decimal('170')) == Decimal('4.000')

    # 171-180s = 5 marks
    assert calculate_tw_marks(Decimal('171')) == Decimal('5.000')
    assert calculate_tw_marks(Decimal('175')) == Decimal('5.000')
    assert calculate_tw_marks(Decimal('180')) == Decimal('5.000')

@pytest.mark.django_db
def test_150_second_margin_rule():
    # 148s and 150s: diff is 2 (<=3), one below 150, one >= 150 -> accepted = 150s
    res1 = reconcile_music_timing(Decimal('148'), Decimal('150'), [], [])
    assert res1['reconciled_tw_seconds'] == 150
    assert res1['tw_marks'] == Decimal('3.000')

    # 150s and 147s: diff is 3 (<=3) -> accepted = 150s
    res2 = reconcile_music_timing(Decimal('150'), Decimal('147'), [], [])
    assert res2['reconciled_tw_seconds'] == 150
    assert res2['tw_marks'] == Decimal('3.000')

    # 146s and 150s: diff is 4 (>3) -> regular average: (146+150)/2 = 148s (<150 -> 0 marks)
    # and flags tw_review_required = True
    res3 = reconcile_music_timing(Decimal('146'), Decimal('150'), [], [])
    assert res3['reconciled_tw_seconds'] == 148
    assert res3['tw_marks'] == Decimal('0.000')
    assert res3['tw_review_required'] is True
    assert "Chief Judge review required" in res3['tw_review_reason']

@pytest.mark.django_db
def test_hold_duration_reconciliation_4_5_and_4_4():
    # T1 = 4s, T2 = 5s -> accepted = 5s (valid)
    res_4_5 = reconcile_music_timing(Decimal('165'), Decimal('165'), [Decimal('4')], [Decimal('5')])
    assert res_4_5['reconciled_holds'][0]['accepted_hold'] == 5
    assert res_4_5['reconciled_holds'][0]['is_valid'] is True
    assert 1 not in res_4_5['invalid_hold_indices']

    # T1 = 5s, T2 = 4s -> accepted = 5s (valid)
    res_5_4 = reconcile_music_timing(Decimal('165'), Decimal('165'), [Decimal('5')], [Decimal('4')])
    assert res_5_4['reconciled_holds'][0]['accepted_hold'] == 5
    assert res_5_4['reconciled_holds'][0]['is_valid'] is True
    assert 1 not in res_5_4['invalid_hold_indices']

    # T1 = 4s, T2 = 4s -> accepted = 4s (invalid, hold < 5s)
    res_4_4 = reconcile_music_timing(Decimal('165'), Decimal('165'), [Decimal('4')], [Decimal('4')])
    assert res_4_4['reconciled_holds'][0]['accepted_hold'] == 4
    assert res_4_4['reconciled_holds'][0]['is_valid'] is False
    assert 1 in res_4_4['invalid_hold_indices']

    # Limitation check: One judge gives 3s, other gives 6s or 7s -> accepted = 0s (invalid / zero)
    res_3_6 = reconcile_music_timing(Decimal('165'), Decimal('165'), [Decimal('3')], [Decimal('6')])
    assert res_3_6['reconciled_holds'][0]['accepted_hold'] == 0
    assert res_3_6['reconciled_holds'][0]['is_valid'] is False
    assert 1 in res_3_6['invalid_hold_indices']

    res_7_3 = reconcile_music_timing(Decimal('165'), Decimal('165'), [Decimal('7')], [Decimal('3')])
    assert res_7_3['reconciled_holds'][0]['accepted_hold'] == 0
    assert res_7_3['reconciled_holds'][0]['is_valid'] is False
    assert 1 in res_7_3['invalid_hold_indices']

    # One judge 4s, other 6s or 7s (>5s) -> wide discrepancy -> 0s
    res_4_6 = reconcile_music_timing(Decimal('165'), Decimal('165'), [Decimal('4')], [Decimal('6')])
    assert res_4_6['reconciled_holds'][0]['accepted_hold'] == 0
    assert res_4_6['reconciled_holds'][0]['is_valid'] is False
    assert 1 in res_4_6['invalid_hold_indices']

@pytest.mark.django_db
def test_overtime_rules_and_deductions():
    # 180s: 0 overtime
    res_180 = reconcile_music_timing(Decimal('180'), Decimal('180'), [], [])
    assert res_180['overtime_seconds'] == 0
    assert res_180['overtime_deduction'] == Decimal('0.000')

    # 185s: 5s overtime -> 5 * 0.50 = 2.50 marks deduction
    res_185 = reconcile_music_timing(Decimal('185'), Decimal('185'), [], [])
    assert res_185['overtime_seconds'] == 5
    assert res_185['overtime_deduction'] == Decimal('2.500')
    assert res_185['is_disqualified'] is False

    # 190s: >= 190s triggers disqualification
    res_190 = reconcile_music_timing(Decimal('190'), Decimal('190'), [], [])
    assert res_190['overtime_seconds'] == 10
    assert res_190['is_disqualified'] is True
    assert "exceeded permitted 10-second extension" in res_190['disqualification_reason']
    assert res_190['tw_marks'] == Decimal('0.000')

    # 192s: > 190s triggers disqualification
    res_192 = reconcile_music_timing(Decimal('192'), Decimal('192'), [], [])
    assert res_192['is_disqualified'] is True

@pytest.mark.django_db
def test_rounding_rules_0_4_down_0_5_up():
    from decimal import ROUND_HALF_UP
    # 0.4 rounds down, 0.5 rounds up, 0.6 rounds up
    assert int(Decimal('4.4').quantize(Decimal('1'), rounding=ROUND_HALF_UP)) == 4
    assert int(Decimal('4.5').quantize(Decimal('1'), rounding=ROUND_HALF_UP)) == 5
    assert int(Decimal('4.6').quantize(Decimal('1'), rounding=ROUND_HALF_UP)) == 5
    assert int(Decimal('165.4').quantize(Decimal('1'), rounding=ROUND_HALF_UP)) == 165
    assert int(Decimal('165.5').quantize(Decimal('1'), rounding=ROUND_HALF_UP)) == 166

@pytest.mark.django_db
def test_mandatory_calculation_example_87_5():
    """
    Mandatory calculation example from prompt & specification:
    Both Time Judges record 165 seconds.
    Nine asana holds are valid (>=5s), one asana hold is invalid (<5s).
    - TW = 4 marks.
    - TA = 9 * 0.50 = 4.50 marks.
    - Total Timing = 8.50 marks.
    Recalculated D Judge totals: 60, 62, 64, 66 -> Trimmed mean = (62 + 64) / 2 = 63.00.
    Artistry score = 16.00.
    Final composite score before other penalties: 63 + 16 + 8.5 = 87.50.
    """
    # 1. Timing reconciliation
    t1_holds = [Decimal('5.0')] * 9 + [Decimal('4.0')]
    t2_holds = [Decimal('5.0')] * 9 + [Decimal('4.0')]
    timing_res = reconcile_music_timing(Decimal('165.0'), Decimal('165.0'), t1_holds, t2_holds)
    assert timing_res['tw_marks'] == Decimal('4.000')
    assert timing_res['ta_marks'] == Decimal('4.500')
    assert timing_res['timing_total'] == Decimal('8.500')
    assert timing_res['invalid_hold_indices'] == [10]

    # 2. D Judge trimmed mean
    d_totals = [
        ('D1_JUDGE', Decimal('60.0')),
        ('D2_JUDGE', Decimal('62.0')),
        ('D3_JUDGE', Decimal('64.0')),
        ('D4_JUDGE', Decimal('66.0')),
    ]
    d_res = calculate_panel_trimmed_mean(d_totals, confirmed_panel_size=4)
    assert d_res['retained_average'] == Decimal('63.000')

    # 3. Artistry score
    artistry_score = Decimal('16.000')

    # 4. Final composite score before penalties
    final_score = d_res['retained_average'] + artistry_score + timing_res['timing_total']
    assert final_score == Decimal('87.500')


@pytest.mark.django_db
def test_all_tw_seconds_progression_table():
    """
    Test exact progression across required boundaries:
    149s, 150s, 160s, 161s, 170s, 171s, 180s, 181s, 182s, 189s, 190s.
    """
    # 149s: 0 marks, 0 overtime
    r149 = reconcile_music_timing(Decimal('149'), Decimal('149'), [], [])
    assert r149['gross_tw_marks'] == Decimal('0.000')
    assert r149['overtime_seconds'] == 0
    assert r149['overtime_deduction'] == Decimal('0.000')
    assert r149['net_tw_marks'] == Decimal('0.000')
    assert r149['is_disqualified'] is False

    # 150s: 3 marks, 0 overtime
    r150 = reconcile_music_timing(Decimal('150'), Decimal('150'), [], [])
    assert r150['gross_tw_marks'] == Decimal('3.000')
    assert r150['overtime_seconds'] == 0
    assert r150['overtime_deduction'] == Decimal('0.000')
    assert r150['net_tw_marks'] == Decimal('3.000')

    # 160s: 3 marks, 0 overtime
    r160 = reconcile_music_timing(Decimal('160'), Decimal('160'), [], [])
    assert r160['gross_tw_marks'] == Decimal('3.000')
    assert r160['overtime_seconds'] == 0

    # 161s: 4 marks, 0 overtime
    r161 = reconcile_music_timing(Decimal('161'), Decimal('161'), [], [])
    assert r161['gross_tw_marks'] == Decimal('4.000')
    assert r161['overtime_seconds'] == 0

    # 170s: 4 marks, 0 overtime
    r170 = reconcile_music_timing(Decimal('170'), Decimal('170'), [], [])
    assert r170['gross_tw_marks'] == Decimal('4.000')
    assert r170['overtime_seconds'] == 0

    # 171s: 5 marks, 0 overtime
    r171 = reconcile_music_timing(Decimal('171'), Decimal('171'), [], [])
    assert r171['gross_tw_marks'] == Decimal('5.000')
    assert r171['overtime_seconds'] == 0

    # 180s: 5 marks, 0 overtime
    r180 = reconcile_music_timing(Decimal('180'), Decimal('180'), [], [])
    assert r180['gross_tw_marks'] == Decimal('5.000')
    assert r180['overtime_seconds'] == 0
    assert r180['overtime_deduction'] == Decimal('0.000')
    assert r180['net_tw_marks'] == Decimal('5.000')

    # 181s: 5 marks gross, 1s overtime -> 0.50 deduction, net = 4.50
    r181 = reconcile_music_timing(Decimal('181'), Decimal('181'), [], [])
    assert r181['gross_tw_marks'] == Decimal('5.000')
    assert r181['overtime_seconds'] == 1
    assert r181['overtime_deduction'] == Decimal('0.500')
    assert r181['net_tw_marks'] == Decimal('4.500')

    # 182s: 5 marks gross, 2s overtime -> 1.00 deduction, net = 4.00
    r182 = reconcile_music_timing(Decimal('182'), Decimal('182'), [], [])
    assert r182['gross_tw_marks'] == Decimal('5.000')
    assert r182['overtime_seconds'] == 2
    assert r182['overtime_deduction'] == Decimal('1.000')
    assert r182['net_tw_marks'] == Decimal('4.000')

    # 189s: 5 marks gross, 9s overtime -> 4.50 deduction, net = 0.50
    r189 = reconcile_music_timing(Decimal('189'), Decimal('189'), [], [])
    assert r189['gross_tw_marks'] == Decimal('5.000')
    assert r189['overtime_seconds'] == 9
    assert r189['overtime_deduction'] == Decimal('4.500')
    assert r189['net_tw_marks'] == Decimal('0.500')
    assert r189['is_disqualified'] is False

    # 190s: >= 190s disqualification threshold (exceeded 10s extension)
    r190 = reconcile_music_timing(Decimal('190'), Decimal('190'), [], [])
    assert r190['is_disqualified'] is True
    assert r190['overtime_seconds'] == 10
    assert r190['gross_tw_marks'] == Decimal('0.000')
    assert r190['net_tw_marks'] == Decimal('0.000')


@pytest.mark.django_db
def test_screenshot_regression_case():
    """
    Mandatory screenshot verification test case:
    T1 = 183s, T2 = 182s.
    Accepted TW = 182s.
    Gross TW marks = 5.0.
    Overtime = 2 seconds.
    Overtime deduction = 1.0 mark.
    Net TW contribution = 4.0 marks.

    10 holds:
    Asana 1: T1=4s, T2=5s -> Accepted 5s (Valid)
    Asana 2: T1=5s, T2=5s -> Accepted 5s (Valid)
    Asana 3: T1=5s, T2=5s -> Accepted 5s (Valid)
    Asana 4: T1=6s, T2=5s -> Accepted 5s (Valid)
    Asana 5: T1=7s, T2=5s -> Accepted 5s (Valid)
    Asana 6: T1=8s, T2=5s -> Accepted 5s (Valid)
    Asana 7: T1=10s, T2=5s -> Accepted 5s (Valid)
    Asana 8: T1=4s, T2=5s -> Accepted 5s (Valid)
    Asana 9: T1=2s, T2=5s -> Accepted 0s (Invalid / Wide Discrepancy)
    Asana 10: T1=5s, T2=5s -> Accepted 5s (Valid)

    Total valid holds = 9. Total invalid holds = 1.
    TA Marks = 9 * 0.5 = 4.50 marks.
    Final Timing Contribution = Net TW (4.0) + TA (4.5) = 8.50 marks.
    """
    t1_holds = [Decimal(s) for s in [4, 5, 5, 6, 7, 8, 10, 4, 2, 5]]
    t2_holds = [Decimal(s) for s in [5, 5, 5, 5, 5, 5, 5, 5, 5, 5]]

    res = reconcile_music_timing(Decimal('183'), Decimal('182'), t1_holds, t2_holds)

    assert res['t1_tw_seconds'] == 183
    assert res['t2_tw_seconds'] == 182
    assert res['reconciled_tw_seconds'] == 182
    assert res['gross_tw_marks'] == Decimal('5.000')
    assert res['overtime_seconds'] == 2
    assert res['overtime_deduction'] == Decimal('1.000')
    assert res['net_tw_marks'] == Decimal('4.000')

    assert res['ta_valid_holds_count'] == 9
    assert res['ta_invalid_holds_count'] == 1
    assert res['ta_marks'] == Decimal('4.500')
    assert res['final_timing_contribution'] == Decimal('8.500')

    # Verify individual hold reconciliation and statuses
    h = res['reconciled_holds']
    assert len(h) == 10
    # Asana 1: 4s vs 5s -> 5s valid
    assert h[0]['accepted_hold'] == 5 and h[0]['is_valid'] is True and h[0]['discrepancy_status'] == 'RECONCILED_MARGIN_4_5'
    # Asanas 2-3: 5s vs 5s -> 5s valid agreed
    assert h[1]['accepted_hold'] == 5 and h[1]['is_valid'] is True and h[1]['discrepancy_status'] == 'AGREED'
    assert h[2]['accepted_hold'] == 5 and h[2]['is_valid'] is True and h[2]['discrepancy_status'] == 'AGREED'
    # Asanas 4-7: >5s vs 5s -> 5s valid
    assert h[3]['accepted_hold'] == 5 and h[3]['is_valid'] is True
    assert h[4]['accepted_hold'] == 5 and h[4]['is_valid'] is True
    assert h[5]['accepted_hold'] == 5 and h[5]['is_valid'] is True
    assert h[6]['accepted_hold'] == 5 and h[6]['is_valid'] is True
    # Asana 8: 4s vs 5s -> 5s valid
    assert h[7]['accepted_hold'] == 5 and h[7]['is_valid'] is True and h[7]['discrepancy_status'] == 'RECONCILED_MARGIN_4_5'
    # Asana 9: 2s vs 5s -> 0s invalid (wide discrepancy)
    assert h[8]['accepted_hold'] == 0 and h[8]['is_valid'] is False and h[8]['discrepancy_status'] == 'DISCREPANCY_REJECTED_ZERO'
    # Asana 10: 5s vs 5s -> 5s valid agreed
    assert h[9]['accepted_hold'] == 5 and h[9]['is_valid'] is True and h[9]['discrepancy_status'] == 'AGREED'

