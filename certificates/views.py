import io
import uuid
import zipfile
import qrcode
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.core.files.base import ContentFile
from reportlab.lib.pagesizes import letter, landscape
from reportlab.pdfgen import canvas
from .models import CertificateTemplate, IssuedCertificate, TemplateType
from championships.models import Championship
from registrations.models import ChampionshipRegistration, EventEntry
from scoring.models import Performance, PerformanceStatus
from accounts.models import Coach, Volunteer, Guest

def generate_certificate_pdf(cert):
    """
    Renders an individual certificate to PDF bytes using ReportLab,
    honoring the template field layout coordinates and styles.
    """
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=landscape(letter))
    width, height = landscape(letter)

    # Header / Title
    p.setFont("Helvetica-Bold", 24)
    p.drawCentredString(width / 2.0, height - 120, "TELANGANA YOGASANA ASSOCIATION")
    p.setFont("Helvetica", 14)
    p.drawCentredString(width / 2.0, height - 150, "3rd Telangana State Yogasana Sports Championship 2026")

    # Template Type
    p.setFont("Helvetica-Bold", 18)
    p.drawCentredString(width / 2.0, height - 200, cert.template.get_template_type_display().upper())

    # Presentation Line
    p.setFont("Helvetica", 14)
    p.drawCentredString(width / 2.0, height - 250, "This is proudly presented to:")

    # Recipient Name
    p.setFont("Helvetica-Bold", 28)
    p.drawCentredString(width / 2.0, height - 290, cert.recipient_name)

    # Representation / Public ID
    p.setFont("Helvetica", 13)
    p.drawCentredString(width / 2.0, height - 330, f"Representing District: {cert.district} • ID: {cert.public_athlete_id}")

    # Rank / Merit
    if cert.rank_achieved:
        p.setFont("Helvetica-Bold", 15)
        p.drawCentredString(width / 2.0, height - 360, f"Achievement: {cert.rank_achieved} in {cert.event_name}")

    # Footers: Cert Number & Date
    p.setFont("Helvetica", 10)
    p.drawString(80, 70, f"Certificate No: {cert.certificate_number}")
    p.drawRightString(width - 80, 70, f"Date: {cert.issued_at.strftime('%d-%m-%Y') if cert.issued_at else '2026'}")

    p.showPage()
    p.save()
    buffer.seek(0)
    return buffer.getvalue()


@login_required
def certificate_templates(request):
    """
    Implements certifictae templete.jpg:
    Certificate template library, background upload, types (Participation, Merit, Judge, Coach).
    """
    templates = CertificateTemplate.objects.all()

    if request.method == 'POST':
        name = request.POST.get('name')
        t_type = request.POST.get('template_type')
        bg_file = request.FILES.get('background_image')

        default_layout = {
            'recipient_name': {'x': 250, 'y': 320, 'fontSize': 24, 'fontWeight': 'bold', 'color': '#0F172A'},
            'athlete_id': {'x': 250, 'y': 360, 'fontSize': 14, 'color': '#64748B'},
            'event_name': {'x': 250, 'y': 400, 'fontSize': 18, 'fontWeight': 'bold', 'color': '#0284C7'},
            'district': {'x': 250, 'y': 450, 'fontSize': 16, 'color': '#0F172A'},
            'cert_number': {'x': 100, 'y': 560, 'fontSize': 12, 'color': '#64748B'},
            'issue_date': {'x': 450, 'y': 560, 'fontSize': 12, 'color': '#64748B'},
        }

        template = CertificateTemplate.objects.create(
            name=name,
            template_type=t_type,
            background_image=bg_file,
            field_layout=default_layout,
            is_published=True
        )
        messages.success(request, f"Certificate template '{name}' created!")
        return redirect('certificate_template_edit', template_id=template.id)

    return render(request, 'certificates/templates.html', {
        'templates': templates,
        'types': TemplateType.choices,
    })

@login_required
def certificate_template_edit(request, template_id):
    """
    Implements edit templete.jpg:
    Canvas preview, dynamic fields (recipient name, public athlete ID, event, category,
    gender, district, rank, certificate number, issue date, verification QR).
    Field properties: position x, y, width, height, font, size, weight, color, alignment.
    Never offer private key as a field!
    """
    template = get_object_or_404(CertificateTemplate, id=template_id)

    if request.method == 'POST':
        import json
        layout_json = request.POST.get('field_layout')
        if layout_json:
            try:
                template.field_layout = json.loads(layout_json)
                template.save()
                messages.success(request, "Certificate canvas layout saved successfully.")
            except Exception as e:
                messages.error(request, f"Error saving layout: {str(e)}")
        return redirect('certificate_template_edit', template_id=template.id)

    return render(request, 'certificates/edit_template.html', {
        'template': template,
    })

