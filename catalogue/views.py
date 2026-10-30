from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from .models import Asana

@login_required
def catalogue_view(request):
    """
    Implements ASANA.JPG:
    Admin catalogue management, search, category filter, BV filter, grid/list toggle,
    add/edit pose, publication control.
    """
    query = request.GET.get('q', '').strip()
    category = request.GET.get('category', '').strip()
    bv = request.GET.get('bv', '').strip()
    show_ref = request.GET.get('show_ref', 'false') == 'true'

    asanas = Asana.objects.all()

    if not show_ref:
        asanas = asanas.filter(is_reference_record=False)
    
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

    # Unique categories for filter dropdown
    categories = Asana.objects.values_list('display_category', flat=True).distinct().order_by('display_category')
    
    # Pagination
    paginator = Paginator(asanas, 24)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add_asana':
            code = request.POST.get('code').strip().upper()
            raw_type = request.POST.get('category').strip()
            display_category = Asana.normalize_category(raw_type)
            display_name = request.POST.get('display_name').strip()
            description = request.POST.get('description').strip()
            base_value = Decimal(request.POST.get('base_value', '0.80'))
            
            last_asana = Asana.objects.order_by('-source_id').first()
            next_id = (last_asana.source_id + 1) if last_asana else 1

            Asana.objects.create(
                source_id=next_id,
                sno=next_id,
                code=code,
                raw_type=raw_type,
                display_category=display_category,
                display_name=display_name,
                description=description,
                base_value=base_value,
                is_published=True,
                is_reference_record=False
            )
            messages.success(request, f"New pose {code} - {display_name} added to published catalogue.")
            return redirect('catalogue_view')

    return render(request, 'catalogue/catalogue.html', {
        'page_obj': page_obj,
        'categories': categories,
        'selected_category': category,
        'query': query,
        'selected_bv': bv,
        'show_ref': show_ref,
        'total_poses': Asana.objects.filter(is_reference_record=False).count(),
        'total_ref': Asana.objects.filter(is_reference_record=True).count(),
    })
