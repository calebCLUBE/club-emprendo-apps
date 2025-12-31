# applications/mentora_autograde.py
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.urls import reverse


APPROVED_SUBJECT = "Siguiente paso: Completa la segunda solicitud"
REJECTED_SUBJECT = "Sobre tu aplicación como mentora voluntaria 🌟"


def _send_html_email(to_email: str, subject: str, html_body: str):
    msg = EmailMultiAlternatives(
        subject=subject,
        body="",
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to_email],
        reply_to=[settings.DEFAULT_FROM_EMAIL],
    )
    msg.attach_alternative(html_body, "text/html")
    msg.send(fail_silently=False)


def autograde_and_email_mentora_a1(request, application, answers_by_slug: dict) -> str:
    """
    Returns: "Aprobado" or "Rechazado"
    Logic matches your Google Apps Script:
      - approve only if meets_requirements includes "sí" AND availability includes "sí"
    """

    email = (application.email or "").strip()

    requisitos = (answers_by_slug.get("meets_requirements") or "").strip().lower()
    disponibilidad = (answers_by_slug.get("availability_ok") or "").strip().lower()

    passes_requisitos = "sí" in requisitos or "si" in requisitos or requisitos == "yes"
    passes_disponibilidad = "sí" in disponibilidad or "si" in disponibilidad or disponibilidad == "yes"

    if passes_requisitos and passes_disponibilidad:
        # Link to your second form (adjust if you want tokenized URLs)
        form2_link = request.build_absolute_uri(reverse("preview_mentora_second"))

        html_body = (
            '<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;margin:0 auto;word-break:break-word;white-space:normal;">'
            '<p><strong>Querida aplicante a Mentora,</strong></p>'
            '<p>Gracias por completar la primera aplicación para ser mentora en Club Emprendo. 🌱</p>'
            '<p>Con base en tus respuestas, confirmamos que cumples con los requisitos y la disponibilidad necesaria, por lo que estás habilitada para continuar con el proceso.</p>'
            '<p>A continuación, te compartimos la <strong>Aplicación #2</strong>, que es el segundo y último paso para postularte como mentora voluntaria.</p>'
            '<p><strong>📌 Instrucciones para acceder a la Aplicación #2:</strong></p>'
            '<ol>'
            f'<li>Haz clic aquí: 👉 <a href="{form2_link}">Aplicación 2</a></li>'
            '<li>Lee con atención y responde cada pregunta.</li>'
            '</ol>'
            '<p>📅 <strong>Fecha límite para completarlo:</strong> Domingo 7 de Septiembre.</p>'
            '<p>📩 Una vez completes esta segunda aplicación, evaluaremos tu postulación y te contactaremos por correo electrónico en las próximas semanas para informarte si has sido seleccionada como mentora para este grupo. Te invitamos a estar atenta a tu bandeja de entrada.</p>'
            '<p>Gracias nuevamente por tu interés y compromiso con otras mujeres emprendedoras 💛</p>'
            '<p>Con cariño,<br><strong>El equipo de Club Emprendo</strong></p>'
            '</div>'
        )
        _send_html_email(email, APPROVED_SUBJECT, html_body)
        return "Aprobado"

    else:
        html_body = (
            '<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;margin:0 auto;word-break:break-word;white-space:normal;">'
            '<p>Querida aplicante a mentora,</p>'
            '<p>Gracias por tu interés en ser parte del programa de mentoría de Club Emprendo. Valoramos profundamente tu deseo de donar tu tiempo y experiencia para apoyar a otras mujeres emprendedoras en su camino. 💛</p>'
            '<p>En la aplicación que completaste, indicaste que actualmente no cumples con uno o más de los requisitos fundamentales o con la disponibilidad necesaria para participar en esta cohorte. Por esa razón, en este momento no podremos enviarte la segunda y última parte del proceso de aplicación.</p>'
            '<p>📌 <strong>Los requisitos esenciales para ser mentora son:</strong></p>'
            '<ul>'
            '<li>Ser mujer</li>'
            '<li>Tener experiencia en emprender o trabajar en negocios de alguna forma</li>'
            '<li>Ser puntual</li>'
            '<li>Tener conexión a internet estable</li>'
            '<li>Estar dispuesta a completar una capacitación previa al programa</li>'
            '<li>Estar dispuesta a responder 3 encuestas de retroalimentación durante el proceso</li>'
            '</ul>'
            '<p>✨ Si por alguna razón marcaste alguna respuesta por error, o si tus circunstancias cambian en los próximos días, puedes volver a completar la aplicación antes de la fecha límite y con gusto la revisaremos nuevamente.</p>'
            '<p>Sabemos que cada etapa de la vida es distinta y que a veces no es el momento adecuado. Agradecemos profundamente tu intención de sumarte, y si en el futuro decides postularte nuevamente, estaremos felices de recibirte.</p>'
            '<p>Con cariño,<br><strong>El equipo de Club Emprendo</strong></p>'
            '</div>'
        )
        _send_html_email(email, REJECTED_SUBJECT, html_body)
        return "Rechazado"
