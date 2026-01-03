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
    path("admin/database/export/<slug:form_slug>.csv", admin_views.export_form_csv, name="admin_export_form_csv"),

    # Django admin (this includes a greedy catch-all)
    path("admin/", admin.site.urls),

    # Public application routes
    path("", include("applications.urls")),
    path("builder/", include("builder.urls")),
]
