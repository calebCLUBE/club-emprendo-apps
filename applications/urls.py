# applications/urls.py
from django.urls import path
from applications import admin_views

from . import views
from . import survey_views

urlpatterns = [
    # ---------- PUBLIC FIRST-STAGE FORMS ----------
    path("apply/emprendedora/", views.apply_emprendedora_first, name="apply_emprendedora_first"),
    path("apply/mentora/", views.apply_mentora_first, name="apply_mentora_first"),

    # ---------- SECOND-STAGE (TOKEN REQUIRED) ----------
    path("apply/emprendedora/continue/<str:token>/", views.apply_emprendedora_second, name="apply_emprendedora_second"),
    path("apply/mentora/continue/<str:token>/", views.apply_mentora_second, name="apply_mentora_second"),

    # ---------- PREVIEW (NO TOKEN) ----------
    path("apply/emprendedora/preview/", views.apply_emprendedora_second_preview, name="preview_emprendedora_second"),
    path("apply/mentora/preview/", views.apply_mentora_second_preview, name="preview_mentora_second"),

    # ---------- GROUP/SLUG ROUTE ----------
    path("apply/<slug:form_slug>/", views.apply_by_slug, name="apply_by_slug"),

    # ---------- THANKS ----------
    path("thanks/", views.application_thanks, name="application_thanks"),

    # ---------- SURVEYS ----------
    path("surveys/", survey_views.surveys_index, name="surveys_index"),
    path("survey/<slug:form_slug>/", survey_views.survey_by_slug, name="survey_by_slug"),

    # ============================
    # ADMIN: APPS DASHBOARD ACTIONS
    # ============================
    path(
        "admin/apps/send-a2-reminders/<slug:form_slug>/",
        admin_views.send_second_stage_reminders,
        name="admin_send_second_stage_reminders",
    ),
    path(
        "admin/apps/toggle-accepting/<slug:form_slug>/",
        admin_views.toggle_form_accepting,
        name="admin_toggle_form_accepting",
    ),
    path(
        "admin/apps/toggle-form/<slug:form_slug>/",
        admin_views.toggle_form_open,
        name="admin_toggle_form_open",
    ),

    # ============================
    # ADMIN: DATABASE ACTIONS
    # ============================
    path(
        "admin/database/delete-answer-file/<int:answer_id>/",
        admin_views.delete_answer_file_value,
        name="admin_delete_answer_file_value",
    ),
    path(
        "admin/database/delete-application-files/<int:app_id>/",
        admin_views.delete_application_files,
        name="admin_delete_application_files",
    ),
    path(
    "admin/database/delete-submission/<int:app_id>/",
    admin_views.delete_submission,
    name="admin_delete_submission",
),


    # ============================
    # ADMIN: GRADING (BATCH)
    # ============================
    path("admin/grading/", admin_views.grading_home, name="admin_grading_home"),

    # batch grade an entire form (ex: G6_E_A2)
    path(
        "admin/grading/grade-form/<slug:form_slug>/",
        admin_views.grade_form_batch,
        name="admin_grade_form_batch",
    ),

    # legacy: grade a single submission (keeps older code working)
    path(
        "admin/grading/grade/<int:app_id>/",
        admin_views.grade_application,
        name="admin_grade_application",
    ),

    # upload CSV into Applications/Answers for a given form
    path(
        "admin/grading/upload-csv/<slug:form_slug>/",
        admin_views.grading_upload_csv,
        name="admin_grading_upload_csv",
    ),

    # download the master CSV for a form (optionally including grade columns)
    path(
        "admin/grading/master-csv/<slug:form_slug>/",
        admin_views.grading_master_csv,
        name="admin_grading_master_csv",
    ),
    path(
    "admin/grading/upload-test/",
    admin_views.grading_upload_test_csv,
    name="admin_grading_upload_test_csv",
    ),


]
