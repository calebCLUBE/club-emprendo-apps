# applications/emprendedora_a1_autograde.py
from __future__ import annotations

import unicodedata

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.urls import reverse


# ====== IMPORTANT ======
# This matches your Apps Script behavior:
# ✅ Approve if (requisitos AND disponibilidad) are "sí/si" (or "yes")
# ❌ Do NOT require "business_active" (Apps Script does not gate on it)
#
# Your actual slugs in DB (from your shell):
# - requisitos     -> "meets_requirements"
# - disponibilidad -> "available_period"
#
FORM_2_LINK_FALLBACK = "https://forms.gle/TM6PyWa2SSMLcQyJ7"

APROBADO_SUBJECT = "Próximo paso para recibir mentorías 💛"
RECHAZADO_SUBJECT = "Sobre tu aplicación al programa de mentoría de Club Emprendo 💛"


def _yesish(v: str) -> bool:
    """
    Replicates Apps Script yesish():
      - remove accents
      - lowercase + trim
      - approve if contains "si" (and also accept "yes")
    """
    t = ((v or "") + "").strip().lower()
    t = unicodedata.normalize("NFD", t)
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")  # strip accents
    return ("si" in t) or ("yes" in t) or ("true" in t) or (t == "1")


def _send_html_email(to_email: str, subject: str, html_body: str):
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None) or "contacto@clubemprendo.org"
    msg = EmailMultiAlternatives(
        subject=subject,
        body="",
        from_email=from_email,
        to=[to_email],
        reply_to=[from_email],
    )
    msg.attach_alternative(html_body, "text/html")
    msg.send(fail_silently=False)


def _approved_html(form_2_link: str) -> str:
    # Exact template from your Apps Script (only difference: FORM_2_LINK injected)
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;margin:0 auto;word-break:break-word;white-space:normal;">'
        "<p>Hola,</p>"
        "<p>Gracias por completar la primera aplicación para participar en nuestro programa de mentoría como emprendedora. Nos alegra contarte que, según tus respuestas, cumples con los requisitos y la disponibilidad necesaria, por lo que puedes avanzar al siguiente paso. 🌟</p>"
        "<p>A continuación, te compartimos la <strong>Aplicación #2</strong>, que es el último paso del proceso de postulación. Esta segunda aplicación nos permitirá conocerte mejor y confirmar si este programa es una buena opción para acompañarte en tu camino emprendedor.</p>"
        "<p><strong>📌 Instrucciones para completar la Aplicación #2:</strong></p>"
        "<ul>"
        "<li>Haz clic en el siguiente enlace:</li>"
        f'<li>👉 <a href="{form_2_link}">Haz clic aquí para completar la Aplicación #2</a></li>'
        "<li>Se abrirá un formulario en una nueva página.</li>"
        "<li>Léelo con calma y responde todas las preguntas.</li>"
        "</ul>"
        "<p><strong>📅 Fecha límite para completar esta aplicación: domingo 14 de septiembre.</strong></p>"
        "<p>📨 Una vez completes esta segunda aplicación, nuestro equipo revisará tu perfil y te informaremos por correo electrónico en las próximas semanas si fuiste seleccionada para participar en esta cohorte. Te recomendamos estar atenta a tu bandeja de entrada.</p>"
        "<p>Gracias nuevamente por tu interés en ser parte de Club Emprendo. ¡Nos emociona la posibilidad de acompañarte en este proceso de crecimiento personal y profesional! 💛</p>"
        "<p>Con cariño,<br><strong>El equipo de Club Emprendo</strong></p>"
        "</div>"
    )


def _rejected_html() -> str:
    # Exact template from your Apps Script
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;margin:0 auto;word-break:break-word;white-space:normal;">'
        "<p>Querida emprendedora,</p>"
        "<p>Gracias por tu interés en participar en nuestro programa de mentoría de Club Emprendo. Valoramos mucho tu deseo de seguir creciendo y fortalecer tu emprendimiento a través de esta experiencia. 🌱</p>"
        "<p>En la aplicación que completaste, indicaste que no cumples actualmente con uno o más de los requisitos fundamentales o con la disponibilidad necesaria para participar en esta cohorte. Por esa razón, no podremos enviarte la segunda y última parte del proceso de postulación.</p>"
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
        "<p>✨ Si crees que marcaste alguna respuesta por error o si tus circunstancias cambian antes de la fecha límite, puedes volver a completar el formulario y con gusto revisaremos nuevamente tu postulación.</p>"
        "<p>Sabemos que cada proceso tiene su tiempo, y si en el futuro decides aplicar de nuevo, estaremos felices de recibir tu solicitud.</p>"
        "<p>Gracias por tu interés en hacer parte de Club Emprendo. ¡Tu iniciativa ya es un paso importante hacia tu crecimiento personal y profesional!</p>"
        "<p>Con cariño,<br><strong>El equipo de Club Emprendo</strong></p>"
        "</div>"
    )


def autograde_and_email_emprendedora_a1(*, request, application, answers_by_slug: dict) -> str:
    """
    Emprendedora A1 autograde:
      - requisitos: slug "meets_requirements"
      - disponibilidad: slug "available_period"
    If BOTH yesish -> Approved + send A2 link
    Else -> Rejected
    Returns: "Aprobado" or "Rechazado"
    """
    requisitos = answers_by_slug.get("meets_requirements", "")
    disponibilidad = answers_by_slug.get("available_period", "")

    passes_requisitos = _yesish(requisitos)
    passes_disponibilidad = _yesish(disponibilidad)

    if passes_requisitos and passes_disponibilidad:
        # Invite token / A2 link just like Mentora
        application.generate_invite_token()
        application.invited_to_second_stage = True
        application.save(update_fields=["invite_token", "invited_to_second_stage"])

        # If you have an internal E_A2 route, use it; otherwise fallback to Google Forms link.
        try:
            a2_url = request.build_absolute_uri(
                reverse("apply_emprendedora_second", kwargs={"token": application.invite_token})
            )
        except Exception:
            a2_url = FORM_2_LINK_FALLBACK

        _send_html_email(
            to_email=application.email,
            subject=APROBADO_SUBJECT,
            html_body=_approved_html(a2_url),
        )
        return "Aprobado"

    application.invited_to_second_stage = False
    application.save(update_fields=["invited_to_second_stage"])

    _send_html_email(
        to_email=application.email,
        subject=RECHAZADO_SUBJECT,
        html_body=_rejected_html(),
    )
    return "Rechazado"
