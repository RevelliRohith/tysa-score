from django.db import migrations


def unlock_unsubmitted_items(apps, schema_editor):
    Item = apps.get_model('scoring', 'AsanaScoreItem')
    Item.objects.filter(submission__is_submitted=False, submission__is_locked=False).update(
        is_draft=True, is_locked=False, is_submitted=False, submitted_at=None)


class Migration(migrations.Migration):
    dependencies = [('scoring', '0004_asanascoreitem_attempt_number_and_more')]
    operations = [migrations.RunPython(unlock_unsubmitted_items, migrations.RunPython.noop)]