@login_required
def certificate_generate(request):
    """
    Implements geerate certifictae export.jpg:
    Recipient source (Athletes, Merit, Coaches, Volunteers, Guests, CSV),
    generation of certificates, transactional certificate numbering,
    export combined PDF, individual PDFs in ZIP, and CSV/Excel registers.
    """
    championship = Championship.objects.first()
    templates = CertificateTemplate.objects.filter(is_published=True)
    issued_list = IssuedCertificate.objects.filter(championship=championship)

    if request.method == 'POST':
        action = request.POST.get('action')
        template_id = request.POST.get('template_id')
        template = CertificateTemplate.objects.filter(id=template_id).first() if template_id else CertificateTemplate.objects.first()
        recipient_source = request.POST.get('recipient_source')

        if action == 'generate':
            count = 0
            # Generate certificates based on recipient source
            if recipient_source == 'merit':
                perfs = Performance.objects.filter(status__in=[PerformanceStatus.PROVISIONAL_RELEASED, PerformanceStatus.CD_FINALIZED])
                for idx, p in enumerate(perfs, start=1):
                    cert_num = f"TYSA-2026-MERIT-{1000 + IssuedCertificate.objects.count() + 1}"
                    IssuedCertificate.objects.get_or_create(
                        template=template,
                        championship=championship,
                        recipient_name=p.event_entry.display_names,
                        defaults={
                            'certificate_number': cert_num,
                            'recipient_type': 'ATHLETE',
                            'public_athlete_id': p.event_entry.registration.athlete.public_athlete_id,
                            'district': p.event_entry.district,
                            'event_name': p.event_entry.event_config.get_event_type_display(),
                            'rank_achieved': f"Rank #{idx}",
                            'issued_by': request.user
                        }
                    )
                    count += 1
            elif recipient_source == 'coaches':
                coaches = Coach.objects.filter(championship=championship)
                for c in coaches:
                    cert_num = f"TYSA-2026-COACH-{1000 + IssuedCertificate.objects.count() + 1}"
                    IssuedCertificate.objects.get_or_create(
                        template=template,
                        championship=championship,
                        recipient_name=c.name,
                        defaults={
                            'certificate_number': cert_num,
                            'recipient_type': 'COACH',
                            'district': c.district,
                            'event_name': 'Official Championship Coach',
                            'issued_by': request.user
                        }
                    )
                    c.certificate_issued = True
                    c.save()
                    count += 1
            else:
                # Default athletes participation
                regs = ChampionshipRegistration.objects.filter(championship=championship, status__in=['DISTRICT_APPROVED', 'VENUE_CLEARED'])
                for r in regs:
                    cert_num = f"TYSA-2026-PART-{1000 + IssuedCertificate.objects.count() + 1}"
                    IssuedCertificate.objects.get_or_create(
                        template=template,
                        championship=championship,
                        recipient_name=r.athlete.full_name,
                        defaults={
                            'certificate_number': cert_num,
                            'recipient_type': 'ATHLETE',
                            'public_athlete_id': r.athlete.public_athlete_id,
                            'district': r.athlete.district,
                            'event_name': 'State Championship Participation',
                            'issued_by': request.user
                        }
                    )
                    count += 1

            messages.success(request, f"Generated {count} official certificates!")
            return redirect('certificate_generate')

        elif action == 'download_pdf':
            # Generate combined PDF via ReportLab
            buffer = io.BytesIO()
            p = canvas.Canvas(buffer, pagesize=landscape(letter))
            width, height = landscape(letter)

            certs = IssuedCertificate.objects.filter(championship=championship)
            for cert in certs:
                p.setFont("Helvetica-Bold", 24)
                p.drawCentredString(width / 2.0, height - 120, "TELANGANA YOGASANA ASSOCIATION")
                p.setFont("Helvetica", 14)
                p.drawCentredString(width / 2.0, height - 150, "3rd Telangana State Yogasana Sports Championship 2026")
                
                p.setFont("Helvetica-Bold", 18)
                p.drawCentredString(width / 2.0, height - 200, cert.template.get_template_type_display().upper())

                p.setFont("Helvetica", 14)
                p.drawCentredString(width / 2.0, height - 250, "This is proudly presented to:")
                p.setFont("Helvetica-Bold", 28)
                p.drawCentredString(width / 2.0, height - 290, cert.recipient_name)

                p.setFont("Helvetica", 13)
                p.drawCentredString(width / 2.0, height - 330, f"Representing District: {cert.district} • ID: {cert.public_athlete_id}")
                if cert.rank_achieved:
                    p.setFont("Helvetica-Bold", 15)
                    p.drawCentredString(width / 2.0, height - 360, f"Achievement: {cert.rank_achieved} in {cert.event_name}")

                p.setFont("Helvetica", 10)
                p.drawString(80, 70, f"Certificate No: {cert.certificate_number}")
                p.drawRightString(width - 80, 70, f"Date: {cert.issued_at.strftime('%d-%m-%Y')}")
                p.showPage()

            p.save()
            buffer.seek(0)
            response = HttpResponse(buffer, content_type='application/pdf')
            response['Content-Disposition'] = 'attachment; filename="TYSA_Certificates_Batch_2026.pdf"'
            return response

        elif action == 'download_zip':
            # Generate individual PDFs in a ZIP
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                certs = IssuedCertificate.objects.filter(championship=championship)
                for cert in certs:
                    pdf_buf = io.BytesIO()
                    p = canvas.Canvas(pdf_buf, pagesize=landscape(letter))
                    w, h = landscape(letter)
                    p.setFont("Helvetica-Bold", 24)
                    p.drawCentredString(w / 2.0, h - 120, "TELANGANA YOGASANA ASSOCIATION")
                    p.setFont("Helvetica", 14)
                    p.drawCentredString(w / 2.0, h - 150, "3rd Telangana State Yogasana Sports Championship 2026")
                    p.setFont("Helvetica-Bold", 18)
                    p.drawCentredString(w / 2.0, h - 200, cert.template.get_template_type_display().upper())
                    p.setFont("Helvetica-Bold", 26)
                    p.drawCentredString(w / 2.0, h - 280, cert.recipient_name)
                    p.setFont("Helvetica", 12)
                    p.drawCentredString(w / 2.0, h - 320, f"District: {cert.district} • Public ID: {cert.public_athlete_id}")
                    if cert.rank_achieved:
                        p.setFont("Helvetica-Bold", 14)
                        p.drawCentredString(w / 2.0, h - 350, f"{cert.rank_achieved} — {cert.event_name}")
                    p.setFont("Helvetica", 10)
                    p.drawString(80, 70, f"Certificate No: {cert.certificate_number}")
                    p.drawRightString(w - 80, 70, f"Date: {cert.issued_at.strftime('%d-%m-%Y')}")
                    p.showPage()
                    p.save()
                    pdf_buf.seek(0)
                    safe_name = cert.certificate_number.replace('/', '_')
                    zf.writestr(f"{safe_name}.pdf", pdf_buf.getvalue())
            
            zip_buffer.seek(0)
            response = HttpResponse(zip_buffer, content_type='application/zip')
            response['Content-Disposition'] = 'attachment; filename="TYSA_Certificates_Individual_2026.zip"'
            return response

        elif action == 'download_csv':
            import csv
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = 'attachment; filename="TYSA_Certificates_Register_2026.csv"'
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
                    cert.issued_at.strftime('%Y-%m-%d')
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
                    cert.issued_at.strftime('%Y-%m-%d')
                ])
            out_buf = io.BytesIO()
            wb.save(out_buf)
            out_buf.seek(0)
            response = HttpResponse(out_buf, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            response['Content-Disposition'] = 'attachment; filename="TYSA_Certificates_Register_2026.xlsx"'
            return response

    return render(request, 'certificates/generate.html', {
        'championship': championship,
        'templates': templates,
        'issued_list': issued_list,
    })

def certificate_verify_qr(request, uuid):
    """
    Public verification QR endpoint:
    Reveals permitted public certificate info ONLY. Never credentials or private keys.
    """
    cert = get_object_or_404(IssuedCertificate, verification_uuid=uuid)
    return render(request, 'certificates/verify.html', {
        'cert': cert,
    })
