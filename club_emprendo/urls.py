# club_emprendo/urls.py
from django.contrib import admin
from django.urls import path, include
from django.shortcuts import redirect

from applications import admin_views
from applications import admin_dashboard_views
from applications import admin_task_views
from applications import admin_profiles_views

urlpatterns = [
    # ============================
    # CUSTOM ADMIN PAGES
    # ============================
    path("admin/apps/", admin_views.apps_list, name="admin_apps_list"),
    path(
        "admin/apps/dashboard/",
        admin_dashboard_views.applications_dashboard,
        name="admin_applications_dashboard",
    ),
    path("admin/apps/create-group/", admin_views.create_group, name="admin_create_group"),
    path("admin/apps/update-group/<int:group_num>/", admin_views.update_group_dates, name="admin_update_group"),
    path("admin/apps/delete-group/<int:group_num>/", admin_views.delete_group, name="admin_delete_group"),
    path("admin/apps/toggle-form/<slug:form_slug>/", admin_views.toggle_form_open, name="admin_toggle_form_open"),
    path("admin/apps/toggle-accepting/<slug:form_slug>/", admin_views.toggle_form_accepting, name="admin_toggle_form_accepting"),
    path("admin/apps/send-a2-reminders/<slug:form_slug>/", admin_views.send_second_stage_reminders, name="admin_send_second_stage_reminders"),

    # ============================
    # TASK MANAGER
    # ============================
    path("admin/task-manager/", admin_task_views.task_manager_home, name="admin_task_manager_home"),
    path("admin/task-manager/my-tasks/", admin_task_views.task_manager_my_tasks, name="admin_task_manager_my_tasks"),
    path("admin/task-manager/overview/", admin_task_views.task_manager_overview, name="admin_task_manager_overview"),
    path("admin/task-manager/task/<int:task_id>/", admin_task_views.task_manager_task_overview, name="admin_task_manager_task_overview"),
    path("admin/task-manager/assign/", admin_task_views.task_manager_assign, name="admin_task_manager_assign"),
    path("admin/task-manager/edit/<int:task_id>/", admin_task_views.task_manager_edit, name="admin_task_manager_edit"),
    path("admin/task-manager/website-revisions/", admin_task_views.task_manager_website_revisions, name="admin_task_manager_website_revisions"),
    path("admin/task-manager/user/<int:user_id>/", admin_task_views.task_manager_user_tasks, name="admin_task_manager_user_tasks"),

    # ============================
    # PROFILES
    # ============================
    path("admin/profiles/", admin_profiles_views.profiles_list, name="admin_profiles_list"),
    path("admin/profiles/sheet/", admin_profiles_views.profiles_sheet, name="admin_profiles_sheet"),
    path(
        "admin/profiles/participants/",
        admin_profiles_views.profiles_participants,
        name="admin_profiles_participants",
    ),
    path(
        "admin/profiles/participants/<int:group_num>/download/",
        admin_profiles_views.profiles_participants_download,
        name="admin_profiles_participants_download",
    ),
    path(
        "admin/profiles/participants/<int:group_num>/<str:track>/",
        admin_profiles_views.profiles_participants_track_sheet,
        name="admin_profiles_participants_track_sheet",
    ),
    path("admin/profiles/<str:identity_key>/", admin_profiles_views.profile_detail, name="admin_profile_detail"),

    # Dropbox Sign webhook (contract signed auto-marking)
    path(
        "webhooks/dropbox-sign/",
        admin_profiles_views.dropbox_sign_webhook,
        name="dropbox_sign_webhook",
    ),

    # ============================
    # DATABASE
    # ============================
    path("admin/database/", admin_views.database_home, name="admin_database"),
    path("admin/database/form/<slug:form_slug>/", admin_views.database_form_detail, name="admin_database_form_detail"),
    path("admin/database/form/<slug:form_slug>/sheet/", admin_views.database_form_sheet, name="admin_database_form_sheet"),
    path("admin/database/form/<slug:form_slug>/master.csv", admin_views.database_form_master_csv, name="admin_database_form_master_csv"),
    path("admin/database/type/<slug:app_type>/", admin_views.database_type_detail, name="admin_database_type_detail"),
    path("admin/database/type/<slug:app_type>/sheet/", admin_views.database_type_sheet, name="admin_database_type_sheet"),
    path("admin/database/type/<slug:app_type>/master.csv", admin_views.database_type_master_csv, name="admin_database_type_master_csv"),
    path("admin/database/combined/<slug:track>/", admin_views.database_track_detail, name="admin_database_track_detail"),
    path("admin/database/combined/<slug:track>/sheet/", admin_views.database_track_sheet, name="admin_database_track_sheet"),
    path("admin/database/combined/<slug:track>/master.csv", admin_views.database_track_master_csv, name="admin_database_track_master_csv"),
    path("admin/database/submission/<int:app_id>/", admin_views.database_submission_detail, name="admin_database_submission_detail"),
    path("admin/database/export/<slug:form_slug>.csv", admin_views.export_form_csv, name="admin_export_form_csv"),

    # ✅ Delete submission + file actions
    path("admin/database/delete-submissions/", admin_views.bulk_delete_submissions, name="admin_bulk_delete_submissions"),
    path("admin/database/copy-application/", admin_views.database_copy_application, name="admin_database_copy_application"),
    path(
        "admin/database/create-assigned-group/",
        admin_views.database_create_assigned_group,
        name="admin_database_create_assigned_group",
    ),
    path("admin/database/sync-drive/", admin_views.database_sync_drive, name="admin_database_sync_drive"),
    path("admin/database/delete-submission/<int:app_id>/", admin_views.delete_submission, name="admin_delete_submission"),
    path("admin/database/delete-graded-file/<int:graded_file_id>/", admin_views.delete_graded_file, name="admin_delete_graded_file"),
    path("admin/database/delete-answer-file/<int:answer_id>/", admin_views.delete_answer_file_value, name="admin_delete_answer_file_value"),
    path("admin/database/delete-application-files/<int:app_id>/", admin_views.delete_application_files, name="admin_delete_application_files"),

    # ============================
    # EMPAREJAMIENTO
    # ============================
    path("admin/emparejamiento/", admin_views.emparejamiento_home, name="admin_emparejamiento_home"),
    path("admin/emparejamiento/pair/<int:group_num>/", admin_views.run_emparejamiento, name="admin_emparejamiento_run"),

    # ============================
    # GRADING
    # ============================
    path("admin/grading/", admin_views.grading_home, name="admin_grading_home"),
    path("admin/grading/upload/<slug:form_slug>/", admin_views.grading_upload_csv, name="admin_grading_upload_csv"),
    path("admin/grading/grade/<slug:form_slug>/", admin_views.grade_form_batch, name="admin_grade_form_batch"),
    path("admin/grading/master/<slug:form_slug>/", admin_views.grading_master_csv, name="admin_grading_master_csv"),
    path("admin/grading/grade-one/e/<int:app_id>/", admin_views.grade_one_emprendedora, name="admin_grade_one_emprendedora"),
    path("admin/grading/job/<int:job_id>/", admin_views.grading_job_status, name="admin_grading_job_status"),
    path("admin/grading/start-job/<slug:form_slug>/", admin_views.start_grading_job, name="admin_start_grading_job"),
    path("admin/grading/sheet/<slug:form_slug>/", admin_views.grading_live_sheet, name="admin_grading_live_sheet"),
    path("admin/grading/download/<int:graded_file_id>/", admin_views.download_graded_csv, name="admin_grading_download_csv"),
    path("admin/grading/download-excel/<int:graded_file_id>/", admin_views.download_graded_excel, name="admin_grading_download_excel"),

    # ============================
    # PUBLIC ROUTES
    # ============================
    path("", include("applications.urls")),
    path("builder/", include("builder.urls")),

    # ============================
    # DJANGO ADMIN CATCH-ALL
    # ============================
    path("admin/", admin.site.urls),

    # ✅ If you want home "/" to redirect to admin, do it LAST
    path("", lambda r: redirect("/admin/")),
]
