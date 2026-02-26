# club_emprendo/urls.py
from django.contrib import admin
from django.urls import path, include
from django.shortcuts import redirect

from applications import admin_views

urlpatterns = [
    # ============================
    # CUSTOM ADMIN PAGES
    # ============================
    path("admin/apps/", admin_views.apps_list, name="admin_apps_list"),
    path("admin/apps/create-group/", admin_views.create_group, name="admin_create_group"),
    path("admin/apps/update-group/<int:group_num>/", admin_views.update_group_dates, name="admin_update_group"),
    path("admin/apps/delete-group/<int:group_num>/", admin_views.delete_group, name="admin_delete_group"),
    path("admin/apps/toggle-form/<slug:form_slug>/", admin_views.toggle_form_open, name="admin_toggle_form_open"),
    path("admin/apps/toggle-accepting/<slug:form_slug>/", admin_views.toggle_form_accepting, name="admin_toggle_form_accepting"),
    path("admin/apps/send-a2-reminders/<slug:form_slug>/", admin_views.send_second_stage_reminders, name="admin_send_second_stage_reminders"),

    # ============================
    # DATABASE
    # ============================
    path("admin/database/", admin_views.database_home, name="admin_database"),
    path("admin/database/form/<slug:form_slug>/", admin_views.database_form_detail, name="admin_database_form_detail"),
    path("admin/database/form/<slug:form_slug>/master.csv", admin_views.database_form_master_csv, name="admin_database_form_master_csv"),
    path("admin/database/type/<slug:app_type>/", admin_views.database_type_detail, name="admin_database_type_detail"),
    path("admin/database/type/<slug:app_type>/master.csv", admin_views.database_type_master_csv, name="admin_database_type_master_csv"),
    path("admin/database/submission/<int:app_id>/", admin_views.database_submission_detail, name="admin_database_submission_detail"),
    path("admin/database/export/<slug:form_slug>.csv", admin_views.export_form_csv, name="admin_export_form_csv"),

    # ✅ Delete submission + file actions
    path("admin/database/delete-submission/<int:app_id>/", admin_views.delete_submission, name="admin_delete_submission"),
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
    path("admin/grading/download/<int:graded_file_id>/", admin_views.download_graded_csv, name="admin_grading_download_csv"),

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
