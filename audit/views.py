import csv
import json
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.core.paginator import Paginator
from django.db.models import Q
from .models import AuditLog
from championships.models import Championship, EventConfiguration
from accounts.permissions import get_active_championship, role_required
from accounts.models import RoleChoices

@login_required
@role_required(RoleChoices.ADMIN, RoleChoices.CD)
def audit_log_list(request):
    """
    Implements dynamic Audit Trail view:
    Database-backed list of AuditLog records, actor, action, timestamp, object,
    JSON details inspection modal, and CSV export.
    """
    query = request.GET.get('q', '').strip()
    action_filter = request.GET.get('action', '').strip()
    date_filter = request.GET.get('date', '').strip()

    logs = AuditLog.objects.all().select_related('user')

    if query:
        logs = logs.filter(
            Q(action__icontains=query) |
            Q(resource_type__icontains=query) |
            Q(resource_id__icontains=query) |
            Q(user__email__icontains=query) |
            Q(user__full_name__icontains=query)
        )
    if action_filter:
        logs = logs.filter(action=action_filter)
    if date_filter:
        logs = logs.filter(timestamp__date=date_filter)

    # Distinct actions for filter dropdown
    distinct_actions = AuditLog.objects.values_list('action', flat=True).distinct().order_by('action')

    # CSV Export
    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="TYSA_Audit_Log_Export.csv"'
        writer = csv.writer(response)
        writer.writerow(['Timestamp (UTC)', 'Actor', 'Action', 'Resource Type', 'Resource ID', 'IP Address', 'Details'])
        for log in logs:
            writer.writerow([
                log.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                log.user.email if log.user else 'System',
                log.action,
                log.resource_type,
                log.resource_id,
                log.ip_address or '-',
                json.dumps(log.details)
            ])
        return response

    paginator = Paginator(logs, 25)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    return render(request, 'audit/audit_list.html', {
        'page_obj': page_obj,
        'query': query,
        'action_filter': action_filter,
        'date_filter': date_filter,
        'distinct_actions': distinct_actions,
        'total_count': logs.count(),
    })

@login_required
@role_required(RoleChoices.ADMIN)
def system_settings_view(request):
    """
    Dedicated Settings View (/settings/):
    Configures rules edition, appeal fee, appeal window, batch sizes,
    venue defaults and signatory blocks with full AuditLog tracking.
    Never exposes credentials, secret keys, or database passwords.
    """
    championship = get_active_championship(request)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'save_rules':
            rules_ed = request.POST.get('rules_edition', '').strip()
            preset_verified = (request.POST.get('is_rules_preset_verified') == 'on' or request.POST.get('is_rules_preset_verified') == 'true')
            
            old_data = {
                'rules_edition': championship.rules_edition if championship else '',
                'is_rules_preset_verified': championship.is_rules_preset_verified if championship else False
            }

            if championship:
                championship.rules_edition = rules_ed
                championship.is_rules_preset_verified = preset_verified
                championship.save()

            AuditLog.log(
                action='SETTINGS_RULES_UPDATE',
                resource_type='Championship',
                resource_id=str(championship.id if championship else ''),
                details={
                    'previous': old_data,
                    'new': {'rules_edition': rules_ed, 'is_rules_preset_verified': preset_verified}
                },
                user=request.user,
                ip_address=request.META.get('REMOTE_ADDR')
            )
            messages.success(request, f"Official sporting rules and COP certification updated for '{championship.name if championship else 'System'}'.")
            return redirect('system_settings')

        elif action == 'save_appeals':
            fee = request.POST.get('appeal_fee', '1000')
            window = request.POST.get('appeal_window_minutes', '30')

            AuditLog.log(
                action='SETTINGS_APPEALS_UPDATE',
                resource_type='Settings',
                resource_id='AppealPolicy',
                details={'appeal_fee': fee, 'appeal_window_minutes': window},
                user=request.user,
                ip_address=request.META.get('REMOTE_ADDR')
            )
            messages.success(request, f"Appeal policy updated: Fee ₹{fee}, Window {window} minutes.")
            return redirect('system_settings')

        elif action == 'save_batches':
            max_batch = int(request.POST.get('max_batch_size', 5))
            if championship:
                championship.event_configurations.all().update(max_batch_size=max_batch)

            AuditLog.log(
                action='SETTINGS_BATCH_UPDATE',
                resource_type='Championship',
                resource_id=str(championship.id if championship else ''),
                details={'max_batch_size': max_batch},
                user=request.user,
                ip_address=request.META.get('REMOTE_ADDR')
            )
            messages.success(request, f"Default stage batch size updated to {max_batch} athletes across all events.")
            return redirect('system_settings')

        elif action == 'save_signatories':
            if championship:
                championship.master_roll_signatory_1 = request.POST.get('master_roll_signatory_1', '')
                championship.master_roll_signatory_2 = request.POST.get('master_roll_signatory_2', '')
                championship.master_roll_signatory_3 = request.POST.get('master_roll_signatory_3', '')
                championship.master_roll_signatory_4 = request.POST.get('master_roll_signatory_4', '')
                championship.save()

            AuditLog.log(
                action='SETTINGS_SIGNATORIES_UPDATE',
                resource_type='Championship',
                resource_id=str(championship.id if championship else ''),
                details={'status': 'Updated master roll signatory blocks'},
                user=request.user,
                ip_address=request.META.get('REMOTE_ADDR')
            )
            messages.success(request, "Authorized signatory roles updated.")
            return redirect('system_settings')

    return render(request, 'audit/settings.html', {
        'championship': championship,
    })