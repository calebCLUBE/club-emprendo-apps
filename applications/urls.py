from django.urls import path
from . import views

urlpatterns = [
    # ---------- FIRST STAGE (PUBLIC) ----------

    path(
        "apply/emprendedora/",
        views.apply_emprendedora_first,
        name="apply_emprendedora_first",
    ),
    path(
        "apply/mentora/",
        views.apply_mentora_first,
        name="apply_mentora_first",
    ),

    # ---------- SECOND STAGE (PREVIEW – NO TOKEN) ----------

    path(
        "apply/emprendedora/continue/preview/",
        views.apply_emprendedora_second_preview,
        name="preview_emprendedora_second",
    ),
    path(
        "apply/mentora/continue/preview/",
        views.apply_mentora_second_preview,
        name="preview_mentora_second",
    ),

    # ---------- SECOND STAGE (REAL – TOKEN REQUIRED) ----------

    path(
        "apply/emprendedora/continue/<uuid:token>/",
        views.apply_emprendedora_second,
        name="apply_emprendedora_second",
    ),
    path(
        "apply/mentora/continue/<uuid:token>/",
        views.apply_mentora_second,
        name="apply_mentora_second",
    ),

    # ---------- THANK YOU ----------

    path("thanks/", views.application_thanks, name="application_thanks"),
]
