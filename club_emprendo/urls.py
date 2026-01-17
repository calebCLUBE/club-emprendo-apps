# club_emprendo/urls.py
from django.contrib import admin
from django.urls import path, include
from django.shortcuts import redirect

from applications import admin_views

urlpatterns = [
    path("", lambda r: redirect("/admin/")),

    # custom admin pages
    path("admin/apps/", admin_views.apps_list, name="admin_apps_list"),
    path("admin/apps/create-group/", admin_views.create_group, name="admin_create_group"),
    path("admin/apps/delete-group/<int:group_num>/", admin_views.delete_group, name="admin_delete_group"),
    path("admin/apps/toggle-form/<slug:form_slug>/", admin_views.toggle_form_open, name="admin_toggle_form_open"),
    path("admin/apps/toggle-accepting/<slug:form_slug>/", admin_views.toggle_form_accepting, name="admin_toggle_form_accepting"),
    path("admin/apps/send-a2-reminders/<slug:form_slug>/", admin_views.send_second_stage_reminders, name="admin_send_second_stage_reminders"),

    path("admin/database/", admin_views.database_home, name="admin_database"),
    path("admin/database/form/<slug:form_slug>/", admin_views.database_form_detail, name="admin_database_form_detail"),
    path("admin/database/form/<slug:form_slug>/master.csv", admin_views.database_form_master_csv, name="admin_database_form_master_csv"),
    path("admin/database/submission/<int:app_id>/", admin_views.database_submission_detail, name="admin_database_submission_detail"),
    path("admin/database/export/<slug:form_slug>.csv", admin_views.export_form_csv, name="admin_export_form_csv"),

    # ✅ ADD THIS
# club_emprendo/urls.py (add these)
    path("admin/grading/", admin_views.grading_home, name="admin_grading_home"),
    path("admin/grading/upload/<slug:form_slug>/", admin_views.grading_upload_csv, name="admin_grading_upload_csv"),
    path("admin/grading/grade/<slug:form_slug>/", admin_views.grade_form_batch, name="admin_grade_form_batch"),
    path("admin/grading/master/<slug:form_slug>/", admin_views.grading_master_csv, name="admin_grading_master_csv"),

    # django admin catch-all
    path("admin/", admin.site.urls),

    # public routes
    path("", include("applications.urls")),
    path("builder/", include("builder.urls")),
]
