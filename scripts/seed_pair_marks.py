import os
import django
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from competition.models import JudgePanel, StageBatch
from registrations.models import EventEntry, AsanaSequenceItem
from scoring.models import Performance, JudgeSubmission, AsanaScoreItem, PerformanceArtistryScore, PerformanceStatus
from scoring.services import calculate_artistic_pair_item, calculate_rhythmic_pair_item, calculate_performance_score, quantize_score
from django.utils import timezone

def seed_panel_performances(panel_id, entry_ids, is_artistic_pair=True):
    panel = JudgePanel.objects.get(id=panel_id)
    event_name = "Artistic Pair" if is_artistic_pair else "Rhythmic Pair"
    print(f"\n=======================================================")
    print(f"SEEDING MARKS FOR {event_name.upper()} (Panel {panel.id} - {panel.stage_name})")
    print(f"=======================================================")

    # Ensure batches and performances for each entry
    batch_num = 1
    existing_batches = StageBatch.objects.filter(panel=panel).order_by('batch_number')
    if existing_batches.exists():
        batch_num = existing_batches.last().batch_number + 1

    entries = [EventEntry.objects.get(id=eid) for eid in entry_ids]

    # Map roles to judges
    judge_mapping = {
        'D1_JUDGE': panel.d1_judge,
        'D2_JUDGE': panel.d2_judge,
        'D3_JUDGE': panel.d3_judge,
        'D4_JUDGE': panel.d4_judge,
        'TIME_JUDGE': panel.time_judge,
        'TIME_JUDGE_2': panel.time_judge_2,
        'ARTISTRY_JUDGE': panel.artistry_judge,
        'ARTISTRY_JUDGE_2': panel.artistry_judge_2,
        'EVALUATOR': panel.evaluator,
    }
    if is_artistic_pair:
        judge_mapping['EVALUATOR_2'] = panel.evaluator_2

    for athlete_idx, entry in enumerate(entries):
        # 1. Batch & Performance
        batch = StageBatch.objects.filter(panel=panel, entries=entry).first()
        if not batch:
            batch = StageBatch.objects.create(
                panel=panel,
                batch_number=batch_num,
                status='COMPLETED'
            )
            batch.entries.add(entry)
            batch_num += 1
        else:
            batch.status = 'COMPLETED'
            batch.save(update_fields=['status'])

        perf = Performance.objects.filter(event_entry=entry, batch=batch).first()
        if not perf:
            perf = Performance.objects.filter(event_entry=entry, batch__isnull=True).first()
            if perf:
                perf.batch = batch
                perf.status = PerformanceStatus.SUBMITTED
                perf.save()
            else:
                perf = Performance.objects.create(
                    event_entry=entry,
                    batch=batch,
                    round_type=panel.round_type,
                    status=PerformanceStatus.SUBMITTED
                )
        else:
            perf.status = PerformanceStatus.SUBMITTED
            perf.save(update_fields=['status'])

        print(f"\n-> Athlete Entry #{athlete_idx + 1}: Chest {entry.chest_number} (Perf ID: {perf.id}, Batch ID: {batch.id})")

        # 2. Get 10 declared asanas or create if missing
        seq_items = list(AsanaSequenceItem.objects.filter(event_entry=entry).order_by('sequence_order')[:10])
        seq_count = len(seq_items)

        # Baseline marks variation based on athlete index
        # Athlete 1: high (8.5), Athlete 2: (8.2), Athlete 3: (7.8), Athlete 4: (7.5), Athlete 5: (7.2)
        base_exec = Decimal(str(8.5 - (athlete_idx * 0.3)))

        # D-Judges submissions
        d_roles = ['D1_JUDGE', 'D2_JUDGE', 'D3_JUDGE', 'D4_JUDGE']
        d_variations = {
            'D1_JUDGE': Decimal('0.0'),
            'D2_JUDGE': Decimal('0.2'),
            'D3_JUDGE': Decimal('-0.2'),
            'D4_JUDGE': Decimal('0.1'),
        }

        for role in d_roles:
            judge_user = judge_mapping[role]
            sub, _ = JudgeSubmission.objects.get_or_create(
                performance=perf,
                judge_role=role,
                defaults={'judge': judge_user}
            )
            sub.judge = judge_user
            sub.is_draft = False
            sub.is_submitted = True
            sub.is_locked = True
            sub.is_zero = False
            sub.submitted_at = timezone.now()
            sub.save()

            judge_raw_total = Decimal('0.000')
            judge_eff_total = Decimal('0.000')

            var = d_variations[role]
            for seq in range(1, 11):
                item_obj, _ = AsanaScoreItem.objects.get_or_create(
                    submission=sub,
                    sequence_order=seq
                )
                item_obj.is_draft = False
                item_obj.is_submitted = True
                item_obj.is_locked = True
                item_obj.is_zero = False
                item_obj.submitted_at = timezone.now()
                item_obj.is_valid = True
                item_obj.invalid_reason = ''

                if is_artistic_pair:
                    item_obj.chest_a_execution = quantize_score(base_exec + var)
                    item_obj.chest_a_bv = Decimal('1.000')
                    item_obj.chest_b_execution = quantize_score(base_exec + var - Decimal('0.1'))
                    item_obj.chest_b_bv = Decimal('1.000')
                    item_obj.formation_score = quantize_score(base_exec + var)

                    res = calculate_artistic_pair_item(
                        execution_1=item_obj.chest_a_execution,
                        bv_1=item_obj.chest_a_bv,
                        execution_2=item_obj.chest_b_execution,
                        bv_2=item_obj.chest_b_bv,
                        formation=item_obj.formation_score,
                        is_invalid=False
                    )
                else: # Rhythmic Pair
                    item_obj.chest_a_execution = quantize_score(base_exec + var)
                    item_obj.chest_b_execution = quantize_score(base_exec + var - Decimal('0.1'))
                    item_obj.base_value = Decimal('1.000')

                    res = calculate_rhythmic_pair_item(
                        execution_1=item_obj.chest_a_execution,
                        execution_2=item_obj.chest_b_execution,
                        shared_bv=item_obj.base_value,
                        is_invalid=False
                    )

                item_obj.raw_item_score = res['raw_item_score']
                item_obj.effective_item_score = res['effective_item_score']
                item_obj.save()

                judge_raw_total += res['raw_item_score']
                judge_eff_total += res['effective_item_score']

            sub.raw_total = quantize_score(judge_raw_total)
            sub.effective_total = quantize_score(judge_eff_total)
            sub.save(update_fields=['raw_total', 'effective_total', 'is_submitted', 'is_locked', 'is_draft'])

        # Time Judges
        t_roles = ['TIME_JUDGE', 'TIME_JUDGE_2']
        t_music_duration = [Decimal('138.00'), Decimal('139.00')] # Safe within 120-150s range -> 4.5 or 5.0 marks
        for t_idx, role in enumerate(t_roles):
            judge_user = judge_mapping[role]
            sub, _ = JudgeSubmission.objects.get_or_create(
                performance=perf,
                judge_role=role,
                defaults={'judge': judge_user}
            )
            sub.judge = judge_user
            sub.is_draft = False
            sub.is_submitted = True
            sub.is_locked = True
            sub.is_zero = False
            sub.submitted_at = timezone.now()
            sub.raw_total = t_music_duration[t_idx]
            sub.effective_total = Decimal('5.000') # 138-139s corresponds to 5.0 marks in 120-150s window
            sub.save()

            for seq in range(1, 11):
                item_obj, _ = AsanaScoreItem.objects.get_or_create(
                    submission=sub,
                    sequence_order=seq
                )
                item_obj.is_draft = False
                item_obj.is_submitted = True
                item_obj.is_locked = True
                item_obj.is_zero = False
                item_obj.submitted_at = timezone.now()
                item_obj.hold_duration_seconds = Decimal('5.5') # >= 5.0s is full valid hold
                item_obj.is_valid = True
                item_obj.save()

        # Artistry Judges
        a_roles = ['ARTISTRY_JUDGE', 'ARTISTRY_JUDGE_2']
        # Artistry criteria: costume(1), theme_music(1), character_expression(3), rhythm_artistic_steps(10), use_of_floor(5) -> max 20
        for a_idx, role in enumerate(a_roles):
            judge_user = judge_mapping[role]
            sub, _ = JudgeSubmission.objects.get_or_create(
                performance=perf,
                judge_role=role,
                defaults={'judge': judge_user}
            )
            sub.judge = judge_user
            sub.is_draft = False
            sub.is_submitted = True
            sub.is_locked = True
            sub.is_zero = False
            sub.submitted_at = timezone.now()

            pas, _ = PerformanceArtistryScore.objects.get_or_create(submission=sub)
            pas.costume = Decimal('1.00')
            pas.theme_music = Decimal('1.00')
            pas.character_expression = Decimal('2.50') if a_idx == 0 else Decimal('2.40')
            pas.rhythm_artistic_steps = Decimal('7.50') if a_idx == 0 else Decimal('7.20')
            pas.use_of_floor = Decimal('4.50') if a_idx == 0 else Decimal('4.40')
            pas.artistry_deductions = Decimal('0.00')
            pas.deductions = Decimal('0.00')
            tot = pas.calculate_total()
            pas.save()

            sub.raw_total = quantize_score(tot)
            sub.effective_total = quantize_score(tot)
            sub.save()

        # Evaluator(s)
        e_roles = ['EVALUATOR']
        if is_artistic_pair:
            e_roles.append('EVALUATOR_2')

        for role in e_roles:
            judge_user = judge_mapping[role]
            sub, _ = JudgeSubmission.objects.get_or_create(
                performance=perf,
                judge_role=role,
                defaults={'judge': judge_user}
            )
            sub.judge = judge_user
            sub.is_draft = False
            sub.is_submitted = True
            sub.is_locked = True
            sub.is_zero = False
            sub.submitted_at = timezone.now()
            sub.raw_total = Decimal('0.000')
            sub.effective_total = Decimal('0.000')
            sub.save()

            for seq in range(1, 11):
                item_obj, _ = AsanaScoreItem.objects.get_or_create(
                    submission=sub,
                    sequence_order=seq
                )
                item_obj.is_draft = False
                item_obj.is_submitted = True
                item_obj.is_locked = True
                item_obj.is_zero = False
                item_obj.submitted_at = timezone.now()
                item_obj.is_valid = True
                item_obj.invalid_reason = ''
                item_obj.save()

        # Now run master calculate_performance_score
        calc_summary = calculate_performance_score(perf, persist=True)
        final_score = calc_summary.get('audit_payload', {}).get('final_composite_score')
        if not final_score and perf.calculation_data:
            final_score = perf.calculation_data.get('final_composite_score')

        perf.provisional_score = Decimal(str(final_score))
        perf.final_score = Decimal(str(final_score))
        perf.save(update_fields=['provisional_score', 'final_score'])

        print(f"   D-Judge Totals: {[f'{r}: {s}' for r, s in calc_summary['d_judge_totals']]}")
        print(f"   Trimmed Mean Retained D: {calc_summary['panel_trim_details']['retained_average']}")
        print(f"   Missing Seats: {calc_summary['missing_seats']}")
        print(f"   Final Composite Score: {final_score}")

print("Starting Marks Seeding Script...")
# Panel 31: Artistic Pair (Event 47) - 5 entries
seed_panel_performances(31, [397, 398, 399, 400, 401], is_artistic_pair=True)

# Panel 33: Rhythmic Pair (Event 48) - 5 entries
seed_panel_performances(33, [409, 410, 411, 412, 413], is_artistic_pair=False)
print("\nMarks Seeding Script Completed Successfully!")
