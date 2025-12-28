# club_emprendo/urls.py
from django.contrib import admin
from django.urls import path, include
from django.shortcuts import redirect
from applications import admin_views

urlpatterns = [
    path("", lambda r: redirect("/admin/")),

    # Custom admin dashboard routes
    path("admin/apps/", admin_views.apps_list, name="admin_apps_list"),
    path("admin/apps/forms/<int:form_id>/", admin_views.app_form_detail, name="admin_app_form_detail"),
    path("admin/submissions/", admin_views.submissions_list, name="admin_submissions_list"),
    path("admin/submissions/<int:app_id>/", admin_views.submission_detail, name="admin_submission_detail"),
    path("admin/database/", admin_views.database_home, name="admin_database"),
    path("accounts/", include("django.contrib.auth.urls")),
    # Django admin
    path("admin/", admin.site.urls),

    # Public site routes
    path("", include("applications.urls")),
    path("builder/", include("builder.urls")),
    path("accounts/", include("django.contrib.auth.urls")),
]
