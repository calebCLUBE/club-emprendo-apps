# applications/management/commands/build_master_m_a2.py
from django.core.management.base import BaseCommand
from applications.models import FormDefinition, Question, Choice


def upsert_form(slug: str, name: str, description: str, is_master=True, is_public=False):
    fd, _ = FormDefinition.objects.get_or_create(slug=slug, defaults={"name": name})
    fd.name = name
    fd.description = description
    fd.is_master = is_master
    fd.is_public = is_public
    fd.group = None  # master has no group
    fd.save()
    return fd


def add_choice_yes_no(q: Question):
    Choice.objects.get_or_create(question=q, value="yes", defaults={"label": "Sí", "position": 1})
    Choice.objects.get_or_create(question=q, value="no", defaults={"label": "No", "position": 2})


class Command(BaseCommand):
    help = "Build master M_A2 (Mentora application #2) with all questions/choices."

    def handle(self, *args, **options):
        slug = "M_A2"
        name = "Solicitud para ser MENTORA de Club Emprendo (Aplicación 2)"

        description = (
            "¡Hola desde el equipo de Club Emprendo!\n\n"
            "Gracias por tu interés en ser MENTORA de Club Emprendo. Recibiste esta solicitud porque completaste nuestra solicitud inicial.\n"
            "Ahora, esta solicitud es un poco más amplia y nos ayudará a determinar si eres una buena candidata para nuestro programa.\n\n"
            "📌 ¿En qué consiste?: Ofrecer apoyo personalizado y asesoramiento para ayudarles a emprendedoras a crear una visión para sus vidas y negocios, "
            "crecer sus negocios, y superar los desafíos.\n"
            "📌 Duración del programa: 3 meses (#(month) a #(month) de #(year)).\n"
            "📌 Frecuencia: Reuniones virtuales semanales.\n\n"
            "¡Lo más importante será tu capacidad de hacer preguntas y ser un socio responsable – NO saber las respuestas a todo!"
        )

        fd = upsert_form(slug, name, description, is_master=True, is_public=False)

        # Make idempotent: wipe existing questions for this form
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

        def q_long(text, slug, required=True, help_text=""):
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

        def q_multi(text, slug, choices, required=True, help_text=""):
            nonlocal pos
            q = Question.objects.create(
                form=fd,
                text=text,
                help_text=help_text,
                field_type=Question.MULTI_CHOICE,
                required=required,
                position=pos,
                slug=slug,
                active=True,
            )
            for i, (value, label) in enumerate(choices, start=1):
                Choice.objects.create(question=q, value=value, label=label, position=i)
            pos += 1
            return q

        # --- Información personal ---
        q_short(
            "¿Cuál es tu número de cédula? (documento de identidad)",
            "id_number",
            required=True,
            help_text=(
                "Solicitamos tu número de cédula únicamente para identificar de forma única tu postulación y evitar aplicaciones duplicadas. "
                "Tu información será utilizada exclusivamente para fines administrativos del programa."
            ),
        )

        q_short("Nombre completo", "full_name", required=True)
        q_short("Nombre de preferencia (para referirnos a ti en el programa)", "preferred_name", required=True)
        q_short("Si eres seleccionada como mentora, nombre que deberíamos poner en el certificado de voluntariado", "certificate_name", required=False)
        q_short("Correo electrónico", "email", required=True)
        q_short("Numero de Whatsapp (incluir código de país, ejemplo +57 para Colombia)", "whatsapp", required=True)
        q_short("Ciudad de residencia", "city_residence", required=True)
        q_short("País de residencia", "country_residence", required=True)
        q_short("País de nacimiento", "country_birth", required=True)

        q_choice(
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

        q_multi(
            "¿Has participado anteriormente en Club Emprendo? (Puedes seleccionar más de una opción.)",
            "prior_participation",
            choices=[
                ("as_entrepreneur", "Como Emprendedora"),
                ("as_mentor", "Como Mentora"),
                ("first_time", "Sería mi primera vez"),
            ],
            required=True,
        )

        q_multi(
            "🛡 Aviso de privacidad: Acepto que los datos proporcionados sean tratados conforme al aviso de privacidad.",
            "privacy_ack",
            choices=[("accept", "Acepto que los datos proporcionados sean tratados conforme al aviso de privacidad.")],
            required=True,
        )

        # --- Requisitos del programa (matrix as individual yes/no questions) ---
        basic_rows = [
            ("Soy mujer.", "req_basic_woman"),
            ("He vivido / vivo en Latinoamérica.", "req_basic_latam"),
            ("Tengo experiencia en emprender o trabajar en negocios de alguna forma.", "req_basic_business_exp"),
            ("Soy puntual.", "req_basic_punctual"),
            ("Tengo conexión a internet y acceso a un dispositivo (computadora o celular) para poder participar en reuniones virtuales semanales.", "req_basic_internet_device"),
            ("Estoy dispuesta a completar la capacitación previa al programa (de 3 a 4 horas de dedicación).", "req_basic_training"),
            ("Estoy dispuesta a completar tres encuestas de retroalimentación durante el programa.", "req_basic_surveys"),
        ]
        for text, slug_row in basic_rows:
            q = Question.objects.create(
                form=fd,
                text=f"Requisitos básicos: {text}",
                help_text="",
                field_type=Question.CHOICE,
                required=True,
                position=pos,
                slug=slug_row,
                active=True,
            )
            add_choice_yes_no(q)
            pos += 1

        availability_rows = [
            ("Estoy disponible para participar desde (#(month) hasta #(month) de #(year).)", "req_avail_sept_dec"),
            ("Puedo donar al menos 2 horas semanales durante estas 12 semanas, de forma voluntaria (sin pago económico).", "req_avail_2hrs_week"),
            ("Estoy disponible el lunes de (#(mont)) de 2025 para asistir a la reunión de lanzamiento del programa de 1 hora (por la tarde).", "req_avail_kickoff"),
        ]
        for text, slug_row in availability_rows:
            q = Question.objects.create(
                form=fd,
                text=f"Requisitos de disponibilidad: {text}",
                help_text="",
                field_type=Question.CHOICE,
                required=True,
                position=pos,
                slug=slug_row,
                active=True,
            )
            add_choice_yes_no(q)
            pos += 1

        q_multi(
            "Marca la casilla para confirmar tu entendimiento:",
            "volunteer_ack",
            choices=[("ack", "Entiendo que ofrezco estos servicios como voluntaria y que no recibiré ningún pago por ser mentora en Club Emprendo.")],
            required=True,
        )

        q_long("Si no cumples alguno(s) de los requisitos anteriores, especifica cuál(es) y el(los) motivo(s).", "req_explain", required=False)

        q_multi(
            "¿Revisaste el PDF (enlace abajo) que ofrece una breve introducción al programa de mentoría de Club Emprendo?",
            "read_pdf",
            choices=[("yes", "Sí")],
            required=True,
        )

        q_choice("¿Has dirigido tu propio negocio?", "owned_business", choices=[("yes", "Sí"), ("no", "No")], required=True)

        # --- Experiencia como emprendedora ---
        q_short("Nombre de tu emprendimiento", "business_name", required=False)
        q_multi(
            "Industria de tu emprendimiento",
            "business_industry",
            choices=[
                ("agri", "Agricultura"),
                ("food", "Alimentos y bebidas"),
                ("crafts", "Artesanías"),
                ("beauty", "Belleza y cuidado personal"),
                ("retail", "Comercio minorista"),
                ("construction", "Construcción y remodelación"),
                ("education", "Educación y capacitación"),
                ("finance_legal", "Finanzas y servicios legales"),
                ("real_estate", "Inmobiliaria"),
                ("media", "Medios y comunicaciones"),
                ("health", "Salud y bienestar"),
                ("services", "Servicios (ej. limpieza, cuidado de niños, turismo)"),
                ("tech", "Tecnología"),
                ("textiles", "Textiles y ropa"),
                ("transport", "Transporte y logística"),
                ("other", "Otros"),
            ],
            required=False,
        )
        q_long("Descripción del negocio", "business_description", required=False)
        q_short("¿Dónde operas tu negocio (o dónde lo operabas, si ya no está en operación)? (ciudad, país etc.)", "business_location", required=False)

        q_choice(
            "¿Cuánto tiempo has estado operando (o por cuánto tiempo se operó, si ya no está en operación)?",
            "business_years",
            choices=[("0_1", "0-1 año"), ("1_5", "1-5 años"), ("5_10", "5-10 años"), ("10_plus", "10+ años")],
            required=False,
        )

        q_choice(
            "¿Tienes empleados? (o tuviste, si ya no está en operación)?",
            "has_employees",
            choices=[
                ("yes", "Sí, empleo a una o más personas (además de mí)"),
                ("no", "No, trabajo sola"),
            ],
            required=False,
        )

        # --- Motivación y experiencia con la mentoría ---
        q_short(
            "¿Cuál es tu área de experiencia profesional más relevante para la mentoría de mujeres microempresarias? (Ej. Marketing, Finanzas, etc.)",
            "professional_expertise",
            required=True,
        )
        q_long("¿Qué te motiva a ser mentora en este programa de Club Emprendo?", "motivation", required=True)
        q_long(
            "¿Por qué crees que serías una buena mentora para una emprendedora en su proceso de crecimiento personal y profesional?",
            "why_good_mentor",
            required=True,
        )

        # Mentoría/coaching experience as two yes/no questions (since your PDF shows a table)
        q = Question.objects.create(
            form=fd,
            text="¿Tienes experiencia previa con mentoría o coaching? (Como mentora o coach)",
            help_text="",
            field_type=Question.CHOICE,
            required=True,
            position=pos,
            slug="mentoring_exp_as_mentor",
            active=True,
        )
        add_choice_yes_no(q)
        pos += 1

        q = Question.objects.create(
            form=fd,
            text="¿Tienes experiencia previa con mentoría o coaching? (Como estudiante / emprendedora)",
            help_text="",
            field_type=Question.CHOICE,
            required=True,
            position=pos,
            slug="mentoring_exp_as_student",
            active=True,
        )
        add_choice_yes_no(q)
        pos += 1

        q_long("Si has tenido experiencia con la mentoría o el coaching, por favor, describe brevemente tu experiencia.", "mentoring_exp_detail", required=False)

        # --- Disponibilidad ---
        q_choice(
            "¿Cuánto tiempo puedes dedicar al programa semanalmente? (preparación y reuniones)",
            "weekly_time",
            choices=[
                ("lt2", "Menos de 2 horas"),
                ("2_3", "2-3 horas"),
                ("3_4", "3-4 horas"),
                ("gt4", "Más de 4 horas"),
            ],
            required=True,
            help_text="Se espera una reunión semanal de ~1.5 horas más preparación.",
        )

        # Checkbox grid simplified: 21 options
        days = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
        times = [("manana", "Mañana"), ("tarde", "Tarde"), ("noche", "Noche")]
        grid_choices = []
        for d in days:
            d_label = d.capitalize()
            for t_val, t_label in times:
                grid_choices.append((f"{d}_{t_val}", f"{d_label} - {t_label}"))

        q_multi(
            "¿En qué horario te resulta más conveniente participar en sesiones virtuales? (Selecciona todas las opciones que correspondan)",
            "availability_grid",
            choices=grid_choices,
            required=True,
        )

        q_long("¿Hay algo más que te gustaría compartir con nosotras?", "additional_comments", required=False)

        self.stdout.write(self.style.SUCCESS("✅ Built master M_A2 successfully."))
