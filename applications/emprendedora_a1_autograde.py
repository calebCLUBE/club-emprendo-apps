# applications/emprendedora_a1_autograde.py
from __future__ import annotations

from django.conf import settings
from django.core.mail import EmailMultiAlternatives

from .a1_eligibility import entrepreneur_a1_passes
from .email_templates import build_form_email_context, resolve_form_email_template
from .models import Application


def _send_html_email(to_email: str, subject: str, html_body: str):
    msg = EmailMultiAlternatives(
        subject=subject,
        body="",
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        to=[to_email],
    )
    msg.attach_alternative(html_body, "text/html")
    msg.send(fail_silently=False)


def emprendedora_a1_passes(answers: dict[str, str]) -> bool:
    return entrepreneur_a1_passes(answers or {})


def autograde_and_email_emprendedora_a1(request, app: Application):
    """
    Emprendedora A1 autograde + email.

    Current master/group slugs in your project:
      - meets_requirements (yes/no)
      - available_period   (yes/no)
      - business_active    (yes/no)

    If all are yes -> APPROVED (invite token only; no A1 approved email).
    Else -> REJECTED email.

    Also sets app.invited_to_second_stage.
    """
    answers = {
        a.question.slug: (a.value or "")
        for a in app.answers.select_related("question").all()
    }

    if emprendedora_a1_passes(answers):
        # ✅ Eligible
        app.generate_invite_token()
        app.invited_to_second_stage = True
        app.save(update_fields=["invite_token", "invited_to_second_stage"])
        return

    # ❌ Not eligible
    app.invited_to_second_stage = False
    app.save(update_fields=["invited_to_second_stage"])

    default_subject = "Sobre tu aplicación al programa de mentoría de Club Emprendo 💛"
    default_html_body = (
        '<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;'
        'margin:0 auto;word-break:break-word;white-space:normal;">'
        "<p>Querida emprendedora,</p>"
        "<p>Gracias por tu interés en participar en nuestro programa de mentoría de Club Emprendo. 🌱</p>"
        "<p>En la aplicación indicaste que no cumples actualmente con uno o más requisitos fundamentales "
        "o con la disponibilidad necesaria para participar en esta cohorte, por eso no podremos enviarte el paso 2.</p>"
        "<p>Con cariño,<br><strong>El equipo de Club Emprendo</strong></p>"
        "</div>"
    )
    replacements = build_form_email_context(
        form_def=app.form,
        role_word="emprendedora",
        deadline=getattr(getattr(app.form, "group", None), "a2_deadline", None),
    )
    subject = resolve_form_email_template(
        form_def=app.form,
        field_name="email_a1_rejected_subject",
        default_text=default_subject,
        replacements=replacements,
        is_subject=True,
    )
    html_body = resolve_form_email_template(
        form_def=app.form,
        field_name="email_a1_rejected_body",
        default_text=default_html_body,
        replacements=replacements,
    )
    _send_html_email(app.email, subject, html_body)
