import datetime
from decimal import Decimal
from django.db import models, transaction
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone
from .models import CDCorrectionGrant, GrantStatus
from scoring.models import Performance, PerformanceStatus, JudgeSubmission
from accounts.models import RoleChoices
from accounts.permissions import user_can_grant_correction

def grant_correction_access(user, performance_id, submission_id, reason, championship):
    """
    Transactional service to grant correction access:
    - Scopes performance and submission strictly to authorized championship.
    - Requires active CD role on championship (Admin without CD role denied).
    - Rejects if performance is CD_FINALIZED.
    - Rejects duplicate/stale grants (if OPEN grant already exists).
    - Requires non-empty documented reason.
    - Atomically creates CDCorrectionGrant and unlocks submission.
    """
    if not user_can_grant_correction(user, championship):
        raise PermissionDenied("Only an officially assigned Competition Director (CD) on this championship can grant correction access.")

    reason = (reason or '').strip()
    if not reason:
        raise ValidationError("A documented justification reason is required to grant correction access.")

    with transaction.atomic():
        perf = Performance.objects.select_for_update().filter(
            id=performance_id,
            event_entry__registration__championship=championship
        ).first()
        if not perf:
            raise ValidationError(f"Performance #{performance_id} not found in championship '{championship.name}'.")

        if perf.status == PerformanceStatus.CD_FINALIZED:
            raise ValidationError("Cannot grant correction on an already finalized performance.")

        sub = JudgeSubmission.objects.select_for_update().filter(
            id=submission_id,
            performance=perf
        ).first()
        if not sub:
            raise ValidationError(f"Submission #{submission_id} does not belong to Performance #{performance_id}.")

        # Check for duplicate / existing open grant
        existing_open = CDCorrectionGrant.objects.filter(
            judge_submission=sub,
            status=GrantStatus.OPEN
        ).exists()
        if existing_open:
            raise ValidationError(f"An open correction grant already exists for {sub.get_judge_role_display()}.")

        grant = CDCorrectionGrant.objects.create(
            performance=perf,
            judge_submission=sub,
            granted_by=user,
            reason=reason,
            status=GrantStatus.OPEN
        )

        sub.is_locked = False
        sub.is_draft = True
        sub.save(update_fields=['is_locked', 'is_draft'])

        return grant

def revoke_correction_access(user, grant_id, championship):
    """
    Transactional service to revoke open correction access:
    - Scopes grant strictly to authorized championship.
    - Requires active CD role on championship.
    - Rejects stale/non-open grants.
    - Atomically updates grant status to REVOKED and relocks submission.
    """
    if not user_can_grant_correction(user, championship):
        raise PermissionDenied("Only an officially assigned Competition Director (CD) on this championship can revoke correction access.")

    with transaction.atomic():
        grant = CDCorrectionGrant.objects.select_for_update().filter(
            id=grant_id,
            performance__event_entry__registration__championship=championship
        ).select_related('judge_submission', 'performance').first()

        if not grant:
            raise ValidationError(f"Correction grant #{grant_id} not found in championship '{championship.name}'.")

        if grant.status != GrantStatus.OPEN:
            raise ValidationError(f"Grant #{grant_id} is already '{grant.get_status_display()}' and cannot be revoked.")

        now = timezone.now()
        grant.status = GrantStatus.REVOKED
        grant.revoked_at = now
        grant.revoked_by = user
        grant.save(update_fields=['status', 'revoked_at', 'revoked_by'])

        sub = grant.judge_submission
        sub.is_locked = True
        sub.save(update_fields=['is_locked'])

        return grant

