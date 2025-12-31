from django.core.management.base import BaseCommand
from applications.models import FormDefinition, Question, Choice


def upsert_form(slug: str, name: str, description: str, is_master=True, is_public=True):
    fd, _ = FormDefinition.objects.get_or_create(slug=slug, defaults={"name": name})
    fd.name = name
    fd.description = description
    fd.is_master = is_master
    fd.is_public = is_public
    fd.group = None  # master sin grupo
    fd.save()
    return fd


class Command(BaseCommand):
    help = "Build master E_A1 (Emprendedoras application #1) with all questions/choices."

    def handle(self, *args, **options):
        slug = "E_A1"
        name = "Aplicación para emprendedoras (Aplicación 1)"
        description = (
            "¡Hola del equipo del Club Emprendo!\n"
            "Gracias por tu interés en postular para recibir mentoría en nuestro programa 100% virtual, "
            "diseñado específicamente para mujeres emprendedoras en América Latina. 🫶\n\n"
            "✨ Esta app está dirigida a microempresarias que quieran participar como beneficiarias del programa de mentoría, "
            "completamente gratis gracias al trabajo voluntario de nuestras mentoras.\n\n"
            "🗓 Formarías parte del Grupo #(group number), que durará de #(month) a #(month) de #(year).\n"
            "🤝 Los participantes participarán en reuniones virtuales semanales individuales con una mentora, "
            "así como en sesiones grupales regulares.\n"
            "🎁 Los beneficios incluyen coaching personalizado; herramientas para crear una visión clara para tu vida y negocio; "
            "y acceso a recursos, cursos y una comunidad de apoyo.\n\n"
            "Asegúrate de escribir bien tu correo electrónico, sin errores, porque allí recibirás los pasos a seguir y toda la información importante."
        )

        fd = upsert_form(slug, name, description, is_master=True, is_public=True)

        # idempotente: borra preguntas existentes de este formulario
        Question.objects.filter(form=fd).delete()

        pos = 1

        def q_short(text, slug, required=True, help_text=""):
            nonlocal pos
            q = Question.objects.create(
                form=fd,
                text=text,
                help_text=help_text,
                field_type=Question.SHORT_TEXT,
                required=required,
                position=pos,
                slug=slug,
                active=True,
            )
            pos += 1
            return q

        def q_choice(text, slug, choices, required=True, help_text=""):
            nonlocal pos
            q = Question.objects.create(
                form=fd,
                text=text,
                help_text=help_text,
                field_type=Question.CHOICE,
                required=required,
                position=pos,
                slug=slug,
                active=True,
            )
            for i, (value, label) in enumerate(choices, start=1):
                Choice.objects.create(question=q, value=value, label=label, position=i)
            pos += 1
            return q

        def q_long(text, slug, required=False, help_text=""):
            nonlocal pos
            q = Question.objects.create(
                form=fd,
                text=text,
                help_text=help_text,
                field_type=Question.LONG_TEXT,
                required=required,
                position=pos,
                slug=slug,
                active=True,
            )
            pos += 1
            return q

        # ---- Preguntas “Información de contacto” ----
        # IMPORTANTE: Aquí solo hay 1 correo.
        q_short("Correo electrónico", "email", required=True)
        q_short("Nombre completo", "full_name", required=True)
        q_short("País donde resides", "country_residence", required=True)
        q_short("Numero de Whatsapp (con indicativo de país ej: +57 para Colombia)", "whatsapp", required=True)

        # ---- Confirmación de requisitos ----
        q_choice(
            "¿Cumples todos estos requisitos enumerados anteriormente?",
            "meets_requirements",
            choices=[
                ("yes", "Sí, cumplo con todos los requisitos."),
                ("no", "No, no cumplo con todos los requisitos."),
            ],
            required=True,
            help_text=(
                "📌 Requisitos generales\n"
                "✔ Ser mujer\n"
                "✔ Vivir en Latinoamérica\n"
                "✔ Tener conexión estable a internet\n"
                "✔ Tener un emprendimiento en marcha (no sólo una idea)\n"
                "✔ Ser puntual\n"
                "✔ Estar dispuesta a completar una capacitación previa al inicio del programa\n"
                "✔ Estar dispuesta a responder 4 encuestas de retroalimentación a lo largo del proceso"
            ),
        )

        # ---- Disponibilidad ----
        q_choice(
            "¿Estás de acuerdo y disponible para participar en el periodo de #(month) a #(month) #(year), por 3 horas a la semana?",
            "available_period",
            choices=[
                ("yes", "Sí, estoy de acuerdo y disponible"),
                ("no", "No, en este momento no puedo comprometerme"),
            ],
            required=True,
            help_text=(
                "📌 Duración: De #(month) a #(month) #(year) (12 semanas en total)\n"
                "📌 Compromiso de tiempo: 3 horas a la semana durante 12 semanas (aproximadamente)"
            ),
        )

        # ---- Emprendimiento ----
        q_choice(
            "¿Actualmente tienes un emprendimiento en funcionamiento? (No se considera una idea de negocio o un proyecto detenido hace tiempo)",
            "business_active",
            choices=[
                ("yes", "Sí, mi emprendimiento está activo actualmente"),
                ("no", "No, solo tengo una idea o el emprendimiento no está funcionando por ahora"),
            ],
            required=True,
        )

        # ---- Comentarios (solo si no cumple / no disponible / no activo) ----
        q_long(
            "Si estás dispuesta, por favor indícanos qué requisito(s) no cumpliste para participar en este programa de mentoría. También puedes compartir cualquier otro comentario que desees.",
            "comments",
            required=False,
        )

        self.stdout.write(self.style.SUCCESS("✅ Built master E_A1 successfully."))
