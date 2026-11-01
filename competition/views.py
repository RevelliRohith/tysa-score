import random
import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
import csv
from django.http import HttpResponse, JsonResponse
from django.contrib.auth import get_user_model
from django.utils import timezone
from .models import DistrictDraw, MasterRoll, MasterRollEntry, JudgePanel, StageBatch, PanelSeatAssignment
from championships.models import Championship, EventConfiguration, AgeCategory, EventType
from registrations.models import EventEntry, RegistrationStatus
from judges.models import UniversalJudge, ApprovalStatus
from scoring.models import Performance, PerformanceStatus, JudgeSubmission, AsanaScoreItem
from accounts.models import RoleChoices, ScopedRole
from audit.models import AuditLog
from accounts.permissions import get_active_championship, role_required
from config.districts import TELANGANA_DISTRICTS

@role_required(RoleChoices.ADMIN, RoleChoices.CD)
def cd_dashboard(request):
    """
    Implements Competition Director (CD) Dashboard:
    Championship readiness, technical operational governance, lottery draw status,
    panel configurations, live stage monitoring, and active correction grants.
    """
    championship = get_active_championship(request)
    if not championship:
        messages.error(request, "No active championship selected or assigned.")
        return redirect('no_role_assigned')

    from corrections.models import CDCorrectionGrant, GrantStatus

    # Active grants for this championship
    active_grants = CDCorrectionGrant.objects.filter(
        performance__event_entry__registration__championship=championship,
        status=GrantStatus.OPEN
    ).select_related('performance', 'judge_submission', 'judge_submission__judge')

    locked_rolls = MasterRoll.objects.filter(championship=championship, is_locked=True).count()
    total_rolls = MasterRoll.objects.filter(championship=championship).count()
    cleared_athletes = EventEntry.objects.filter(
        registration__championship=championship,
        registration__status=RegistrationStatus.VENUE_CLEARED
    ).count()

    panels = JudgePanel.objects.filter(championship=championship)
    total_panels = panels.count()
    active_panels = panels.filter(is_active=True).count()
    approved_judges = UniversalJudge.objects.filter(approval_status=ApprovalStatus.APPROVED).count()

    active_batches = StageBatch.objects.filter(panel__championship=championship, status='ACTIVE').count()
    scored_performances = Performance.objects.filter(
        event_entry__registration__championship=championship,
        status__in=[PerformanceStatus.SUBMITTED, PerformanceStatus.CHIEF_REVIEWED, PerformanceStatus.PROVISIONAL_RELEASED, PerformanceStatus.CD_FINALIZED]
    ).count()
    cd_finalized_count = Performance.objects.filter(
        event_entry__registration__championship=championship,
        status=PerformanceStatus.CD_FINALIZED
    ).count()

    district_count = championship.district_quotas.count()

    return render(request, 'competition/cd_dashboard.html', {
        'championship': championship,
        'active_grants': active_grants,
        'locked_rolls': locked_rolls,
        'total_rolls': total_rolls,
        'cleared_athletes': cleared_athletes,
        'total_panels': total_panels,
        'active_panels': active_panels,
        'approved_judges': approved_judges,
        'active_batches': active_batches,
        'scored_performances': scored_performances,
        'cd_finalized_count': cd_finalized_count,
        'district_count': district_count,
    })

