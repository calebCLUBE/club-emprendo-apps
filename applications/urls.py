# applications/urls.py
from django.urls import path

from . import views
from . import survey_views

urlpatterns = [
    # ---------- PUBLIC FIRST-STAGE FORMS ----------
    path("apply/emprendedora/", views.apply_emprendedora_first, name="apply_emprendedora_first"),
    path("apply/mentora/", views.apply_mentora_first, name="apply_mentora_first"),

    # ---------- SECOND-STAGE (TOKEN REQUIRED) ----------
    path("apply/emprendedora/continue/<str:token>/", views.apply_emprendedora_second, name="apply_emprendedora_second"),
    path("apply/mentora/continue/<str:token>/", views.apply_mentora_second, name="apply_mentora_second"),

    # ---------- PREVIEW (NO TOKEN) ----------
    path("apply/emprendedora/preview/", views.apply_emprendedora_second_preview, name="preview_emprendedora_second"),
    path("apply/mentora/preview/", views.apply_mentora_second_preview, name="preview_mentora_second"),

    # ---------- GROUP/SLUG ROUTE ----------
    path("apply/<slug:form_slug>/", views.apply_by_slug, name="apply_by_slug"),

    # ---------- THANKS ----------
    path("thanks/", views.application_thanks, name="application_thanks"),

    # ---------- SURVEYS ----------
    path("surveys/", survey_views.surveys_index, name="surveys_index"),
    path("survey/<slug:form_slug>/", survey_views.survey_by_slug, name="survey_by_slug"),
]
