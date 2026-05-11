from django.db import migrations


A1_APPROVED_SUBJECT_E = "Próximo paso para recibir mentorías 💛"
A1_APPROVED_BODY_E = (
    '<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;'
    'margin:0 auto;word-break:break-word;white-space:normal;">'
    "<p>Hola,</p>"
    "<p>Gracias por completar la primera aplicación para participar en nuestro programa de mentoría como emprendedora. "
    "Nos alegra contarte que, según tus respuestas, cumples con los requisitos y la disponibilidad necesaria, por lo que "
    "puedes avanzar al siguiente paso. 🌟</p>"
    "<p>A continuación, te compartimos la <strong>Aplicación #2</strong>, que es el último paso del proceso de postulación.</p>"
    "<p><strong>📌 Instrucciones para completar la Aplicación #2:</strong></p>"
    "<ul>"
    '<li>👉 <a href="{{ a2_link }}">Haz clic aquí para completar la Aplicación #2</a> - Fecha límite: {{ deadline_text }}</li>'
    "</ul>"
    "<p>Con cariño,<br><strong>El equipo de Club Emprendo</strong></p>"
    "</div>"
)
A1_REJECTED_SUBJECT_E = "Sobre tu aplicación al programa de mentoría de Club Emprendo 💛"
A1_REJECTED_BODY_E = (
    '<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;'
    'margin:0 auto;word-break:break-word;white-space:normal;">'
    "<p>Querida emprendedora,</p>"
    "<p>Gracias por tu interés en participar en nuestro programa de mentoría de Club Emprendo. 🌱</p>"
    "<p>En la aplicación indicaste que no cumples actualmente con uno o más requisitos fundamentales "
    "o con la disponibilidad necesaria para participar en esta cohorte, por eso no podremos enviarte el paso 2.</p>"
    "<p>Con cariño,<br><strong>El equipo de Club Emprendo</strong></p>"
    "</div>"
)

A1_APPROVED_SUBJECT_M = "Siguiente paso: Completa la segunda solicitud"
A1_APPROVED_BODY_M = (
    '<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;margin:0 auto;word-break:break-word;white-space:normal;">'
    "<p><strong>Querida aplicante a Mentora,</strong></p>"
    "<p>Gracias por completar la primera aplicación para ser mentora en Club Emprendo. 🌱</p>"
    "<p>Con base en tus respuestas, confirmamos que cumples con los requisitos y la disponibilidad necesaria, por lo que estás habilitada para continuar con el proceso.</p>"
    "<p>A continuación, te compartimos la <strong>Aplicación #2</strong>, que es el segundo y último paso para postularte como mentora voluntaria.</p>"
    "<p><strong>📌 Instrucciones para acceder a la Aplicación #2:</strong></p>"
    "<ol>"
    '<li>Haz clic aquí: 👉 <a href="{{ a2_link }}">Aplicación 2</a> — Fecha límite: {{ deadline_text }}</li>'
    "<li>Lee con atención y responde cada pregunta.</li>"
    "</ol>"
    "<p>Gracias nuevamente por tu interés y compromiso con otras mujeres emprendedoras 💛</p>"
    "<p>Con cariño,<br><strong>El equipo de Club Emprendo</strong></p>"
    "</div>"
)
A1_REJECTED_SUBJECT_M = "Sobre tu aplicación como mentora voluntaria 🌟"
A1_REJECTED_BODY_M = (
    '<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;margin:0 auto;word-break:break-word;white-space:normal;">'
    "<p>Querida aplicante a mentora,</p>"
    "<p>Gracias por tu interés en ser parte del programa de mentoría de Club Emprendo. 💛</p>"
    "<p>En la aplicación indicaste que no cumples uno o más requisitos o disponibilidad para esta cohorte, por eso no podremos enviarte el paso 2.</p>"
    "<p>Con cariño,<br><strong>El equipo de Club Emprendo</strong></p>"
    "</div>"
)

A1_TO_A2_REMINDER_SUBJECT = "Recordatorio: completa tu segunda aplicación"
A1_TO_A2_REMINDER_BODY = (
    '<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;margin:0 auto;">'
    "<p>Hola,</p>"
    "<p>"
    "Queremos recordarte que según tu primera aplicación fuiste invitada a continuar al segundo paso del "
    "proceso para participar como <strong>{{ role_word }}</strong> en Club Emprendo."
    "</p>"
    "<p>Te recordamos que la fecha límite para completar tu aplicación es el <strong>{{ deadline_text }}</strong>.</p>"
    "<p>"
    "Si aún no has completado la segunda aplicación, puedes hacerlo aquí:"
    '👉 <a href="{{ a2_link }}">{{ a2_link }}</a>'
    "</p>"
    "<p>Con cariño,<br><strong>Equipo Club Emprendo</strong></p>"
    "</div>"
)

