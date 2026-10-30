import io
import uuid
import zipfile
import csv
import qrcode
from reportlab.lib.pagesizes import letter, landscape, A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.lib.colors import HexColor

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.core.files.base import ContentFile
from django.db import transaction

from .models import CertificateTemplate, IssuedCertificate, CertificateSequence, TemplateType
from championships.models import Championship, EventConfiguration, AgeCategory
from registrations.models import ChampionshipRegistration, EventEntry
from scoring.models import Performance, PerformanceStatus
from scoring.services import calculate_event_division_standings, TiePolicy
from accounts.models import Coach, Volunteer, Guest

PAGE_WIDTH = 792.0
PAGE_HEIGHT = 612.0

def generate_certificate_pdf(cert):
    """
    Renders an individual certificate to PDF bytes using ReportLab,
    honoring the saved template background, dynamic field coordinates,
    typography styles, verification QR code, and dynamic championship metadata.
    """
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=(PAGE_WIDTH, PAGE_HEIGHT))
    width, height = PAGE_WIDTH, PAGE_HEIGHT

    template = cert.template
    layout = template.field_layout or {}

    # 1. Background image if present
    if template.background_image and hasattr(template.background_image, 'path'):
        try:
            p.drawImage(template.background_image.path, 0, 0, width=width, height=height)
        except Exception:
            pass

    # 2. Championship header info
    champ = cert.championship
    champ_title = champ.name if champ else "Telangana Yogasana Association"
    event_subtitle = f"{champ.venue_city} • {champ.rules_edition}" if champ else "Official State Championship"

    # Default header if not overridden in background
    p.setFont("Helvetica-Bold", 22)
    p.setFillColor(HexColor('#0F172A'))
    p.drawCentredString(width / 2.0, height - 70, champ_title.upper())
    
    p.setFont("Helvetica", 11)
    p.setFillColor(HexColor('#64748B'))
    p.drawCentredString(width / 2.0, height - 90, event_subtitle)

    p.setFont("Helvetica-Bold", 14)
    p.setFillColor(HexColor('#0369A1'))
    p.drawCentredString(width / 2.0, height - 120, cert.template.get_template_type_display().upper())

    p.setFont("Helvetica", 12)
    p.setFillColor(HexColor('#475569'))
    p.drawCentredString(width / 2.0, height - 145, "This is proudly presented to:")

    # Helper function to render a layout field
    def draw_field(field_key, default_text, default_x, default_y, default_size=14, default_bold=False, default_color='#0F172A'):
        cfg = layout.get(field_key, {})
        # In layout JSON, y is from top (0 = top, 612 = bottom). Convert to ReportLab (0 = bottom).
        top_y = float(cfg.get('y', default_y))
        rl_y = height - top_y
        rl_x = float(cfg.get('x', default_x))
        font_size = float(cfg.get('fontSize', default_size))
        is_bold = (cfg.get('fontWeight') in ['bold', '900'] or default_bold)
        color_hex = cfg.get('color', default_color)

        try:
            p.setFillColor(HexColor(color_hex))
        except Exception:
            p.setFillColor(HexColor(default_color))

        font_name = "Helvetica-Bold" if is_bold else "Helvetica"
        p.setFont(font_name, font_size)
        p.drawCentredString(rl_x, rl_y, default_text)

    # 3. Dynamic fields based on template layout
    draw_field('recipient_name', cert.recipient_name, width / 2.0, 200, default_size=26, default_bold=True, default_color='#0F172A')
    
    if cert.public_athlete_id:
        draw_field('athlete_id', f"Public ID: {cert.public_athlete_id}", width / 2.0, 235, default_size=12, default_color='#64748B')
    
    if cert.event_name:
        draw_field('event_name', f"Event: {cert.event_name}", width / 2.0, 265, default_size=15, default_bold=True, default_color='#0284C7')
    
    if cert.rank_achieved:
        draw_field('rank', f"Achievement: {cert.rank_achieved}", width / 2.0, 295, default_size=16, default_bold=True, default_color='#D97706')
        
    if cert.district:
        draw_field('district', f"Representing District: {cert.district}", width / 2.0, 325, default_size=13, default_color='#334155')

    # Footers
    issue_date_str = cert.issued_at.strftime('%d-%m-%Y') if cert.issued_at else '2026'
    draw_field('cert_number', f"Certificate No: {cert.certificate_number}", 160, 540, default_size=10, default_color='#64748B')
    draw_field('issue_date', f"Date of Issue: {issue_date_str}", width - 160, 540, default_size=10, default_color='#64748B')

    # 4. Verification QR Code
    try:
        verify_url = f"https://tysa.score.in/certificates/verify/{cert.certificate_number}/"
        qr = qrcode.QRCode(box_size=3, border=1)
        qr.add_data(verify_url)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white")
        qr_buf = io.BytesIO()
        qr_img.save(qr_buf, format='PNG')
        qr_buf.seek(0)
        p.drawImage(ImageReader(qr_buf), (width / 2.0) - 30, 45, width=60, height=60)
        p.setFont("Helvetica", 8)
        p.setFillColor(HexColor('#94A3B8'))
        p.drawCentredString(width / 2.0, 35, "Scan QR to Authenticate")
    except Exception:
        pass

    p.showPage()
    p.save()
    buffer.seek(0)
    return buffer.getvalue()


