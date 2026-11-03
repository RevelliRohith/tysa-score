import re
from django.db import transaction
from django.utils import timezone
from config.districts import TELANGANA_DISTRICTS
from registrations.models import EventEntry
from competition.models import DistrictDraw

def ensure_all_entries_have_chest_numbers(championship=None, event_config=None, age_category=None, gender=None):
    """
    Authoritative enforcement: Every athlete must have a chest number.
    Without a chest number, an athlete cannot compete.
    Auto-assigns unique sequential numbers to any entries that currently lack a chest number.
    Returns the count of entries newly assigned.
    """
    qs = EventEntry.objects.all()
    if championship:
        qs = qs.filter(registration__championship=championship)
    if event_config:
        qs = qs.filter(event_config=event_config)
    if age_category:
        qs = qs.filter(age_category=age_category)
    if gender:
        qs = qs.filter(gender=gender)

    unassigned = list(qs.filter(chest_number__in=['', None]).order_by('id'))
    if not unassigned:
        return 0

    # Determine highest assigned numeric chest number across this championship
    all_assigned = (
        EventEntry.objects.filter(registration__championship=championship)
        .exclude(chest_number__in=['', None])
        .values_list('chest_number', flat=True)
    )
    max_num = 100
    for val in all_assigned:
        digits = re.findall(r'\d+', str(val))
        if digits:
            num = int(digits[-1])
            if num > max_num:
                max_num = num

    assigned_count = 0
    with transaction.atomic():
        for entry in unassigned:
            max_num += 1
            entry.chest_number = str(max_num)
            entry.save(update_fields=['chest_number'])
            assigned_count += 1

    return assigned_count


def resequence_chest_numbers(
    championship,
    event_config=None,
    age_category=None,
    gender=None,
    district=None,
    start_number=101,
    prefix='',
    order_by='DRAW',
    unassigned_only=False
):
    """
    Dedicated Chest Number Sequencing Service:
    Resequences chest numbers in bulk for selected scope.
    order_by can be:
      - 'DRAW': Uses Championship District Lottery Draw order
      - 'DISTRICT': District alphabetical, then athlete name
      - 'NAME': Athlete name alphabetical
      - 'ID': Original registration order
    """
    qs = EventEntry.objects.filter(registration__championship=championship).select_related(
        'registration__athlete', 'partner_athlete', 'event_config', 'age_category'
    )
    if event_config:
        qs = qs.filter(event_config=event_config)
    if age_category:
        qs = qs.filter(age_category=age_category)
    if gender:
        qs = qs.filter(gender=gender)
    if district:
        qs = qs.filter(registration__athlete__district=district)

    if unassigned_only:
        entries_to_process = list(qs.filter(chest_number__in=['', None]))
    else:
        entries_to_process = list(qs)

    if not entries_to_process:
        return 0

    # Sort entries according to order_by
    if order_by == 'DRAW':
        draw = DistrictDraw.objects.filter(championship=championship, event_config__isnull=True).first()
        if not draw:
            draw = DistrictDraw.objects.filter(championship=championship).first()
        draw_order = draw.draw_order if (draw and draw.draw_order) else list(TELANGANA_DISTRICTS)
        order_map = {d: i for i, d in enumerate(draw_order)}

        entries_to_process.sort(
            key=lambda e: (
                order_map.get(e.district, 999),
                e.registration.athlete.full_name.lower(),
                e.id
            )
        )
    elif order_by == 'DISTRICT':
        entries_to_process.sort(
            key=lambda e: (
                e.district.lower() if e.district else '',
                e.registration.athlete.full_name.lower(),
                e.id
            )
        )
    elif order_by == 'NAME':
        entries_to_process.sort(
            key=lambda e: (
                e.registration.athlete.full_name.lower(),
                e.id
            )
        )
    else:  # 'ID'
        entries_to_process.sort(key=lambda e: e.id)

    # Determine start counter
    current_num = int(start_number)
    clean_prefix = str(prefix).strip()

    updated_count = 0
    with transaction.atomic():
        for entry in entries_to_process:
            new_chest = f"{clean_prefix}{current_num}" if clean_prefix else str(current_num)
            entry.chest_number = new_chest
            entry.save(update_fields=['chest_number'])
            current_num += 1
            updated_count += 1

    return updated_count
