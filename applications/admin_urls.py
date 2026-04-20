# applications/admin_urls.py
from django.urls import path
from . import admin_views, admin_dashboard_views, admin_profiles_views

urlpatterns = [
    path("apps/", admin_views.apps_list, name="admin_apps_list"),
    path("apps/dashboard/", admin_dashboard_views.applications_dashboard, name="admin_applications_dashboard"),
    path("apps/create-group/", admin_views.create_group, name="admin_create_group"),
    path("apps/update-group/<int:group_num>/", admin_views.update_group_dates, name="admin_update_group"),
    path("apps/delete-group/<int:group_num>/", admin_views.delete_group, name="admin_delete_group"),
    path("apps/forms/<int:form_id>/", admin_views.app_form_detail, name="admin_app_form_detail"),
    path("database/", admin_views.database_home, name="admin_database"),
    path(
        "database/create-assigned-group/",
        admin_views.database_create_assigned_group,
        name="admin_database_create_assigned_group",
    ),
    path("profiles/", admin_profiles_views.profiles_list, name="admin_profiles_list"),
    path(
        "profiles/participants/",
        admin_profiles_views.profiles_participants,
        name="admin_profiles_participants",
    ),
    path(
        "profiles/participants/<int:group_num>/download/",
        admin_profiles_views.profiles_participants_download,
        name="admin_profiles_participants_download",
    ),
    path(
        "profiles/participants/<int:group_num>/<str:track>/",
        admin_profiles_views.profiles_participants_track_sheet,
        name="admin_profiles_participants_track_sheet",
    ),
    path("profiles/<str:identity_key>/", admin_profiles_views.profile_detail, name="admin_profile_detail"),
]
