# applications/views.py
import re

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse

from .forms import build_application_form
from .models import Application, Answer, FormDefinition
from .emprendedora_a1_autograde import autograde_and_email_emprendedora_a1


GROUP_SLUG_RE = re.compile(r"^G(?P<num>\d+)_")


# -------------------------
# Utilities
# -------------------------
def _latest_group_form_slug(suffix: str) -> str | None:
    pattern = re.compile(rf"^G(?P<num>\d+)_{re.escape(suffix)}$")
    best = None
    best_num = -1

    for fd in FormDefinition.objects.filter(slug__endswith=suffix):
        m = pattern.match(fd.slug or "")
        if not m:
            continue
        n = int(m.group("num"))
        if n > best_num:
            best_num = n
            best = fd.slug

    return best


def _send_html_email(to_email: str, subject: str, html_body: str):
    msg = EmailMultiAlternatives(
        subject=subject,
        body="",
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        to=[to_email],
    )
    msg.attach_alternative(html_body, "text/html")
    msg.send(fail_silently=False)


def _mentor_a1_autograde_and_email(request, app: Application):
    """
    Mentora A1 autograde + email.
    """
    answers = {
        a.question.slug: (a.value or "")
        for a in app.answers.select_related("question").all()
    }

    def yesish(v: str) -> bool:
        t = (v or "").strip().lower()
        return ("si" in t) or ("sí" in t) or ("yes" in t) or (t == "true") or (t == "1") or (t == "yes")

    requisitos = (
        answers.get("meets_requirements")
        or answers.get("m1_meet_requirements")
        or answers.get("m1_meets_requirements")
        or answers.get("m1_requirements_ok")
        or ""
    )

    disponibilidad = (
        answers.get("available_period")          # ✅ FIX: this is your real slug
        or answers.get("availability_ok")
        or answers.get("m1_availability_ok")
        or answers.get("m1_available_period")
        or answers.get("m1_available")
        or ""
    )

    passes_requisitos = yesish(requisitos) or ((requisitos or "").strip().lower() == "yes")
    passes_disponibilidad = yesish(disponibilidad) or ((disponibilidad or "").strip().lower() == "yes")

    if passes_requisitos and passes_disponibilidad:
        app.generate_invite_token()
        app.invited_to_second_stage = True
        app.save(update_fields=["invite_token", "invited_to_second_stage"])

        form2_url = request.build_absolute_uri(
            reverse("apply_mentora_second", kwargs={"token": app.invite_token})
        )

        subject = "Siguiente paso: Completa la segunda solicitud"
        html_body = (
            '<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;margin:0 auto;word-break:break-word;white-space:normal;">'
            "<p><strong>Querida aplicante a Mentora,</strong></p>"
            "<p>Gracias por completar la primera aplicación para ser mentora en Club Emprendo. 🌱</p>"
            "<p>Con base en tus respuestas, confirmamos que cumples con los requisitos y la disponibilidad necesaria, por lo que estás habilitada para continuar con el proceso.</p>"
            "<p>A continuación, te compartimos la <strong>Aplicación #2</strong>, que es el segundo y último paso para postularte como mentora voluntaria.</p>"
            "<p><strong>📌 Instrucciones para acceder a la Aplicación #2:</strong></p>"
            "<ol>"
            f'<li>Haz clic aquí: 👉 <a href="{form2_url}">Aplicación 2</a></li>'
            "<li>Lee con atención y responde cada pregunta.</li>"
            "</ol>"
            "<p>Gracias nuevamente por tu interés y compromiso con otras mujeres emprendedoras 💛</p>"
            "<p>Con cariño,<br><strong>El equipo de Club Emprendo</strong></p>"
            "</div>"
        )
        _send_html_email(app.email, subject, html_body)
        return

    app.invited_to_second_stage = False
    app.save(update_fields=["invited_to_second_stage"])

    subject = "Sobre tu aplicación como mentora voluntaria 🌟"
    html_body = (
        '<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;margin:0 auto;word-break:break-word;white-space:normal;">'
        "<p>Querida aplicante a mentora,</p>"
        "<p>Gracias por tu interés en ser parte del programa de mentoría de Club Emprendo. 💛</p>"
        "<p>En la aplicación indicaste que no cumples uno o más requisitos o disponibilidad para esta cohorte, por eso no podremos enviarte el paso 2.</p>"
        "<p>Con cariño,<br><strong>El equipo de Club Emprendo</strong></p>"
        "</div>"
    )
    _send_html_email(app.email, subject, html_body)