def resubmit_judge_correction(user, grant_id, championship):
    """
    Transactional service to resubmit corrected score sheet:
    - STRICT ENFORCEMENT: ONLY the original assigned judge may resubmit their own sheet!
      Admin/CD cannot impersonate the judge or mark another judge's sheet as resubmitted.
    - Scopes grant and submission strictly to authorized championship.
    - Rejects if grant is not OPEN.
    - Preserves immutable revision (increments version).
    - Automatically relocks the submission and marks grant RESUBMITTED.
    """
    with transaction.atomic():
        grant = CDCorrectionGrant.objects.select_for_update().filter(
            id=grant_id,
            performance__event_entry__registration__championship=championship
        ).select_related('judge_submission', 'performance').first()

        if not grant:
            raise ValidationError(f"Correction grant #{grant_id} not found in championship '{championship.name}'.")

        if grant.status != GrantStatus.OPEN:
            raise ValidationError(f"Correction grant #{grant_id} is not open for resubmission.")

        sub = grant.judge_submission

        # Security check: only the actual judge may resubmit their submission!
        if sub.judge_id != user.id:
            raise PermissionDenied(
                f"Unauthorized: Only the assigned judge ({sub.judge.full_name or sub.judge.email}) "
                f"can resubmit their corrected score sheet. CD/Admin cannot impersonate judge resubmissions."
            )

        now = timezone.now()

        # Update submission with immutable incremented version
        sub.version += 1
        sub.is_draft = False
        sub.is_submitted = True
        sub.is_locked = True
        sub.submitted_at = now

        # Recalculate raw and effective total from items
        items = sub.items.all()
        if items.exists():
            total = sum(item.effective_item_score for item in items)
            sub.raw_total = total
            sub.effective_total = total

        sub.save()

        # Mark grant as resubmitted
        grant.status = GrantStatus.RESUBMITTED
        grant.resubmitted_at = now
        grant.save(update_fields=['status', 'resubmitted_at'])

        # Recalculate performance with authoritative calculation engine
        from scoring.services import recalculate_performance_after_correction
        recalculate_performance_after_correction(grant.performance)

        return grant, sub

def finalize_performance_by_cd(user, performance_id, championship):
    """
    Transactional service for CD Finalization:
    - Requires active CD role on championship.
    - Scopes performance strictly to authorized championship.
    - Validates business rules before finalization:
      1. Rejects if any active/open correction grants exist for this performance.
      2. Rejects if any required judge submissions are missing or unsubmitted/in-draft.
      3. Rejects if performance is not in an admissible state.
    - Atomically sets CD_FINALIZED, stamps cd_finalized_by and cd_finalized_at.
    """
    if not user_can_grant_correction(user, championship):
        raise PermissionDenied("Only an officially assigned Competition Director (CD) on this championship can finalize performance results.")

    with transaction.atomic():
        perf = Performance.objects.select_for_update().filter(
            id=performance_id,
            event_entry__registration__championship=championship
        ).first()

        if not perf:
            raise ValidationError(f"Performance #{performance_id} not found in championship '{championship.name}'.")

        # 1. Check open correction grants
        open_grants_count = perf.correction_grants.filter(status=GrantStatus.OPEN).count()
        if open_grants_count > 0:
            raise ValidationError(f"Cannot finalize Performance for Chest {perf.chest_number}: {open_grants_count} correction grant(s) are still OPEN.")

        # 2. Check for incomplete or draft judge submissions
        submissions = perf.submissions.all()
        if not submissions.exists():
            raise ValidationError(f"Cannot finalize Performance for Chest {perf.chest_number}: No judge submissions recorded.")

        incomplete_submissions = submissions.filter(models.Q(is_draft=True) | models.Q(is_submitted=False) | models.Q(is_locked=False))
        if incomplete_submissions.exists():
            roles = [s.get_judge_role_display() for s in incomplete_submissions]
            raise ValidationError(f"Cannot finalize Performance for Chest {perf.chest_number}: Incomplete/unlocked submissions present for: {', '.join(roles)}.")

        now = timezone.now()
        perf.status = PerformanceStatus.CD_FINALIZED
        perf.cd_finalized_by = user
        perf.cd_finalized_at = now

        # Recalculate with authoritative scoring calculation engine
        from scoring.services import recalculate_performance_after_correction
        recalc_res = recalculate_performance_after_correction(perf)
        perf.final_score = recalc_res.get('final_total', perf.provisional_score or Decimal('0.000'))

        perf.save(update_fields=['status', 'cd_finalized_by', 'cd_finalized_at', 'final_score', 'calculation_data'])

        return perf
