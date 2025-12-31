from django.core.management.base import BaseCommand
from applications.models import FormDefinition, Question, Choice


class Command(BaseCommand):
    help = "Build master E_A2 (Emprendedoras application #2) with all questions/choices."

    def handle(self, *args, **options):
        fd, _ = FormDefinition.objects.get_or_create(
            slug="E_A2",
            defaults={"name": "Aplicación para emprendedoras (Aplicación 2)"},
        )

        fd.name = "Aplicación para emprendedoras (Aplicación 2)"
        fd.description = (
            "Hola desde el equipo de Club Emprendo!\n"
            "Esta aplicación está diseñada para identificar microemprendedoras interesadas en participar en nuestro programa de mentoría.\n\n"
            "📌 Duración del programa: 3 meses (#(month)-#(month) #(year))\n"
            "📌 Frecuencia de reuniones: Reuniones semanales de mentoría, con reuniones grupales periódicas\n"
            "📌 Beneficios: Apoyo personalizado y asesoramiento para ayudarte a crear una visión para tu vida y negocio, acceso a cursos (Certificados) y recursos, comunidad de apoyo\n"
            "📌 Requisitos: Ser mujer, vivir en Latino America, tener un emprendimiento existente, y comprometerte a 3 horas a la semana durante 3 meses\n\n"
            "Por favor, completa el siguiente formulario para que podamos entender mejor tus necesidades y cómo podemos potencialmente emparejarte con una mentora adecuada."
        )
        fd.is_master = True
        fd.is_public = False  # A2 normalmente via link/token
        fd.group = None
        fd.save()

        # Idempotente: borramos preguntas del form y recreamos
        Question.objects.filter(form=fd).delete()

        pos = 1

        def add_short(text, slug, required=True, help_text=""):
            nonlocal pos
            Question.objects.create(
                form=fd,
                text=text,
                slug=slug,
                field_type=Question.SHORT_TEXT,
                required=required,
                help_text=help_text,
                position=pos,
                active=True,
            )
            pos += 1

        def add_long(text, slug, required=True, help_text=""):
            nonlocal pos
            Question.objects.create(
                form=fd,
                text=text,
                slug=slug,
                field_type=Question.LONG_TEXT,
                required=required,
                help_text=help_text,
                position=pos,
                active=True,
            )
            pos += 1

        def add_choice(text, slug, choices, required=True, help_text=""):
            nonlocal pos
            q = Question.objects.create(
                form=fd,
                text=text,
                slug=slug,
                field_type=Question.CHOICE,
                required=required,
                help_text=help_text,
                position=pos,
                active=True,
            )
            for i, (value, label) in enumerate(choices, start=1):
                Choice.objects.create(question=q, value=value, label=label, position=i)
            pos += 1

        def add_multi(text, slug, choices, required=True, help_text=""):
            nonlocal pos
            q = Question.objects.create(
                form=fd,
                text=text,
                slug=slug,
                field_type=Question.MULTI_CHOICE,
                required=required,
                help_text=help_text,
                position=pos,
                active=True,
            )
            for i, (value, label) in enumerate(choices, start=1):
                Choice.objects.create(question=q, value=value, label=label, position=i)
            pos += 1

        # ---------- Información personal ----------
        add_short(
            "¿Cuál es tu número de cédula? (documento de identidad)",
            "cedula",
            required=True,
            help_text=(
                "Solicitamos tu número de cédula únicamente para identificar de forma única tu postulación y evitar aplicaciones duplicadas.\n"
                "Tu información será utilizada exclusivamente para fines administrativos del programa de mentoría y tratada con estricta confidencialidad, "
                "conforme a la legislación de protección de datos personales vigente en tu país."
            ),
        )

        add_short("Nombre completo", "full_name", required=True)

        # Solo 1 correo en todo el form:
        add_short("Correo electrónico", "email", required=True)

        add_short(
            "Numero de Whatsapp (Con indicativo de pais ej: +57 para Colombia)",
            "whatsapp",
            required=True,
        )
        add_short("Ciudad de residencia", "city_residence", required=True)
        add_short("País de residencia", "country_residence", required=True)

        add_choice(
            "Edad",
            "age_range",
            choices=[
                ("18_24", "18-24"),
                ("25_34", "25-34"),
                ("35_44", "35-44"),
                ("45_54", "45-54"),
                ("55_plus", "55+"),
                ("other", "Otra"),
            ],
            required=True,
        )

        add_multi(
            "¿Has participado anteriormente en Club Emprendo? (Puedes seleccionar más de una opción)",
            "participated_before",
            choices=[
                ("yes_entrepreneur", "Sí, como emprendedora"),
                ("yes_mentor", "Sí, como mentora"),
                ("no_first_time", "No, sería mi primera vez"),
                ("other", "Otros"),
            ],
            required=True,
        )

        add_multi(
            "Acepto que los datos proporcionados sean tratados conforme al aviso de privacidad.",
            "privacy_accept",
            choices=[("accept", "Acepto")],
            required=True,
            help_text=(
                "🛡 Aviso de privacidad:\n"
                "Club Emprendo recopila datos personales limitados, como tu nombre y número de cédula, con fines administrativos relacionados con el proceso de postulación.\n"
                "Nos comprometemos a tratar esta información de forma confidencial, segura y conforme a las leyes de protección de datos aplicables en América Latina.\n"
                "Puedes ejercer tus derechos de acceso, corrección o eliminación de datos escribiéndonos a: contacto@clubemprendo.org"
            ),
        )

        # ---------- Información del emprendimiento ----------
        add_short("Nombre de tu emprendimiento", "business_name", required=True)

        add_choice(
            "Industria de tu emprendimiento:",
            "industry",
            choices=[
                ("products", "Productos (ropa, artesanías, cosmética, etc.)"),
                ("services", "Servicios (consultoría, turismo, marketing, etc.)"),
                ("tech", "Tecnología (apps, software, etc.)"),
                ("other", "Otros"),
            ],
            required=True,
        )

        add_long("Descripción del negocio:", "business_description", required=True)

        add_choice(
            "Edad del negocio:",
            "business_age",
            choices=[
                ("idea", "Idea en desarrollo"),
                ("lt_1y", "Recién lanzado (menos de 1 año)"),
                ("1_3y", "En crecimiento (1-3 años)"),
                ("4_6y", "Establecido (4-6 años)"),
                ("7_10y", "Maduro (7-10 años)"),
                ("gt_10y", "Mas de 10 años"),
            ],
            required=True,
        )

        add_choice(
            "¿Tienes empleados?",
            "has_employees",
            choices=[
                ("yes", "Sí, empleo a una o más personas (además de mí)"),
                ("no", "No, trabajo sola"),
            ],
            required=True,
        )

        # ---------- Motivación y compromiso ----------
        add_long(
            "¿Cómo crees que este programa puede ayudarte a crecer como emprendedora?",
            "growth_how",
            required=True,
            help_text=(
                "💡 Tip importante:\n"
                "En las preguntas abiertas, te recomendamos que seas lo más amplia posible al compartir tu experiencia, motivaciones y visión. 📝 ✨\n"
                "Evita responder solo con una o dos frases — ¡queremos conocerte mejor!"
            ),
        )

        add_long(
            "¿Cuál es tu mayor desafío actualmente como emprendedora y cómo lo estás abordando?",
            "biggest_challenge",
            required=True,
        )

        add_choice(
            "¿Estás dispuesta a comprometerte a asistir a reuniones de mentoría semanales durante los 3 meses completos?",
            "commit_3_months",
            choices=[
                ("yes", "Sí, estoy comprometida a completar el programa."),
                ("unsure", "No estoy segura."),
                ("no", "No, no puedo comprometerme en este momento."),
            ],
            required=True,
        )

        add_choice(
            "¿Cuánto tiempo puedes dedicar al programa semanalmente? (Estudio personal y reuniones)",
            "hours_per_week",
            choices=[
                ("lt_2", "Menos de 2 horas"),
                ("2_4", "Entre 2 y 4 horas"),
                ("gt_4", "Más de 4 horas"),
            ],
            required=True,
        )

        add_choice(
            "¿Tienes alguna experiencia previa con mentoría para tu empresa?",
            "prior_mentoring",
            choices=[("yes", "Sí"), ("no", "No")],
            required=True,
        )

        add_multi(
            "¿Revisaste el PDF (enlace abajo) que ofrece una breve introducción al programa de mentoría de Club Emprendo?",
            "reviewed_pdf",
            choices=[("yes", "Sí")],
            required=True,
            help_text="PDF",
        )

        # ---------- Disponibilidad y acceso ----------
        add_choice(
            "¿Tienes acceso a internet y un dispositivo (computadora o celular) para participar en reuniones virtuales?",
            "internet_access",
            choices=[
                ("yes_ok", "Sí, sin problemas."),
                ("yes_some", "Sí, pero con algunas dificultades."),
                ("no", "No, tengo problemas de acceso."),
            ],
            required=True,
        )

        add_multi(
            "¿En qué horario te resulta más conveniente participar en sesiones virtuales? (Selecciona todas las opciones que correspondan)",
            "preferred_schedule",
            choices=[
                ("mon_morning", "Lunes - Mañana"),
                ("mon_afternoon", "Lunes - Tarde"),
                ("mon_night", "Lunes - Noche"),
                ("tue_morning", "Martes - Mañana"),
                ("tue_afternoon", "Martes - Tarde"),
                ("tue_night", "Martes - Noche"),
                ("wed_morning", "Miercoles - Mañana"),
                ("wed_afternoon", "Miercoles - Tarde"),
                ("wed_night", "Miercoles - Noche"),
                ("thu_morning", "Jueves - Mañana"),
                ("thu_afternoon", "Jueves - Tarde"),
                ("thu_night", "Jueves - Noche"),
                ("fri_morning", "Viernes - Mañana"),
                ("fri_afternoon", "Viernes - Tarde"),
                ("fri_night", "Viernes - Noche"),
                ("sat_morning", "Sabado - Mañana"),
                ("sat_afternoon", "Sabado - Tarde"),
                ("sat_night", "Sabado - Noche"),
                ("sun_morning", "Domingo - Mañana"),
                ("sun_afternoon", "Domingo - Tarde"),
                ("sun_night", "Domingo - Noche"),
            ],
            required=True,
        )

        add_long(
            "¿Te gustaría dejarnos algún comentario, duda o sugerencia adicional? (Este espacio es opcional, pero siempre estamos felices de leerte.)",
            "additional_comments",
            required=False,
        )

        self.stdout.write(self.style.SUCCESS("✅ Built master E_A2 successfully."))
