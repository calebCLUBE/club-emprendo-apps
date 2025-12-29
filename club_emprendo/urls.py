# club_emprendo/urls.py
from django.contrib import admin
from django.urls import path, include
from django.shortcuts import redirect

from applications import admin_views

urlpatterns = [
    # send root to admin dashboard
    path("", lambda r: redirect("/admin/")),

    # custom admin dashboard pages (MUST be before admin.site.urls)
    path("admin/apps/", admin_views.apps_list, name="admin_apps_list"),
    path("admin/apps/create-group/", admin_views.create_group, name="admin_create_group"),
    path("admin/database/", admin_views.database_home, name="admin_database"),

    # Django admin
    path("admin/", admin.site.urls),

    # public app routes (if you need them)
    path("", include("applications.urls")),
    path("builder/", include("builder.urls")),
]
