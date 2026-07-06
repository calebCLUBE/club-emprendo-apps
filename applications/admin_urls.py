# applications/admin_urls.py
from django.urls import path
from . import admin_views, admin_dashboard_views, admin_profiles_views

urlpatterns = [
    path("apps/", admin_views.apps_list, name="admin_apps_list"),
    path("apps/dashboard/", admin_dashboard_views.applications_dashboard, name="admin_applications_dashboard"),
    path("dashboards/", admin_dashboard_views.dashboards_home, name="admin_dashboards_home"),
    path("dashboards/marketing/", admin_dashboard_views.marketing_dashboard, name="admin_marketing_dashboard"),
    path("dashboards/impact/", admin_dashboard_views.impact_dashboard, name="admin_impact_dashboard"),
    path("dashboards/application-progress/", admin_dashboard_views.application_progress_dashboard, name="admin_application_progress_dashboard"),
    path("dashboards/impact/report.pdf", admin_dashboard_views.impact_dashboard_pdf, name="admin_impact_dashboard_pdf"),
    path("apps/create-group/", admin_views.create_group, name="admin_create_group"),
    path("apps/rename-group/<int:group_num>/", admin_views.rename_group, name="admin_rename_group"),
    path("apps/update-group/<int:group_num>/", admin_views.update_group_dates, name="admin_update_group"),
    path("apps/delete-group/<int:group_num>/", admin_views.delete_group, name="admin_delete_group"),
    path("apps/forms/<int:form_id>/", admin_views.app_form_detail, name="admin_app_form_detail"),
    path("database/", admin_views.database_home, name="admin_database"),
    path("database/encuestas/sheet/", admin_views.database_encuestas_sheet, name="admin_database_encuestas_sheet"),
    path("database/encuestas.csv", admin_views.database_encuestas_csv, name="admin_database_encuestas_csv"),
    path(
        "database/encuestas-final/sheet/",
        admin_views.database_encuestas_final_sheet,
        name="admin_database_encuestas_final_sheet",
    ),
    path(
        "database/encuestas-final.csv",
        admin_views.database_encuestas_final_csv,
        name="admin_database_encuestas_final_csv",
    ),
    path(
        "database/encuestas-mentoras/sheet/",
        admin_views.database_encuestas_mentoras_sheet,
        name="admin_database_encuestas_mentoras_sheet",
    ),
    path(
        "database/encuestas-mentoras.csv",
        admin_views.database_encuestas_mentoras_csv,
        name="admin_database_encuestas_mentoras_csv",
    ),
    path(
        "database/encuestas-mentoras-final/sheet/",
        admin_views.database_encuestas_mentoras_final_sheet,
        name="admin_database_encuestas_mentoras_final_sheet",
    ),
    path(
        "database/encuestas-mentoras-final.csv",
        admin_views.database_encuestas_mentoras_final_csv,
        name="admin_database_encuestas_mentoras_final_csv",
    ),
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
        "profiles/participants/google-sheet/",
        admin_profiles_views.profiles_participants_google_sheet,
        name="admin_profiles_participants_google_sheet",
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
