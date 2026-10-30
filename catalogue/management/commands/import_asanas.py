import json
import csv
import os
from decimal import Decimal
from pathlib import Path
from django.core.management.base import BaseCommand
from catalogue.models import Asana

class Command(BaseCommand):
    help = 'Idempotently imports asana catalogue from JSON or CSV (250 poses + 5 base difficulty records BAC-A..BAC-E)'

    def add_arguments(self, parser):
        parser.add_argument('--file', type=str, help='Custom path to asanas.json or asanas.csv')

    def handle(self, *args, **options):
        # Locate data file
        file_path = options.get('file')
        if not file_path:
            candidates = [
                r"C:\Users\My Files\Desktop\NEWWWW UI IMG\TYSA_Score_Antigravity_Reference_Pack\references\data\asanas.json",
                r"C:\Users\My Files\Downloads\asanas.json",
                r"references\data\asanas.json"
            ]
            for c in candidates:
                if os.path.exists(c):
                    file_path = c
                    break

        if not file_path or not os.path.exists(file_path):
            self.stdout.write(self.style.ERROR(f"Asanas data file not found in candidates."))
            return

        self.stdout.write(f"Loading asanas from: {file_path}")
        
        is_json = file_path.endswith('.json')
        records = []
        if is_json:
            with open(file_path, 'r', encoding='utf-8') as f:
                records = json.load(f)
        else:
            with open(file_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                records = list(reader)

        created_count = 0
        updated_count = 0

        for r in records:
            source_id = int(r.get('id') or r.get('sno'))
            sno = int(r.get('sno') or source_id)
            code = str(r['code']).strip()
            raw_type = str(r.get('type') or '').strip()
            display_category = Asana.normalize_category(raw_type)
            
            bv_raw = r.get('base_value')
            bv = Decimal(str(bv_raw).strip()) if bv_raw not in (None, '') else Decimal('0.80')
            
            description = str(r.get('description') or '').strip()
            
            # Derive crisp display name from description
            # If description has parentheses (e.g. "Uttanasana (Deep standing...)"), take before parentheses
            if '(' in description:
                display_name = description.split('(')[0].strip()
            else:
                display_name = description[:80].strip() or code

            is_ref = (code.startswith('BAC-') or raw_type.lower() == 'base asana code')
            is_published = not is_ref # Base references are for difficulty calibration, not selectable poses

            image_fn = r.get('image_filename')
            image_url = f"/static/asanas/{image_fn}" if image_fn else None

            asana, created = Asana.objects.update_or_create(
                code=code,
                defaults={
                    'source_id': source_id,
                    'sno': sno,
                    'raw_type': raw_type,
                    'display_category': display_category,
                    'base_value': bv,
                    'image_filename': image_fn,
                    'image_url': image_url,
                    'description': description,
                    'display_name': display_name,
                    'is_reference_record': is_ref,
                    'is_published': is_published,
                }
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

        self.stdout.write(self.style.SUCCESS(
            f"Successfully processed {len(records)} records. Created: {created_count}, Updated: {updated_count}. "
            f"Total selectable poses in DB: {Asana.objects.filter(is_reference_record=False).count()}, "
            f"Base reference records: {Asana.objects.filter(is_reference_record=True).count()}."
        ))
