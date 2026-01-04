# applications/emprendedora_a1_autograde.py
from __future__ import annotations

import unicodedata

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.urls import reverse

from .models import Application


def _send_html_email(to_email: str, subject: str, html_body: str):
    if not (to_email or "").strip():
        # Don't attempt to send if email is missing
        return

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
    Treat "sí" or "si" as yes; handle None safely.
    Matches your Apps Script behavior: 'si' anywhere in the text => True
    """
    t = ((v or "") + "").strip()
    t = unicodedata.normalize("NFD", t)
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")
    t = t.lower()
    return "si" in t


def autograde_and_email_emprendedora_a1(request, app: Application):
    """
    Emprendedora A1 autograde + email.

    Uses your actual DB slugs on Render:
      - e1_meet_requirements
      - e1_available_period
      - e1_has_running_business

    If all three yesish => approved (send website link to E_A2 / token route).
    Else => rejection.
    """
    answers = {
        a.question.slug: (a.value or "")
        for a in app.answers.select_related("question").all()
    }

    # ✅ Correct slug keys (note: meet, not meets)
    requisitos = answers.get("e1_meet_requirements") or answers.get("meets_requirements") or ""
    disponibilidad = answers.get("e1_available_period") or answers.get("availability_ok") or ""
    emprendimiento = answers.get("e1_has_running_business") or answers.get("business_active") or ""

    passes_requisitos = yesish(requisitos)
    passes_disponibilidad = yesish(disponibilidad)
    has_emprendimiento = yesish(emprendimiento)

    if passes_requisitos and passes_disponibilidad and has_emprendimiento:
        # ✅ Eligible
        app.generate_invite_token()
        app.invited_to_second_stage = True
        app.save(update_fields=["invite_token", "invited_to_second_stage"])

        # ✅ WEBSITE link (NOT google forms)
        form2_url = request.build_absolute_uri(
            reverse("apply_emprendedora_second", kwargs={"token": app.invite_token})
        )

        subject = "Próximo paso para recibir mentorías 💛"
        html_body = (
            '<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;margin:0 auto;word-break:break-word;white-space:normal;">'
            "<p>Hola,</p>"
            "<p>Gracias por completar la primera aplicación para participar en nuestro programa de mentoría como emprendedora. Nos alegra contarte que, según tus respuestas, cumples con los requisitos y la disponibilidad necesaria, por lo que puedes avanzar al siguiente paso. 🌟</p>"
            "<p>A continuación, te compartimos la <strong>Aplicación #2</strong>, que es el último paso del proceso de postulación.</p>"
            "<p><strong>📌 Instrucciones:</strong></p>"
            "<ul>"
            "<li>Haz clic en el siguiente enlace:</li>"
            f'<li>👉 <a href="{form2_url}">Completar la Aplicación #2</a></li>'
            "<li>Se abrirá una nueva página en nuestro sitio web.</li>"
            "</ul>"
            "<p>📨 Una vez completes esta segunda aplicación, nuestro equipo revisará tu perfil y te informaremos por correo electrónico en las próximas semanas si fuiste seleccionada.</p>"
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
        '<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;margin:0 auto;word-break:break-word;white-space:normal;">'
        "<p>Querida emprendedora,</p>"
        "<p>Gracias por tu interés en participar en nuestro programa de mentoría de Club Emprendo. 🌱</p>"
        "<p>Según tus respuestas, actualmente no cumples con uno o más de los requisitos fundamentales o con la disponibilidad necesaria para participar en esta cohorte. Por esa razón, no podremos enviarte la segunda parte del proceso.</p>"
        "<p>Con cariño,<br><strong>El equipo de Club Emprendo</strong></p>"
        "</div>"
    )
    _send_html_email(app.email, subject, html_body)
