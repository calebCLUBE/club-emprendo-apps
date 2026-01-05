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
# Email helpers
# -------------------------
def _send_html_email(to_email: str, subject: str, html_body: str):
    # Guard: never attempt to send to empty recipient
    if not (to_email or "").strip():
        return

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
    Replicates your Google Apps Script logic for Mentora A1 (M_A1 and G#_M_A1):

    - Check answers to:
        meets_requirements (yes/no)
        availability_ok (yes/no)

    - If both yes:
        generate token
        email token-link to /apply/mentora/continue/<token>/
    - else:
        rejection email

    Also sets invited_to_second_stage flag.
    """

    answers = {
        a.question.slug: (a.value or "")
        for a in app.answers.select_related("question").all()
    }

    def _yesish(v: str) -> bool:
        t = (v or "").strip().lower()
        # accept "yes", "sí/si", and also "true" just in case
        return ("yes" in t) or ("si" in t) or (t == "true")

    # NEW slugs (Render) + old slugs (fallback)
    requisitos = (
        answers.get("m1_meet_requirements")
        or answers.get("meets_requirements")
        or ""
    )
    disponibilidad = (
        answers.get("m1_availability_ok")
        or answers.get("availability_ok")
        or answers.get("m1_available_period")   # if you used this wording on mentora too
        or ""
    )

    passes_requisitos = _yesish(requisitos)
    passes_disponibilidad = _yesish(disponibilidad)


    if passes_requisitos and passes_disponibilidad:
        app.generate_invite_token()
        app.invited_to_second_stage = True
        app.save()

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
            "<p>📩 Una vez completes esta segunda aplicación, evaluaremos tu postulación y te contactaremos por correo electrónico en las próximas semanas para informarte si has sido seleccionada como mentora para este grupo.</p>"
            "<p>Gracias nuevamente por tu interés y compromiso con otras mujeres emprendedoras 💛</p>"
            "<p>Con cariño,<br><strong>El equipo de Club Emprendo</strong></p>"
            "</div>"
        )
        _send_html_email(app.email, subject, html_body)
        return

    # rejection
    app.invited_to_second_stage = False
    app.save()

    subject = "Sobre tu aplicación como mentora voluntaria 🌟"
    html_body = (
        '<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;margin:0 auto;word-break:break-word;white-space:normal;">'
        "<p>Querida aplicante a mentora,</p>"
        "<p>Gracias por tu interés en ser parte del programa de mentoría de Club Emprendo. Valoramos profundamente tu deseo de donar tu tiempo y experiencia para apoyar a otras mujeres emprendedoras en su camino. 💛</p>"
        "<p>En la aplicación que completaste, indicaste que actualmente no cumples con uno o más de los requisitos fundamentales o con la disponibilidad necesaria para participar en esta cohorte. Por esa razón, en este momento no podremos enviarte la segunda y última parte del proceso de aplicación.</p>"
        "<p>📌 <strong>Los requisitos esenciales para ser mentora son:</strong></p>"
        "<ul>"
        "<li>Ser mujer</li>"
        "<li>Tener experiencia en emprender o trabajar en negocios de alguna forma</li>"
        "<li>Ser puntual</li>"
        "<li>Tener conexión a internet estable</li>"
        "<li>Estar dispuesta a completar una capacitación previa al programa</li>"
        "<li>Estar dispuesta a responder 3 encuestas de retroalimentación durante el proceso</li>"
        "</ul>"
        "<p>✨ Si por alguna razón marcaste alguna respuesta por error, o si tus circunstancias cambian en los próximos días, puedes volver a completar la aplicación antes de la fecha límite y con gusto la revisaremos nuevamente.</p>"
        "<p>Sabemos que cada etapa de la vida es distinta y que a veces no es el momento adecuado. Agradecemos profundamente tu intención de sumarte, y si en el futuro decides postularte nuevamente, estaremos felices de recibirte.</p>"
        "<p>Con cariño,<br><strong>El equipo de Club Emprendo</strong></p>"
        "</div>"
    )
    _send_html_email(app.email, subject, html_body)


# -------------------------
# Core handler
# -------------------------
def _handle_application_form(request, form_slug: str, second_stage: bool = False):
    """
    Creates Application + Answers for any FormDefinition.slug (master or group clone).

    IMPORTANT:
    - We do NOT assume the form has top-level "name" and "email" fields.
    - We extract them from this form's question slugs dynamically (works for e1_email, m1_email, etc).
    """
    form_def = get_object_or_404(FormDefinition, slug=form_slug)
    ApplicationForm = build_application_form(form_slug)

    if request.method == "POST":
        form = ApplicationForm(request.POST)
        if form.is_valid():
            # ---- Robust name/email extraction based on THIS form's question slugs ----
            def _pick_value_by_slug_contains(contains_any):
                """
                Looks through form_def questions and returns the first non-empty cleaned_data
                value where the question.slug contains any of the substrings.
                """
                contains_any = [c.lower() for c in contains_any]
                for q in form_def.questions.filter(active=True).order_by("position", "id"):
                    s = (q.slug or "").lower()
                    if any(c in s for c in contains_any):
                        v = (form.cleaned_data.get(f"q_{q.slug}") or "").strip()
                        if v:
                            return v
                return ""

            full_name = _pick_value_by_slug_contains(["full_name", "nombre", "name"])
            email = _pick_value_by_slug_contains(["email", "correo"])

            # fallback: scan any field that looks like an email
            if not email:
                for k, v in form.cleaned_data.items():
                    if not str(k).startswith("q_"):
                        continue
                    s = (v or "").strip()
                    if "@" in s and "." in s:
                        email = s
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

            # ✅ Mentora A1 autograde+email
            if form_def.slug.endswith("M_A1"):
                _mentor_a1_autograde_and_email(request, app)

            # ✅ Emprendedora A1 autograde+email (MASTER or GROUP)
            if form_def.slug.endswith("E_A1"):
                autograde_and_email_emprendedora_a1(request, app)

            return redirect("application_thanks")
    else:
        form = ApplicationForm()

    return render(
        request,
        "applications/application_form.html",
        {"form": form, "form_def": form_def, "second_stage": second_stage},
    )


# ---------- PUBLIC FIRST-STAGE FORMS ----------
def apply_emprendedora_first(request):
    return _handle_application_form(request, "E_A1", second_stage=False)


def apply_mentora_first(request):
    return _handle_application_form(request, "M_A1", second_stage=False)


# ---------- SECOND-STAGE (TOKEN REQUIRED) ----------
def apply_emprendedora_second(request, token):
    first_app = get_object_or_404(Application, invite_token=token)

    # If first stage was G#_E_A1, try to route to G#_E_A2
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

    # Default to master M_A2
    form_slug = "M_A2"

    # If first stage was group like G6_M_A1, try G6_M_A2
    m = GROUP_SLUG_RE.match(first_app.form.slug or "")
    if m:
        gnum = m.group("num")
        candidate = f"G{gnum}_M_A2"
        if FormDefinition.objects.filter(slug=candidate).exists():
            form_slug = candidate

    return _handle_application_form(request, form_slug, second_stage=True)


# ---------- PREVIEW (NO TOKEN) ----------
def apply_emprendedora_second_preview(request):
    return _handle_application_form(request, "E_A2", second_stage=True)


def apply_mentora_second_preview(request):
    return _handle_application_form(request, "M_A2", second_stage=True)


# ---------- GROUP/SLUG ROUTE ----------
def apply_by_slug(request, form_slug):
    # A2 (or *_A2) should be treated as second stage for grading/etc
    second_stage = str(form_slug).endswith("_A2")
    return _handle_application_form(request, form_slug, second_stage=second_stage)

def survey_by_slug(request, form_slug):
    # Surveys are not A2 / no token logic; just render/save like any other form
    return _handle_application_form(request, form_slug, second_stage=False)

def application_thanks(request):
    return render(request, "applications/thanks.html")
# applications/views.py

from django.shortcuts import render
from django.contrib.admin.views.decorators import staff_member_required


@staff_member_required
def surveys_home(request):
    """
    Simple landing page listing all surveys (public links),
    plus quick links to their submissions in the admin.
    """
    surveys = [
        {"slug": "PRIMER_E", "title": "PRIMER · Emprendedoras"},
        {"slug": "PRIMER_M", "title": "PRIMER · Mentoras"},
        {"slug": "FINAL_E",  "title": "FINAL · Emprendedoras"},
        {"slug": "FINAL_M",  "title": "FINAL · Mentoras"},
    ]
    return render(request, "applications/surveys_home.html", {"surveys": surveys})
