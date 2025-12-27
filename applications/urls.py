from django.urls import path
from . import views

urlpatterns = [
    # First applications
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

    # Second applications – preview (no token)
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

    # Second applications – real (with token)
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

    path("thanks/", views.application_thanks, name="application_thanks"),
]
