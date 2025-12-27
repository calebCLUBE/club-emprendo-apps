from django.urls import path
from . import views

app_name = "builder"

urlpatterns = [
    path("", views.builder_home, name="home"),
    path("form/<int:form_id>/", views.form_editor, name="form_editor"),

    # HTMX endpoints
    path("form/<int:form_id>/question/add/", views.question_add, name="question_add"),
    path("question/<int:question_id>/panel/", views.question_panel, name="question_panel"),
    path("question/<int:question_id>/update/", views.question_update, name="question_update"),
    path("question/<int:question_id>/delete/", views.question_delete, name="question_delete"),
]
