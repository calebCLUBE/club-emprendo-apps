# club_emprendo/urls.py

from django.contrib import admin
from django.urls import path, include
from django.shortcuts import redirect

from applications import admin_views

urlpatterns = [
    # Redirect root to your admin dashboard
    path("", lambda r: redirect("/admin/")),

    # ----------------------------
    # Custom admin dashboard routes
    # MUST come before "admin/"
    # ----------------------------
    path("admin/apps/", admin_views.apps_list, name="admin_apps_list"),
    path(
        "admin/apps/forms/<int:form_id>/",
        admin_views.app_form_detail,
        name="admin_app_form_detail",
    ),

    path("admin/submissions/", admin_views.submissions_list, name="admin_submissions_list"),
    path(
        "admin/submissions/<int:app_id>/",
        admin_views.submission_detail,
        name="admin_submission_detail",
    ),

    path("admin/database/", admin_views.database_home, name="admin_database"),

    # (Optional) If you later add custom user admin pages, they go here too:
    # path("admin/users/", admin_views.users_list, name="admin_users_list"),
    # path("admin/users/invite/", admin_views.invite_user, name="admin_users_invite"),

    # ----------------------------
    # Django admin (catch-all)
    # ----------------------------
    path("admin/", admin.site.urls),

    # ----------------------------
    # Auth routes (login/logout/password reset)
    # ----------------------------
    path("accounts/", include("django.contrib.auth.urls")),

    # ----------------------------
    # Public app routes
    # ----------------------------
    path("", include("applications.urls")),

    # Internal builder
    path("builder/", include("builder.urls")),
]
