from django.contrib import admin
from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from django.shortcuts import redirect

import accounts.views as accounts_views
import championships.views as champ_views
import catalogue.views as cat_views
import athletes.views as athlete_views
import judges.views as judge_views
import registrations.views as reg_views
import competition.views as comp_views
import scoring.panel_views as panel_scoring_views
import corrections.views as corr_views
import results.views as result_views
import certificates.views as cert_views
import reports.views as report_views
import audit.views as audit_views

def root_router(request):
    if request.user.is_authenticated:
        from accounts.permissions import get_user_landing_url
        return redirect(get_user_landing_url(request))
    return redirect('login')

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', root_router, name='root'),
    
    # 01_Login & Dashboard
    path('accounts/login/', accounts_views.login_view, name='login'),
    path('accounts/logout/', accounts_views.logout_view, name='logout'),
    path('accounts/role-select/', accounts_views.role_select_view, name='role_select'),
    path('accounts/no-role/', accounts_views.no_role_assigned, name='no_role_assigned'),
    path('dashboard/', accounts_views.admin_dashboard, name='admin_dashboard'),
    path('cd/dashboard/', comp_views.cd_dashboard, name='cd_dashboard'),
    path('accounts/users/', accounts_views.users_and_permissions, name='users_and_permissions'),
    path('accounts/users-and-permissions/', accounts_views.users_and_permissions, name='users_and_permissions_dash'),
    path('registers/', accounts_views.registers_view, name='registers_view'),
    path('registers/attendance/', accounts_views.attendance_and_duties, name='attendance_and_duties'),

    # Championships (Multi-Championship Architecture)
    path('championships/', champ_views.championship_list, name='championship_list'),
    path('championships/create/', champ_views.championship_create, name='championship_create'),
    path('championships/switch/<int:champ_id>/', champ_views.championship_switch, name='championship_switch'),
    path('championships/<int:champ_id>/setup/', champ_views.championship_setup, name='championship_setup'),
    path('championships/<int:champ_id>/branding/', champ_views.championship_branding, name='championship_branding'),

    # Catalogue
    path('catalogue/', cat_views.catalogue_view, name='catalogue_view'),

    # Athlete Registration Flow (5 Steps) & ID Card
    path('athletes/', athlete_views.athletes_list, name='athletes_list'),
    path('athletes/directory/', athlete_views.athletes_list, name='athletes_directory'),
    path('athletes/entries/', athlete_views.athlete_entries_list, name='athlete_entries_list'),
    path('register/athlete/step-1/', athlete_views.athlete_register_step1, name='athlete_register_step1'),
    path('register/athlete/step-2/', athlete_views.athlete_register_step2, name='athlete_register_step2'),
    path('register/athlete/step-3/', athlete_views.athlete_register_step3, name='athlete_register_step3'),
    path('register/athlete/step-4/', athlete_views.athlete_register_step4, name='athlete_register_step4'),
    path('register/athlete/step-5/', athlete_views.athlete_register_step5, name='athlete_register_step5'),
    path('athletes/id-card/<str:athlete_id>/', athlete_views.athlete_id_card, name='athlete_id_card'),
    path('athletes/id-cards/batch/', athlete_views.athlete_id_cards_batch_print, name='athlete_id_cards_batch_print'),
    path('athletes/id-cards/batch/print/', athlete_views.athlete_id_cards_batch_print, name='athlete_id_cards_batch'),
    path('register/athlete/returning/', athlete_views.athlete_returning, name='athlete_returning'),

    # Universal Judges
    path('judges/', judge_views.judge_approval_queue, name='judge_list'),
    path('judges/apply/', judge_views.universal_judge_register, name='universal_judge_register'),
    path('judges/portal/', judge_views.judge_portal, name='judge_portal'),
    path('judges/<int:judge_id>/', judge_views.judge_profile, name='judge_profile'),
    path('judges/<int:judge_id>/id-card/', judge_views.judge_id_card, name='judge_id_card'),
    path('judges/id-cards/batch/', judge_views.judge_id_cards_batch_print, name='judge_id_cards_batch_print'),
    path('judges/id-cards/batch/print/', judge_views.judge_id_cards_batch_print, name='judge_id_cards_batch'),
    path('judges/approval-queue/', judge_views.judge_approval_queue, name='judge_approval_queue'),
    path('judges/credentials/', judge_views.judge_credentials_admin, name='judge_credentials_admin'),
    path('judges/credentials/<int:judge_id>/reveal/', judge_views.judge_credential_reveal, name='judge_credential_reveal'),
    path('judges/credentials/<int:judge_id>/reset/', judge_views.judge_credential_reset, name='judge_credential_reset'),
    path('judges/credentials/<int:judge_id>/delivery/', judge_views.judge_credential_delivery, name='judge_credential_delivery'),
    path('judges/set-password/', judge_views.judge_change_password, name='judge_change_password'),

    # District & Venue
    path('district/approvals/', reg_views.district_approvals, name='district_approvals'),
    path('venue/verification/', reg_views.venue_verification, name='venue_verification'),

    # Competition Operations
    path('competition/draw/', comp_views.district_draw, name='district_draw'),
    path('competition/chest-numbers/', comp_views.chest_number_manager, name='chest_number_manager'),
    path('competition/master-roll/', comp_views.master_roll, name='master_roll'),
    path('competition/panels/', comp_views.panel_assignments, name='panel_assignments'),
    path('competition/stage/', comp_views.stage_manager, name='stage_manager'),
    path('competition/stage-manager/', comp_views.stage_manager, name='stage_manager_alt'),
    path('display/second-screen/', comp_views.second_screen, name='second_screen'),

    path('scoring/reopened-desk/', panel_scoring_views.score_desk, name='reopened_score_desk'),
    # Scoring Desks
    path('scoring/traditional-d/', panel_scoring_views.score_desk, name='scoring_traditional_d'),
    path('scoring/traditional-d-final/', panel_scoring_views.score_desk, name='scoring_traditional_d_final'),
    path('scoring/artistic-single-d/', panel_scoring_views.score_desk, name='scoring_artistic_single_d'),
    path('scoring/artistic-pair-d/', panel_scoring_views.score_desk, name='scoring_artistic_pair_d'),
    path('scoring/rhythmic-pair-d/', panel_scoring_views.score_desk, name='scoring_rhythmic_pair_d'),
    path('scoring/traditional-time/', panel_scoring_views.score_desk, name='scoring_traditional_time'),
    path('scoring/music-time/', panel_scoring_views.score_desk, name='scoring_music_time'),
    path('scoring/artistry/', panel_scoring_views.score_desk, name='scoring_artistry'),
    path('scoring/evaluator/', panel_scoring_views.score_desk, name='scoring_evaluator'),
    path('scoring/chief-judge/', panel_scoring_views.chief_dashboard, name='scoring_chief_judge'),

    # CD Corrections & Jury & Appeals
    path('cd/corrections/', corr_views.cd_corrections, name='cd_corrections'),
    path('jury/', corr_views.jury_view, name='jury_view'),
    path('appeals/', corr_views.appeal_list, name='appeal_list'),
    path('appeals/create/', corr_views.appeal_create, name='appeal_create'),
    path('appeals/<int:appeal_id>/', corr_views.appeal_detail, name='appeal_detail'),

    # Live Audience & Results
    path('display/live/', result_views.public_live_display, name='public_live_display'),
    path('results/scoreboard/', result_views.scoreboard_view, name='scoreboard_view'),

    # Certificates
    path('certificates/templates/', cert_views.certificate_templates, name='certificate_templates'),
    path('certificates/templates/<int:template_id>/edit/', cert_views.certificate_template_edit, name='certificate_template_edit'),
    path('certificates/generate/', cert_views.certificate_generate, name='certificate_generate'),
    path('certificates/verify/<uuid:uuid>/', cert_views.certificate_verify_qr, name='certificate_verify_qr'),

    # Reports
    path('reports/statistics/', report_views.report_statistics, name='report_statistics'),
    path('reports/trophies/', report_views.report_trophies, name='report_trophies'),
    path('reports/ceremonies/', report_views.report_ceremonies, name='report_ceremonies'),
    path('reports/championship/', report_views.report_complete, name='report_complete'),
    path('reports/complete/', report_views.report_complete, name='report_complete_alt'),

    # Settings & Audit
    path('settings/', audit_views.system_settings_view, name='system_settings'),
    path('settings/association/', audit_views.system_settings_view, name='association_settings'),
    path('audit/', audit_views.audit_log_list, name='audit_list'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