def _get_next_cert_number(championship, prefix='CERT'):
    """
    Allocates an atomic sequential certificate number using dedicated
    CertificateSequence counter with row-level select_for_update.
    """
    champ_code = championship.code if championship else "TYSA-2026"
    seq_obj, _ = CertificateSequence.objects.select_for_update().get_or_create(
        championship=championship,
        prefix=prefix,
        defaults={'last_number': 1000}
    )
    seq_obj.last_number += 1
    seq_obj.save(update_fields=['last_number'])
    return f"{champ_code}-{prefix}-{seq_obj.last_number:04d}"


def _user_can_manage_certificates(user, championship=None):
    if user.is_superuser:
        return True
    if championship:
        return user.scoped_roles.filter(championship=championship, role__in=['ADMIN', 'CD']).exists()
    return user.scoped_roles.filter(role__in=['ADMIN', 'CD']).exists()


@login_required
def certificate_templates(request):
    """
    Certificate template library and background upload.
    Enforces CD and Admin permission checks.
    """
    if not _user_can_manage_certificates(request.user):
        return HttpResponseForbidden("Access restricted: Only Championship Director or Admin can manage certificates.")

    templates = CertificateTemplate.objects.all()

    if request.method == 'POST':
        name = request.POST.get('name')
        t_type = request.POST.get('template_type')
        bg_file = request.FILES.get('background_image')

        default_layout = {
            'recipient_name': {'x': 396, 'y': 200, 'fontSize': 26, 'fontWeight': 'bold', 'color': '#0F172A'},
            'athlete_id': {'x': 396, 'y': 235, 'fontSize': 12, 'color': '#64748B'},
            'event_name': {'x': 396, 'y': 265, 'fontSize': 15, 'fontWeight': 'bold', 'color': '#0284C7'},
            'rank': {'x': 396, 'y': 295, 'fontSize': 16, 'fontWeight': 'bold', 'color': '#D97706'},
            'district': {'x': 396, 'y': 325, 'fontSize': 13, 'color': '#334155'},
            'cert_number': {'x': 160, 'y': 540, 'fontSize': 10, 'color': '#64748B'},
            'issue_date': {'x': 632, 'y': 540, 'fontSize': 10, 'color': '#64748B'},
        }

        template = CertificateTemplate.objects.create(
            name=name,
            template_type=t_type,
            background_image=bg_file,
            field_layout=default_layout,
            is_published=True
        )
        messages.success(request, f"Certificate template '{name}' created successfully!")
        return redirect('certificate_template_edit', template_id=template.id)

    return render(request, 'certificates/templates.html', {
        'templates': templates,
        'types': TemplateType.choices,
    })


@login_required
def certificate_template_edit(request, template_id):
    """
    Dynamic Field Canvas Editor.
    Enforces CD and Admin permission checks.
    """
    if not _user_can_manage_certificates(request.user):
        return HttpResponseForbidden("Access restricted: Only Championship Director or Admin can edit templates.")

    template = get_object_or_404(CertificateTemplate, id=template_id)

    if request.method == 'POST':
        field_layout_raw = request.POST.get('field_layout')
        if field_layout_raw:
            try:
                import json
                new_layout = json.loads(field_layout_raw)
                template.field_layout = new_layout
                template.version += 1
                template.save()
                messages.success(request, f"Certificate template '{template.name}' saved (v{template.version}).")
            except Exception as e:
                messages.error(request, f"Error saving canvas layout: {str(e)}")
        return redirect('certificate_template_edit', template_id=template.id)

    return render(request, 'certificates/edit_template.html', {
        'template': template,
    })


