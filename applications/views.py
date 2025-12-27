# applications/views.py
from django.shortcuts import render, redirect, get_object_or_404
from .forms import build_application_form
from .models import Application, Answer, FormDefinition
from .grading import grade_from_answers


def _handle_application_form(request, form_slug: str, second_stage: bool = False):
    ApplicationForm = build_application_form(form_slug)
    form_def = get_object_or_404(FormDefinition, slug=form_slug)

    if request.method == "POST":
        form = ApplicationForm(request.POST)
        if form.is_valid():
            app = Application.objects.create(
                form=form_def,
                name=form.cleaned_data["name"],
                email=form.cleaned_data["email"],
            )

            for q in form_def.questions.filter(active=True):
                field_name = f"q_{q.slug}"
                value = form.cleaned_data.get(field_name)
                # MultipleChoiceField returns a list: join into comma string
                if isinstance(value, list):
                    value = ",".join(value)
                Answer.objects.create(
                    application=app,
                    question=q,
                    value=str(value),
                )

            # Only grade second-stage forms (A2)
            if second_stage:
                scores = grade_from_answers(app)
                app.tablestakes_score = scores["tablestakes_score"]
                app.commitment_score = scores["commitment_score"]
                app.nice_to_have_score = scores["nice_to_have_score"]
                app.overall_score = scores["overall_score"]
                app.recommendation = scores["recommendation"]
                app.save()

            return redirect("application_thanks")
    else:
        form = ApplicationForm()

    context = {
        "form": form,
        "form_def": form_def,
        "second_stage": second_stage,
    }
    return render(request, "applications/application_form.html", context)


# ---------- PUBLIC FIRST-STAGE FORMS ----------

def apply_emprendedora_first(request):
    return _handle_application_form(request, "E_A1", second_stage=False)


def apply_mentora_first(request):
    return _handle_application_form(request, "M_A1", second_stage=False)


# ---------- SECOND-STAGE (EMAIL LINK) ----------

def apply_emprendedora_second(request, token):
    first_app = get_object_or_404(Application, invite_token=token)
    # form slug for second stage
    form_slug = "E_A2"
    # prefill name/email – we override POST later
    request.GET._mutable = True
    request.GET["name"] = first_app.name
    request.GET["email"] = first_app.email
    return _handle_application_form(request, form_slug, second_stage=True)


def apply_mentora_second(request, token):
    first_app = get_object_or_404(Application, invite_token=token)
    form_slug = "M_A2"
    request.GET._mutable = True
    request.GET["name"] = first_app.name
    request.GET["email"] = first_app.email
    return _handle_application_form(request, form_slug, second_stage=True)


def application_thanks(request):
    return render(request, "applications/thanks.html")

def apply_emprendedora_second_preview(request):
    """
    Preview version of the second Emprendedora application.
    Does NOT require a token or existing Application.
    """
    return _handle_application_form(request, "E_A2", second_stage=True)


def apply_mentora_second_preview(request):
    """
    Preview version of the second Mentora application.
    Does NOT require a token or existing Application.
    """
    return _handle_application_form(request, "M_A2", second_stage=True)

