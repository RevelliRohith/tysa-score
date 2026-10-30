from decimal import Decimal
import datetime
from django.db import transaction
from django.core.exceptions import PermissionDenied, ValidationError
from championships.models import Championship, DistrictQuota
from registrations.models import ChampionshipRegistration, RegistrationStatus
from scoring.models import Performance, PerformanceStatus, JudgeSubmission
from corrections.models import CDCorrectionGrant, GrantStatus
from certificates.models import CertificateTemplate, IssuedCertificate, CertificateSequence
from certificates.views import _get_next_cert_number


class ChampionshipService:
    @staticmethod
    def approve_district_registration(registration_id: int, user) -> bool:
        """
        Production service for district registration approval with atomic quota check.
        """
        with transaction.atomic():
            reg = ChampionshipRegistration.objects.select_for_update().get(id=registration_id)
            district_name = reg.athlete.district
            quota = DistrictQuota.objects.select_for_update().get(
                championship=reg.championship,
                district_name=district_name
            )

            if quota.quota_used >= quota.quota_limit:
                return False

            quota.quota_used += 1
            quota.save(update_fields=['quota_used'])

            reg.status = RegistrationStatus.DISTRICT_APPROVED
            reg.approved_by = user
            reg.approved_at = datetime.datetime.now()
            reg.save(update_fields=['status', 'approved_by', 'approved_at'])
            return True

    @staticmethod
    def submit_judge_score(performance_id: int, judge_user, judge_role: str, raw_total: Decimal) -> JudgeSubmission:
        """
        Production service for submitting and locking an official judge score.
        """
        with transaction.atomic():
            perf = Performance.objects.select_for_update().get(id=performance_id)
            sub, created = JudgeSubmission.objects.get_or_create(
                performance=perf,
                judge=judge_user,
                judge_role=judge_role,
                version=1,
                defaults={
                    'raw_total': raw_total,
                    'effective_total': raw_total,
                    'is_locked': True,
                    'is_submitted': True,
                    'is_draft': False,
                    'submitted_at': datetime.datetime.now()
                }
            )
            return sub

    @staticmethod
    def grant_cd_correction(performance_id: int, submission_id: int, cd_user, reason: str) -> CDCorrectionGrant:
        """
        Production service for granting CD correction access to a single judge submission.
        """
        with transaction.atomic():
            perf = Performance.objects.select_for_update().get(id=performance_id)
            sub = JudgeSubmission.objects.select_for_update().get(id=submission_id, performance=perf)

            # Ensure any existing open grant is resolved
            CDCorrectionGrant.objects.filter(judge_submission=sub, status=GrantStatus.OPEN).update(
                status=GrantStatus.REVOKED,
                revoked_at=datetime.datetime.now(),
                revoked_by=cd_user
            )

            grant = CDCorrectionGrant.objects.create(
                performance=perf,
                judge_submission=sub,
                granted_by=cd_user,
                reason=reason,
                status=GrantStatus.OPEN
            )
            sub.is_locked = False
            sub.is_draft = True
            sub.save(update_fields=['is_locked', 'is_draft'])
            return grant

    @staticmethod
    def resubmit_cd_correction(grant_id: int, judge_user, revised_total: Decimal) -> JudgeSubmission:
        """
        Production service for resubmitting a corrected judge sheet.
        Validates judge ownership, increments submission version, and relocks sheet.
        """
        with transaction.atomic():
            grant = CDCorrectionGrant.objects.select_for_update().get(id=grant_id, status=GrantStatus.OPEN)
            sub = grant.judge_submission

            if sub.judge != judge_user:
                raise PermissionDenied("Only the original assigned judge can resubmit this submission.")

            sub.raw_total = revised_total
            sub.effective_total = revised_total
            sub.version += 1
            sub.is_locked = True
            sub.is_draft = False
            sub.is_submitted = True
            sub.submitted_at = datetime.datetime.now()
            sub.save(update_fields=['raw_total', 'effective_total', 'version', 'is_locked', 'is_draft', 'is_submitted', 'submitted_at'])

            grant.status = GrantStatus.RESUBMITTED
            grant.resubmitted_at = datetime.datetime.now()
            grant.save(update_fields=['status', 'resubmitted_at'])
            return sub

    @staticmethod
    def issue_certificate_record(template_id: int, championship_id: int, recipient_name: str, prefix: str = 'MERIT', issuer_user = None) -> IssuedCertificate:
        """
        Production service for allocating and creating an atomic collision-free certificate.
        """
        with transaction.atomic():
            champ = Championship.objects.get(id=championship_id)
            template = CertificateTemplate.objects.get(id=template_id)
            cert_no = _get_next_cert_number(champ, prefix=prefix)
            cert = IssuedCertificate.objects.create(
                template=template,
                championship=champ,
                certificate_number=cert_no,
                recipient_type='ATHLETE',
                recipient_name=recipient_name,
                issued_by=issuer_user
            )
            return cert