A2_REJECTED_SUBJECT = "Sobre tu aplicación al Programa de Mentorías"
A2_REJECTED_BODY = (
    '<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;margin:0 auto;word-break:break-word;white-space:normal;">'
    "<p>Hola querida aplicante,</p>"
    "<p>Gracias por tomarte el tiempo de completar la segunda aplicación para nuestro programa de mentorías. "
    "Valoramos muchísimo tu interés en ser parte de Club Emprendo y el deseo que tienes de crecer y aprender.</p>"
    "<p>Después de revisar cuidadosamente tu información, queremos contarte que en esta ocasión no pudimos seleccionarte para este grupo. "
    "Esto se debe a que, según tu aplicación, actualmente se presenta al menos una de estas situaciones:</p>"
    "<ul>"
    "<li>Cuentas con menos de 2 horas de disponibilidad semanal para las mentorías.</li>"
    "<li>Tienes dificultades con la conexión a internet, lo cual es clave para poder comunicarse con tu mentora.</li>"
    "<li>Tu emprendimiento se encuentra aún en etapa de idea y no está en marcha. (si estás aplicando para recibir las mentorías)</li>"
    "<li>No cumples con los requisitos o disponibilidad.</li>"
    "</ul>"
    "<p>Para que el proceso de mentoría sea realmente efectivo y beneficioso para ti, en este grupo necesitamos que las emprendedoras "
    "cuenten con más de 2 horas de disponibilidad, buena conexión a internet y un emprendimiento ya en funcionamiento.</p>"
    "<p>La buena noticia es que, si en el futuro estas condiciones cambian, puedes volver a aplicar sin ningún problema.</p>"
    "<p>Gracias por confiar en Club Emprendo y por dar este primer paso. Te enviamos un abrazo grande y mucho ánimo en tu camino emprendedor.</p>"
    "<p>Con cariño,<br><strong>Equipo Club Emprendo</strong></p>"
    "</div>"
)

A2_RECEIVED_SUBJECT = "Hemos recibido tu aplicación – Programa de Mentorías"
A2_RECEIVED_BODY_E = (
    '<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;margin:0 auto;word-break:break-word;white-space:normal;">'
    "<p>Hola querida aplicante ✨</p>"
    "<p>Gracias por completar tu aplicación para recibir mentoría en nuestro Programa de Mentorías.</p>"
    "<p>Tu aplicación ha sido enviada correctamente y no necesitas realizar ninguna acción adicional por ahora.</p>"
    "<p>En la fecha indicada dentro de la aplicación, recibirás un correo electrónico en esta misma dirección únicamente si eres seleccionada, "
    "con los siguientes pasos a seguir.</p>"
    "<p>Te recomendamos estar pendiente de tu correo, incluyendo la bandeja de spam o promociones, para no perder esta información importante.</p>"
    "<p>Si eres seleccionada, deberás completar dentro de la fecha límite que se te indicará:</p>"
    "<ul>"
    "<li>✅ Firmar el Acta de Compromiso</li>"
    "<li>✅ Completar la capacitación</li>"
    "</ul>"
    "<p>Estos pasos son necesarios para poder asignarte una mentora y participar en la reunión de lanzamiento e inicio de mentorías.</p>"
    "<p>Gracias por tu interés en ser parte de esta comunidad 💗</p>"
    "<p>Con gratitud,<br><strong>Equipo de Club Emprendo</strong></p>"
    "</div>"
)
A2_RECEIVED_BODY_M = (
    '<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;margin:0 auto;word-break:break-word;white-space:normal;">'
    "<p>Hola querida aplicante ✨</p>"
    "<p>Gracias por completar tu aplicación para ser mentora en nuestro Programa de Mentorías.</p>"
    "<p>Tu aplicación ha sido enviada correctamente y no necesitas realizar ninguna acción adicional por ahora.</p>"
    "<p>En la fecha indicada dentro de la aplicación, recibirás un correo electrónico en esta misma dirección únicamente si eres seleccionada, "
    "con los siguientes pasos a seguir.</p>"
    "<p>Te recomendamos estar pendiente de tu correo, incluyendo la bandeja de spam o promociones, para no perder esta información importante.</p>"
    "<p>Si eres seleccionada, deberás completar dentro de la fecha límite que se te indicará:</p>"
    "<ul>"
    "<li>✅ Firmar el Acta de Compromiso</li>"
    "<li>✅ Completar la capacitación</li>"
    "</ul>"
    "<p>Estos pasos son necesarios para poder asignarte una mentora y participar en la reunión de lanzamiento e inicio de mentorías.</p>"
    "<p>Gracias por tu interés en ser parte de esta comunidad 💗</p>"
    "<p>Con gratitud,<br><strong>Equipo de Club Emprendo</strong></p>"
    "</div>"
)

