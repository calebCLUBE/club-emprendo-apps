# club_emprendo/urls.py

from django.contrib import admin
from django.urls import path, include
from django.shortcuts import redirect

from applications import admin_views

urlpatterns = [
    # Root -> admin index
    path("", lambda r: redirect("/admin/")),

    # ✅ Custom admin dashboard pages (MUST be BEFORE admin.site.urls)
    path("admin/apps/", admin_views.apps_list, name="admin_apps_list"),
    path("admin/apps/create-group/", admin_views.create_group, name="admin_create_group"),
    path("admin/apps/delete-group/<int:group_num>/", admin_views.delete_group, name="admin_delete_group"),

    # Database dashboards
    path("admin/database/", admin_views.database_home, name="admin_database"),
    path(
        "admin/database/form/<slug:form_slug>/",
        admin_views.database_form_detail,
        name="admin_database_form_detail",
    ),
    path(
        "admin/database/form/<slug:form_slug>/master.csv",
        admin_views.database_form_master_csv,
        name="admin_database_form_master_csv",
    ),
    path(
        "admin/database/submission/<int:app_id>/",
        admin_views.database_submission_detail,
        name="admin_database_submission_detail",
    ),
    path(
        "admin/database/export/<slug:form_slug>.csv",
        admin_views.export_form_csv,
        name="admin_export_form_csv",
    ),

    # 🔥 FILE DELETE ROUTES (must be BEFORE admin.site.urls)
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


    # 🚨 Django admin (greedy catch-all — MUST stay last)
    path("admin/", admin.site.urls),

    # Public routes
    path("", include("applications.urls")),
    path("builder/", include("builder.urls")),
]
