# applications/survey_views.py
from django.shortcuts import render, get_object_or_404

from .models import FormDefinition
from .views import _handle_application_form  # uses your existing renderer


SURVEY_SLUGS = ["PRIMER_E", "PRIMER_M", "FINAL_E", "FINAL_M"]


def surveys_index(request):
    surveys = FormDefinition.objects.filter(slug__in=SURVEY_SLUGS).order_by("slug")
    return render(request, "applications/surveys_index.html", {"surveys": surveys})


def survey_by_slug(request, form_slug):
    # Only allow the known survey slugs
    if form_slug not in SURVEY_SLUGS:
        # You can change this to 404 if you prefer
        fd = get_object_or_404(FormDefinition, slug=form_slug)
        # If it exists but isn't in allowed list, block it:
        return render(request, "applications/surveys_not_allowed.html", {"form_def": fd}, status=403)

    return _handle_application_form(request, form_slug, second_stage=False)
