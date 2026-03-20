# applications/emprendedora_a1_autograde.py
from __future__ import annotations

import unicodedata

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.urls import reverse

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


def yesish(v) -> bool:
    """
    Treat 'sí' or 'si' as yes; handle null/undefined safely.
    Accent-insensitive, substring match.
    """
    t = ((v or "") + "")
    t = unicodedata.normalize("NFD", t)
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")
    t = t.lower().strip()
    return "si" in t


def _is_yes_value(v: str) -> bool:
    """
    Your DB stores choice values like 'yes'/'no' for most of these.
    Also supports yesish() for older free-text variants.
    """
    vv = (v or "").strip().lower()
    return vv == "yes" or yesish(v)


def emprendedora_a1_passes(answers: dict[str, str]) -> bool:
    requisitos = (
        answers.get("meets_requirements")
        or answers.get("e1_meet_requirements")
        or ""
    )

    disponibilidad = (
        answers.get("available_period")
        or answers.get("e1_available_period")
        or answers.get("availability_ok")
        or ""
    )

    emprendimiento = (
        answers.get("business_active")
        or answers.get("e1_has_running_business")
        or ""
    )

    passes_requisitos = _is_yes_value(requisitos)
    passes_disponibilidad = _is_yes_value(disponibilidad)
    has_emprendimiento = _is_yes_value(emprendimiento)
    return passes_requisitos and passes_disponibilidad and has_emprendimiento


def autograde_and_email_emprendedora_a1(request, app: Application):
    """
    Emprendedora A1 autograde + email.

    Current master/group slugs in your project:
      - meets_requirements (yes/no)
      - available_period   (yes/no)
      - business_active    (yes/no)

    If all are yes -> APPROVED (invite token + email with link to A2).
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

        form2_url = request.build_absolute_uri(
            reverse("apply_emprendedora_second", kwargs={"token": app.invite_token})
        )

        subject = "Próximo paso para recibir mentorías 💛"
        deadline_str = ""
        grp = getattr(app.form, "group", None)
        if grp and getattr(grp, "a2_deadline", None):
            deadline_str = grp.a2_deadline.strftime("%d/%m/%Y")

        html_body = (
            '<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;'
            'margin:0 auto;word-break:break-word;white-space:normal;">'
            "<p>Hola,</p>"
            "<p>Gracias por completar la primera aplicación para participar en nuestro programa de mentoría como emprendedora. "
            "Nos alegra contarte que, según tus respuestas, cumples con los requisitos y la disponibilidad necesaria, por lo que "
            "puedes avanzar al siguiente paso. 🌟</p>"
            "<p>A continuación, te compartimos la <strong>Aplicación #2</strong>, que es el último paso del proceso de postulación.</p>"
            "<p><strong>📌 Instrucciones para completar la Aplicación #2:</strong></p>"
            "<ul>"
            f'<li>👉 <a href="{form2_url}">Haz clic aquí para completar la Aplicación #2</a>'
            f"{' - Fecha límite: ' + deadline_str if deadline_str else ''}</li>"
            "</ul>"
            "<p>Con cariño,<br><strong>El equipo de Club Emprendo</strong></p>"
            "</div>"
        )
        _send_html_email(app.email, subject, html_body)
        return

    # ❌ Not eligible
    app.invited_to_second_stage = False
    app.save(update_fields=["invited_to_second_stage"])

    subject = "Sobre tu aplicación al programa de mentoría de Club Emprendo 💛"
    html_body = (
        '<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;'
        'margin:0 auto;word-break:break-word;white-space:normal;">'
        "<p>Querida emprendedora,</p>"
        "<p>Gracias por tu interés en participar en nuestro programa de mentoría de Club Emprendo. 🌱</p>"
        "<p>En la aplicación indicaste que no cumples actualmente con uno o más requisitos fundamentales "
        "o con la disponibilidad necesaria para participar en esta cohorte, por eso no podremos enviarte el paso 2.</p>"
        "<p>Con cariño,<br><strong>El equipo de Club Emprendo</strong></p>"
        "</div>"
    )
    _send_html_email(app.email, subject, html_body)