@role_required(RoleChoices.ADMIN, RoleChoices.CD)
def district_draw(request):
    """
    Universal Championship District Draw:
    Determines the single statewide 1–33 district lottery sequence
    that applies across all events and age categories in the championship.
    """
    championship = get_active_championship(request)
    if not championship:
        messages.error(request, "No active championship selected or assigned.")
        return redirect('no_role_assigned')

    # Universal Draw across entire championship (event_config is None)
    draw = DistrictDraw.objects.filter(championship=championship, event_config__isnull=True).first()
    if not draw:
        draw = DistrictDraw.objects.filter(championship=championship).first()

    current_districts_order = draw.draw_order if (draw and draw.draw_order) else list(TELANGANA_DISTRICTS)
    for d in TELANGANA_DISTRICTS:
        if d not in current_districts_order:
            current_districts_order.append(d)

    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'save_numbering':
            district_num_pairs = []
            for d in TELANGANA_DISTRICTS:
                num_val = request.POST.get(f'number_{d}', '').strip()
                try:
                    n = int(num_val)
                except ValueError:
                    n = 999
                district_num_pairs.append((n, d))
            district_num_pairs.sort(key=lambda x: (x[0], x[1]))
            sorted_districts = [pair[1] for pair in district_num_pairs]

            draw, _ = DistrictDraw.objects.update_or_create(
                championship=championship,
                event_config=None,
                defaults={
                    'draw_order': sorted_districts,
                    'created_by': request.user,
                    'draw_seed': f'DRAW-{datetime.datetime.now().strftime("%Y%m%d%H%M")}'
                }
            )
            messages.success(request, "Universal championship district lottery numbering saved successfully!")
            return redirect('district_draw')

        elif action == 'run_draw':
            sorted_districts = list(TELANGANA_DISTRICTS)
            random.seed(int(datetime.datetime.now().timestamp()))
            random.shuffle(sorted_districts)

            draw, _ = DistrictDraw.objects.update_or_create(
                championship=championship,
                event_config=None,
                defaults={
                    'draw_order': sorted_districts,
                    'created_by': request.user,
                    'draw_seed': f'DRAW-{datetime.datetime.now().strftime("%Y%m%d%H%M")}'
                }
            )
            messages.success(request, "Auditable random district draw completed for the entire championship!")
            return redirect('district_draw')

    draw_districts_list = []
    for idx, d_name in enumerate(current_districts_order, start=1):
        draw_districts_list.append({
            'name': d_name,
            'number': idx
        })

    # Prepare side-by-side pairs for 2-column display (1-17 left, 18-33 right)
    half = (len(draw_districts_list) + 1) // 2
    draw_pairs = []
    for i in range(half):
        d1 = draw_districts_list[i]
        d2 = draw_districts_list[i + half] if (i + half) < len(draw_districts_list) else None
        draw_pairs.append((d1, d2))

    # PDF Export for District Draw
    if request.GET.get('export') == 'pdf':
        import io
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib import colors

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=25,
            leftMargin=25,
            topMargin=25,
            bottomMargin=25
        )
        elements = []
        styles = getSampleStyleSheet()

        title_style = ParagraphStyle(
            'HeaderTitle',
            parent=styles['Heading1'],
            fontSize=13,
            leading=16,
            textColor=colors.HexColor('#1A365D'),
            alignment=1
        )
        sub_style = ParagraphStyle(
            'HeaderSub',
            parent=styles['Normal'],
            fontSize=9,
            leading=12,
            textColor=colors.HexColor('#4A5568'),
            alignment=1
        )

        champ_title = championship.name if championship else "TELANGANA STATE YOGASANA CHAMPIONSHIP"
        elements.append(Paragraph("<b>TELANGANA YOGASANA SPORTS ASSOCIATION</b>", title_style))
        elements.append(Paragraph(f"{champ_title} — OFFICIAL DISTRICT LOTTERY DRAW", sub_style))
        elements.append(Spacer(1, 8))

        draw_seed = draw.draw_seed if (draw and draw.draw_seed) else 'DRAW-OFFICIAL'
        elements.append(Paragraph(
            f"<b>Lottery Sequence:</b> 1 to 33 Districts &nbsp;&nbsp;|&nbsp;&nbsp; <b>Draw Seed:</b> {draw_seed} &nbsp;&nbsp;|&nbsp;&nbsp; <b>Date:</b> {datetime.date.today().strftime('%d %B %Y')}",
            sub_style
        ))
        elements.append(Spacer(1, 10))

        table_data = [[
            'Draw #', 'District Name', 'Draw #', 'District Name'
        ]]
        for d1, d2 in draw_pairs:
            table_data.append([
                str(d1['number']),
                d1['name'],
                str(d2['number']) if d2 else '—',
                d2['name'] if d2 else '—'
            ])

        col_widths = [55, 215, 55, 215]
        t = Table(table_data, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1A365D')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('ALIGN', (0, 0), (0, -1), 'CENTER'),
            ('ALIGN', (2, 0), (2, -1), 'CENTER'),
            ('ALIGN', (1, 0), (1, -1), 'LEFT'),
            ('ALIGN', (3, 0), (3, -1), 'LEFT'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#94A3B8')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F8FAFC')]),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        elements.append(t)
        elements.append(Spacer(1, 20))

        # Signatures
        sig_data = [
            ['______________________', '______________________', '______________________', '______________________'],
            ['Drawn By (Scrutiny)', 'Chief Judge', 'Competition Director (CD)', 'General Secretary'],
            ['Date: ______________', 'Date: ______________', 'Date: ______________', 'Date: ______________']
        ]
        sig_table = Table(sig_data, colWidths=[135, 135, 135, 135])
        sig_table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#1E293B')),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ]))
        elements.append(sig_table)
        doc.build(elements)

        buffer.seek(0)
        response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="District_Draw_{championship.code if championship else "Official"}.pdf"'
        return response

    state_logo = championship.branding_logos.filter(logo_type='STATE').first() if championship else None
    district_logo = championship.branding_logos.filter(logo_type='DISTRICT').first() if championship else None
    fed_logo = championship.branding_logos.filter(logo_type='FEDERATION').first() if championship else None

    return render(request, 'competition/district_draw.html', {
        'championship': championship,
        'draw': draw,
        'draw_districts': draw_districts_list,
        'draw_pairs': draw_pairs,
        'state_logo': state_logo,
        'district_logo': district_logo,
        'fed_logo': fed_logo,
    })

@role_required(RoleChoices.ADMIN, RoleChoices.CD)
def master_roll(request):
    """
    Dedicated Master Roll Generation & Official Printing:
    Generates division-specific performance rolls sorted strictly by the championship's
    universal district lottery draw. Provides signature column toggle, cross-links with
    Judge Panels, and formats with configurable authorized signatures.
    """
    championship = get_active_championship(request)
    if not championship:
        messages.error(request, "No active championship selected.")
        return redirect('admin_dashboard')

    event_configs = championship.event_configurations.filter(is_enabled=True)
    selected_event_id = request.GET.get('event_id')
    current_event = event_configs.filter(id=selected_event_id).first() if selected_event_id else event_configs.first()

    categories = current_event.age_categories.all() if current_event else AgeCategory.objects.all().order_by('display_order')
    selected_cat_id = request.GET.get('cat_id')
    current_cat = categories.filter(id=selected_cat_id).first() if selected_cat_id else categories.first()

    gender = request.GET.get('gender', 'M')
    round_type = request.GET.get('round_type', 'PRELIMINARY')
    view_mode = request.GET.get('view_mode', 'without_sig') # 'without_sig' or 'with_sig'

    # Universal championship district lottery draw
    draw = DistrictDraw.objects.filter(championship=championship, event_config__isnull=True).first()
    if not draw:
        draw = DistrictDraw.objects.filter(championship=championship).first()

    sorted_districts = draw.draw_order if (draw and draw.draw_order) else list(TELANGANA_DISTRICTS)

    # Master Roll for this event/category/gender/round
    master_roll = MasterRoll.objects.filter(
        championship=championship,
        event_config=current_event,
        age_category=current_cat,
        gender=gender,
        round_type=round_type
    ).first()

    # Cross-link: Judge Panel assignment check
    assigned_panel = JudgePanel.objects.filter(
        championship=championship,
        event_config=current_event,
        age_category=current_cat,
        gender=gender,
        round_type=round_type
    ).first()

    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'generate_roll':
            if not master_roll:
                master_roll = MasterRoll.objects.create(
                    championship=championship,
                    event_config=current_event,
                    age_category=current_cat,
                    gender=gender,
                    round_type=round_type,
                    version=1
                )

            eligible_entries = list(EventEntry.objects.filter(
                event_config=current_event,
                age_category=current_cat,
                gender=gender,
                registration__championship=championship
            ).select_related('registration__athlete'))

            # Map entries into sorted district buckets according to lottery draw
            district_buckets = {d: [] for d in sorted_districts}
            for e in eligible_entries:
                d = e.district
                if d in district_buckets:
                    district_buckets[d].append(e)
                else:
                    district_buckets.setdefault(d, []).append(e)

            # Round robin cycle sequence
            master_roll.roll_entries.all().delete()
            perf_order = 1
            cycle = 1
            has_more = True

            while has_more:
                has_more = False
                for d in sorted_districts:
                    if district_buckets.get(d):
                        entry = district_buckets[d].pop(0)
                        MasterRollEntry.objects.create(
                            master_roll=master_roll,
                            event_entry=entry,
                            cycle_number=cycle,
                            performance_order=perf_order
                        )
                        perf_order += 1
                        has_more = True
                cycle += 1

            messages.success(request, f"Master Roll generated with {perf_order - 1} athletes ordered by championship lottery draw!")
            return redirect(f"/competition/master-roll/?event_id={current_event.id if current_event else ''}&cat_id={current_cat.id if current_cat else ''}&gender={gender}&round_type={round_type}&view_mode={view_mode}")

        elif action == 'lock_roll':
            if master_roll:
                master_roll.is_locked = True
                master_roll.locked_by = request.user
                master_roll.locked_at = datetime.datetime.now()
                master_roll.save()
                messages.success(request, "Master Roll officially locked. Ordinary declarations are now frozen.")
            return redirect(f"/competition/master-roll/?event_id={current_event.id if current_event else ''}&cat_id={current_cat.id if current_cat else ''}&gender={gender}&round_type={round_type}&view_mode={view_mode}")

    roll_entries = master_roll.roll_entries.select_related(
        'event_entry__registration__athlete',
        'event_entry__partner_athlete',
        'event_entry__event_config',
        'event_entry__age_category'
    ).order_by('performance_order') if master_roll else []

    # PDF Export for Master Roll (with_sig or without_sig)
    if request.GET.get('export') == 'pdf':
        import io
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib import colors

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=landscape(A4),
            rightMargin=20,
            leftMargin=20,
            topMargin=20,
            bottomMargin=20
        )
        elements = []
        styles = getSampleStyleSheet()

        title_style = ParagraphStyle(
            'HeaderTitle',
            parent=styles['Heading1'],
            fontSize=13,
            leading=16,
            textColor=colors.HexColor('#1A365D'),
            alignment=1
        )
        sub_style = ParagraphStyle(
            'HeaderSub',
            parent=styles['Normal'],
            fontSize=9,
            leading=12,
            textColor=colors.HexColor('#4A5568'),
            alignment=1
        )
        cell_style = ParagraphStyle(
            'CellText',
            parent=styles['Normal'],
            fontSize=8,
            leading=10
        )

        champ_title = championship.name if championship else "TELANGANA STATE YOGASANA CHAMPIONSHIP"
        sig_mode_label = "WITH SIGNATURE" if view_mode == 'with_sig' else "WITHOUT SIGNATURE"
        elements.append(Paragraph("<b>TELANGANA YOGASANA SPORTS ASSOCIATION</b>", title_style))
        elements.append(Paragraph(f"{champ_title} — OFFICIAL MASTER ROLL ({sig_mode_label})", sub_style))
        elements.append(Spacer(1, 6))

        ev_label = current_event.get_event_type_display() if current_event else "Traditional"
        cat_label = current_cat.name if current_cat else "All Categories"
        gen_label = "Boys / Men" if gender == 'M' else "Girls / Women"
        elements.append(Paragraph(
            f"<b>Event:</b> {ev_label} &nbsp;&nbsp;|&nbsp;&nbsp; <b>Category:</b> {cat_label} &nbsp;&nbsp;|&nbsp;&nbsp; <b>Division:</b> {gen_label} &nbsp;&nbsp;|&nbsp;&nbsp; <b>Round:</b> {round_type} &nbsp;&nbsp;|&nbsp;&nbsp; <b>Competitors:</b> {len(roll_entries)}",
            sub_style
        ))
        elements.append(Spacer(1, 10))

        if view_mode == 'with_sig':
            table_data = [[
                'Order', 'Chest No', 'Athlete Name(s)', 'Permanent ID', 'District', 'Mobile', 'Athlete Signature'
            ]]
            col_widths = [45, 65, 230, 95, 120, 95, 150]
        else:
            table_data = [[
                'Order', 'Chest No', 'Athlete Name(s)', 'Permanent ID', 'District', 'Mobile'
            ]]
            col_widths = [55, 80, 310, 110, 145, 100]

        for entry in roll_entries:
            ee = entry.event_entry
            ath = ee.registration.athlete
            row = [
                str(entry.performance_order),
                str(ee.chest_number or f"{entry.performance_order:03d}"),
                Paragraph(ee.display_names, cell_style),
                ath.public_athlete_id,
                ee.district,
                ath.mobile or "—"
            ]
            if view_mode == 'with_sig':
                row.append("____________________")
            table_data.append(row)

        if not roll_entries:
            table_data.append(["—", "—", "No master roll entries generated yet.", "—", "—", "—"] + (["—"] if view_mode == 'with_sig' else []))

        t = Table(table_data, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1A365D')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 8),
            ('ALIGN', (0, 0), (1, -1), 'CENTER'),
            ('ALIGN', (2, 1), (2, -1), 'LEFT'),
            ('ALIGN', (3, 0), (-1, -1), 'CENTER'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#94A3B8')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F8FAFC')]),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        elements.append(t)

        # Bottom Signatures only if with_sig!
        if view_mode == 'with_sig':
            elements.append(Spacer(1, 18))
            sig1 = championship.master_roll_signatory_1 or "Stage Manager"
            sig2 = championship.master_roll_signatory_2 or "Chief Judge"
            sig3 = championship.master_roll_signatory_3 or "Competition Director (CD)"
            sig4 = championship.master_roll_signatory_4 or "General Secretary"
            sig_data = [
                ['_________________________', '_________________________', '_________________________', '_________________________'],
                [sig1, sig2, sig3, sig4],
                ['Signature & Date', 'Signature & Date', 'Signature & Date', 'Signature & Date']
            ]
            sig_table = Table(sig_data, colWidths=[200, 200, 200, 200])
            sig_table.setStyle(TableStyle([
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#1E293B')),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ]))
            elements.append(sig_table)

        doc.build(elements)
        buffer.seek(0)
        response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
        filename = f"Master_Roll_{ev_label}_{cat_label}_{gender}_{sig_mode_label}.pdf".replace(' ', '_')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    state_logo = championship.branding_logos.filter(logo_type='STATE').first() if championship else None
    district_logo = championship.branding_logos.filter(logo_type='DISTRICT').first() if championship else None
    fed_logo = championship.branding_logos.filter(logo_type='FEDERATION').first() if championship else None

    return render(request, 'competition/master_roll.html', {
        'championship': championship,
        'event_configs': event_configs,
        'current_event': current_event,
        'categories': categories,
        'current_cat': current_cat,
        'gender': gender,
        'round_type': round_type,
        'view_mode': view_mode,
        'master_roll': master_roll,
        'roll_entries': roll_entries,
        'draw': draw,
        'assigned_panel': assigned_panel,
        'state_logo': state_logo,
        'district_logo': district_logo,
        'fed_logo': fed_logo,
    })

def get_busy_judge_map(championship, current_panel_id=None):
    """
    Returns a dict mapping judge_user_id -> dict of info about the OPEN event panel they are assigned to.
    Only panels where is_active=True and is_closed=False are considered open.
    If current_panel_id is provided, assignments on that panel are excluded.
    """
    if not championship:
        return {}

    open_panels = JudgePanel.objects.filter(
        championship=championship,
        is_active=True,
        is_closed=False
    ).select_related('event_config', 'age_category')

    if current_panel_id:
        open_panels = open_panels.exclude(id=current_panel_id)

    busy_map = {}
    for panel in open_panels:
        evt_name = panel.event_config.get_event_type_display() if panel.event_config else "Event"
        cat_name = panel.age_category.name if panel.age_category else ""
        gender_display = "Boys" if panel.gender == 'M' else "Girls"
        panel_desc = f"{evt_name} • {cat_name} ({gender_display}) [{panel.round_type}]"

        for field in JudgePanel.SEAT_FIELDS:
            uid = getattr(panel, f"{field}_id", None)
            if uid:
                seat_label = JudgePanel.SEAT_LABELS.get(field, field)
                busy_map[uid] = {
                    'panel_id': panel.id,
                    'event_name': evt_name,
                    'category_name': cat_name,
                    'gender': panel.gender,
                    'round_type': panel.round_type,
                    'division': f"{cat_name} ({gender_display}) [{panel.round_type}]",
                    'panel_desc': panel_desc,
                    'seat_name': seat_label,
                    'seat_field': field,
                    'stage_name': panel.stage_name,
                }
    return busy_map

@role_required(RoleChoices.ADMIN, RoleChoices.CD)
def panel_assignments(request):
    """
    Implements 15_CD_Judge_Panel_Assignment.png:
    - Step 1: Filter/Select Event, Age Category, Gender Division, Round.
    - Step 2: Assign approved judges via searchable dropdowns with availability checking.
      * One judge if assigned till that event closes cannot be assigned to other events.
      * Intra-panel duplicate protection (no judge can take multiple seats in the same panel).
      * Official event closure & re-opening actions.
    - Step 3: Print preview, printable format, downloadable CSV file.
    """
    User = get_user_model()
    championship = get_active_championship(request)
    event_configs = championship.event_configurations.filter(is_enabled=True) if championship else EventConfiguration.objects.none()
    
    selected_event_id = request.GET.get('event_id')
    current_event = event_configs.filter(id=selected_event_id).first() if selected_event_id else event_configs.first()

    categories = current_event.age_categories.all() if current_event else AgeCategory.objects.all().order_by('display_order')
    selected_cat_id = request.GET.get('cat_id')
    current_cat = categories.filter(id=selected_cat_id).first() if selected_cat_id else categories.first()

    gender = request.GET.get('gender', 'M')
    round_type = request.GET.get('round_type', 'PRELIMINARY')

    approved_judges = UniversalJudge.objects.filter(approval_status=ApprovalStatus.APPROVED).select_related('user').order_by('district', 'user__full_name')

    # Find or auto-create panel for this specific event / category / gender / round
    current_panel = None
    if championship and current_event:
        current_panel, _ = JudgePanel.objects.get_or_create(
            championship=championship,
            event_config=current_event,
            age_category=current_cat,
            gender=gender,
            round_type=round_type,
            defaults={
                'stage_name': 'Stage 1',
                'session_name': 'Morning Session',
                'is_active': True,
                'is_closed': False,
            }
        )

    # Handle Close / Re-open / Save POST actions
    if request.method == 'POST':
        target_panel_id = request.POST.get('panel_id')
        panel = get_object_or_404(JudgePanel, id=target_panel_id, championship=championship)
        action = request.POST.get('action', 'save_panel')

        redirect_url = f"/competition/panels/?event_id={current_event.id if current_event else ''}&cat_id={current_cat.id if current_cat else ''}&gender={gender}&round_type={round_type}"

        if action == 'close_event':
            panel.is_closed = True
            panel.closed_at = timezone.now()
            panel.closed_by = request.user
            panel.save(update_fields=['is_closed', 'closed_at', 'closed_by'])

            # Synchronize PanelSeatAssignment
            PanelSeatAssignment.objects.filter(panel=panel).update(is_active=False)

            assigned_count = len(panel.get_assigned_judge_ids())
            evt_desc = f"{panel.event_config.get_event_type_display()} - {panel.age_category.name if panel.age_category else ''} ({gender}) [{round_type}]"
            messages.success(
                request,
                f"Event '{evt_desc}' is now officially CLOSED. All {assigned_count} assigned officials are released and can now be assigned to subsequent events."
            )
            return redirect(redirect_url)

        elif action == 'reopen_event':
            busy_map = get_busy_judge_map(championship, current_panel_id=panel.id)
            conflict_warnings = []
            for uid in panel.get_assigned_judge_ids():
                if uid in busy_map:
                    judge_obj = User.objects.filter(id=uid).first()
                    name = judge_obj.full_name if (judge_obj and judge_obj.full_name) else (judge_obj.email if judge_obj else f"Judge #{uid}")
                    conflict_warnings.append(f"{name} (in '{busy_map[uid]['panel_desc']}')")

            panel.is_closed = False
            panel.closed_at = None
            panel.closed_by = None
            panel.save(update_fields=['is_closed', 'closed_at', 'closed_by'])

            PanelSeatAssignment.objects.filter(panel=panel).update(is_active=True)

            if conflict_warnings:
                messages.warning(
                    request,
                    f"Event re-opened. Notice: Some officials are currently assigned to other open events: {', '.join(conflict_warnings)}. Please review and update seats."
                )
            else:
                messages.success(request, f"Event '{panel}' has been re-opened for active competition and panel editing.")
            return redirect(redirect_url)

        # Standard panel assignment save
        if panel.is_closed:
            messages.error(request, "This event panel is officially CLOSED and finalized. Click 'Re-open Event' before making any assignment changes.")
            return redirect(redirect_url)

        # Extract submitted seats
        seat_values = {}
        for field in JudgePanel.SEAT_FIELDS:
            raw_val = request.POST.get(field)
            seat_values[field] = int(raw_val) if (raw_val and raw_val.isdigit()) else None

        # 1. Intra-panel duplicate check: A judge cannot take multiple seats in the same panel
        seen_seats = {}
        intra_conflicts = []
        for field, uid in seat_values.items():
            if uid:
                if uid in seen_seats:
                    first_field = seen_seats[uid]
                    judge_obj = User.objects.filter(id=uid).first()
                    name = judge_obj.full_name if (judge_obj and judge_obj.full_name) else (judge_obj.email if judge_obj else f"Judge #{uid}")
                    label1 = JudgePanel.SEAT_LABELS.get(first_field, first_field)
                    label2 = JudgePanel.SEAT_LABELS.get(field, field)
                    intra_conflicts.append(f"{name} cannot be assigned to both '{label1}' and '{label2}' in the same panel.")
                seen_seats[uid] = field

        if intra_conflicts:
            for err in intra_conflicts:
                messages.error(request, err)
            return redirect(redirect_url)

        # 2. Open-event lock check: A judge assigned to another open event cannot be assigned here until that event closes
        busy_map = get_busy_judge_map(championship, current_panel_id=panel.id)
        busy_conflicts = []
        for field, uid in seat_values.items():
            if uid and uid in busy_map:
                judge_obj = User.objects.filter(id=uid).first()
                name = judge_obj.full_name if (judge_obj and judge_obj.full_name) else (judge_obj.email if judge_obj else f"Judge #{uid}")
                c_info = busy_map[uid]
                seat_label = JudgePanel.SEAT_LABELS.get(field, field)
                busy_conflicts.append(
                    f"Conflict for {seat_label}: Judge {name} is already assigned to active event '{c_info['panel_desc']}'. A judge cannot be assigned to another panel until that event closes."
                )

        if busy_conflicts:
            for err in busy_conflicts:
                messages.error(request, err)
            return redirect(redirect_url)

        # Update panel fields
        for field, uid in seat_values.items():
            setattr(panel, f"{field}_id", uid)
        panel.save()

        # Synchronize PanelSeatAssignment records
        PanelSeatAssignment.objects.filter(panel=panel).delete()
        for field, uid in seat_values.items():
            if uid:
                PanelSeatAssignment.objects.create(
                    panel=panel,
                    seat_name=JudgePanel.SEAT_LABELS.get(field, field.upper()),
                    judge_id=uid,
                    assigned_by=request.user,
                    is_active=True
                )

        messages.success(request, f"Judge panel successfully confirmed & saved for {panel.event_config.get_event_type_display()} ({gender}, {round_type}).")
        return redirect(redirect_url)

    # CSV Download Export
    if request.GET.get('export') == 'csv' and current_panel:
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="judge_panel_{current_panel.event_config.event_type}_{gender}_{round_type}.csv"'
        writer = csv.writer(response)
        writer.writerow(['TYSA Score - Official Judge Panel Assignment'])
        writer.writerow(['Championship', championship.name if championship else ''])
        writer.writerow(['Event', current_panel.event_config.get_event_type_display()])
        writer.writerow(['Category', current_cat.name if current_cat else ''])
        writer.writerow(['Gender', 'Male' if gender == 'M' else 'Female'])
        writer.writerow(['Round', round_type])
        writer.writerow(['Status', 'CLOSED' if current_panel.is_closed else 'OPEN / ACTIVE'])
        if current_panel.is_closed and current_panel.closed_at:
            writer.writerow(['Closed At', current_panel.closed_at.strftime('%Y-%m-%d %H:%M')])
            writer.writerow(['Closed By', current_panel.closed_by.full_name if current_panel.closed_by else ''])
        writer.writerow([])
        writer.writerow(['Seat / Duty', 'Assigned Judge', 'Email', 'District', 'Certification Level'])
        
        seats = [
            ('Chief Judge', current_panel.chief_judge),
            ('D1 Judge', current_panel.d1_judge),
            ('D2 Judge', current_panel.d2_judge),
            ('D3 Judge', current_panel.d3_judge),
            ('D4 Judge', current_panel.d4_judge),
            ('Time Judge', current_panel.time_judge),
            ('Time Judge 2', current_panel.time_judge_2),
            ('Evaluator', current_panel.evaluator),
            ('Evaluator 2', current_panel.evaluator_2),
            ('Artistry Judge', current_panel.artistry_judge),
            ('Artistry Judge 2', current_panel.artistry_judge_2),
            ('Stage Manager', current_panel.stage_manager),
            ('Jury Member', current_panel.jury_member),
        ]
        for seat_name, judge_user in seats:
            if judge_user:
                j_prof = getattr(judge_user, 'judge_profile', None)
                writer.writerow([
                    seat_name,
                    judge_user.full_name or judge_user.email,
                    judge_user.email,
                    j_prof.district if j_prof else '',
                    j_prof.get_certification_level_display() if j_prof else ''
                ])
            else:
                writer.writerow([seat_name, 'Unassigned', '', '', ''])
        return response

    busy_map = get_busy_judge_map(championship, current_panel_id=current_panel.id if current_panel else None)

    annotated_judges = []
    busy_roster = []
    available_count = 0
    busy_count = 0

    for j in approved_judges:
        uid = j.user_id
        is_busy = (uid in busy_map)
        b_info = busy_map.get(uid)
        if is_busy:
            busy_count += 1
            busy_roster.append({
                'judge': j,
                'user': j.user,
                'info': b_info
            })
        else:
            available_count += 1

        annotated_judges.append({
            'obj': j,
            'user': j.user,
            'is_busy': is_busy,
            'busy_info': b_info
        })

    state_logo = championship.branding_logos.filter(logo_type='STATE').first() if championship else None
    district_logo = championship.branding_logos.filter(logo_type='DISTRICT').first() if championship else None
    fed_logo = championship.branding_logos.filter(logo_type='FEDERATION').first() if championship else None

    existing_roll = None
    if championship and current_event:
        existing_roll = MasterRoll.objects.filter(
            championship=championship,
            event_config=current_event,
            age_category=current_cat,
            gender=gender,
            round_type=round_type
        ).first()

    return render(request, 'competition/panel_assignments.html', {
        'championship': championship,
        'event_configs': event_configs,
        'current_event': current_event,
        'categories': categories,
        'current_cat': current_cat,
        'gender': gender,
        'round_type': round_type,
        'current_panel': current_panel,
        'existing_roll': existing_roll,
        'approved_judges': approved_judges,
        'annotated_judges': annotated_judges,
        'busy_map': busy_map,
        'busy_count': busy_count,
        'available_count': available_count,
        'busy_roster': busy_roster,
        'total_approved_count': approved_judges.count(),
        'state_logo': state_logo,
        'district_logo': district_logo,
        'fed_logo': fed_logo,
    })

@role_required(RoleChoices.ADMIN, RoleChoices.STAGE_MANAGER, RoleChoices.CD)
def stage_manager(request):
    """
    Stage Manager Workspace & Inside-Panel Stage Control:
    - Displays complete finalized master roll for assigned panel in saved performance order.
    - Call and operational status: Present, Absent, Hold, Resume, Disqualify with reasons & audit.
    - Push to All Assigned Judges enforcing strict batch limits.
    - Item-wise and athlete-wise judge submission progress monitor.
    - Audience second-screen controls and approved-score publication.
    - Navigation link to return to Assigned Panels.
    """
    championship = get_active_championship(request)
    panels = JudgePanel.objects.filter(championship=championship, is_active=True).select_related('event_config', 'age_category', 'stage_manager')
    panel_id = request.GET.get('panel_id')
    
    # Prioritize user's own assigned panel if judge
    user_panels = panels.filter(stage_manager=request.user)
    if panel_id:
        current_panel = panels.filter(id=panel_id).first()
    elif user_panels.exists():
        current_panel = user_panels.first()
    else:
        current_panel = panels.first()

    if not current_panel:
        messages.info(request, "No active panels configured for this championship yet.")
        return redirect('judge_portal')

    # Security check: If user is pure Stage Manager judge (not admin/CD), ensure panel is assigned to them
    if not request.user.is_superuser and not ScopedRole.objects.filter(user=request.user, championship=championship, role=RoleChoices.CD).exists():
        if current_panel.stage_manager != request.user and user_panels.exists():
            current_panel = user_panels.first()

    # Active batch on stage for this specific panel
    active_batch = StageBatch.objects.filter(panel=current_panel, status='ACTIVE').first()
    
    # Find or auto-initialize MasterRoll for current_panel
    roll = MasterRoll.objects.filter(
        championship=championship,
        event_config=current_panel.event_config,
        age_category=current_panel.age_category,
        gender=current_panel.gender,
        round_type=current_panel.round_type
    ).order_by('-version').first()

    if not roll:
        candidates = EventEntry.objects.filter(
            registration__championship=championship,
            event_config=current_panel.event_config,
            age_category=current_panel.age_category,
            gender=current_panel.gender,
            registration__venue_cleared=True
        ).order_by('id')
        if not candidates.exists():
            candidates = EventEntry.objects.filter(
                registration__championship=championship,
                event_config=current_panel.event_config,
                age_category=current_panel.age_category,
                gender=current_panel.gender
            ).order_by('id')

        roll = MasterRoll.objects.create(
            championship=championship,
            event_config=current_panel.event_config,
            age_category=current_panel.age_category,
            gender=current_panel.gender,
            round_type=current_panel.round_type,
            version=current_panel.version,
            is_locked=True,
            locked_by=request.user
        )
        MasterRollEntry.objects.bulk_create([
            MasterRollEntry(
                master_roll=roll,
                event_entry=c,
                performance_order=idx,
                attendance_status='PRESENT'
            )
            for idx, c in enumerate(candidates, start=1)
        ])

    roll_entries = roll.roll_entries.select_related(
        'event_entry__registration__athlete',
        'event_entry__partner_athlete',
        'disqualified_by'
    ).order_by('performance_order')

    # Calculate batch limits according to COP sporting rules
    ev_type = current_panel.event_config.event_type if current_panel.event_config else 'TRADITIONAL'
    round_type = current_panel.round_type
    if ev_type == 'TRADITIONAL':
        max_batch_size = 3 if round_type == 'FINAL' else 5
        batch_limit_text = "Final: maximum 3 simultaneous athletes." if round_type == 'FINAL' else "Preliminary: maximum 5 simultaneous athletes."
    elif ev_type == 'ARTISTIC_SINGLE':
        max_batch_size = 1
        batch_limit_text = "Artistic Single: 1 athlete per performance."
    else:
        max_batch_size = 1  # 1 pair
        batch_limit_text = "Artistic/Rhythmic Pair: 1 pair per performance."

    if request.method == 'POST':
        action = request.POST.get('action')
        redirect_url = f"/competition/stage/?panel_id={current_panel.id}"

        # 1. Update operational attendance / disqualification status
        if action == 'update_entry_status':
            entry_id = request.POST.get('roll_entry_id')
            new_status = request.POST.get('status')
            roll_entry = get_object_or_404(MasterRollEntry, id=entry_id, master_roll__championship=championship)

            if new_status == 'DISQUALIFIED':
                reason = request.POST.get('disqualification_reason', '').strip()
                dq_type = request.POST.get('dq_type', 'OPERATIONAL')
                authorizer = request.POST.get('dq_authorized_by', '').strip()
                if not reason:
                    messages.error(request, "Disqualification requires a recorded reason.")
                    return redirect(redirect_url)

                roll_entry.attendance_status = 'DISQUALIFIED'
                roll_entry.disqualification_reason = reason
                roll_entry.dq_type = dq_type
                roll_entry.dq_authorized_by = authorizer
                roll_entry.disqualified_by = request.user
                roll_entry.disqualified_at = timezone.now()
                roll_entry.save()

                AuditLog.log(
                    action="DISQUALIFY_ENTRY",
                    resource_type="MasterRollEntry",
                    resource_id=str(roll_entry.id),
                    details={
                        "chest_number": roll_entry.event_entry.chest_number,
                        "dq_type": dq_type,
                        "authorizer": authorizer or "Stage Manager",
                        "reason": reason,
                    },
                    user=request.user
                )
                messages.warning(request, f"Entry #{roll_entry.event_entry.chest_number} marked DISQUALIFIED: {reason}")
                return redirect(redirect_url)

            elif new_status == 'RESUME':
                if roll_entry.attendance_status == 'DISQUALIFIED':
                    messages.error(request, "Disqualified entries cannot be resumed through ordinary queue controls.")
                    return redirect(redirect_url)
                roll_entry.attendance_status = 'PRESENT'
                roll_entry.save(update_fields=['attendance_status'])
                messages.success(request, f"Entry #{roll_entry.event_entry.chest_number} resumed to Present.")
                return redirect(redirect_url)

            elif new_status in ['PRESENT', 'ABSENT', 'HOLD']:
                if roll_entry.attendance_status == 'DISQUALIFIED':
                    messages.error(request, "Disqualified entries cannot be changed through ordinary status controls.")
                    return redirect(redirect_url)
                roll_entry.attendance_status = new_status
                roll_entry.save(update_fields=['attendance_status'])
                messages.info(request, f"Entry #{roll_entry.event_entry.chest_number} status set to {new_status}.")
                return redirect(redirect_url)

        # 2. Push eligible batch to judges
        elif action == 'push_batch':
            selected_entry_ids = request.POST.getlist('entry_ids')
            if not selected_entry_ids:
                messages.error(request, "Please select at least 1 Present athlete to push to stage.")
                return redirect(redirect_url)

            # Enforce batch limit
            if len(selected_entry_ids) > max_batch_size:
                messages.error(request, f"Cannot push more than {max_batch_size} athletes. {batch_limit_text}")
                return redirect(redirect_url)

            # Ensure all selected are PRESENT and not Disqualified or Completed
            invalid_entries = roll_entries.filter(event_entry_id__in=selected_entry_ids).exclude(attendance_status='PRESENT')
            if invalid_entries.exists():
                names = [e.event_entry.display_names for e in invalid_entries]
                messages.error(request, f"Cannot push: The following athletes are not in Present status: {', '.join(names)}")
                return redirect(redirect_url)

            # Retire any previous ACTIVE batch on this panel
            StageBatch.objects.filter(panel=current_panel, status='ACTIVE').update(status='COMPLETED')

            # Create new active batch
            batch_num = (StageBatch.objects.filter(panel=current_panel).count() + 1)
            batch = StageBatch.objects.create(
                panel=current_panel,
                batch_number=batch_num,
                status='ACTIVE',
                current_asana_index=1
            )
            entries = EventEntry.objects.filter(id__in=selected_entry_ids)
            batch.entries.set(entries)

            # Create or bind Performance records
            for e in entries:
                perf, _ = Performance.objects.get_or_create(
                    event_entry=e,
                    round_type=current_panel.round_type,
                    defaults={'batch': batch, 'status': PerformanceStatus.IN_PROGRESS}
                )
                perf.batch = batch
                perf.status = PerformanceStatus.IN_PROGRESS
                perf.save(update_fields=['batch', 'status'])

            AuditLog.log(
                action="STAGE_PUSH_BATCH",
                resource_type="StageBatch",
                resource_id=str(batch.id),
                details={
                    "batch_number": batch_num,
                    "panel_id": current_panel.id,
                    "stage_name": current_panel.stage_name,
                    "athletes_count": len(entries),
                    "chest_numbers": [e.chest_number for e in entries],
                },
                user=request.user
            )
            messages.success(request, f"Batch #{batch_num} pushed to all assigned judges! Scoring desks are now open.")
            return redirect(redirect_url)

        # 3. Publish approved scores
        elif action == 'publish_batch':
            if active_batch:
                perfs = Performance.objects.filter(batch=active_batch)
                for p in perfs:
                    p.status = PerformanceStatus.PROVISIONAL_RELEASED
                    p.chief_approved_by = current_panel.chief_judge or request.user
                    p.chief_approved_at = timezone.now()
                    p.save(update_fields=['status', 'chief_approved_by', 'chief_approved_at'])

                # Mark master roll entries as COMPLETED
                MasterRollEntry.objects.filter(master_roll=roll, event_entry__in=active_batch.entries.all()).update(attendance_status='COMPLETED')

                active_batch.status = 'COMPLETED'
                active_batch.save(update_fields=['status'])
                messages.success(request, f"Provisional scores officially released and published for Batch #{active_batch.batch_number}!")
            return redirect(redirect_url)

    # Calculate real-time judge submission progress for active_batch
    judge_progress_list = []
    total_required_items = 7 if ev_type == 'TRADITIONAL' else 10
    batch_entries_count = active_batch.entries.count() if active_batch else 0

    if active_batch:
        seat_definitions = [
            ('Chief Judge', current_panel.chief_judge, 'CHIEF_JUDGE'),
            ('D1 Judge', current_panel.d1_judge, 'D1_JUDGE'),
            ('D2 Judge', current_panel.d2_judge, 'D2_JUDGE'),
            ('D3 Judge', current_panel.d3_judge, 'D3_JUDGE'),
            ('D4 Judge', current_panel.d4_judge, 'D4_JUDGE'),
            ('Time Judge', current_panel.time_judge, 'TIME_JUDGE'),
            ('Evaluator', current_panel.evaluator, 'EVALUATOR'),
        ]
        if current_panel.artistry_judge:
            seat_definitions.append(('Artistry Judge', current_panel.artistry_judge, 'ARTISTRY_JUDGE'))

        for seat_title, judge_user, role_code in seat_definitions:
            if not judge_user:
                judge_progress_list.append({
                    'seat': seat_title,
                    'judge_name': 'Unassigned',
                    'status': 'Not Assigned',
                    'badge_class': 'bg-slate-100 text-slate-500',
                    'items_done': 0,
                    'is_complete': False
                })
                continue

            subs = JudgeSubmission.objects.filter(performance__batch=active_batch, judge_role=role_code)
            all_submitted = subs.exists() and all(s.is_submitted for s in subs)
            
            # Count how many individual asana items submitted across the batch
            items_submitted = AsanaScoreItem.objects.filter(
                submission__in=subs,
                is_submitted=True
            ).count()
            
            # Normalize per athlete
            items_per_athlete = (items_submitted // batch_entries_count) if batch_entries_count > 0 else 0

            if all_submitted:
                status_desc = f"{total_required_items} of {total_required_items} items (Submitted)"
                badge_class = "bg-emerald-100 text-emerald-800"
                is_complete = True
            elif items_per_athlete > 0:
                status_desc = f"{items_per_athlete} of {total_required_items} items submitted"
                badge_class = "bg-blue-100 text-blue-800"
                is_complete = False
            else:
                status_desc = "Waiting for Input"
                badge_class = "bg-amber-100 text-amber-800"
                is_complete = False

            judge_progress_list.append({
                'seat': seat_title,
                'judge_name': judge_user.full_name or judge_user.email,
                'status': status_desc,
                'badge_class': badge_class,
                'items_done': items_per_athlete,
                'is_complete': is_complete
            })

    return render(request, 'competition/stage_manager.html', {
        'championship': championship,
        'panels': panels,
        'current_panel': current_panel,
        'active_batch': active_batch,
        'roll': roll,
        'roll_entries': roll_entries,
        'max_batch_size': max_batch_size,
        'batch_limit_text': batch_limit_text,
        'judge_progress_list': judge_progress_list,
        'total_required_items': total_required_items,
    })

def second_screen(request):
    """
    Implements 16.1 second screen.jpg:
    Audience second screen display visual treatment and latest approved results mode.
    High-contrast stage standings, active batch display, and synchronization status.
    """
    championship = get_active_championship(request)
    active_batch = StageBatch.objects.filter(status='ACTIVE').first()
    
    # Latest released performances
    released_performances = Performance.objects.filter(
        status__in=[PerformanceStatus.PROVISIONAL_RELEASED, PerformanceStatus.CD_FINALIZED]
    ).select_related('event_entry__registration__athlete').order_by('-final_score', '-provisional_score')[:8]

    return render(request, 'competition/second_screen.html', {
        'hide_navbar': True,
        'championship': championship,
        'active_batch': active_batch,
        'released_performances': released_performances,
    })
