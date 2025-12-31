# applications/admin_urls.py
from django.urls import path
from . import admin_views

urlpatterns = [
    path("apps/", admin_views.apps_list, name="admin_apps_list"),
    path("apps/create-group/", admin_views.create_group, name="admin_create_group"),
    path("apps/delete-group/<int:group_num>/", admin_views.delete_group, name="admin_delete_group"),
    path("apps/forms/<int:form_id>/", admin_views.app_form_detail, name="admin_app_form_detail"),
    path("database/", admin_views.database_home, name="admin_database"),
]