@login_required
def certificate_generate(request):
    """
    Generate and export official certificates:
    - Scoped strictly to authorized championship
    - Merit certificates issued from CD finalized results and actual rank standings
    - Individual certificates issued for both members of pair entries
    - Transaction-safe atomic numbering
    - Downloads: Combined PDF, Individual ZIP, CSV, XLSX
    """
    championship = Championship.objects.first()
    if not _user_can_manage_certificates(request.user, championship=championship):
        return HttpResponseForbidden("Access restricted: Only Championship Director or Admin can generate certificates.")

    templates = CertificateTemplate.objects.filter(is_published=True)
    issued_list = IssuedCertificate.objects.filter(championship=championship)

    if request.method == 'POST':
        action = request.POST.get('action')
        template_id = request.POST.get('template_id')
        template = CertificateTemplate.objects.filter(id=template_id).first() if template_id else CertificateTemplate.objects.first()
        recipient_source = request.POST.get('recipient_source')

        if action == 'generate':
            count = 0
            with transaction.atomic():
                if recipient_source == 'merit':
                    # Group by Championship, Event Configuration, Age Category, Gender, and Round
                    events = EventConfiguration.objects.filter(championship=championship)
                    age_cats = AgeCategory.objects.all()

                    for ev in events:
                        for age in age_cats:
                            for g in ['M', 'F']:
                                for rnd in ['FINAL', 'PRELIMINARY']:
                                    standings = calculate_event_division_standings(
                                        championship=championship,
                                        event_config=ev,
                                        age_category=age,
                                        gender=g,
                                        round_type=rnd,
                                        tie_policy=TiePolicy.STANDARD
                                    )
                                    # If finals exist for this division, don't generate duplicate merit certs for prelims
                                    if rnd == 'PRELIMINARY' and Performance.objects.filter(
                                        event_entry__event_config=ev,
                                        event_entry__age_category=age,
                                        event_entry__gender=g,
                                        round_type='FINAL',
                                        status=PerformanceStatus.CD_FINALIZED
                                    ).exists():
                                        continue

                                    for item in standings:
                                        entry = item['event_entry']
                                        rank_label = item['rank_label']
                                        full_event_title = f"{item['event_name']} — {item['age_category_name']} ({item['gender']})"

                                        # Primary Athlete Certificate
                                        cert_num_1 = _get_next_cert_number(championship, prefix='MERIT')
                                        IssuedCertificate.objects.get_or_create(
                                            template=template,
                                            championship=championship,
                                            recipient_name=item['athlete'].full_name,
                                            public_athlete_id=item['athlete'].public_athlete_id,
                                            defaults={
                                                'certificate_number': cert_num_1,
                                                'recipient_type': 'ATHLETE',
                                                'district': item['district'],
                                                'event_name': full_event_title,
                                                'age_category': item['age_category_name'],
                                                'gender': item['gender'],
                                                'rank_achieved': rank_label,
                                                'issued_by': request.user
                                            }
                                        )
                                        count += 1

                                        # Partner Athlete Certificate if Pair Event
                                        if item['is_pair'] and item['partner']:
                                            cert_num_2 = _get_next_cert_number(championship, prefix='MERIT')
                                            IssuedCertificate.objects.get_or_create(
                                                template=template,
                                                championship=championship,
                                                recipient_name=item['partner'].full_name,
                                                public_athlete_id=item['partner'].public_athlete_id,
                                                defaults={
                                                    'certificate_number': cert_num_2,
                                                    'recipient_type': 'ATHLETE',
                                                    'district': item['district'],
                                                    'event_name': full_event_title,
                                                    'age_category': item['age_category_name'],
                                                    'gender': item['gender'],
                                                    'rank_achieved': rank_label,
                                                    'issued_by': request.user
                                                }
                                            )
                                            count += 1

                elif recipient_source == 'coaches':
                    coaches = Coach.objects.filter(championship=championship)
                    for c in coaches:
                        cert_num = _get_next_cert_number(championship, prefix='COACH')
                        IssuedCertificate.objects.get_or_create(
                            template=template,
                            championship=championship,
                            recipient_name=c.name,
                            defaults={
                                'certificate_number': cert_num,
                                'recipient_type': 'COACH',
                                'district': c.district,
                                'event_name': 'Official Accredited Coach',
                                'issued_by': request.user
                            }
                        )
                        c.certificate_issued = True
                        c.save()
                        count += 1

                elif recipient_source == 'volunteers':
                    volunteers = Volunteer.objects.filter(championship=championship)
                    for v in volunteers:
                        cert_num = _get_next_cert_number(championship, prefix='VOL')
                        IssuedCertificate.objects.get_or_create(
                            template=template,
                            championship=championship,
                            recipient_name=v.name,
                            defaults={
                                'certificate_number': cert_num,
                                'recipient_type': 'VOLUNTEER',
                                'district': v.role,
                                'event_name': 'Volunteer Service Certificate',
                                'issued_by': request.user
                            }
                        )
                        count += 1

                else:
                    # Participation: Venue cleared and approved athletes
                    regs = ChampionshipRegistration.objects.filter(
                        championship=championship,
                        status__in=['DISTRICT_APPROVED', 'VENUE_CLEARED']
                    )
                    for r in regs:
                        cert_num = _get_next_cert_number(championship, prefix='PART')
                        IssuedCertificate.objects.get_or_create(
                            template=template,
                            championship=championship,
                            recipient_name=r.athlete.full_name,
                            public_athlete_id=r.athlete.public_athlete_id,
                            defaults={
                                'certificate_number': cert_num,
                                'recipient_type': 'ATHLETE',
                                'district': r.athlete.district,
                                'event_name': 'State Championship Participation',
                                'issued_by': request.user
                            }
                        )
                        count += 1

            messages.success(request, f"Successfully generated {count} official certificates!")
            return redirect('certificate_generate')

        elif action == 'download_pdf':
            buffer = io.BytesIO()
            certs = IssuedCertificate.objects.filter(championship=championship).order_by('certificate_number')
            
            # Combine individual generated PDF pages
            from pypdf import PdfWriter, PdfReader
            writer = PdfWriter()
            
            for cert in certs:
                pdf_bytes = generate_certificate_pdf(cert)
                reader = PdfReader(io.BytesIO(pdf_bytes))
                for page in reader.pages:
                    writer.add_page(page)

            writer.write(buffer)
            buffer.seek(0)
            response = HttpResponse(buffer, content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="TYSA_Certificates_{championship.code if championship else 2026}.pdf"'
            return response

        elif action == 'download_zip':
            zip_buffer = io.BytesIO()
            certs = IssuedCertificate.objects.filter(championship=championship)
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                for cert in certs:
                    pdf_bytes = generate_certificate_pdf(cert)
                    safe_name = cert.certificate_number.replace('/', '_')
                    zf.writestr(f"{safe_name}.pdf", pdf_bytes)

            zip_buffer.seek(0)
            response = HttpResponse(zip_buffer, content_type='application/zip')
            response['Content-Disposition'] = f'attachment; filename="TYSA_Certificates_{championship.code if championship else 2026}_ZIP.zip"'
            return response

        elif action == 'download_csv':
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = f'attachment; filename="TYSA_Certificates_{championship.code if championship else 2026}_Register.csv"'
            writer = csv.writer(response)
            writer.writerow(['Certificate No', 'Recipient Name', 'Type', 'Public Athlete ID', 'District', 'Event', 'Rank', 'Issue Date'])
            for cert in IssuedCertificate.objects.filter(championship=championship):
                writer.writerow([
                    cert.certificate_number,
                    cert.recipient_name,
                    cert.template.get_template_type_display(),
                    cert.public_athlete_id,
                    cert.district,
                    cert.event_name,
                    cert.rank_achieved,
                    cert.issued_at.strftime('%Y-%m-%d') if cert.issued_at else ''
                ])
            return response

        elif action == 'download_xlsx':
            import openpyxl
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Certificates Register"
            ws.append(['Certificate No', 'Recipient Name', 'Type', 'Public Athlete ID', 'District', 'Event', 'Rank', 'Issue Date'])
            for cert in IssuedCertificate.objects.filter(championship=championship):
                ws.append([
                    cert.certificate_number,
                    cert.recipient_name,
                    cert.template.get_template_type_display(),
                    cert.public_athlete_id,
                    cert.district,
                    cert.event_name,
                    cert.rank_achieved,
                    cert.issued_at.strftime('%Y-%m-%d') if cert.issued_at else ''
                ])
            out_buf = io.BytesIO()
            wb.save(out_buf)
            out_buf.seek(0)
            response = HttpResponse(out_buf, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            response['Content-Disposition'] = f'attachment; filename="TYSA_Certificates_{championship.code if championship else 2026}_Register.xlsx"'
            return response

    return render(request, 'certificates/generate.html', {
        'championship': championship,
        'templates': templates,
        'issued_list': issued_list,
    })


def certificate_verify_qr(request, cert_number):
    """
    Public verification QR endpoint:
    Displays verified certificate attributes without disclosing any private keys or credentials.
    """
    cert = get_object_or_404(IssuedCertificate, certificate_number=cert_number)
    return render(request, 'certificates/verify.html', {
        'cert': cert,
    })
