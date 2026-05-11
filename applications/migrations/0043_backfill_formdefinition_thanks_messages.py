from django.db import migrations


A1_APPROVED_MESSAGE = (
    "📩¡Atención!\n"
    "Has terminado la primera aplicación ✅.\n"
    "👉 Ahora recibirás en tu correo electrónico la segunda y última aplicación, que debes completar para continuar en el proceso de postulación.\n\n"
    "⚠️ Muy importante:\n"
    "Revisa que escribiste tu correo correctamente.\n"
    "El correo puede llegar a tu bandeja de entrada o a la carpeta de spam / correo no deseado / promociones.\n"
    "Si no lo ves en los próximos minutos, busca en esas carpetas.\n\n"
    "Si aún así no recibes la segunda aplicación, escríbenos por Instagram y con gusto te ayudamos.\n\n"
    "💡 Consejo: Guarda nuestra dirección de correo como “segura” para que no se pierdan los mensajes."
)

A1_REJECTED_E_MESSAGE = (
    "Lamentamos que en esta ocasión no puedas ser parte del {{ group_label }} de emprendedoras, "
    "ya que en tu aplicación indicaste que no cumples con alguno de los requisitos o la disponibilidad "
    "de tiempo necesaria para el programa.\n\n"
    "Seguiremos abriendo más grupos en el futuro, y nos encantaría que puedas aplicar nuevamente cuando "
    "tu situación lo permita :sparkles:\n\n"
    "¡Seguimos en contacto y te mandamos un abrazo grande!"
)

A1_REJECTED_M_MESSAGE = (
    "Lamentamos que no puedas ser parte del {{ group_label }} de mentoras en esta ocasión, "
    "pero seguiremos abriendo más grupos en el futuro.\n\n"
    "¡Nos encantaría que puedas aplicar más adelante! Seguimos en contacto y te mandamos un abrazo grande."
)

A2_APPROVED_E_TITLE = "¡Gracias! 💛"
A2_APPROVED_E_MESSAGE = (
    "¡Gracias por completar esta última encuesta y brindarnos toda esta información tan valiosa!\n\n"
    "🗓 En unas semanas te estaremos enviando un correo del estatus de tu aplicación junto con más detalles "
    "sobre los siguientes pasos para formar parte del {{ group_label }} de mentorías.\n\n"
    "Agradecemos mucho tu tiempo, tu disposición y tu entusiasmo por ser parte de esta comunidad. "
    "¡Estamos muy emocionadas de tenerte cerca!"
)

A2_REJECTED_E_TITLE = "¡Gracias! 💛"
A2_REJECTED_E_MESSAGE = (
    "Lamentamos que en esta ocasión no puedas ser parte del {{ group_label }} de emprendedoras, "
    "ya que en tu aplicación indicaste que no cumples con alguno de los requisitos o la disponibilidad "
    "de tiempo necesaria para el programa.\n\n"
    "Seguiremos abriendo más grupos en el futuro, y nos encantaría que puedas aplicar nuevamente cuando "
    "tu situación lo permita ✨\n\n"
    "¡Seguimos en contacto y te mandamos un abrazo grande!"
)

A2_APPROVED_M_TITLE = "¡Gracias! 💛"
A2_APPROVED_M_MESSAGE = (
    "¡Gracias por completar esta última aplicación y brindarnos toda esta información tan valiosa!\n\n"
    "🗓 En un plazo máximo de dos semanas te informaremos por correo electrónico si has sido seleccionada "
    "para participar como mentora en el {{ group_label }} de Club Emprendo.\n\n"
    "💌 Te invitamos a estar muy atenta a tu bandeja de entrada (y no olvides revisar la carpeta de spam, por si acaso).\n\n"
    "Agradecemos mucho tu tiempo, tu disposición y tu entusiasmo por ser parte de esta comunidad. "
    "¡Estamos muy emocionadas de tenerte cerca!"
)

A2_REJECTED_M_TITLE = "¡Gracias! 💛"
A2_REJECTED_M_MESSAGE = (
    "Lamentamos que en esta ocasión no puedas ser parte del {{ group_label }} de mentoras, "
    "ya que en tu aplicación indicaste que no cumples con alguno de los requisitos o la disponibilidad "
    "de tiempo necesaria para el programa.\n\n"
    "Seguiremos abriendo más grupos en el futuro, y nos encantaría que puedas aplicar nuevamente cuando "
    "tu situación lo permita :sparkles:\n\n"
    "¡Seguimos en contacto y te mandamos un abrazo grande!"
)


def _defaults_for_slug(slug: str):
    s = (slug or "").strip().upper()
    if s.endswith("E_A1"):
        return ("", A1_APPROVED_MESSAGE, "", A1_REJECTED_E_MESSAGE)
    if s.endswith("M_A1"):
        return ("", A1_APPROVED_MESSAGE, "", A1_REJECTED_M_MESSAGE)
    if s.endswith("E_A2"):
        return (A2_APPROVED_E_TITLE, A2_APPROVED_E_MESSAGE, A2_REJECTED_E_TITLE, A2_REJECTED_E_MESSAGE)
    if s.endswith("M_A2"):
        return (A2_APPROVED_M_TITLE, A2_APPROVED_M_MESSAGE, A2_REJECTED_M_TITLE, A2_REJECTED_M_MESSAGE)
    return None


def populate_thanks_fields(apps, schema_editor):
    FormDefinition = apps.get_model("applications", "FormDefinition")

    for fd in FormDefinition.objects.all().iterator():
        defaults = _defaults_for_slug(getattr(fd, "slug", "") or "")
        if not defaults:
            continue

        approved_title, approved_message, rejected_title, rejected_message = defaults
        updates = []

        if not (getattr(fd, "thanks_approved_title", "") or "").strip() and approved_title:
            fd.thanks_approved_title = approved_title
            updates.append("thanks_approved_title")
        if not (getattr(fd, "thanks_approved_message", "") or "").strip() and approved_message:
            fd.thanks_approved_message = approved_message
            updates.append("thanks_approved_message")
        if not (getattr(fd, "thanks_rejected_title", "") or "").strip() and rejected_title:
            fd.thanks_rejected_title = rejected_title
            updates.append("thanks_rejected_title")
        if not (getattr(fd, "thanks_rejected_message", "") or "").strip() and rejected_message:
            fd.thanks_rejected_message = rejected_message
            updates.append("thanks_rejected_message")

        if updates:
            fd.save(update_fields=updates)


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0042_formdefinition_thanks_message_fields"),
    ]

    operations = [
        migrations.RunPython(populate_thanks_fields, migrations.RunPython.noop),
    ]
