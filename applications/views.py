# applications/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.conf import settings
from django.core.mail import EmailMultiAlternatives

from .forms import build_application_form
from .models import Application, Answer, FormDefinition
from .grading import grade_from_answers
from .mentora_a1_autograde import autograde_and_email_mentora_a1


def _render_description_with_group(form_def: FormDefinition) -> str:
    """
    Replace #(group number), #(month), #(year) placeholders using FormGroup.
    NOTE: This is simple string replacement. It's deterministic + easy.
    """
    desc = form_def.description or ""
    g = getattr(form_def, "group", None)
    if not g:
        return desc

    # Replace group number and year
    desc = desc.replace("#(group number)", str(g.number))
    desc = desc.replace("#(year)", str(g.year))

    # Replace the first two occurrences of #(month) (start then end)
    if "#(month)" in desc:
        desc = desc.replace("#(month)", g.start_month, 1)
    if "#(month)" in desc:
        desc = desc.replace("#(month)", g.end_month, 1)

    return desc


def _get_cleaned(form, slug: str, default=""):
    return (form.cleaned_data.get(f"q_{slug}") or default)


def _send_email(to_email: str, subject: str, html_body: str):
    """
    Uses Django EMAIL_* settings.
    If EMAIL_HOST is blank, it will fail. We'll fail loudly in dev; in prod you can decide.
    """
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None) or "contacto@clubemprendo.org"
    msg = EmailMultiAlternatives(subject=subject, body="", from_email=from_email, to=[to_email])
    msg.attach_alternative(html_body, "text/html")
    msg.send(fail_silently=False)


# applications/views.py
from django.shortcuts import render, redirect, get_object_or_404
from .forms import build_application_form
from .models import Application, Answer, FormDefinition


def _handle_application_form(request, form_slug: str, second_stage: bool = False):
    ApplicationForm = build_application_form(form_slug)
    form_def = get_object_or_404(FormDefinition, slug=form_slug)

    if request.method == "POST":
        form = ApplicationForm(request.POST)
        if form.is_valid():

            # Pull name/email from Question slugs (so we never duplicate them)
            full_name = form.cleaned_data.get("q_full_name", "").strip()
            email = form.cleaned_data.get("q_email", "").strip()

            app = Application.objects.create(
                form=form_def,
                name=full_name,
                email=email,
            )

            answers_by_slug = {}

            for q in form_def.questions.filter(active=True).order_by("position"):
                field_name = f"q_{q.slug}"
                value = form.cleaned_data.get(field_name)

                if isinstance(value, list):
                    value = ",".join(value)

                value_str = "" if value is None else str(value)

                Answer.objects.create(
                    application=app,
                    question=q,
                    value=value_str,
                )
                answers_by_slug[q.slug] = value_str

            # If this is Mentora A1, do the autograde + email here
            if form_slug == "M_A1":
                from .mentora_autograde import autograde_and_email_mentora_a1
                result = autograde_and_email_mentora_a1(request, app, answers_by_slug)
                app.recommendation = result
                app.save(update_fields=["recommendation"])

            return redirect("application_thanks")
    else:
        form = ApplicationForm()

    return render(
        request,
        "applications/application_form.html",
        {"form": form, "form_def": form_def, "second_stage": second_stage},
    )


def apply_emprendedora_first(request):
    return _handle_application_form(request, "E_A1", second_stage=False)


def apply_mentora_first(request):
    return _handle_application_form(request, "M_A1", second_stage=False)


def apply_emprendedora_second(request, token):
    first_app = get_object_or_404(Application, invite_token=token)
    request.GET._mutable = True
    request.GET["prefill_name"] = first_app.name
    request.GET["prefill_email"] = first_app.email
    return _handle_application_form(request, "E_A2", second_stage=True)


def apply_mentora_second(request, token):
    first_app = get_object_or_404(Application, invite_token=token)
    request.GET._mutable = True
    request.GET["prefill_name"] = first_app.name
    request.GET["prefill_email"] = first_app.email
    return _handle_application_form(request, "M_A2", second_stage=True)


def application_thanks(request):
    return render(request, "applications/thanks.html")



def apply_emprendedora_second_preview(request):
    return _handle_application_form(request, "E_A2", second_stage=True)


def apply_mentora_second_preview(request):
    return _handle_application_form(request, "M_A2", second_stage=True)

