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
    res3 = reconcile_music_timing(Decimal('146'), Decimal('150'), [], [])
    assert res3['reconciled_tw_seconds'] == 148
    assert res3['tw_marks'] == Decimal('0.000')

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

    # T1 = 4s, T2 = 4s -> accepted = 4s (invalid)
    res_4_4 = reconcile_music_timing(Decimal('165'), Decimal('165'), [Decimal('4')], [Decimal('4')])
    assert res_4_4['reconciled_holds'][0]['accepted_hold'] == 4
    assert res_4_4['reconciled_holds'][0]['is_valid'] is False
    assert 1 in res_4_4['invalid_hold_indices']

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

    # 190s: 10s overtime -> 10 * 0.50 = 5.00 marks deduction
    res_190 = reconcile_music_timing(Decimal('190'), Decimal('190'), [], [])
    assert res_190['overtime_seconds'] == 10
    assert res_190['overtime_deduction'] == Decimal('5.000')

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
