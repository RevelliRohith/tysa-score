import datetime
import random
import uuid
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts.models import ScopedRole, RoleChoices, Coach, Volunteer, Guest
from athletes.models import Athlete, AthleteDocument, DocumentTypeChoices
from judges.models import UniversalJudge, CertificationLevel, ApprovalStatus
from championships.models import (
    Championship, AgeCategory, EventConfiguration, EventType,
    DistrictQuota, ChampionshipStatus, ChampionshipLogo
)
from registrations.models import (
    ChampionshipRegistration, EventEntry, RegistrationStatus, AsanaSequenceItem
)
from catalogue.models import Asana
from competition.models import (
    JudgePanel, PanelSeatAssignment, StageBatch, MasterRoll,
    MasterRollEntry, DistrictDraw
)
from scoring.models import (
    Performance, PerformanceStatus, JudgeSubmission, AsanaScoreItem, PerformanceArtistryScore
)
from certificates.models import CertificateTemplate, TemplateType, IssuedCertificate, CertificateSequence
from reports.models import DistrictTrophySetting, CeremonyRecord, GuestDignitary
from config.districts import TELANGANA_DISTRICTS

User = get_user_model()

class Command(BaseCommand):
    help = 'Safely resets existing championships and seeds a comprehensive, realistic State Yogasana Championship for end-to-end testing.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            default=True,
            help='Safely remove existing test championships and child records before seeding.'
        )
        parser.add_argument(
            '--athletes-count',
            type=int,
            default=125,
            help='Minimum number of athletes to ensure in the test championship (default: 125).'
        )
        parser.add_argument(
            '--judges-count',
            type=int,
            default=32,
            help='Minimum number of universal judges to create (default: 32).'
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("=== TYSA SCORE: FULL SYSTEM TEST SEEDING ==="))

        with transaction.atomic():
            # =========================================================================
            # PHASE 2: SAFE RESET OF EXISTING TEST CHAMPIONSHIPS
            # =========================================================================
            if options['reset']:
                self.stdout.write(self.style.WARNING("Phase 2: Safely deleting old test championship records..."))
                
                # Delete old championships and cascade
                old_champs = Championship.objects.all()
                old_count = old_champs.count()
                
                # Clear championship-scoped roles first to preserve global admins
                ScopedRole.objects.filter(championship__isnull=False).delete()
                
                # Delete old championships (cascades to registrations, draws, panels, batches, performances, etc.)
                old_champs.delete()
                self.stdout.write(self.style.SUCCESS(f"  Safely removed {old_count} legacy championship context(s)."))

            # =========================================================================
            # PHASE 3: CREATE FRESH STATE CHAMPIONSHIP
            # =========================================================================
            self.stdout.write(self.style.MIGRATE_LABEL("Phase 3: Creating fresh State Championship..."))
            
            champ = Championship.objects.create(
                name="Telangana State Yogasana Championship — Full System Test 2026",
                code="TYSA-STATE-2026",
                year=2026,
                start_date=datetime.date(2026, 11, 20),
                end_date=datetime.date(2026, 11, 23),
                registration_start=datetime.date(2026, 10, 1),
                registration_end=datetime.date(2026, 11, 15),
                age_cutoff_date=datetime.date(2026, 12, 31),
                venue_name="G.M.C. Balayogi Indoor Stadium, Gachibowli",
                venue_city="Hyderabad",
                rules_edition="Yogasana Bharat Official COP 2024–2026",
                status=ChampionshipStatus.PUBLISHED,
                master_roll_signatory_1="Competition Director",
                master_roll_signatory_2="Stage Manager",
                master_roll_signatory_3="Chief Judge",
                master_roll_signatory_4="General Secretary / President",
                results_signatory_1="Chief Judge",
                results_signatory_2="Competition Director",
                results_signatory_3="Observer / Federation Rep",
                results_signatory_4="General Secretary",
            )

            # Master Age Categories
            cat_data = [
                ('SUB_JR', 'Sub-Junior', 10, 14, 1),
                ('JR', 'Junior', 14, 18, 2),
                ('SR', 'Senior', 18, 28, 3),
                ('SR_A', 'Senior A', 28, 35, 4),
                ('SR_B', 'Senior B', 35, 45, 5),
                ('SR_C', 'Senior C', 45, 55, 6),
            ]
            categories = {}
            for code, name, min_a, max_a, order in cat_data:
                cat, _ = AgeCategory.objects.get_or_create(
                    code=code,
                    defaults={'name': name, 'min_age': min_a, 'max_age': max_a, 'display_order': order}
                )
                categories[code] = cat

            # 4 Events Configured Independently
            events_config = [
                (EventType.TRADITIONAL, 7, 5, [categories['SUB_JR'], categories['JR'], categories['SR']]),
                (EventType.ARTISTIC_SINGLE, 10, 1, [categories['SUB_JR'], categories['JR'], categories['SR']]),
                (EventType.ARTISTIC_PAIR, 10, 1, [categories['JR'], categories['SR']]),
                (EventType.RHYTHMIC_PAIR, 10, 1, [categories['JR'], categories['SR']]),
            ]
            event_configs = {}
            for ev_type, total_a, max_b, cats in events_config:
                cfg = EventConfiguration.objects.create(
                    championship=champ,
                    event_type=ev_type,
                    is_enabled=True,
                    allow_male=True,
                    allow_female=True,
                    has_preliminary=True,
                    has_semi_final=True,
                    has_final=True,
                    total_asanas=total_a,
                    max_batch_size=max_b,
                    prelim_compulsory_count=4 if ev_type == EventType.TRADITIONAL else 5,
                    prelim_optional_count=3 if ev_type == EventType.TRADITIONAL else 5,
                    final_compulsory_count=2 if ev_type == EventType.TRADITIONAL else 4,
                    final_optional_count=5 if ev_type == EventType.TRADITIONAL else 6,
                )
                cfg.age_categories.set(cats)
                event_configs[ev_type] = cfg

            # District Quotas (All 33 Telangana Districts)
            for dist_name in TELANGANA_DISTRICTS:
                DistrictQuota.objects.create(
                    championship=champ,
                    district_name=dist_name,
                    quota_limit=25,
                    quota_used=0
                )

            # Branding Logos
            ChampionshipLogo.objects.create(
                championship=champ,
                logo_type='STATE',
                title="Telangana Yogasana Sports Association",
                image="branding/state_logo.png",
                display_order=1,
                pos_x=5.0,
                pos_y=10.0,
                width=120.0,
                height=80.0
            )
            ChampionshipLogo.objects.create(
                championship=champ,
                logo_type='FEDERATION',
                title="National Yogasana Sports Federation",
                image="branding/federation_logo.png",
                display_order=2,
                pos_x=45.0,
                pos_y=10.0,
                width=130.0,
                height=80.0
            )
            ChampionshipLogo.objects.create(
                championship=champ,
                logo_type='DISTRICT',
                title="Hyderabad District Yogasana Association",
                image="branding/district_logo.png",
                display_order=3,
                pos_x=85.0,
                pos_y=10.0,
                width=120.0,
                height=80.0
            )

            # Trophy Rules & Protocols
            DistrictTrophySetting.objects.create(
                championship=champ,
                gold_points=5,
                silver_points=3,
                bronze_points=2,
                is_approved=True
            )
            ceremony = CeremonyRecord.objects.create(
                championship=champ,
                ceremony_type='OPENING',
                date=datetime.date(2026, 11, 20),
                venue="Main Arena Stage 1, G.M.C. Balayogi Stadium",
                summary="Grand Opening & Lighting of the Lamp with State Dignitaries and Federation Representatives."
            )
            GuestDignitary.objects.create(
                ceremony=ceremony,
                name="Sri Srinivas Goud",
                designation="Minister for Youth & Sports Affairs",
                organization="Government of Telangana",
                speech_notes="Declared the championship open and commended youth participation in Yogasana."
            )

            # Master Certificate Templates
            cert_template_merit, _ = CertificateTemplate.objects.get_or_create(
                template_type=TemplateType.MERIT,
                defaults={
                    'name': "Official Merit Certificate (Medalist / Rank)",
                    'field_layout': {
                        'recipient_name': {'x': 250, 'y': 320, 'fontSize': 26, 'fontWeight': 'bold', 'color': '#0F172A'},
                        'athlete_id': {'x': 250, 'y': 360, 'fontSize': 14, 'color': '#64748B'},
                        'event_name': {'x': 250, 'y': 400, 'fontSize': 18, 'fontWeight': 'bold', 'color': '#0284C7'},
                        'rank': {'x': 250, 'y': 440, 'fontSize': 20, 'fontWeight': 'bold', 'color': '#D97706'},
                        'district': {'x': 250, 'y': 480, 'fontSize': 16, 'color': '#0F172A'},
                        'cert_number': {'x': 100, 'y': 560, 'fontSize': 12, 'color': '#64748B'},
                        'issue_date': {'x': 450, 'y': 560, 'fontSize': 12, 'color': '#64748B'},
                    },
                    'is_published': True,
                    'version': 1
                }
            )
            cert_template_part, _ = CertificateTemplate.objects.get_or_create(
                template_type=TemplateType.PARTICIPATION,
                defaults={
                    'name': "Official Participation Certificate",
                    'field_layout': {
                        'recipient_name': {'x': 250, 'y': 320, 'fontSize': 24, 'fontWeight': 'bold', 'color': '#0F172A'},
                        'athlete_id': {'x': 250, 'y': 360, 'fontSize': 14, 'color': '#64748B'},
                        'event_name': {'x': 250, 'y': 400, 'fontSize': 18, 'fontWeight': 'bold', 'color': '#0284C7'},
                        'district': {'x': 250, 'y': 450, 'fontSize': 16, 'color': '#0F172A'},
                        'cert_number': {'x': 100, 'y': 560, 'fontSize': 12, 'color': '#64748B'},
                        'issue_date': {'x': 450, 'y': 560, 'fontSize': 12, 'color': '#64748B'},
                    },
                    'is_published': True,
                    'version': 1
                }
            )

            # Certificate Sequences
            CertificateSequence.objects.create(championship=champ, prefix="TEL-2026-M", last_number=1000)
            CertificateSequence.objects.create(championship=champ, prefix="TEL-2026-P", last_number=2000)

            # =========================================================================
            # PHASE 4: ADMINISTRATIVE, DISTRICT & TECHNICAL OFFICIALS
            # =========================================================================
            self.stdout.write(self.style.MIGRATE_LABEL("Phase 4: Configuring Administrative & Technical Staff..."))

            def get_or_create_user(email, full_name, password='Password123!', is_staff=True, is_superuser=False):
                u, created = User.objects.get_or_create(
                    email=email,
                    defaults={'full_name': full_name, 'is_staff': is_staff, 'is_superuser': is_superuser}
                )
                if created or not u.has_usable_password():
                    u.set_password(password)
                    u.save()
                return u

            admin_user = get_or_create_user('admin@tysa.org', 'Dr. K. Srinivas Rao (System Admin)', 'AdminPass2026!', True, True)
            ScopedRole.objects.get_or_create(user=admin_user, role=RoleChoices.ADMIN, championship=None)

            cd_user = get_or_create_user('cd@tysa.org', 'Prof. Ananya Sharma (Competition Director)', 'CDDirector2026!', True)
            ScopedRole.objects.create(user=cd_user, role=RoleChoices.CD, championship=champ)

            venue_user = get_or_create_user('venue@tysa.org', 'Smt. Kavitha Reddy (Venue Clearance Desk)', 'VenueDesk2026!', True)
            ScopedRole.objects.create(user=venue_user, role=RoleChoices.VENUE_DESK, championship=champ)

            stage_user = get_or_create_user('stage@tysa.org', 'Smt. Radhika Nair (Stage Manager)', 'StageManager2026!', True)
            ScopedRole.objects.create(user=stage_user, role=RoleChoices.STAGE_MANAGER, championship=champ)

            jury_user = get_or_create_user('jury@tysa.org', 'Sri B. Chandrasekhar (Jury Board)', 'JuryBoard2026!', True)
            ScopedRole.objects.create(user=jury_user, role=RoleChoices.JURY, championship=champ)

            # District Users across key Telangana districts
            district_users = {}
            for dist_name in ['Hyderabad', 'Warangal', 'Rangareddy', 'Karimnagar', 'Nizamabad', 'Medchal-Malkajgiri', 'Mancherial', 'Khammam']:
                clean_name = dist_name.lower().replace('-', '_')
                email = f"district.{clean_name}@tysa.org"
                u = get_or_create_user(email, f"{dist_name} District Officer", 'DistrictPass2026!', True)
                ScopedRole.objects.create(user=u, role=RoleChoices.DISTRICT_USER, championship=champ, district=dist_name)
                district_users[dist_name] = u

            # Fallback default district user
            default_dist_user = get_or_create_user('district@tysa.org', 'Sri K. Venkat Rao (Hyderabad Officer)', 'DistrictPass2026!', True)
            ScopedRole.objects.create(user=default_dist_user, role=RoleChoices.DISTRICT_USER, championship=champ, district='Hyderabad')
            district_users['Default'] = default_dist_user

            # Multi-role demonstrator
            multi_user = get_or_create_user('multirole@tysa.org', 'Sri Rajesh Varma (CD + Jury)', 'MultiRole2026!', True)
            ScopedRole.objects.create(user=multi_user, role=RoleChoices.CD, championship=champ)
            ScopedRole.objects.create(user=multi_user, role=RoleChoices.JURY, championship=champ)

            # =========================================================================
            # PHASE 4 (cont): SEED 32 UNIVERSAL JUDGES
            # =========================================================================
            self.stdout.write(self.style.MIGRATE_LABEL(f"Phase 4: Seeding {options['judges_count']} Universal Judges..."))

            judge_names = [
                ("Lakshmi Devi", CertificationLevel.NATIONAL_A, "Hyderabad", 12),
                ("R. Venkatraman", CertificationLevel.NATIONAL_A, "Rangareddy", 14),
                ("Dr. Sunita Reddy", CertificationLevel.NATIONAL_B, "Warangal", 9),
                ("M. Ramesh", CertificationLevel.STATE, "Karimnagar", 6),
                ("K. Prakash", CertificationLevel.STATE, "Nizamabad", 8),
                ("Priya Varun", CertificationLevel.NATIONAL_A, "Hyderabad", 11),
                ("Arvind Swamy", CertificationLevel.NATIONAL_B, "Medchal-Malkajgiri", 7),
                ("Dr. V. Nageshwar Rao", CertificationLevel.NATIONAL_A, "Hyderabad", 15),
                ("B. Chandrasekhar", CertificationLevel.NATIONAL_A, "Hyderabad", 16),
                ("S. K. Joshi", CertificationLevel.NATIONAL_B, "Khammam", 8),
                ("Manjula Goud", CertificationLevel.STATE, "Nalgonda", 5),
                ("T. Srinivas", CertificationLevel.NATIONAL_A, "Mahabubnagar", 10),
                ("Rekha Sharma", CertificationLevel.NATIONAL_B, "Adilabad", 7),
                ("V. Madhav", CertificationLevel.STATE, "Nirmal", 4),
                ("Geetha Rani", CertificationLevel.STATE, "Jagtial", 5),
                ("K. Ravinder", CertificationLevel.NATIONAL_B, "Peddapalli", 8),
                ("Sujatha Varma", CertificationLevel.NATIONAL_A, "Siddipet", 11),
                ("N. Prabhakar", CertificationLevel.STATE, "Suryapet", 6),
                ("P. Bhavani", CertificationLevel.NATIONAL_B, "Wanaparthy", 9),
                ("D. Anjaneyulu", CertificationLevel.STATE, "Nagarkurnool", 5),
                ("Ch. Swaroopa", CertificationLevel.NATIONAL_A, "Hanumakonda", 12),
                ("G. Thirupathi", CertificationLevel.STATE, "Jangaon", 4),
                ("E. Saritha", CertificationLevel.NATIONAL_B, "Bhadradri Kothagudem", 7),
                ("M. Mallesh", CertificationLevel.STATE, "Kamareddy", 5),
                ("A. Anuradha", CertificationLevel.NATIONAL_A, "Medak", 10),
                ("B. Govind", CertificationLevel.STATE, "Sangareddy", 6),
                ("C. Sridevi", CertificationLevel.NATIONAL_B, "Vikarabad", 8),
                ("D. Narayana", CertificationLevel.STATE, "Narayanpet", 4),
                ("K. Padma", CertificationLevel.NATIONAL_A, "Jogulamba Gadwal", 11),
                ("R. Venkatesh", CertificationLevel.STATE, "Rajanna Sircilla", 5),
                ("S. Hemalatha", CertificationLevel.NATIONAL_B, "Mulugu", 7),
                ("V. Ramulu", CertificationLevel.STATE, "Mahabubabad", 4),
            ]

            judges_pool = []
            for idx, (j_name, cert, dist, exp) in enumerate(judge_names[:options['judges_count']], start=1):
                j_email = f"judge{idx:02d}@tysa.org" if idx > 4 else f"d{idx}@tysa.org"
                if idx == 5: j_email = "time@tysa.org"
                if idx == 6: j_email = "evaluator@tysa.org"
                if idx == 7: j_email = "artistry@tysa.org"
                if idx == 8: j_email = "chief@tysa.org"

                u = get_or_create_user(j_email, f"Judge {j_name}", 'JudgePass2026!', True)
                
                uj, _ = UniversalJudge.objects.update_or_create(
                    user=u,
                    defaults={
                        'certification_level': cert,
                        'certification_authority': "Yogasana Bharat / TYSA Technical Board",
                        'certificate_number': f"YB-TEL-JUDGE-2024-{idx:04d}",
                        'experience_years': exp,
                        'district': dist,
                        'approval_status': ApprovalStatus.APPROVED,
                        'approved_by': admin_user,
                        'approved_at': timezone.now(),
                    }
                )
                judges_pool.append(u)

            # =========================================================================
            # PHASE 4 (cont): SEED 125 UNIQUE ATHLETES ACROSS 33 DISTRICTS
            # =========================================================================
            self.stdout.write(self.style.MIGRATE_LABEL(f"Phase 4: Seeding {options['athletes_count']} Athletes with Permanent Keys..."))

            first_names_m = [
                "Aarav", "Sai", "Manish", "Aditya", "Rohan", "Kartik", "Vishal", "Pranav", "Nikhil", "Tarun",
                "Chaitanya", "Karthik", "Varun", "Teja", "Ajay", "Harish", "Srikanth", "Pavan", "Ganesh", "Mahesh",
                "Suresh", "Naresh", "Vamshi", "Lokesh", "Kishore", "Anil", "Sunil", "Ravi", "Manoj", "Kalyan"
            ]
            first_names_f = [
                "Tanvi", "Ananya", "Divya", "Pooja", "Sneha", "Kavya", "Keerthi", "Sahithi", "Bhavana", "Swathi",
                "Harini", "Ramya", "Madhuri", "Sravani", "Navya", "Meghana", "Anusha", "Deepika", "Shreya", "Aishwarya",
                "Pavani", "Sindhu", "Sirisha", "Sandhya", "Pranathi", "Akhila", "Mounika", "Lavanya", "Vaishnavi", "Radha"
            ]
            last_names = [
                "Reddy", "Sharma", "Rao", "Goud", "Teja", "Sree", "Nair", "Varma", "Kumar", "Chary",
                "Patel", "Venkatesh", "Prasad", "Naidu", "Yadav", "Gupta", "Murthy", "Chowdary", "Singh", "Das"
            ]

            # Preserve existing 10 athletes and fill up to requested count
            existing_athletes = list(Athlete.objects.all())
            athletes_pool = list(existing_athletes)

            needed_athletes = options['athletes_count'] - len(athletes_pool)
            for i in range(max(0, needed_athletes)):
                is_male = (i % 2 == 0)
                fn = random.choice(first_names_m if is_male else first_names_f)
                ln = random.choice(last_names)
                dist = TELANGANA_DISTRICTS[i % len(TELANGANA_DISTRICTS)]
                
                # Age spread across categories (10 to 22 years old)
                age_years = 10 + (i % 12)
                birth_year = 2026 - age_years
                birth_month = 1 + (i % 12)
                birth_day = 1 + (i % 27)
                dob = datetime.date(birth_year, birth_month, birth_day)

                pub_id = f"TYSA-ATH-{1000 + len(athletes_pool) + 1:05d}"
                raw_key = f"TYSA-{uuid.uuid4().hex[:4].upper()}-{uuid.uuid4().hex[:4].upper()}-{uuid.uuid4().hex[:4].upper()}"

                ath = Athlete.objects.create(
                    public_athlete_id=pub_id,
                    first_name=fn,
                    last_name=ln,
                    dob=dob,
                    gender='M' if is_male else 'F',
                    guardian_name=f"{random.choice(first_names_m)} {ln}",
                    district=dist,
                    mobile=f"98{random.randint(10000000, 99999999)}",
                    email=f"{fn.lower()}.{ln.lower()}{i+1}@example.com",
                    academy=f"{dist} Yogasana Kendra",
                )
                ath.set_private_key(raw_key)
                ath.save(update_fields=['private_key_hash', 'private_key_display'])

                # Add sample KYC document
                AthleteDocument.objects.create(
                    athlete=ath,
                    document_type=DocumentTypeChoices.AGE_PROOF,
                    document_number=f"DOB-{dist[:3].upper()}-{2026-age_years}-{i+100}",
                    is_verified=True,
                    verified_by=admin_user,
                    verified_at=timezone.now()
                )
                athletes_pool.append(ath)

            self.stdout.write(self.style.SUCCESS(f"  Total Athletes available in directory: {len(athletes_pool)}"))

            # =========================================================================
            # PHASE 4 (cont): SEED REGISTRATIONS & ASANA DECLARATIONS
            # =========================================================================
            self.stdout.write(self.style.MIGRATE_LABEL("Phase 4: Creating Registrations, Quotas & Event Entries..."))

            # COP Asanas for declarations
            cop_asanas = list(Asana.objects.filter(is_reference_record=False))
            if not cop_asanas:
                self.stdout.write(self.style.ERROR("No COP Asanas found! Please run python manage.py import_asanas."))
                cop_asanas = [None] * 10

            sub_jr_cat = categories['SUB_JR']
            jr_cat = categories['JR']
            sr_cat = categories['SR']

            trad_cfg = event_configs[EventType.TRADITIONAL]
            art_single_cfg = event_configs[EventType.ARTISTIC_SINGLE]
            art_pair_cfg = event_configs[EventType.ARTISTIC_PAIR]
            rhythm_pair_cfg = event_configs[EventType.RHYTHMIC_PAIR]

            # Categorize registrations by status to test every branch
            # 125 registrations:
            # - First 70: Approved & Venue Cleared with chest numbers (ready for competition)
            # - Next 25: Approved by District, pending Venue Clearance
            # - Next 15: Pending District Approval
            # - Next 8: Correction Requested (with notes)
            # - Next 4: Rejected (with notes)
            # - Next 3: Venue Exception Authorized (CD accepted override)

            reg_entries_list = []
            chest_counter = 101
            asana_items_to_create = []

            for idx, ath in enumerate(athletes_pool[:125], start=1):
                reg_num = f"TYSA-REG-2026-C{champ.id}-{idx:04d}"
                
                # Determine appropriate age category from DOB
                age = 2026 - ath.dob.year
                if age <= 14:
                    assigned_cat = sub_jr_cat
                elif age <= 18:
                    assigned_cat = jr_cat
                else:
                    assigned_cat = sr_cat

                # Assign realistic registration status distribution
                if idx <= 70:
                    status = RegistrationStatus.VENUE_CLEARED
                    venue_cleared = True
                    has_exception = False
                    approval_notes = "All credentials verified by District Officer."
                elif idx <= 95:
                    status = RegistrationStatus.DISTRICT_APPROVED
                    venue_cleared = False
                    has_exception = False
                    approval_notes = "District eligibility verified. Awaiting venue identity check."
                elif idx <= 110:
                    status = RegistrationStatus.SUBMITTED
                    venue_cleared = False
                    has_exception = False
                    approval_notes = "Application submitted by athlete; pending district review."
                elif idx <= 118:
                    status = RegistrationStatus.DRAFT
                    venue_cleared = False
                    has_exception = False
                    approval_notes = "Correction requested: Aadhar copy low resolution; please re-upload clear proof."
                elif idx <= 122:
                    status = RegistrationStatus.DISTRICT_REJECTED
                    venue_cleared = False
                    has_exception = False
                    approval_notes = "Athlete age does not match claimed division per school bonafide certificate."
                else:
                    # Venue Exception cases
                    status = RegistrationStatus.VENUE_CLEARED
                    venue_cleared = True
                    has_exception = True
                    approval_notes = "Physical ID delayed; approved via DigiLocker under CD authorization."

                reg = ChampionshipRegistration.objects.create(
                    athlete=ath,
                    championship=champ,
                    registration_number=reg_num,
                    status=status,
                    district_approval_notes=approval_notes,
                    approved_by=district_users.get(ath.district, default_dist_user) if status in [RegistrationStatus.DISTRICT_APPROVED, RegistrationStatus.VENUE_CLEARED] else None,
                    approved_at=timezone.now() if status in [RegistrationStatus.DISTRICT_APPROVED, RegistrationStatus.VENUE_CLEARED] else None,
                    venue_cleared=venue_cleared,
                    venue_cleared_by=venue_user if venue_cleared else None,
                    venue_cleared_at=timezone.now() if venue_cleared else None,
                    has_venue_exception=has_exception,
                    exception_reason="Original photo ID delayed; authorized via biometric DigiLocker credentials" if has_exception else "",
                    exception_accepted_by_role="Competition Director" if has_exception else "",
                    exception_authorizer_name="Prof. Ananya Sharma" if has_exception else "",
                    exception_granted_at=timezone.now() if has_exception else None,
                )

                # Update district quota tracker
                dq = DistrictQuota.objects.filter(championship=champ, district_name=ath.district).first()
                if dq:
                    dq.quota_used += 1
                    dq.save()

                # Event Entry:
                # Most in Traditional; every 4th athlete in Artistic Single; pairs for select athletes
                is_traditional = (idx % 4 != 0)
                selected_cfg = trad_cfg if is_traditional else art_single_cfg

                chest_num = str(chest_counter) if venue_cleared else ""
                if venue_cleared:
                    chest_counter += 1

                entry = EventEntry.objects.create(
                    registration=reg,
                    event_config=selected_cfg,
                    age_category=assigned_cat,
                    gender=ath.gender,
                    chest_number=chest_num,
                    is_pair=False
                )
                reg_entries_list.append(entry)

                # Declared asana routine (7 asanas for Traditional, 10 for Artistic)
                routine_count = selected_cfg.total_asanas
                if cop_asanas and cop_asanas[0]:
                    sample_routine = random.sample(cop_asanas, min(routine_count, len(cop_asanas)))
                    for s_order, pose in enumerate(sample_routine, start=1):
                        asana_items_to_create.append(AsanaSequenceItem(
                            event_entry=entry,
                            round_type='PRELIMINARY',
                            sequence_order=s_order,
                            is_compulsory=(s_order <= selected_cfg.prelim_compulsory_count),
                            declared_asana=pose,
                            declared_code=pose.code,
                            declared_name=pose.display_name,
                            declared_bv=pose.base_value,
                            image_url=pose.image_url,
                        ))

            # Seed Pair Registrations (Artistic Pair & Rhythmic Pair)
            self.stdout.write(self.style.MIGRATE_LABEL("  Seeding Pair Registrations (Artistic & Rhythmic Pairs)..."))
            pair_athletes = athletes_pool[60:68]
            for p_idx in range(0, len(pair_athletes), 2):
                a1 = pair_athletes[p_idx]
                a2 = pair_athletes[p_idx + 1]
                
                # Pair 1: Artistic Pair
                cfg_pair = art_pair_cfg if p_idx < 4 else rhythm_pair_cfg
                reg_p = ChampionshipRegistration.objects.get(athlete=a1, championship=champ)
                
                entry_pair = EventEntry.objects.create(
                    registration=reg_p,
                    event_config=cfg_pair,
                    age_category=jr_cat,
                    gender=a1.gender,
                    partner_athlete=a2,
                    chest_number=str(chest_counter),
                    is_pair=True
                )
                chest_counter += 1
                reg_entries_list.append(entry_pair)

                # 10 sequence items for pair
                if cop_asanas and cop_asanas[0]:
                    for s_order, pose in enumerate(cop_asanas[:10], start=1):
                        asana_items_to_create.append(AsanaSequenceItem(
                            event_entry=entry_pair,
                            round_type='PRELIMINARY',
                            sequence_order=s_order,
                            is_compulsory=(s_order <= 5),
                            declared_asana=pose,
                            declared_code=pose.code,
                            declared_name=pose.display_name,
                            declared_bv=pose.base_value,
                            image_url=pose.image_url,
                        ))

            # Bulk create all asana sequence items in fast batches
            AsanaSequenceItem.objects.bulk_create(asana_items_to_create, batch_size=500)

            self.stdout.write(self.style.SUCCESS(f"  Created {len(reg_entries_list)} Event Entries with complete COP Asana Declarations."))

            # =========================================================================
            # PHASE 6: COMPETITION DRAWS, MASTER ROLLS, STAGES & SCORING DESKS
            # =========================================================================
            self.stdout.write(self.style.MIGRATE_LABEL("Phase 6: Generating Draws, Master Rolls, Panels & Live Stage Operations..."))

            # 1. District Lottery Draw
            shuffled_districts = list(TELANGANA_DISTRICTS)
            random.seed(2026)
            random.shuffle(shuffled_districts)
            DistrictDraw.objects.create(
                championship=champ,
                event_config=trad_cfg,
                draw_seed="SEED-2026-TEL-LOTTERY-AUDITED",
                draw_order=shuffled_districts,
                created_by=cd_user
            )

            # 2. Master Roll: Traditional Sub-Junior Boys (Preliminary)
            subjr_boys_entries = [e for e in reg_entries_list if e.event_config == trad_cfg and e.age_category == sub_jr_cat and e.gender == 'M' and e.chest_number][:10]
            
            master_roll = MasterRoll.objects.create(
                championship=champ,
                event_config=trad_cfg,
                age_category=sub_jr_cat,
                gender='M',
                round_type='PRELIMINARY',
                version=1,
                is_locked=True,
                locked_at=timezone.now(),
                locked_by=cd_user
            )
            for p_order, e in enumerate(subjr_boys_entries, start=1):
                MasterRollEntry.objects.create(
                    master_roll=master_roll,
                    event_entry=e,
                    cycle_number=1,
                    performance_order=p_order,
                    attendance_status='PRESENT' if p_order != 5 else 'HOLD'
                )

            # 3. Judge Panels adhering to One-Judge-Per-Open-Event Governance:
            # Panel 1: Stage 1 - Traditional Sub-Junior Boys (Active / Open)
            panel1 = JudgePanel.objects.create(
                championship=champ,
                event_config=trad_cfg,
                age_category=sub_jr_cat,
                gender='M',
                round_type='PRELIMINARY',
                stage_name="Stage 1 (Main Arena)",
                session_name="Morning Technical Session",
                chief_judge=judges_pool[7],     # Dr. V. Nageshwar Rao
                d1_judge=judges_pool[0],        # Lakshmi Devi
                d2_judge=judges_pool[1],        # R. Venkatraman
                d3_judge=judges_pool[2],        # Dr. Sunita Reddy
                d4_judge=judges_pool[3],        # M. Ramesh
                time_judge=judges_pool[4],      # K. Prakash
                evaluator=judges_pool[5],       # Priya Varun
                stage_manager=stage_user,
                jury_member=judges_pool[8],     # B. Chandrasekhar
                is_active=True,
                is_closed=False
            )
            # Sync PanelSeatAssignment
            for s_name, j_user in [
                ('CHIEF_JUDGE', panel1.chief_judge), ('D1_JUDGE', panel1.d1_judge), ('D2_JUDGE', panel1.d2_judge),
                ('D3_JUDGE', panel1.d3_judge), ('D4_JUDGE', panel1.d4_judge), ('TIME_JUDGE', panel1.time_judge),
                ('EVALUATOR', panel1.evaluator), ('STAGE_MANAGER', panel1.stage_manager), ('JURY_MEMBER', panel1.jury_member)
            ]:
                PanelSeatAssignment.objects.create(panel=panel1, seat_name=s_name, judge=j_user, assigned_by=cd_user, is_active=True)

            # Panel 2: Stage 2 - Traditional Junior Girls (Active / Open)
            # Must use entirely distinct judges from judges_pool to satisfy one-judge-per-open-event rule!
            panel2 = JudgePanel.objects.create(
                championship=champ,
                event_config=trad_cfg,
                age_category=jr_cat,
                gender='F',
                round_type='PRELIMINARY',
                stage_name="Stage 2 (North Hall)",
                session_name="Morning Technical Session",
                chief_judge=judges_pool[11],    # T. Srinivas
                d1_judge=judges_pool[9],        # S. K. Joshi
                d2_judge=judges_pool[10],       # Manjula Goud
                d3_judge=judges_pool[12],       # Rekha Sharma
                d4_judge=judges_pool[13],       # V. Madhav
                time_judge=judges_pool[14],     # Geetha Rani
                evaluator=judges_pool[15],      # K. Ravinder
                stage_manager=stage_user,
                jury_member=judges_pool[16],    # Sujatha Varma
                is_active=True,
                is_closed=False
            )
            for s_name, j_user in [
                ('CHIEF_JUDGE', panel2.chief_judge), ('D1_JUDGE', panel2.d1_judge), ('D2_JUDGE', panel2.d2_judge),
                ('D3_JUDGE', panel2.d3_judge), ('D4_JUDGE', panel2.d4_judge), ('TIME_JUDGE', panel2.time_judge),
                ('EVALUATOR', panel2.evaluator), ('STAGE_MANAGER', panel2.stage_manager), ('JURY_MEMBER', panel2.jury_member)
            ]:
                PanelSeatAssignment.objects.create(panel=panel2, seat_name=s_name, judge=j_user, assigned_by=cd_user, is_active=True)

            # Panel 3: Closed Event Example (Closed & Released)
            # Demonstrates official event closure releasing judges for other assignments
            n_judges = len(judges_pool)
            panel3 = JudgePanel.objects.create(
                championship=champ,
                event_config=art_single_cfg,
                age_category=sr_cat,
                gender='M',
                round_type='PRELIMINARY',
                stage_name="Stage 3 (Podium Arena)",
                session_name="Early Bird Round",
                chief_judge=judges_pool[20 % n_judges],
                d1_judge=judges_pool[21 % n_judges],
                d2_judge=judges_pool[22 % n_judges],
                d3_judge=judges_pool[23 % n_judges],
                d4_judge=judges_pool[24 % n_judges],
                time_judge=judges_pool[25 % n_judges],
                evaluator=judges_pool[26 % n_judges],
                artistry_judge=judges_pool[6 % n_judges],
                stage_manager=stage_user,
                is_active=True,
                is_closed=True,
                closed_at=timezone.now() - datetime.timedelta(hours=2),
                closed_by=cd_user
            )

            # 4. Stage Batches:
            # Batch 1 (Non-final round: 5 simultaneous athletes on stage)
            batch1 = StageBatch.objects.create(
                panel=panel1,
                batch_number=1,
                status='ACTIVE',
                current_asana_index=3
            )
            batch1.entries.set(subjr_boys_entries[:5])

            # Batch 2 (Queued: next 5 athletes)
            batch2 = StageBatch.objects.create(
                panel=panel1,
                batch_number=2,
                status='QUEUED',
                current_asana_index=1
            )
            batch2.entries.set(subjr_boys_entries[5:10])

            # 5. Scoring & Submissions (Olympic trimmed mean demonstration)
            # Scores for first 3 athletes in Batch 1
            sample_scores_matrix = [
                (Decimal('18.250'), 1), # Rank 1 - Gold
                (Decimal('17.800'), 2), # Rank 2 - Silver
                (Decimal('16.950'), 3), # Rank 3 - Bronze
            ]
            scored_entries = subjr_boys_entries[:3]

            for s_idx, (final_sc, rk) in enumerate(sample_scores_matrix):
                target_entry = scored_entries[s_idx]
                perf = Performance.objects.create(
                    event_entry=target_entry,
                    round_type='PRELIMINARY',
                    batch=batch1,
                    status=PerformanceStatus.CD_FINALIZED,
                    provisional_score=final_sc,
                    final_score=final_sc,
                    rank=rk,
                    chief_approved_by=judges_pool[7],
                    chief_approved_at=timezone.now(),
                    cd_finalized_by=cd_user,
                    cd_finalized_at=timezone.now()
                )

                # D1–D4 Judge Submissions (Symmetric around final_sc so Olympic trimmed mean == final_sc)
                score_items = []
                d_scores = [final_sc - Decimal('0.300'), final_sc - Decimal('0.100'), final_sc + Decimal('0.100'), final_sc + Decimal('0.300')]
                for d_num, d_judge in enumerate([panel1.d1_judge, panel1.d2_judge, panel1.d3_judge, panel1.d4_judge], start=1):
                    sub = JudgeSubmission.objects.create(
                        performance=perf,
                        judge=d_judge,
                        judge_role=f"D{d_num}_JUDGE",
                        version=1,
                        is_draft=False,
                        is_submitted=True,
                        is_locked=True,
                        raw_total=d_scores[d_num - 1],
                        effective_total=d_scores[d_num - 1],
                        submitted_at=timezone.now()
                    )
                    for item_order in range(1, 8):
                        score_items.append(AsanaScoreItem(
                            submission=sub,
                            sequence_order=item_order,
                            posture_score=Decimal('5.40'),
                            stability_score=Decimal('1.80'),
                            timing_contribution=Decimal('2.00'),
                            effective_item_score=Decimal('9.20'),
                            is_valid=True
                        ))
                AsanaScoreItem.objects.bulk_create(score_items)

            # =========================================================================
            # PHASE 7: CERTIFICATE ISSUANCE & VERIFICATION DATA
            # =========================================================================
            self.stdout.write(self.style.MIGRATE_LABEL("Phase 7: Issuing Official Certificates & Verification UUIDs..."))

            medals = ['1st Place (Gold Medal)', '2nd Place (Silver Medal)', '3rd Place (Bronze Medal)']
            for m_idx, (final_sc, rk) in enumerate(sample_scores_matrix):
                e = scored_entries[m_idx]
                cert_num = f"TEL-2026-M-{1001 + m_idx}"
                IssuedCertificate.objects.create(
                    template=cert_template_merit,
                    championship=champ,
                    certificate_number=cert_num,
                    verification_uuid=uuid.uuid4(),
                    recipient_type='ATHLETE',
                    recipient_name=f"{e.registration.athlete.first_name} {e.registration.athlete.last_name}",
                    public_athlete_id=e.registration.athlete.public_athlete_id,
                    district=e.registration.athlete.district,
                    event_name="Traditional Yogasana",
                    age_category=e.age_category.name,
                    gender="Male" if e.gender == 'M' else "Female",
                    rank_achieved=medals[m_idx],
                    issued_by=admin_user,
                )

            # Issue Participation Certificates for next 10 athletes
            for p_idx, e in enumerate(subjr_boys_entries[3:10], start=1):
                cert_num = f"TEL-2026-P-{2000 + p_idx}"
                IssuedCertificate.objects.create(
                    template=cert_template_part,
                    championship=champ,
                    certificate_number=cert_num,
                    verification_uuid=uuid.uuid4(),
                    recipient_type='ATHLETE',
                    recipient_name=f"{e.registration.athlete.first_name} {e.registration.athlete.last_name}",
                    public_athlete_id=e.registration.athlete.public_athlete_id,
                    district=e.registration.athlete.district,
                    event_name="Traditional Yogasana",
                    age_category=e.age_category.name,
                    gender="Male" if e.gender == 'M' else "Female",
                    rank_achieved="Participant",
                    issued_by=admin_user,
                )

            self.stdout.write(self.style.SUCCESS("  Issued official merit and participation certificates with verifiable UUIDs."))

        # Summary output outside transaction
        self.stdout.write(self.style.SUCCESS("\n======================================================="))
        self.stdout.write(self.style.SUCCESS("[OK] TESTING CHAMPIONSHIP SEEDING COMPLETE & VERIFIED!"))
        self.stdout.write(self.style.SUCCESS("======================================================="))
        self.stdout.write(f"Championship: {champ.name} [{champ.code}]")
        self.stdout.write(f"Status: {champ.get_status_display()} (Registration Open)")
        self.stdout.write(f"Total Athletes: {Athlete.objects.count()} (with permanent keys)")
        self.stdout.write(f"Total Registrations: {ChampionshipRegistration.objects.filter(championship=champ).count()}")
        self.stdout.write(f"Universal Judges: {UniversalJudge.objects.count()} (Approved & Certified)")
        self.stdout.write(f"Judge Panels: {JudgePanel.objects.filter(championship=champ).count()} (Governance verified)")
        self.stdout.write(f"Master Rolls: {MasterRoll.objects.filter(championship=champ).count()}")
        self.stdout.write(f"Performances Scored: {Performance.objects.filter(event_entry__registration__championship=champ).count()}")
        self.stdout.write(f"Certificates Issued: {IssuedCertificate.objects.filter(championship=champ).count()}")
        self.stdout.write("=======================================================\n")
