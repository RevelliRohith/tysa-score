import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from competition.models import JudgePanel
from scoring.models import Performance

print("=" * 80)
print("VERIFICATION OF MARKS SUBMISSION FOR ARTISTIC PAIR & RHYTHMIC PAIR")
print("=" * 80)

for pid in [31, 33]:
    panel = JudgePanel.objects.get(id=pid)
    print(f"\n--- PANEL {panel.id}: {panel.stage_name} ({panel.event_config.event_type}) ---")
    perfs = Performance.objects.filter(batch__panel=panel).order_by('batch__batch_number')
    for perf in perfs:
        subs = perf.submissions.filter(is_submitted=True)
        roles = list(subs.values_list('judge_role', flat=True))
        cdata = perf.calculation_data or {}
        print(f"Batch {perf.batch.batch_number} | Chest {perf.event_entry.chest_number} (Perf ID: {perf.id})")
        print(f"   Final Composite Score: {perf.final_score}")
        print(f"   Retained D: {cdata.get('retained_d_average')} | Artistry: {cdata.get('artistry_average')} | Timing: {cdata.get('timing_score')}")
        print(f"   Submitted Judges ({len(roles)}): {', '.join(sorted(roles))}")
        print(f"   Can Release: {cdata.get('can_release')}, Missing: {cdata.get('missing_seats') or 'None'}")
