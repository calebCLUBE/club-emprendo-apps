# applications/urls.py
from django.urls import path

from .views import (
    apply_emprendedora_first,
    apply_mentora_first,
    apply_emprendedora_second_preview,
    apply_mentora_second_preview,
    apply_emprendedora_second,
    apply_mentora_second,
    application_thanks,
    apply_by_slug,
)

urlpatterns = [
    # ---------- FIRST STAGE (PUBLIC) ----------
    path("apply/emprendedora/", apply_emprendedora_first, name="apply_emprendedora_first"),
    path("apply/mentora/", apply_mentora_first, name="apply_mentora_first"),

    # ---------- SECOND STAGE (PREVIEW – NO TOKEN) ----------
    path("apply/emprendedora/continue/preview/", apply_emprendedora_second_preview, name="preview_emprendedora_second"),
    path("apply/mentora/continue/preview/", apply_mentora_second_preview, name="preview_mentora_second"),

    # ---------- SECOND STAGE (REAL – TOKEN REQUIRED) ----------
    path("apply/emprendedora/continue/<uuid:token>/", apply_emprendedora_second, name="apply_emprendedora_second"),
    path("apply/mentora/continue/<uuid:token>/", apply_mentora_second, name="apply_mentora_second"),

    # ---------- GENERIC APPLY BY SLUG (GROUP FORMS) ----------
    path("apply/<slug:form_slug>/", apply_by_slug, name="apply_by_slug"),

    # ---------- THANK YOU ----------
    path("thanks/", application_thanks, name="application_thanks"),
]
