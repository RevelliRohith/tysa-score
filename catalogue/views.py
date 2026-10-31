import json
import csv
import io
import os
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from .models import Asana, AsanaStatus
from audit.models import AuditLog

@login_required
def catalogue_view(request):
    """
    Dynamic Asana Library & Official Competition Catalogue Management:
    - Search by code, name, description.
    - Category, Base Value (BV), and Status (Published, Draft, Archived) filters.
    - Sorting by Code, Name, Base Value, and S.No.
    - View Mode toggle (Grid view vs Table/List view).
    - Add, edit, and upload pose image.
    - Draft, publish, and archive actions.
    - JSON, CSV, and XLSX import with field mapping, validation, and duplicate handling.
    - Export catalogue to CSV.
    - Change history logging via AuditLog.
    """
    tab = request.GET.get('tab', 'published').strip().lower()
    if tab not in ['published', 'draft', 'archived', 'all']:
        tab = 'published'

    query = request.GET.get('q', '').strip()
    category = request.GET.get('category', '').strip()
    bv = request.GET.get('bv', '').strip()
    sort_by = request.GET.get('sort', 'sno').strip()
    view_mode = request.GET.get('view', 'grid').strip() # 'grid' or 'list'
    show_ref = request.GET.get('show_ref', 'false') == 'true'

    # Handle POST mutations
    if request.method == 'POST':
        action = request.POST.get('action')

        # 1. Add New Asana
        if action == 'add_asana':
            code = request.POST.get('code', '').strip().upper()
            raw_type = request.POST.get('category', '').strip()
            display_category = Asana.normalize_category(raw_type)
            display_name = request.POST.get('display_name', '').strip()
            description = request.POST.get('description', '').strip()
            bv_val = Decimal(request.POST.get('base_value', '0.80'))
            submit_type = request.POST.get('submit_type', 'publish')
            target_status = AsanaStatus.DRAFT if submit_type == 'draft' else AsanaStatus.PUBLISHED

            image_url = ""
            image_filename = ""
            if 'image' in request.FILES:
                img_file = request.FILES['image']
                ext = os.path.splitext(img_file.name)[1].lower()
                clean_filename = f"custom_{code}{ext}"
                file_path = default_storage.save(f"asanas/{clean_filename}", ContentFile(img_file.read()))
                image_url = default_storage.url(file_path)
                image_filename = clean_filename

            last_asana = Asana.objects.order_by('-source_id').first()
            next_id = (last_asana.source_id + 1) if last_asana else 1

            if Asana.objects.filter(code=code).exists():
                messages.error(request, f"Cannot add pose: Code '{code}' already exists in the catalogue.")
            else:
                new_pose = Asana.objects.create(
                    source_id=next_id,
                    sno=next_id,
                    code=code,
                    raw_type=raw_type,
                    display_category=display_category,
                    display_name=display_name,
                    description=description,
                    base_value=bv_val,
                    status=target_status,
                    is_published=(target_status == AsanaStatus.PUBLISHED),
                    is_reference_record=code.startswith('BAC-'),
                    image_url=image_url or f"/static/asanas/{image_filename}" if image_filename else None,
                    image_filename=image_filename or None
                )

                AuditLog.log(
                    action='ASANA_CREATED',
                    resource_type='Asana',
                    resource_id=new_pose.id,
                    details={'code': code, 'name': display_name, 'bv': str(bv_val), 'status': target_status},
                    user=request.user
                )

                status_label = "Draft" if target_status == AsanaStatus.DRAFT else "Published"
                messages.success(request, f"Pose '{code} - {display_name}' saved successfully as {status_label}.")
            return redirect(f"/catalogue/?tab={tab}&view={view_mode}")

        # 2. Edit Existing Asana
        elif action == 'edit_asana':
            asana_id = request.POST.get('asana_id')
            asana = get_object_or_404(Asana, id=asana_id)

            code = request.POST.get('code', '').strip().upper()
            raw_type = request.POST.get('category', '').strip()
            display_name = request.POST.get('display_name', '').strip()
            description = request.POST.get('description', '').strip()
            bv_val = Decimal(request.POST.get('base_value', '0.80'))

            if code and code != asana.code and Asana.objects.filter(code=code).exclude(id=asana.id).exists():
                messages.error(request, f"Code '{code}' is already used by another pose.")
                return redirect(f"/catalogue/?tab={tab}&view={view_mode}")

            asana.code = code
            asana.raw_type = raw_type
            asana.display_category = Asana.normalize_category(raw_type)
            asana.display_name = display_name
            asana.description = description
            asana.base_value = bv_val

            if 'image' in request.FILES:
                img_file = request.FILES['image']
                ext = os.path.splitext(img_file.name)[1].lower()
                clean_filename = f"custom_{code}{ext}"
                file_path = default_storage.save(f"asanas/{clean_filename}", ContentFile(img_file.read()))
                asana.image_url = default_storage.url(file_path)
                asana.image_filename = clean_filename

            asana.version += 1
            asana.save()

            AuditLog.log(
                action='ASANA_UPDATED',
                resource_type='Asana',
                resource_id=asana.id,
                details={'code': code, 'name': display_name, 'bv': str(bv_val), 'version': asana.version},
                user=request.user
            )

            messages.success(request, f"Updated pose '{asana.code} - {asana.display_name}' (v{asana.version}).")
            return redirect(f"/catalogue/?tab={tab}&view={view_mode}")

        # 3. Update Status (Publish, Draft, Archive)
        elif action == 'update_status':
            asana_id = request.POST.get('asana_id')
            target_status = request.POST.get('target_status')
            if target_status in [AsanaStatus.PUBLISHED, AsanaStatus.DRAFT, AsanaStatus.ARCHIVED]:
                asana = get_object_or_404(Asana, id=asana_id)
                old_status = asana.status
                asana.status = target_status
                asana.is_published = (target_status == AsanaStatus.PUBLISHED)
                asana.save(update_fields=['status', 'is_published', 'updated_at'])

                AuditLog.log(
                    action='ASANA_STATUS_CHANGED',
                    resource_type='Asana',
                    resource_id=asana.id,
                    details={'code': asana.code, 'from': old_status, 'to': target_status},
                    user=request.user
                )

                messages.success(request, f"Pose '{asana.code} - {asana.display_name}' moved to {target_status.title()}.")
            return redirect(f"/catalogue/?tab={tab}&view={view_mode}")

        # 4. Import Poses from JSON, CSV, or XLSX
        elif action == 'import_catalogue':
            import_file = request.FILES.get('catalogue_file')
            duplicate_strategy = request.POST.get('duplicate_strategy', 'update') # 'update' or 'skip'

            if not import_file:
                messages.error(request, "Please select a JSON, CSV, or XLSX catalogue file to upload.")
                return redirect(f"/catalogue/?tab={tab}&view={view_mode}")

            fname = import_file.name.lower()
            records = []
            try:
                if fname.endswith('.json'):
                    content = import_file.read().decode('utf-8')
                    records = json.loads(content)
                elif fname.endswith('.csv'):
                    content = import_file.read().decode('utf-8', errors='replace')
                    reader = csv.DictReader(io.StringIO(content))
                    records = list(reader)
                elif fname.endswith('.xlsx'):
                    import openpyxl
                    wb = openpyxl.load_workbook(import_file, data_only=True)
                    ws = wb.active
                    headers = [str(cell.value or '').strip().lower() for cell in ws[1]]
                    for row in ws.iter_rows(min_row=2, values_only=True):
                        if any(row):
                            records.append(dict(zip(headers, row)))
                else:
                    messages.error(request, "Unsupported file format. Please upload .json, .csv, or .xlsx.")
                    return redirect(f"/catalogue/?tab={tab}&view={view_mode}")

                created_count = 0
                updated_count = 0
                skipped_count = 0

                for r in records:
                    code = str(r.get('code') or r.get('Code') or '').strip().upper()
                    if not code:
                        continue

                    raw_type = str(r.get('type') or r.get('Type') or r.get('category') or r.get('Category') or '').strip()
                    display_cat = Asana.normalize_category(raw_type)
                    desc = str(r.get('description') or r.get('Description') or '').strip()
                    
                    # Display name derivation
                    display_name = str(r.get('display_name') or r.get('name') or r.get('Name') or '').strip()
                    if not display_name and desc:
                        first_line = desc.split('\n')[0].strip()
                        display_name = first_line.split('.')[0].strip()
                    if not display_name:
                        display_name = code

                    bv_raw = r.get('base_value') or r.get('Base Value') or r.get('base_difficulty')
                    try:
                        bv_val = Decimal(str(bv_raw).strip())
                    except Exception:
                        bv_val = Decimal('0.80')

                    is_ref = code.startswith('BAC-') or ('base asana' in raw_type.lower())

                    # Check existing
                    existing = Asana.objects.filter(code=code).first()
                    if existing:
                        if duplicate_strategy == 'update':
                            existing.raw_type = raw_type
                            existing.display_category = display_cat
                            existing.display_name = display_name
                            existing.description = desc
                            existing.base_value = bv_val
                            existing.is_reference_record = is_ref
                            existing.save()
                            updated_count += 1
                        else:
                            skipped_count += 1
                    else:
                        last_a = Asana.objects.order_by('-source_id').first()
                        next_id = (last_a.source_id + 1) if last_a else 1
                        
                        sno_raw = r.get('sno') or r.get('s_no') or r.get('id')
                        try:
                            sno_int = int(sno_raw)
                        except Exception:
                            sno_int = next_id

                        Asana.objects.create(
                            source_id=next_id,
                            sno=sno_int,
                            code=code,
                            raw_type=raw_type,
                            display_category=display_cat,
                            display_name=display_name,
                            description=desc,
                            base_value=bv_val,
                            status=AsanaStatus.PUBLISHED,
                            is_published=True,
                            is_reference_record=is_ref,
                            image_filename=f"{sno_int:03d}_{code}.png" if not is_ref else None,
                            image_url=f"/static/asanas/{sno_int:03d}_{code}.png" if not is_ref else None
                        )
                        created_count += 1

                AuditLog.log(
                    action='CATALOGUE_IMPORTED',
                    resource_type='Asana',
                    details={'created': created_count, 'updated': updated_count, 'skipped': skipped_count, 'file': fname},
                    user=request.user
                )

                messages.success(
                    request,
                    f"Catalogue Import Complete: {created_count} new poses created, {updated_count} updated, {skipped_count} skipped."
                )
            except Exception as e:
                messages.error(request, f"Import failed: {e}")
            return redirect(f"/catalogue/?tab={tab}&view={view_mode}")

    # Handle Export
    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="TYSA_Asana_Catalogue_{tab}.csv"'
        writer = csv.writer(response)
        writer.writerow(['S.No', 'Code', 'Pose Name', 'Category', 'Base Value', 'Status', 'Is Reference', 'Image File'])
        export_qs = Asana.objects.all().order_by('sno')
        if tab == 'published':
            export_qs = export_qs.filter(status=AsanaStatus.PUBLISHED, is_reference_record=False)
        elif tab == 'draft':
            export_qs = export_qs.filter(status=AsanaStatus.DRAFT)
        elif tab == 'archived':
            export_qs = export_qs.filter(status=AsanaStatus.ARCHIVED)
        
        for a in export_qs:
            writer.writerow([a.sno, a.code, a.display_name, a.display_category, a.base_value, a.status, a.is_reference_record, a.image_filename or ''])
        return response

    # Base Queryset
    asanas = Asana.objects.all()

    if not show_ref:
        asanas = asanas.filter(is_reference_record=False)

    # Filter by tab
    if tab == 'published':
        asanas = asanas.filter(status=AsanaStatus.PUBLISHED)
    elif tab == 'draft':
        asanas = asanas.filter(status=AsanaStatus.DRAFT)
    elif tab == 'archived':
        asanas = asanas.filter(status=AsanaStatus.ARCHIVED)

    if query:
        asanas = asanas.filter(
            Q(code__icontains=query) |
            Q(display_name__icontains=query) |
            Q(description__icontains=query)
        )

    if category:
        asanas = asanas.filter(display_category=category)

    if bv:
        try:
            bv_dec = Decimal(bv)
            asanas = asanas.filter(base_value=bv_dec)
        except Exception:
            pass

    # Sorting
    if sort_by == 'code':
        asanas = asanas.order_by('code')
    elif sort_by == 'name':
        asanas = asanas.order_by('display_name')
    elif sort_by == 'bv_asc':
        asanas = asanas.order_by('base_value', 'code')
    elif sort_by == 'bv_desc':
        asanas = asanas.order_by('-base_value', 'code')
    else:
        asanas = asanas.order_by('sno', 'id')

    # Real counts
    count_published = Asana.objects.filter(is_reference_record=False, status=AsanaStatus.PUBLISHED).count()
    count_draft = Asana.objects.filter(is_reference_record=False, status=AsanaStatus.DRAFT).count()
    count_archived = Asana.objects.filter(is_reference_record=False, status=AsanaStatus.ARCHIVED).count()
    count_ref = Asana.objects.filter(is_reference_record=True).count()

    # Distinct categories for filter
    categories = Asana.objects.exclude(is_reference_record=True).values_list('display_category', flat=True).distinct().order_by('display_category')
    
    # Distinct base values for filter
    base_values = Asana.objects.exclude(is_reference_record=True).values_list('base_value', flat=True).distinct().order_by('base_value')

    # Recent Audit Changes for Asanas
    recent_changes = AuditLog.objects.filter(resource_type='Asana').order_by('-timestamp')[:15]

    # Pagination
    per_page = 24 if view_mode == 'grid' else 30
    paginator = Paginator(asanas, per_page)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'catalogue/catalogue.html', {
        'page_obj': page_obj,
        'tab': tab,
        'view_mode': view_mode,
        'sort_by': sort_by,
        'count_published': count_published,
        'count_draft': count_draft,
        'count_archived': count_archived,
        'count_ref': count_ref,
        'categories': categories,
        'base_values': base_values,
        'selected_category': category,
        'query': query,
        'selected_bv': bv,
        'show_ref': show_ref,
        'total_poses': count_published + count_draft + count_archived,
        'recent_changes': recent_changes,
    })
