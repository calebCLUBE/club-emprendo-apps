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
    Mirrors your Apps Script logic (accent-insensitive, substring match).
    """
    t = ((v or "") + "")
    t = unicodedata.normalize("NFD", t)
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")
    t = t.lower().strip()
    return "si" in t


def autograde_and_email_emprendedora_a1(request, app: Application):
    """
    Emprendedora A1 autograde + email.

    Uses your Render DB slugs (and falls back to older slugs if needed):
      - e1_meet_requirements  (requisitos)
      - e1_available_period   (disponibilidad)
      - e1_has_running_business (emprendimiento)

    If all are yes-ish -> APPROVED:
      - generate invite token
      - send email with WEBSITE link to E_A2 (token route)

    Else -> REJECTED email.

    Also sets app.invited_to_second_stage.
    """
    answers = {
        a.question.slug: (a.value or "")
        for a in app.answers.select_related("question").all()
    }

    requisitos = (
        answers.get("e1_meet_requirements")
        or answers.get("meets_requirements")
        or ""
    )
    disponibilidad = (
        answers.get("e1_available_period")
        or answers.get("availability_ok")
        or ""
    )
    emprendimiento = (
        answers.get("e1_has_running_business")
        or answers.get("business_active")
        or ""
    )

    passes_requisitos = yesish(requisitos) or ((requisitos or "").strip().lower() == "yes")
    passes_disponibilidad = yesish(disponibilidad) or ((disponibilidad or "").strip().lower() == "yes")
    has_emprendimiento = yesish(emprendimiento) or ((emprendimiento or "").strip().lower() == "yes")

    if passes_requisitos and passes_disponibilidad and has_emprendimiento:
        # ✅ Eligible
        app.generate_invite_token()
        app.invited_to_second_stage = True
        app.save(update_fields=["invite_token", "invited_to_second_stage"])

        # ✅ WEBSITE link (token-protected) to Application #2
        form2_url = request.build_absolute_uri(
            reverse("apply_emprendedora_second", kwargs={"token": app.invite_token})
        )

        subject = "Próximo paso para recibir mentorías 💛"
        html_body = (
            '<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;'
            'margin:0 auto;word-break:break-word;white-space:normal;">'
            "<p>Hola,</p>"
            "<p>Gracias por completar la primera aplicación para participar en nuestro programa de mentoría como emprendedora. "
            "Nos alegra contarte que, según tus respuestas, cumples con los requisitos y la disponibilidad necesaria, por lo que "
            "puedes avanzar al siguiente paso. 🌟</p>"
            "<p>A continuación, te compartimos la <strong>Aplicación #2</strong>, que es el último paso del proceso de postulación. "
            "Esta segunda aplicación nos permitirá conocerte mejor y confirmar si este programa es una buena opción para acompañarte "
            "en tu camino emprendedor.</p>"
            "<p><strong>📌 Instrucciones para completar la Aplicación #2:</strong></p>"
            "<ul>"
            "<li>Haz clic en el siguiente enlace:</li>"
            f'<li>👉 <a href="{form2_url}">Haz clic aquí para completar la Aplicación #2</a></li>'
            "<li>Se abrirá una nueva página en nuestro sitio web.</li>"
            "<li>Léelo con calma y responde todas las preguntas.</li>"
            "</ul>"
            "<p>📨 Una vez completes esta segunda aplicación, nuestro equipo revisará tu perfil y te informaremos por correo electrónico "
            "en las próximas semanas si fuiste seleccionada para participar en esta cohorte. Te recomendamos estar atenta a tu bandeja de entrada.</p>"
            "<p>Gracias nuevamente por tu interés en ser parte de Club Emprendo. ¡Nos emociona la posibilidad de acompañarte en este proceso de "
            "crecimiento personal y profesional! 💛</p>"
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
        "<p>Gracias por tu interés en participar en nuestro programa de mentoría de Club Emprendo. "
        "Valoramos mucho tu deseo de seguir creciendo y fortalecer tu emprendimiento a través de esta experiencia. 🌱</p>"
        "<p>En la aplicación que completaste, indicaste que no cumples actualmente con uno o más de los requisitos fundamentales "
        "o con la disponibilidad necesaria para participar en esta cohorte. Por esa razón, no podremos enviarte la segunda y última "
        "parte del proceso de postulación.</p>"
        "<p>📌 <strong>Requisitos generales del programa:</strong></p>"
        "<ul>"
        "<li>Ser mujer</li>"
        "<li>Vivir en Latinoamérica</li>"
        "<li>Tener conexión estable a internet</li>"
        "<li>Tener un emprendimiento en marcha (no solo una idea)</li>"
        "<li>Ser puntual</li>"
        "<li>Estar dispuesta a completar una capacitación previa al inicio del programa</li>"
        "<li>Estar dispuesta a responder 4 encuestas de retroalimentación a lo largo del proceso</li>"
        "</ul>"
        "<p>✨ Si crees que marcaste alguna respuesta por error o si tus circunstancias cambian antes de la fecha límite, "
        "puedes volver a completar el formulario y con gusto revisaremos nuevamente tu postulación.</p>"
        "<p>Sabemos que cada proceso tiene su tiempo, y si en el futuro decides aplicar de nuevo, estaremos felices de recibir tu solicitud.</p>"
        "<p>Gracias por tu interés en hacer parte de Club Emprendo. ¡Tu iniciativa ya es un paso importante hacia tu crecimiento personal y profesional!</p>"
        "<p>Con cariño,<br><strong>El equipo de Club Emprendo</strong></p>"
        "</div>"
    )

    _send_html_email(app.email, subject, html_body)
