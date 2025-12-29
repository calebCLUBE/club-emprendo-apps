from django.contrib import admin
from django.urls import path, include
from django.shortcuts import redirect
from applications import admin_views

urlpatterns = [
    path("", lambda r: redirect("/admin/")),

    # Apps page + create group
    path("admin/apps/", admin_views.apps_list, name="admin_apps_list"),
    path("admin/apps/create-group/", admin_views.create_group, name="admin_create_group"),

    path("admin/", admin.site.urls),
    path("", include("applications.urls")),
    path("builder/", include("builder.urls")),
]
