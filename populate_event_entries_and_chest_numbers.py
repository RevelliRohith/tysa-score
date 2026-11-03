import os
import django
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from championships.models import Championship, EventConfiguration, AgeCategory
from registrations.models import ChampionshipRegistration, EventEntry, AsanaSequenceItem
from athletes.models import Athlete
from catalogue.models import Asana

def run():
    champ = Championship.objects.get(id=12)
    print(f"Fast populating entries for Championship: {champ.name}")

    # Set registrations to VENUE_CLEARED
    ChampionshipRegistration.objects.filter(championship=champ).update(status='VENUE_CLEARED')

    catalog_asanas = list(Asana.objects.all().order_by('id')[:30])
    events = {ev.event_type: ev for ev in champ.event_configurations.filter(is_enabled=True)}
    trad_ev = events.get('TRADITIONAL')
    art_single_ev = events.get('ARTISTIC_SINGLE')
    art_pair_ev = events.get('ARTISTIC_PAIR')
    rhythm_pair_ev = events.get('RHYTHMIC_PAIR')

    athletes = list(Athlete.objects.all().order_by('id'))
    male_athletes = [a for a in athletes if a.gender == 'M']
    female_athletes = [a for a in athletes if a.gender == 'F']

    # 1. TRADITIONAL EVENT ROUTINES
    existing_items_entries = set(
        AsanaSequenceItem.objects.filter(
            event_entry__registration__championship=champ
        ).values_list('event_entry_id', flat=True).distinct()
    )

    items_to_create = []
    if trad_ev:
        trad_entries = list(EventEntry.objects.filter(event_config=trad_ev))
        for entry in trad_entries:
            if entry.id not in existing_items_entries:
                for idx in range(1, 8):
                    asana_obj = catalog_asanas[(idx - 1) % len(catalog_asanas)]
                    is_comp = idx <= 5
                    bv_val = Decimal('1.00') if is_comp else (asana_obj.base_value or Decimal('0.80'))
                    items_to_create.append(AsanaSequenceItem(
                        event_entry=entry,
                        round_type='PRELIMINARY',
                        sequence_order=idx,
                        is_compulsory=is_comp,
                        declared_asana=asana_obj,
                        declared_code=asana_obj.code,
                        declared_name=asana_obj.display_name,
                        declared_bv=bv_val,
                        image_url=asana_obj.image_url or ''
                    ))

    # Helper function for fast entry & routine generation
    def setup_event_fast(ev_cfg, is_pair=False):
        if not ev_cfg:
            return
        categories = list(ev_cfg.age_categories.all().order_by('display_order'))
        for cat in categories:
            for g, a_pool in [('M', male_athletes), ('F', female_athletes)]:
                if not a_pool:
                    a_pool = athletes

                if is_pair:
                    for p_i in range(3):  # 3 pairs per division
                        if len(a_pool) < (p_i * 2 + 2):
                            break
                        p1 = a_pool[p_i * 2]
                        p2 = a_pool[p_i * 2 + 1]

                        reg, _ = ChampionshipRegistration.objects.get_or_create(
                            championship=champ, athlete=p1, defaults={'status': 'VENUE_CLEARED'}
                        )
                        reg.status = 'VENUE_CLEARED'
                        reg.save(update_fields=['status'])

                        entry, created = EventEntry.objects.get_or_create(
                            registration=reg,
                            event_config=ev_cfg,
                            defaults={
                                'age_category': cat,
                                'gender': g,
                                'is_pair': True,
                                'partner_athlete': p2
                            }
                        )
                        if not created:
                            entry.age_category = cat
                            entry.gender = g
                            entry.is_pair = True
                            entry.partner_athlete = p2
                            entry.save(update_fields=['age_category', 'gender', 'is_pair', 'partner_athlete'])

                        if entry.id not in existing_items_entries:
                            for seq in range(1, 11):
                                pose = catalog_asanas[(seq + p_i) % len(catalog_asanas)]
                                items_to_create.append(AsanaSequenceItem(
                                    event_entry=entry,
                                    round_type='PRELIMINARY',
                                    sequence_order=seq,
                                    is_compulsory=False,
                                    declared_asana=pose,
                                    declared_code=pose.code,
                                    declared_name=pose.display_name,
                                    declared_bv=pose.base_value or Decimal('0.80'),
                                    image_url=pose.image_url or ''
                                ))
                else:
                    for a_i, a in enumerate(a_pool[:6]):  # 6 athletes per division
                        reg, _ = ChampionshipRegistration.objects.get_or_create(
                            championship=champ, athlete=a, defaults={'status': 'VENUE_CLEARED'}
                        )
                        reg.status = 'VENUE_CLEARED'
                        reg.save(update_fields=['status'])

                        entry, created = EventEntry.objects.get_or_create(
                            registration=reg,
                            event_config=ev_cfg,
                            defaults={
                                'age_category': cat,
                                'gender': g,
                                'is_pair': False
                            }
                        )
                        if not created:
                            entry.age_category = cat
                            entry.gender = g
                            entry.is_pair = False
                            entry.save(update_fields=['age_category', 'gender', 'is_pair'])

                        if entry.id not in existing_items_entries:
                            for seq in range(1, 11):
                                pose = catalog_asanas[(seq + a_i) % len(catalog_asanas)]
                                items_to_create.append(AsanaSequenceItem(
                                    event_entry=entry,
                                    round_type='PRELIMINARY',
                                    sequence_order=seq,
                                    is_compulsory=False,
                                    declared_asana=pose,
                                    declared_code=pose.code,
                                    declared_name=pose.display_name,
                                    declared_bv=pose.base_value or Decimal('0.80'),
                                    image_url=pose.image_url or ''
                                ))

    setup_event_fast(art_single_ev, is_pair=False)
    setup_event_fast(art_pair_ev, is_pair=True)
    setup_event_fast(rhythm_pair_ev, is_pair=True)

    if items_to_create:
        print(f"Bulk creating {len(items_to_create)} sequence items...")
        AsanaSequenceItem.objects.bulk_create(items_to_create, batch_size=200, ignore_conflicts=True)

    # 3. ASSIGN UNIQUE SEQUENTIAL CHEST NUMBERS TO EVERY ATHLETE
    print("Assigning unique chest numbers...")
    all_entries = list(EventEntry.objects.filter(registration__championship=champ).order_by('id'))
    existing_nums = set()
    for e in all_entries:
        if e.chest_number:
            existing_nums.add(str(e.chest_number).strip())

    counter = 101
    updated_entries = []
    for e in all_entries:
        if not e.chest_number:
            while str(counter) in existing_nums:
                counter += 1
            e.chest_number = str(counter)
            existing_nums.add(str(counter))
            updated_entries.append(e)
            counter += 1

    if updated_entries:
        print(f"Bulk updating {len(updated_entries)} entries with sequential chest numbers...")
        EventEntry.objects.bulk_update(updated_entries, ['chest_number'], batch_size=100)

    # Verify 0 missing
    missing = EventEntry.objects.filter(registration__championship=champ, chest_number__in=['', None]).count()
    print(f"Missing chest numbers remaining: {missing}")

    print("\n--- Event Entry & Chest Number Summary ---")
    for ev in champ.event_configurations.filter(is_enabled=True):
        count = EventEntry.objects.filter(event_config=ev).count()
        with_chest = EventEntry.objects.filter(event_config=ev).exclude(chest_number='').count()
        print(f" * {ev.get_event_type_display()}: {count} athletes (All {with_chest} with Chest Numbers)")

if __name__ == '__main__':
    run()
