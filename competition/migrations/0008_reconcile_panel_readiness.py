from django.db import migrations


def reconcile_panels(apps, schema_editor):
    Panel = apps.get_model('competition', 'JudgePanel')
    for panel in Panel.objects.select_related('event_config').all().iterator():
        traditional = panel.event_config.event_type == 'TRADITIONAL'
        required = ['chief_judge', 'd1_judge', 'd2_judge', 'd3_judge', 'd4_judge',
                    'time_judge', 'evaluator', 'stage_manager']
        if traditional:
            required += ['d5_judge']
        else:
            required += ['artistry_judge', 'artistry_judge_2', 'time_judge_2']
            if panel.event_config.event_type == 'ARTISTIC_PAIR':
                required += ['evaluator_2']
        ids = [getattr(panel, f + '_id') for f in required]
        all_seats = set(required) | {'d5_judge', 'artistry_judge', 'artistry_judge_2', 'time_judge_2', 'evaluator_2'}
        incomplete = not panel.age_category_id or None in ids or len(ids) != len(set(ids)) or any(
            getattr(panel, f + '_id') for f in all_seats - set(required))
        # Keep assignments, batches and all scoring history. Incomplete panels await CD completion.
        Panel.objects.filter(pk=panel.pk).update(d_judges_count=5 if traditional else 4,
            is_active=panel.is_active and not incomplete and not panel.is_closed)


class Migration(migrations.Migration):
    dependencies = [('competition', '0007_judgepanel_d5_judge_alter_judgepanel_is_active')]
    operations = [migrations.RunPython(reconcile_panels, migrations.RunPython.noop)]
