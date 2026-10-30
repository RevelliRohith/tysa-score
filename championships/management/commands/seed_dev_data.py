import datetime
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from accounts.models import ScopedRole, RoleChoices, Coach, Volunteer, Guest
from athletes.models import Athlete
from judges.models import UniversalJudge, CertificationLevel, ApprovalStatus
from championships.models import Championship, AgeCategory, EventConfiguration, EventType, DistrictQuota, ChampionshipStatus
from registrations.models import ChampionshipRegistration, EventEntry, RegistrationStatus, AsanaSequenceItem
from catalogue.models import Asana
from competition.models import JudgePanel, StageBatch, MasterRoll, MasterRollEntry, DistrictDraw
from scoring.models import Performance, PerformanceStatus, JudgeSubmission, AsanaScoreItem
from certificates.models import CertificateTemplate, TemplateType

User = get_user_model()

class Command(BaseCommand):
    help = 'Seeds initial demonstration data for local development and testing'

    def handle(self, *args, **options):
        self.stdout.write("Seeding TYSA Score development fixtures...")

        # 1. Championship Contexts
        champ, _ = Championship.objects.get_or_create(
            code='TYSA-STATE-2026',
            defaults={
                'name': '3rd Telangana State Yogasana Sports Championship 2026',
                'year': 2026,
                'start_date': datetime.date(2026, 11, 15),
                'end_date': datetime.date(2026, 11, 17),
                'registration_start': datetime.date(2026, 10, 1),
                'registration_end': datetime.date(2026, 11, 10),
                'age_cutoff_date': datetime.date(2026, 12, 31),
                'venue_name': 'G.M.C. Balayogi Indoor Stadium, Gachibowli',
                'venue_city': 'Hyderabad',
                'rules_edition': 'Yogasana Bharat Official COP 2024–2026',
                'status': ChampionshipStatus.IN_PROGRESS,
            }
        )

        champ2, _ = Championship.objects.get_or_create(
            code='TYSA-DIST-2025',
            defaults={
                'name': '2nd Telangana Inter-District Yogasana Championship 2025',
                'year': 2025,
                'start_date': datetime.date(2025, 8, 16),
                'end_date': datetime.date(2025, 8, 17),
                'registration_start': datetime.date(2025, 7, 1),
                'registration_end': datetime.date(2025, 8, 10),
                'age_cutoff_date': datetime.date(2025, 12, 31),
                'venue_name': 'Kotla Vijaya Bhaskara Reddy Indoor Stadium, Yousufguda',
                'venue_city': 'Hyderabad',
                'rules_edition': 'Yogasana Bharat Official COP 2024–2026',
                'status': ChampionshipStatus.COMPLETED,
            }
        )

        # 2. Admin & Officials Users (Preserve passwords if existing)
        admin, created = User.objects.get_or_create(
            email='admin@tysa.org',
            defaults={'full_name': 'Dr. K. Srinivas Rao (System Admin)', 'is_staff': True, 'is_superuser': True}
        )
        if created:
            admin.set_password('admin123')
            admin.save()
        ScopedRole.objects.get_or_create(user=admin, role=RoleChoices.ADMIN, championship=None)

        cd_user, created = User.objects.get_or_create(
            email='cd@tysa.org',
            defaults={'full_name': 'Prof. Ananya Sharma (Competition Director)', 'is_staff': True}
        )
        if created:
            cd_user.set_password('cd123')
            cd_user.save()
        ScopedRole.objects.get_or_create(user=cd_user, role=RoleChoices.CD, championship=champ)

        district_user, created = User.objects.get_or_create(
            email='district@tysa.org',
            defaults={'full_name': 'Hyderabad District Officer', 'is_staff': True}
        )
        if created:
            district_user.set_password('district123')
            district_user.save()
        ScopedRole.objects.get_or_create(user=district_user, role=RoleChoices.DISTRICT_USER, championship=champ, district='Hyderabad')

        venue_user, created = User.objects.get_or_create(
            email='venue@tysa.org',
            defaults={'full_name': 'Gachibowli Venue Verification Officer', 'is_staff': True}
        )
        if created:
            venue_user.set_password('venue123')
            venue_user.save()
        ScopedRole.objects.get_or_create(user=venue_user, role=RoleChoices.VENUE_DESK, championship=champ)

        # Multi-role demonstration user (CD in Champ 1, Jury in Champ 2)
        multi_user, created = User.objects.get_or_create(
            email='multirole@tysa.org',
            defaults={'full_name': 'Multi-Role Official (CD + Jury)', 'is_staff': True}
        )
        if created:
            multi_user.set_password('multi123')
            multi_user.save()
        ScopedRole.objects.get_or_create(user=multi_user, role=RoleChoices.CD, championship=champ)
        ScopedRole.objects.get_or_create(user=multi_user, role=RoleChoices.JURY, championship=champ2)

        # 3. Universal Judges
        judge_specs = [
            ('d1@tysa.org', 'Smt. Lakshmi Devi', CertificationLevel.NATIONAL_A, 'Hyderabad', RoleChoices.D_JUDGE),
            ('d2@tysa.org', 'Sri R. Venkatraman', CertificationLevel.NATIONAL_A, 'Rangareddy', RoleChoices.D_JUDGE),
            ('d3@tysa.org', 'Dr. Sunita Reddy', CertificationLevel.NATIONAL_B, 'Warangal', RoleChoices.D_JUDGE),
            ('d4@tysa.org', 'Sri M. Ramesh', CertificationLevel.STATE, 'Karimnagar', RoleChoices.D_JUDGE),
            ('time@tysa.org', 'Sri K. Prakash', CertificationLevel.STATE, 'Nizamabad', RoleChoices.TIME_JUDGE),
            ('evaluator@tysa.org', 'Smt. Priya Varun', CertificationLevel.NATIONAL_A, 'Hyderabad', RoleChoices.EVALUATOR),
            ('artistry@tysa.org', 'Sri Arvind Swamy', CertificationLevel.NATIONAL_B, 'Medchal', RoleChoices.ARTISTRY_JUDGE),
            ('chief@tysa.org', 'Dr. V. Nageshwar Rao', CertificationLevel.NATIONAL_A, 'Hyderabad', RoleChoices.CHIEF_JUDGE),
            ('jury@tysa.org', 'Sri B. Chandrasekhar', CertificationLevel.NATIONAL_A, 'Hyderabad', RoleChoices.JURY),
            ('stage@tysa.org', 'Smt. Radhika Nair', CertificationLevel.STATE, 'Hyderabad', RoleChoices.STAGE_MANAGER),
        ]

        judges_created = {}
        for email, name, cert, dist, role in judge_specs:
            u, created = User.objects.get_or_create(
                email=email,
                defaults={'full_name': name, 'is_staff': True}
            )
            if created:
                u.set_password('judge123')
                u.save()
            ScopedRole.objects.get_or_create(user=u, role=role, championship=champ)
            
            # Universal Judge Profile (Admin Approved)
            uj, _ = UniversalJudge.objects.get_or_create(
                user=u,
                defaults={
                    'certification_level': cert,
                    'district': dist,
                    'approval_status': ApprovalStatus.APPROVED,
                    'approved_by': admin,
                    'experience_years': 8
                }
            )
            judges_created[email] = u

        # Age Categories
        cat_specs = [
            ('SUB_JR', 'Sub Junior', 10, 14, 1),
            ('JR', 'Junior', 14, 18, 2),
            ('SR', 'Senior', 18, 28, 3),
            ('SR_A', 'Senior A', 28, 35, 4),
            ('SR_B', 'Senior B', 35, 45, 5),
            ('SR_C', 'Senior C', 45, 55, 6),
        ]
        categories = {}
        for code, name, min_a, max_a, order in cat_specs:
            cat, _ = AgeCategory.objects.get_or_create(
                code=code,
                defaults={'name': name, 'min_age': min_a, 'max_age': max_a, 'display_order': order}
            )
            categories[code] = cat

        # 4 Events configured independently
        events = [
            (EventType.TRADITIONAL, 7, 5, [categories['SUB_JR'], categories['JR'], categories['SR']]),
            (EventType.ARTISTIC_SINGLE, 10, 1, [categories['SUB_JR'], categories['JR'], categories['SR']]),
            (EventType.ARTISTIC_PAIR, 10, 1, [categories['JR'], categories['SR']]),
            (EventType.RHYTHMIC_PAIR, 10, 1, [categories['JR'], categories['SR']]),
        ]
        event_configs = {}
        for ev_type, total_a, max_b, cats in events:
            ev_cfg, _ = EventConfiguration.objects.get_or_create(
                championship=champ,
                event_type=ev_type,
                defaults={
                    'total_asanas': total_a,
                    'max_batch_size': max_b,
                    'is_enabled': True,
                    'allow_male': True,
                    'allow_female': True,
                }
            )
            ev_cfg.age_categories.set(cats)
            event_configs[ev_type] = ev_cfg

        # District Quotas
        districts = [
            'Hyderabad', 'Rangareddy', 'Medchal-Malkajgiri', 'Warangal', 
            'Karimnagar', 'Nizamabad', 'Khammam', 'Nalgonda', 'Mahabubnagar', 'Adilabad'
        ]
        for dist in districts:
            DistrictQuota.objects.get_or_create(
                championship=champ,
                district_name=dist,
                defaults={'quota_limit': 25, 'quota_used': 6}
            )

        # 4. Coaches, Volunteers, Guests (Admin entered records)
        Coach.objects.get_or_create(
            championship=champ,
            name="Acharya Satyananda",
            defaults={'mobile': '9876543210', 'district': 'Hyderabad', 'academy_org': 'Telangana Yoga Academy', 'qualification': 'Master of Yoga Science', 'attendance_status': 'PRESENT'}
        )
        Coach.objects.get_or_create(
            championship=champ,
            name="Guru Rajeshwar",
            defaults={'mobile': '9876543211', 'district': 'Rangareddy', 'academy_org': 'Patanjali Yoga Kendra', 'qualification': 'Senior Coach Certified', 'attendance_status': 'PRESENT'}
        )

        Volunteer.objects.get_or_create(
            championship=champ,
            name="Karthik Varma",
            defaults={'mobile': '9876543220', 'district': 'Hyderabad', 'role_assigned': 'Stage Logistics & Marshall', 'attendance_status': 'PRESENT'}
        )
        Guest.objects.get_or_create(
            championship=champ,
            name="Hon. Sports Minister of Telangana",
            defaults={'designation': 'Minister for Youth & Sports Affairs', 'organization': 'Govt of Telangana', 'attendance_status': 'INVITED'}
        )

        # 5. Seed Demonstration Athletes with Permanent Public ID & High-Entropy Private Key
        athlete_specs = [
            ("Aarav", "Reddy", datetime.date(2010, 4, 15), "M", "Srinivas Reddy", "Hyderabad", "9848012345", "aarav@example.com", "TYSA-ATH-00101", "TYSA-A1B2-C3D4-E5F6"),
            ("Tanvi", "Sharma", datetime.date(2011, 8, 22), "F", "Mahesh Sharma", "Rangareddy", "9848012346", "tanvi@example.com", "TYSA-ATH-00102", "TYSA-B2C3-D4E5-F6A1"),
            ("Sai", "Teja", datetime.date(2009, 2, 10), "M", "V. Narayana", "Warangal", "9848012347", "saiteja@example.com", "TYSA-ATH-00103", "TYSA-C3D4-E5F6-A1B2"),
            ("Ananya", "Rao", datetime.date(2010, 11, 5), "F", "K. V. Rao", "Karimnagar", "9848012348", "ananya@example.com", "TYSA-ATH-00104", "TYSA-D4E5-F6A1-B2C3"),
            ("Manish", "Goud", datetime.date(2009, 6, 18), "M", "Balaram Goud", "Nizamabad", "9848012349", "manish@example.com", "TYSA-ATH-00105", "TYSA-E5F6-A1B2-C3D4"),
            ("Divya", "Sree", datetime.date(2010, 9, 30), "F", "R. Swamy", "Medchal-Malkajgiri", "9848012350", "divya@example.com", "TYSA-ATH-00106", "TYSA-F6A1-B2C3-D4E5"),
        ]

        athletes_created = []
        for fn, ln, dob, gender, g_name, dist, mob, email, pub_id, priv_key in athlete_specs:
            ath, _ = Athlete.objects.get_or_create(
                public_athlete_id=pub_id,
                defaults={
                    'first_name': fn,
                    'last_name': ln,
                    'dob': dob,
                    'gender': gender,
                    'guardian_name': g_name,
                    'district': dist,
                    'mobile': mob,
                    'email': email,
                }
            )
            ath.set_private_key(priv_key)
            ath.save()
            athletes_created.append(ath)

        # 6. Registrations, Entries & Sequence
        trad_cfg = event_configs[EventType.TRADITIONAL]
        sub_jr = categories['SUB_JR']
        
        # Poses from DB
        sample_poses = list(Asana.objects.filter(is_reference_record=False)[:7])

        entries = []
        for idx, ath in enumerate(athletes_created):
            reg, _ = ChampionshipRegistration.objects.get_or_create(
                athlete=ath,
                championship=champ,
                defaults={
                    'registration_number': f"TYSA-REG-2026-{101+idx:04d}",
                    'status': RegistrationStatus.VENUE_CLEARED,
                    'venue_cleared': True,
                    'venue_cleared_at': datetime.datetime.now(),
                    'approved_at': datetime.datetime.now(),
                    'approved_by': admin,
                }
            )
            chest_num = str(101 + idx)
            entry, _ = EventEntry.objects.get_or_create(
                registration=reg,
                event_config=trad_cfg,
                defaults={
                    'age_category': sub_jr,
                    'gender': ath.gender,
                    'chest_number': chest_num,
                    'is_pair': False,
                }
            )
            entries.append(entry)

            # 7 asana sequence
            for s_idx, pose in enumerate(sample_poses, start=1):
                AsanaSequenceItem.objects.get_or_create(
                    event_entry=entry,
                    round_type='PRELIMINARY',
                    sequence_order=s_idx,
                    defaults={
                        'is_compulsory': (s_idx <= 4),
                        'declared_asana': pose,
                        'declared_code': pose.code,
                        'declared_name': pose.display_name,
                        'declared_bv': pose.base_value,
                        'image_url': pose.image_url,
                    }
                )

        # 7. Judge Panel & Active Stage Batch
        panel, _ = JudgePanel.objects.get_or_create(
            championship=champ,
            event_config=trad_cfg,
            stage_name="Stage 1 (Main Arena)",
            round_type='PRELIMINARY',
            defaults={
                'chief_judge': judges_created['chief@tysa.org'],
                'd1_judge': judges_created['d1@tysa.org'],
                'd2_judge': judges_created['d2@tysa.org'],
                'd3_judge': judges_created['d3@tysa.org'],
                'd4_judge': judges_created['d4@tysa.org'],
                'time_judge': judges_created['time@tysa.org'],
                'evaluator': judges_created['evaluator@tysa.org'],
                'stage_manager': judges_created['stage@tysa.org'],
                'is_active': True,
            }
        )

        batch, _ = StageBatch.objects.get_or_create(
            panel=panel,
            batch_number=1,
            defaults={'status': 'ACTIVE', 'current_asana_index': 1}
        )
        batch.entries.set(entries[:3]) # 3 simultaneous athletes on stage

        # Create performances and sample scores for demonstration
        for e in entries[:3]:
            perf, _ = Performance.objects.get_or_create(
                event_entry=e,
                round_type='PRELIMINARY',
                defaults={
                    'batch': batch,
                    'status': PerformanceStatus.PROVISIONAL_RELEASED,
                    'provisional_score': Decimal('16.450'),
                    'final_score': Decimal('16.450'),
                    'rank': 1 if e.chest_number == '101' else 2
                }
            )
            # D1 submission
            sub, _ = JudgeSubmission.objects.get_or_create(
                performance=perf,
                judge=judges_created['d1@tysa.org'],
                judge_role='D1_JUDGE',
                version=1,
                defaults={
                    'is_draft': False,
                    'is_submitted': True,
                    'is_locked': True,
                    'raw_total': Decimal('16.500'),
                    'effective_total': Decimal('16.500'),
                    'submitted_at': datetime.datetime.now(),
                }
            )
            for seq_idx in range(1, 8):
                AsanaScoreItem.objects.get_or_create(
                    submission=sub,
                    sequence_order=seq_idx,
                    defaults={
                        'posture_score': Decimal('5.20'),
                        'stability_score': Decimal('1.80'),
                        'timing_contribution': Decimal('2.00'),
                        'effective_item_score': Decimal('9.00'),
                        'is_valid': True,
                    }
                )

        # 8. Certificate Templates
        CertificateTemplate.objects.get_or_create(
            name="Official Merit Certificate (1st / 2nd / 3rd Place)",
            template_type=TemplateType.MERIT,
            defaults={
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
        CertificateTemplate.objects.get_or_create(
            name="Official Participation Certificate",
            template_type=TemplateType.PARTICIPATION,
            defaults={
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

        self.stdout.write(self.style.SUCCESS("Dev fixtures successfully seeded! Ready for development and UI inspection."))