A2_FINAL_REMINDER_SUBJECT = "Últimos días para completar la segunda aplicación"
A2_FINAL_REMINDER_BODY = (
    '<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;margin:0 auto;">'
    "<p>Hola,</p>"
    "<p>Esperamos que te encuentres muy bien.</p>"
    "<p>"
    "Queremos recordarte que, según la primera aplicación que completaste, cumples con el perfil para ser "
    "<strong>{{ role_word }}</strong>, y nos encantaría que continúes con el proceso."
    "</p>"
    "<p>"
    "Te recordamos que es necesario completar la segunda aplicación, ya que la fecha límite es el "
    "<strong>{{ deadline_text }}</strong>. Estamos en los últimos días para aplicar."
    "</p>"
    "<p>A continuación, te dejamos nuevamente el enlace y las instrucciones:</p>"
    "<ol>"
    '<li>Haz clic en el enlace: 👉 <a href="{{ a2_link }}">{{ a2_link }}</a></li>'
    "<li>Responde las preguntas (no toma más de 10 minutos).</li>"
    "<li>Haz clic en <strong>Enviar</strong> para completar tu aplicación.</li>"
    "</ol>"
    "<p>"
    "Tu participación es muy valiosa para nosotras, y esperamos contar contigo en esta nueva etapa del programa. "
    "Si tienes alguna pregunta o inconveniente, no dudes en escribirnos."
    "</p>"
    "<p>Con cariño,<br><strong>Melanie Guzmán</strong></p>"
    "</div>"
)


def _defaults_for_slug(slug: str):
    s = (slug or "").strip().upper()
    if s.endswith("E_A1"):
        return {
            "email_a1_approved_subject": A1_APPROVED_SUBJECT_E,
            "email_a1_approved_body": A1_APPROVED_BODY_E,
            "email_a1_rejected_subject": A1_REJECTED_SUBJECT_E,
            "email_a1_rejected_body": A1_REJECTED_BODY_E,
            "email_a1_to_a2_reminder_subject": A1_TO_A2_REMINDER_SUBJECT,
            "email_a1_to_a2_reminder_body": A1_TO_A2_REMINDER_BODY,
        }
    if s.endswith("M_A1"):
        return {
            "email_a1_approved_subject": A1_APPROVED_SUBJECT_M,
            "email_a1_approved_body": A1_APPROVED_BODY_M,
            "email_a1_rejected_subject": A1_REJECTED_SUBJECT_M,
            "email_a1_rejected_body": A1_REJECTED_BODY_M,
            "email_a1_to_a2_reminder_subject": A1_TO_A2_REMINDER_SUBJECT,
            "email_a1_to_a2_reminder_body": A1_TO_A2_REMINDER_BODY,
        }
    if s.endswith("E_A2"):
        return {
            "email_a2_received_subject": A2_RECEIVED_SUBJECT,
            "email_a2_received_body": A2_RECEIVED_BODY_E,
            "email_a2_rejected_subject": A2_REJECTED_SUBJECT,
            "email_a2_rejected_body": A2_REJECTED_BODY,
            "email_a2_final_reminder_subject": A2_FINAL_REMINDER_SUBJECT,
            "email_a2_final_reminder_body": A2_FINAL_REMINDER_BODY,
        }
    if s.endswith("M_A2"):
        return {
            "email_a2_received_subject": A2_RECEIVED_SUBJECT,
            "email_a2_received_body": A2_RECEIVED_BODY_M,
            "email_a2_rejected_subject": A2_REJECTED_SUBJECT,
            "email_a2_rejected_body": A2_REJECTED_BODY,
            "email_a2_final_reminder_subject": A2_FINAL_REMINDER_SUBJECT,
            "email_a2_final_reminder_body": A2_FINAL_REMINDER_BODY,
        }
    return None


def populate_email_templates(apps, schema_editor):
    FormDefinition = apps.get_model("applications", "FormDefinition")

    for fd in FormDefinition.objects.all().iterator():
        defaults = _defaults_for_slug(getattr(fd, "slug", "") or "")
        if not defaults:
            continue

        updates = []
        for field_name, default_value in defaults.items():
            current = getattr(fd, field_name, "") or ""
            if not current.strip() and default_value:
                setattr(fd, field_name, default_value)
                updates.append(field_name)

        if updates:
            fd.save(update_fields=updates)


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0044_formdefinition_email_a1_approved_body_and_more"),
    ]

    operations = [
        migrations.RunPython(populate_email_templates, migrations.RunPython.noop),
    ]