# -------------------------
# Core handler
# -------------------------
def _handle_application_form(request, form_slug: str, second_stage: bool = False):
    form_def = get_object_or_404(FormDefinition, slug=form_slug)
    ApplicationForm = build_application_form(form_slug)

    rendered_description = ""
    for attr in ("description", "intro", "intro_text", "public_description"):
        if hasattr(form_def, attr):
            v = getattr(form_def, attr) or ""
            if str(v).strip():
                rendered_description = str(v)
                break

    if request.method == "POST":
        form = ApplicationForm(request.POST)
        if form.is_valid():

            def _pick_first(*keys: str) -> str:
                for k in keys:
                    v = (form.cleaned_data.get(k) or "").strip()
                    if v:
                        return v
                return ""

            full_name = _pick_first(
                "q_full_name", "q_name", "q_nombre",
                "q_e1_full_name", "q_m1_full_name",
                "q_e2_full_name", "q_m2_full_name",
            )
            email = _pick_first(
                "q_email", "q_correo", "q_correo_electronico",
                "q_e1_email", "q_m1_email",
                "q_e2_email", "q_m2_email",
            )

            if not email:
                for k, v in form.cleaned_data.items():
                    if not k.startswith("q_"):
                        continue
                    s = (v or "").strip()
                    if "@" in s and "." in s:
                        email = s
                        break

            if not full_name:
                for k, v in form.cleaned_data.items():
                    if not k.startswith("q_"):
                        continue
                    lk = k.lower()
                    if ("name" in lk) or ("nombre" in lk):
                        s = (v or "").strip()
                        if s:
                            full_name = s
                            break

            app = Application.objects.create(
                form=form_def,
                name=full_name,
                email=email,
            )

            for q in form_def.questions.filter(active=True).order_by("position", "id"):
                field_name = f"q_{q.slug}"
                value = form.cleaned_data.get(field_name)
                if isinstance(value, list):
                    value = ",".join(value)
                Answer.objects.create(
                    application=app,
                    question=q,
                    value=str(value or ""),
                )

            if form_def.slug.endswith("M_A1"):
                _mentor_a1_autograde_and_email(request, app)

            if form_def.slug.endswith("E_A1"):
                autograde_and_email_emprendedora_a1(request, app)

            return redirect("application_thanks")
    else:
        form = ApplicationForm()

    return render(
        request,
        "applications/application_form.html",
        {
            "form": form,
            "form_def": form_def,
            "second_stage": second_stage,
            "rendered_description": rendered_description,
        },
    )


# ---------- PUBLIC FIRST-STAGE FORMS ----------
def apply_emprendedora_first(request):
    latest = _latest_group_form_slug("E_A1")
    return _handle_application_form(request, latest or "E_A1", second_stage=False)


def apply_mentora_first(request):
    latest = _latest_group_form_slug("M_A1")
    return _handle_application_form(request, latest or "M_A1", second_stage=False)


# ---------- SECOND-STAGE (TOKEN REQUIRED) ----------
def apply_emprendedora_second(request, token):
    first_app = get_object_or_404(Application, invite_token=token)

    form_slug = "E_A2"
    m = GROUP_SLUG_RE.match(first_app.form.slug or "")
    if m:
        gnum = m.group("num")
        candidate = f"G{gnum}_E_A2"
        if FormDefinition.objects.filter(slug=candidate).exists():
            form_slug = candidate

    return _handle_application_form(request, form_slug, second_stage=True)


def apply_mentora_second(request, token):
    first_app = get_object_or_404(Application, invite_token=token)

    form_slug = "M_A2"
    m = GROUP_SLUG_RE.match(first_app.form.slug or "")
    if m:
        gnum = m.group("num")
        candidate = f"G{gnum}_M_A2"
        if FormDefinition.objects.filter(slug=candidate).exists():
            form_slug = candidate

    return _handle_application_form(request, form_slug, second_stage=True)


# ---------- PREVIEW (NO TOKEN) ----------
def apply_emprendedora_second_preview(request):
    latest = _latest_group_form_slug("E_A2")
    return _handle_application_form(request, latest or "E_A2", second_stage=True)


def apply_mentora_second_preview(request):
    latest = _latest_group_form_slug("M_A2")
    return _handle_application_form(request, latest or "M_A2", second_stage=True)


# ---------- GROUP/SLUG ROUTE ----------
def apply_by_slug(request, form_slug):
    second_stage = str(form_slug).endswith("_A2")
    return _handle_application_form(request, form_slug, second_stage=second_stage)


def application_thanks(request):
    return render(request, "applications/thanks.html")


# ---------- SURVEYS ----------
def survey_by_slug(request, form_slug):
    return _handle_application_form(request, form_slug, second_stage=False)
