# club_emprendo_site/applications/management/commands/build_master_forms.py

from django.core.management.base import BaseCommand
from django.db import transaction

from applications.models import FormDefinition, Question, Choice


MASTER_SLUGS = ["E_A1", "E_A2", "M_A1", "M_A2"]


def upsert_form(
    slug: str,
    name: str,
    description: str = "",
    is_public: bool = True,
    is_master: bool = True,
):
    fd, _ = FormDefinition.objects.get_or_create(
        slug=slug,
        defaults={
            "name": name,
            "description": description,
            "is_public": is_public,
            "is_master": is_master,
            "group": None,
        },
    )
    fd.name = name
    fd.description = description
    fd.is_public = is_public
    fd.is_master = is_master
    fd.group = None
    fd.save()
    return fd


def rebuild_questions(fd: FormDefinition, questions_payload: list[dict]):
    """
    Hard reset the questions for this form to exactly match `questions_payload`.
    Safe to rerun.
    """
    Question.objects.filter(form=fd).delete()

    for idx, q in enumerate(questions_payload, start=1):
        qobj = Question.objects.create(
            form=fd,
            text=q["text"],
            help_text=q.get("help_text", ""),
            field_type=q.get("field_type", Question.SHORT_TEXT),
            required=q.get("required", True),
            position=q.get("position", idx),
            slug=q["slug"],
            active=q.get("active", True),
        )
        for cpos, choice in enumerate(q.get("choices", []), start=1):
            Choice.objects.create(
                question=qobj,
                label=choice["label"],
                value=choice.get("value", choice["label"]),
                position=choice.get("position", cpos),
            )


def yes_no_choices():
    return [
        {"label": "Sí", "value": "yes"},
        {"label": "No", "value": "no"},
    ]


def availability_grid_choices():
    # Matches the PDF-style grid via 21 checkbox options
    days = [
        ("lunes", "Lunes"),
        ("martes", "Martes"),
        ("miercoles", "Miércoles"),
        ("jueves", "Jueves"),
        ("viernes", "Viernes"),
        ("sabado", "Sábado"),
        ("domingo", "Domingo"),
    ]
    times = [
        ("manana", "Mañana"),
        ("tarde", "Tarde"),
        ("noche", "Noche"),
    ]
    out = []
    for d_key, d_label in days:
        for t_key, t_label in times:
            out.append(
                {
                    "label": f"{d_label} - {t_label}",
                    "value": f"{d_key}_{t_key}",
                }
            )
    return out


class Command(BaseCommand):
    help = "Build/refresh master forms (E_A1, E_A2, M_A1, M_A2). Safe to rerun."

    def add_arguments(self, parser):
        parser.add_argument(
            "--only",
            choices=MASTER_SLUGS,
            help="Only rebuild one master form slug (e.g., E_A2).",
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        only = opts.get("only")
        forms_to_build = [only] if only else MASTER_SLUGS

        # -------------------------
        # E_A1 (Emprendedoras A1)
        # -------------------------
        e_a1_description = (
            "¡Hola desde el equipo del Club Emprendo!\n\n"
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

        e_a1_questions = [
            {
                "text": "Correo electrónico",
                "slug": "email",
                "field_type": Question.SHORT_TEXT,
                "required": True,
                "position": 1,
            },
            {
                "text": "Nombre completo",
                "slug": "full_name",
                "field_type": Question.SHORT_TEXT,
                "required": True,
                "position": 2,
            },
            {
                "text": "📌 Requisitos generales\n"
                        "✔ Ser mujer\n"
                        "✔ Vivir en Latinoamérica\n"
                        "✔ Tener conexión estable a internet\n"
                        "✔ Tener un emprendimiento en marcha (no sólo una idea)\n"
                        "✔ Ser puntual\n"
                        "✔ Estar dispuesta a completar una capacitación previa al inicio del programa\n"
                        "✔ Estar dispuesta a responder 4 encuestas de retroalimentación a lo largo del proceso",
                "slug": "requirements_block",
                "field_type": Question.LONG_TEXT,
                "required": False,
                "position": 3,
                "help_text": "",
            },
            {
                "text": "¿Cumples todos estos requisitos enumerados anteriormente?",
                "slug": "meets_requirements",
                "field_type": Question.CHOICE,
                "required": True,
                "position": 4,
                "choices": [
                    {"label": "Sí, cumplo con todos los requisitos. Ir a la pregunta 7", "value": "yes"},
                    {"label": "No, no cumplo con todos los requisitos. Ir a la pregunta 9", "value": "no"},
                ],
            },
            {
                "text": "País donde resides",
                "slug": "country_residence",
                "field_type": Question.SHORT_TEXT,
                "required": True,
                "position": 5,
            },
            {
                "text": "Numero de Whatsapp (con indicativo de país ej: +57 para Colombia)",
                "slug": "whatsapp",
                "field_type": Question.SHORT_TEXT,
                "required": True,
                "position": 6,
            },
            {
                "text": "📌 Duración: De #(month) a #(month) #(year) (12 semanas en total)\n"
                        "📌 Compromiso de tiempo: 3 horas a la semana durante 12 semanas (aproximadamente)",
                "slug": "availability_block",
                "field_type": Question.LONG_TEXT,
                "required": False,
                "position": 7,
            },
            {
                "text": "¿Estás de acuerdo y disponible para participar en el periodo de #(month) a #(month) #(year), por 3 horas a la semana?",
                "slug": "available_period",
                "field_type": Question.CHOICE,
                "required": True,
                "position": 8,
                "choices": [
                    {"label": "Sí, estoy de acuerdo y disponible. Ir a la pregunta 8", "value": "yes"},
                    {"label": "No, en este momento no puedo comprometerme. Ir a la pregunta 9", "value": "no"},
                ],
            },
            {
                "text": "¿Actualmente tienes un emprendimiento en funcionamiento? (No se considera una idea de negocio o un proyecto detenido hace tiempo)",
                "slug": "business_active",
                "field_type": Question.CHOICE,
                "required": True,
                "position": 9,
                "choices": [
                    {"label": "Sí, mi emprendimiento está activo actualmente. Ir a la pregunta 10", "value": "yes"},
                    {"label": "No, solo tengo una idea o el emprendimiento no está funcionando por ahora. Ir a la pregunta 9", "value": "no"},
                ],
            },
            {
                "text": "Si estás dispuesta, por favor indícanos qué requisito(s) no cumpliste para participar en este programa de mentoría. También puedes compartir cualquier otro comentario que desees.",
                "slug": "comments",
                "field_type": Question.LONG_TEXT,
                "required": False,
                "position": 10,
            },
        ]

        # -------------------------
        # M_A1 (Mentoras A1)
        # -------------------------
        m_a1_description = (
            "¡Hola desde el equipo de Club Emprendo, y gracias por tu interés en este voluntariado! 🙌\n\n"
            "✨ Esta aplicación está diseñada para identificar mentoras interesadas en participar en\n"
            "nuestro programa de mentoría COMO MENTORA.\n\n"
            "🗓 Formarías parte del Grupo #(group number), que durará de #(month) a #(month) de #(year).\n\n"
            "🫶 Este es un voluntariado 100% virtual enfocado en apoyar a mujeres emprendedoras en\n"
            "Latinoamérica.\n\n"
            "💕 Las mentoras de Club Emprendo amablemente ofrecen su tiempo de forma voluntaria y\n"
            "las emprendedoras reciben la mentoría de forma gratis."
        )

        m_a1_questions = [
            {
                "text": "Nombre completo",
                "slug": "full_name",
                "field_type": Question.SHORT_TEXT,
                "required": True,
                "position": 1,
            },
            {
                "text": "Correo electrónico",
                "slug": "email",
                "field_type": Question.SHORT_TEXT,
                "required": True,
                "position": 2,
            },
            {
                "text": "País donde resides",
                "slug": "country_residence",
                "field_type": Question.SHORT_TEXT,
                "required": True,
                "position": 3,
            },
            {
                "text": "Numero de Whatsapp (con indicativo de país ej: +57 para Colombia)",
                "slug": "whatsapp_number",
                "field_type": Question.SHORT_TEXT,
                "required": True,
                "position": 4,
            },
            {
                "text": "📌 Requisitos generales\n"
                        "✔ Ser mujer\n"
                        "✔ Vivir en Latinoamérica\n"
                        "✔ Tener conexión estable a internet\n"
                        "✔ Tener experiencia emprendiendo o trabajando en negocios\n"
                        "✔ Ser puntual\n"
                        "✔ Estar dispuesta a completar una capacitación previa al inicio del programa\n"
                        "✔ Estar dispuesta a responder 4 encuestas de retroalimentación a lo largo del proceso",
                "slug": "requirements_block",
                "field_type": Question.LONG_TEXT,
                "required": False,
                "position": 5,
            },
            {
                "text": "¿Cumples estos requisitos enumerados anteriormente?",
                "slug": "meets_requirements",
                "field_type": Question.CHOICE,
                "required": True,
                "position": 6,
                "choices": [
                    {"label": "Sí, cumplo con todos los requisitos.", "value": "yes"},
                    {"label": "No, no cumplo con todos los requisitos.", "value": "no"},
                ],
            },
            {
                "text": "📌 Duración: De #(month) a #(month) #(year) (12 semanas en total)\n"
                        "📌 Compromiso de tiempo: 2 horas a la semana durante 12 semanas (aproximadamente)",
                "slug": "availability_block",
                "field_type": Question.LONG_TEXT,
                "required": False,
                "position": 7,
            },
            {
                "text": "¿Estás de acuerdo y disponible para participar en el periodo de #(month) a #(month) #(year), por 2 horas a la semana?",
                "slug": "availability_ok",
                "field_type": Question.CHOICE,
                "required": True,
                "position": 8,
                "choices": [
                    {"label": "Sí, estoy de acuerdo y disponible", "value": "yes"},
                    {"label": "No, en este momento no puedo comprometerme", "value": "no"},
                ],
            },
            {
                "text": "Si estás dispuesta, por favor indícanos qué requisito(s) no cumpliste para participar en este programa de mentoría. También puedes compartir cualquier otro comentario que desees.",
                "slug": "comments_if_not_eligible",
                "field_type": Question.LONG_TEXT,
                "required": False,
                "position": 9,
            },
        ]

        # -------------------------
        # E_A2 (Emprendedoras A2)
        # -------------------------
        e_a2_description = (
            "Hola desde el equipo de Club Emprendo!\n"
            "Esta aplicación está diseñada para identificar microemprendedoras interesadas en participar en nuestro programa de mentoría.\n\n"
            "📌 Duración del programa: 3 meses (#(month)-#(month) #(year))\n"
            "📌 Frecuencia de reuniones: Reuniones semanales de mentoría, con reuniones grupales periódicas\n"
            "📌 Beneficios: Apoyo personalizado y asesoramiento para ayudarte a crear una visión para tu vida y negocio, acceso a cursos (Certificados) y recursos, comunidad de apoyo\n"
            "📌 Requisitos: Ser mujer, vivir en Latino America, tener un emprendimiento existente, y comprometerte a 3 horas a la semana durante 3 meses\n\n"
            "Por favor, completa el siguiente formulario para que podamos entender mejor tus necesidades y cómo podemos potencialmente emparejarte con una mentora adecuada."
        )

        e_a2_questions = [
            {
                "text": "¿Cuál es tu número de cédula? (documento de identidad)",
                "slug": "cedula",
                "field_type": Question.SHORT_TEXT,
                "required": True,
                "position": 1,
                "help_text": (
                    "Solicitamos tu número de cédula únicamente para identificar de forma única tu postulación y evitar aplicaciones duplicadas.\n"
                    "Tu información será utilizada exclusivamente para fines administrativos del programa de mentoría y tratada con estricta confidencialidad, "
                    "conforme a la legislación de protección de datos personales vigente en tu país."
                ),
            },
            {"text": "Nombre completo", "slug": "full_name", "field_type": Question.SHORT_TEXT, "required": True, "position": 2},
            {"text": "Correo electrónico", "slug": "email", "field_type": Question.SHORT_TEXT, "required": True, "position": 3},
            {
                "text": "Numero de Whatsapp (Con indicativo de pais ej: +57 para Colombia)",
                "slug": "whatsapp",
                "field_type": Question.SHORT_TEXT,
                "required": True,
                "position": 4,
            },
            {"text": "Ciudad de residencia", "slug": "city_residence", "field_type": Question.SHORT_TEXT, "required": True, "position": 5},
            {"text": "País de residencia", "slug": "country_residence", "field_type": Question.SHORT_TEXT, "required": True, "position": 6},
            {
                "text": "Edad",
                "slug": "age_range",
                "field_type": Question.CHOICE,
                "required": True,
                "position": 7,
                "choices": [
                    {"label": "18-24", "value": "18_24"},
                    {"label": "25-34", "value": "25_34"},
                    {"label": "35-44", "value": "35_44"},
                    {"label": "45-54", "value": "45_54"},
                    {"label": "55+", "value": "55_plus"},
                    {"label": "Otra", "value": "other"},
                ],
            },
            {
                "text": "¿Has participado anteriormente en Club Emprendo? (Puedes seleccionar más de una opción)",
                "slug": "participated_before",
                "field_type": Question.MULTI_CHOICE,
                "required": True,
                "position": 8,
                "choices": [
                    {"label": "Sí, como emprendedora", "value": "yes_entrepreneur"},
                    {"label": "Sí, como mentora", "value": "yes_mentor"},
                    {"label": "No, primera vez", "value": "no_first_time"},
                ],
            },
            {
                "text": "Acepto que los datos proporcionados sean tratados conforme al aviso de privacidad.",
                "slug": "privacy_accept",
                "field_type": Question.MULTI_CHOICE,
                "required": True,
                "position": 9,
                "choices": [{"label": "Acepto", "value": "accept"}],
                "help_text": (
                    "🛡 Aviso de privacidad:\n"
                    "Club Emprendo recopila datos personales limitados, como tu nombre y número de cédula, con fines administrativos relacionados con el proceso de postulación.\n"
                    "Nos comprometemos a tratar esta información de forma confidencial, segura y conforme a las leyes de protección de datos aplicables en América Latina.\n"
                    "Puedes ejercer tus derechos de acceso, corrección o eliminación de datos escribiéndonos a: contacto@clubemprendo.org"
                ),
            },
            {"text": "Nombre de tu emprendimiento", "slug": "business_name", "field_type": Question.SHORT_TEXT, "required": True, "position": 10},
            {
                "text": "Industria de tu emprendimiento:",
                "slug": "industry",
                "field_type": Question.CHOICE,
                "required": True,
                "position": 11,
                "choices": [
                    {"label": "Productos (ropa, artesanías, cosmética, etc.)", "value": "products"},
                    {"label": "Servicios (consultoría, turismo, marketing, etc.)", "value": "services"},
                    {"label": "Tecnología (apps, software, etc.)", "value": "tech"},
                    {"label": "Otros", "value": "other"},
                ],
            },
            {"text": "Descripción del negocio:", "slug": "business_description", "field_type": Question.LONG_TEXT, "required": True, "position": 12},
            {
                "text": "Edad del negocio:",
                "slug": "business_age",
                "field_type": Question.CHOICE,
                "required": True,
                "position": 13,
                "choices": [
                    {"label": "Idea en desarrollo", "value": "idea"},
                    {"label": "Recién lanzado (menos de 1 año)", "value": "lt_1y"},
                    {"label": "En crecimiento (1-3 años)", "value": "1_3y"},
                    {"label": "Establecido (4-6 años)", "value": "4_6y"},
                    {"label": "Maduro (7-10 años)", "value": "7_10y"},
                    {"label": "Más de 10 años", "value": "gt_10y"},
                ],
            },
            {
                "text": "¿Tienes empleados?",
                "slug": "has_employees",
                "field_type": Question.CHOICE,
                "required": True,
                "position": 14,
                "choices": [
                    {"label": "Sí, empleo a una o más personas (además de mí)", "value": "yes"},
                    {"label": "No, trabajo sola", "value": "no"},
                ],
            },
            {
                "text": "¿Cómo crees que este programa puede ayudarte a crecer como emprendedora?",
                "slug": "growth_how",
                "field_type": Question.LONG_TEXT,
                "required": True,
                "position": 15,
                "help_text": (
                    "💡 Tip importante:\n"
                    "En las preguntas abiertas, te recomendamos que seas lo más amplia posible al compartir tu experiencia, motivaciones y visión. 📝 ✨\n"
                    "Evita responder solo con una o dos frases — ¡queremos conocerte mejor!"
                ),
            },
            {
                "text": "¿Cuál es tu mayor desafío actualmente como emprendedora y cómo lo estás abordando?",
                "slug": "biggest_challenge",
                "field_type": Question.LONG_TEXT,
                "required": True,
                "position": 16,
            },
            {
                "text": "¿Estás dispuesta a comprometerte a asistir a reuniones de mentoría semanales durante los 3 meses completos?",
                "slug": "commit_3_months",
                "field_type": Question.CHOICE,
                "required": True,
                "position": 17,
                "choices": [
                    {"label": "Sí", "value": "yes"},
                    {"label": "No estoy segura", "value": "unsure"},
                    {"label": "No", "value": "no"},
                ],
            },
            {
                "text": "¿Cuánto tiempo puedes dedicar al programa semanalmente? (Estudio personal y reuniones)",
                "slug": "hours_per_week",
                "field_type": Question.CHOICE,
                "required": True,
                "position": 18,
                "choices": [
                    {"label": "Menos de 2 horas", "value": "lt_2"},
                    {"label": "2-4 horas", "value": "2_4"},
                    {"label": "Más de 4 horas", "value": "gt_4"},
                ],
            },
            {
                "text": "¿Tienes alguna experiencia previa con mentoría para tu empresa?",
                "slug": "prior_mentoring",
                "field_type": Question.CHOICE,
                "required": True,
                "position": 19,
                "choices": [{"label": "Sí", "value": "yes"}, {"label": "No", "value": "no"}],
            },
            {
                "text": "¿Revisaste el PDF (enlace abajo) que ofrece una breve introducción al programa de mentoría de Club Emprendo?",
                "slug": "reviewed_pdf",
                "field_type": Question.MULTI_CHOICE,
                "required": True,
                "position": 20,
                "choices": [{"label": "Sí", "value": "yes"}],
                "help_text": "PDF",
            },
            {
                "text": "¿Tienes acceso a internet y un dispositivo (computadora o celular) para participar en reuniones virtuales?",
                "slug": "internet_access",
                "field_type": Question.CHOICE,
                "required": True,
                "position": 21,
                "choices": [
                    {"label": "Sí, sin problemas.", "value": "yes_ok"},
                    {"label": "Sí, pero con algunas dificultades.", "value": "yes_some"},
                    {"label": "No", "value": "no"},
                ],
            },
            {
                "text": "Disponibilidad (Selecciona todas las opciones que correspondan)",
                "slug": "preferred_schedule",
                "field_type": Question.MULTI_CHOICE,
                "required": True,
                "position": 22,
                "choices": availability_grid_choices(),
            },
            {
                "text": "¿Te gustaría dejarnos algún comentario, duda o sugerencia adicional? (Este espacio es opcional, pero siempre estamos felices de leerte.)",
                "slug": "additional_comments",
                "field_type": Question.LONG_TEXT,
                "required": False,
                "position": 23,
            },
        ]

        # -------------------------
        # M_A2 (Mentoras A2)
        # -------------------------
        m_a2_description = (
            "¡Hola desde el equipo de Club Emprendo!\n\n"
            "Gracias por tu interés en ser MENTORA de Club Emprendo. Recibiste esta solicitud porque completaste nuestra solicitud inicial.\n"
            "Ahora, esta solicitud es un poco más amplia y nos ayudará a determinar si eres una buena candidata para nuestro programa.\n\n"
            "📌 Duración del programa: 3 meses (#(month) a #(month) de #(year)).\n"
            "📌 Frecuencia: Reuniones virtuales semanales.\n\n"
            "¡Lo más importante será tu capacidad de hacer preguntas y ser un socio responsable – NO saber las respuestas a todo!"
        )

        m_a2_questions = [
            {
                "text": "¿Cuál es tu número de cédula? (documento de identidad)",
                "slug": "id_number",
                "field_type": Question.SHORT_TEXT,
                "required": True,
                "position": 1,
                "help_text": (
                    "Solicitamos tu número de cédula únicamente para identificar de forma única tu postulación y evitar aplicaciones duplicadas. "
                    "Tu información será utilizada exclusivamente para fines administrativos del programa."
                ),
            },
            {"text": "Nombre completo", "slug": "full_name", "field_type": Question.SHORT_TEXT, "required": True, "position": 2},
            {"text": "Nombre de preferencia (para referirnos a ti en el programa)", "slug": "preferred_name", "field_type": Question.SHORT_TEXT, "required": True, "position": 3},
            {"text": "Si eres seleccionada como mentora, nombre que deberíamos poner en el certificado de voluntariado", "slug": "certificate_name", "field_type": Question.SHORT_TEXT, "required": False, "position": 4},
            {"text": "Correo electrónico", "slug": "email", "field_type": Question.SHORT_TEXT, "required": True, "position": 5},
            {"text": "Numero de Whatsapp (incluir código de país, ejemplo +57 para Colombia)", "slug": "whatsapp", "field_type": Question.SHORT_TEXT, "required": True, "position": 6},
            {"text": "Ciudad de residencia", "slug": "city_residence", "field_type": Question.SHORT_TEXT, "required": True, "position": 7},
            {"text": "País de residencia", "slug": "country_residence", "field_type": Question.SHORT_TEXT, "required": True, "position": 8},
            {"text": "País de nacimiento", "slug": "country_birth", "field_type": Question.SHORT_TEXT, "required": True, "position": 9},
            {
                "text": "Edad",
                "slug": "age_range",
                "field_type": Question.CHOICE,
                "required": True,
                "position": 10,
                "choices": [
                    {"label": "18-24", "value": "18_24"},
                    {"label": "25-34", "value": "25_34"},
                    {"label": "35-44", "value": "35_44"},
                    {"label": "45-54", "value": "45_54"},
                    {"label": "55+", "value": "55_plus"},
                    {"label": "Otra", "value": "other"},
                ],
            },
            {
                "text": "¿Has participado anteriormente en Club Emprendo? (Puedes seleccionar más de una opción.)",
                "slug": "prior_participation",
                "field_type": Question.MULTI_CHOICE,
                "required": True,
                "position": 11,
                "choices": [
                    {"label": "Como Emprendedora", "value": "as_entrepreneur"},
                    {"label": "Como Mentora", "value": "as_mentor"},
                    {"label": "Sería mi primera vez", "value": "first_time"},
                ],
            },
            {
                "text": "🛡 Aviso de privacidad: Acepto que los datos proporcionados sean tratados conforme al aviso de privacidad.",
                "slug": "privacy_ack",
                "field_type": Question.MULTI_CHOICE,
                "required": True,
                "position": 12,
                "choices": [
                    {"label": "Acepto que los datos proporcionados sean tratados conforme al aviso de privacidad.", "value": "accept"}
                ],
            },

            # Requirements “grid” as individual yes/no questions
            {"text": "Requisitos básicos: Soy mujer.", "slug": "req_basic_woman", "field_type": Question.CHOICE, "required": True, "position": 13, "choices": yes_no_choices()},
            {"text": "Requisitos básicos: He vivido / vivo en Latinoamérica.", "slug": "req_basic_latam", "field_type": Question.CHOICE, "required": True, "position": 14, "choices": yes_no_choices()},
            {"text": "Requisitos básicos: Tengo experiencia en emprender o trabajar en negocios de alguna forma.", "slug": "req_basic_business_exp", "field_type": Question.CHOICE, "required": True, "position": 15, "choices": yes_no_choices()},
            {"text": "Requisitos básicos: Soy puntual.", "slug": "req_basic_punctual", "field_type": Question.CHOICE, "required": True, "position": 16, "choices": yes_no_choices()},
            {"text": "Requisitos básicos: Tengo conexión a internet y acceso a un dispositivo (computadora o celular) para poder participar en reuniones virtuales semanales.", "slug": "req_basic_internet_device", "field_type": Question.CHOICE, "required": True, "position": 17, "choices": yes_no_choices()},
            {"text": "Requisitos básicos: Estoy dispuesta a completar la capacitación previa al programa (de 3 a 4 horas de dedicación).", "slug": "req_basic_training", "field_type": Question.CHOICE, "required": True, "position": 18, "choices": yes_no_choices()},
            {"text": "Requisitos básicos: Estoy dispuesta a completar tres encuestas de retroalimentación durante el programa.", "slug": "req_basic_surveys", "field_type": Question.CHOICE, "required": True, "position": 19, "choices": yes_no_choices()},

            {"text": "Requisitos de disponibilidad: Estoy disponible para participar desde #(month) hasta #(month) de #(year).", "slug": "req_avail_period", "field_type": Question.CHOICE, "required": True, "position": 20, "choices": yes_no_choices()},
            {"text": "Requisitos de disponibilidad: Puedo donar al menos 2 horas semanales durante estas 12 semanas, de forma voluntaria (sin pago económico).", "slug": "req_avail_2hrs_week", "field_type": Question.CHOICE, "required": True, "position": 21, "choices": yes_no_choices()},
            {"text": "Requisitos de disponibilidad: Estoy disponible el lunes de #(month) de #(year) para asistir a la reunión de lanzamiento del programa de 1 hora (por la tarde).", "slug": "req_avail_kickoff", "field_type": Question.CHOICE, "required": True, "position": 22, "choices": yes_no_choices()},

            {
                "text": "Marca la casilla para confirmar tu entendimiento:",
                "slug": "volunteer_ack",
                "field_type": Question.MULTI_CHOICE,
                "required": True,
                "position": 23,
                "choices": [
                    {
                        "label": "Entiendo que ofrezco estos servicios como voluntaria y que no recibiré ningún pago por ser mentora en Club Emprendo.",
                        "value": "ack",
                    }
                ],
            },
            {"text": "Si no cumples alguno(s) de los requisitos anteriores, especifica cuál(es) y el(los) motivo(s).", "slug": "req_explain", "field_type": Question.LONG_TEXT, "required": False, "position": 24},
            {
                "text": "¿Revisaste el PDF (enlace abajo) que ofrece una breve introducción al programa de mentoría de Club Emprendo?",
                "slug": "read_pdf",
                "field_type": Question.MULTI_CHOICE,
                "required": True,
                "position": 25,
                "choices": [{"label": "Sí", "value": "yes"}],
            },
            {
                "text": "¿Has dirigido tu propio negocio?",
                "slug": "owned_business",
                "field_type": Question.CHOICE,
                "required": True,
                "position": 26,
                "choices": [{"label": "Sí", "value": "yes"}, {"label": "No", "value": "no"}],
            },

            # Business (optional details)
            {"text": "Nombre de tu emprendimiento", "slug": "business_name", "field_type": Question.SHORT_TEXT, "required": False, "position": 27},
            {
                "text": "Industria de tu emprendimiento",
                "slug": "business_industry",
                "field_type": Question.MULTI_CHOICE,
                "required": False,
                "position": 28,
                "choices": [
                    {"label": "Agricultura", "value": "agri"},
                    {"label": "Alimentos y bebidas", "value": "food"},
                    {"label": "Artesanías", "value": "crafts"},
                    {"label": "Belleza y cuidado personal", "value": "beauty"},
                    {"label": "Comercio minorista", "value": "retail"},
                    {"label": "Construcción y remodelación", "value": "construction"},
                    {"label": "Educación y capacitación", "value": "education"},
                    {"label": "Finanzas y servicios legales", "value": "finance_legal"},
                    {"label": "Inmobiliaria", "value": "real_estate"},
                    {"label": "Medios y comunicaciones", "value": "media"},
                    {"label": "Salud y bienestar", "value": "health"},
                    {"label": "Servicios (ej. limpieza, cuidado de niños, turismo)", "value": "services"},
                    {"label": "Tecnología", "value": "tech"},
                    {"label": "Textiles y ropa", "value": "textiles"},
                    {"label": "Transporte y logística", "value": "transport"},
                    {"label": "Otros", "value": "other"},
                ],
            },
            {"text": "Descripción del negocio", "slug": "business_description", "field_type": Question.LONG_TEXT, "required": False, "position": 29},
            {"text": "¿Dónde operas tu negocio (o dónde lo operabas, si ya no está en operación)? (ciudad, país etc.)", "slug": "business_location", "field_type": Question.SHORT_TEXT, "required": False, "position": 30},
            {
                "text": "¿Cuánto tiempo has estado operando (o por cuánto tiempo se operó, si ya no está en operación)?",
                "slug": "business_years",
                "field_type": Question.CHOICE,
                "required": False,
                "position": 31,
                "choices": [
                    {"label": "0-1 año", "value": "0_1"},
                    {"label": "1-5 años", "value": "1_5"},
                    {"label": "5-10 años", "value": "5_10"},
                    {"label": "10+ años", "value": "10_plus"},
                ],
            },
            {
                "text": "¿Tienes empleados? (o tuviste, si ya no está en operación)?",
                "slug": "has_employees",
                "field_type": Question.CHOICE,
                "required": False,
                "position": 32,
                "choices": [
                    {"label": "Sí, empleo a una o más personas (además de mí)", "value": "yes"},
                    {"label": "No, trabajo sola", "value": "no"},
                ],
            },

            # Motivation + mentoring experience
            {
                "text": "¿Cuál es tu área de experiencia profesional más relevante para la mentoría de mujeres microempresarias? (Ej. Marketing, Finanzas, etc.)",
                "slug": "professional_expertise",
                "field_type": Question.SHORT_TEXT,
                "required": True,
                "position": 33,
            },
            {"text": "¿Qué te motiva a ser mentora en este programa de Club Emprendo?", "slug": "motivation", "field_type": Question.LONG_TEXT, "required": True, "position": 34},
            {"text": "¿Por qué crees que serías una buena mentora para una emprendedora en su proceso de crecimiento personal y profesional?", "slug": "why_good_mentor", "field_type": Question.LONG_TEXT, "required": True, "position": 35},

            {"text": "¿Tienes experiencia previa con mentoría o coaching? (Como mentora o coach)", "slug": "mentoring_exp_as_mentor", "field_type": Question.CHOICE, "required": True, "position": 36, "choices": yes_no_choices()},
            {"text": "¿Tienes experiencia previa con mentoría o coaching? (Como estudiante / emprendedora)", "slug": "mentoring_exp_as_student", "field_type": Question.CHOICE, "required": True, "position": 37, "choices": yes_no_choices()},
            {"text": "Si has tenido experiencia con la mentoría o el coaching, por favor, describe brevemente tu experiencia.", "slug": "mentoring_exp_detail", "field_type": Question.LONG_TEXT, "required": False, "position": 38},

            {
                "text": "¿Cuánto tiempo puedes dedicar al programa semanalmente? (preparación y reuniones)",
                "slug": "weekly_time",
                "field_type": Question.CHOICE,
                "required": True,
                "position": 39,
                "choices": [
                    {"label": "Menos de 2 horas", "value": "lt2"},
                    {"label": "2-3 horas", "value": "2_3"},
                    {"label": "3-4 horas", "value": "3_4"},
                    {"label": "Más de 4 horas", "value": "gt4"},
                ],
                "help_text": "Se espera una reunión semanal de ~1.5 horas más preparación.",
            },
            {
                "text": "Disponibilidad (Selecciona todas las opciones que correspondan)",
                "slug": "availability_grid",
                "field_type": Question.MULTI_CHOICE,
                "required": True,
                "position": 40,
                "choices": availability_grid_choices(),
            },
            {"text": "¿Hay algo más que te gustaría compartir con nosotras?", "slug": "additional_comments", "field_type": Question.LONG_TEXT, "required": False, "position": 41},
        ]

        # Build selected forms
        for slug in forms_to_build:
            if slug == "E_A1":
                fd = upsert_form("E_A1", "Aplicación para emprendedoras (Aplicación 1)", e_a1_description, is_public=True, is_master=True)
                rebuild_questions(fd, e_a1_questions)
                self.stdout.write(self.style.SUCCESS("Built E_A1 master form."))
            elif slug == "E_A2":
                fd = upsert_form("E_A2", "Aplicación para emprendedoras (Aplicación 2)", e_a2_description, is_public=False, is_master=True)
                rebuild_questions(fd, e_a2_questions)
                self.stdout.write(self.style.SUCCESS("Built E_A2 master form."))
            elif slug == "M_A1":
                fd = upsert_form("M_A1", "Aplicación para mentoras voluntarias (A1)", m_a1_description, is_public=True, is_master=True)
                rebuild_questions(fd, m_a1_questions)
                self.stdout.write(self.style.SUCCESS("Built M_A1 master form."))
            elif slug == "M_A2":
                fd = upsert_form("M_A2", "Solicitud para ser MENTORA de Club Emprendo (Aplicación 2)", m_a2_description, is_public=False, is_master=True)
                rebuild_questions(fd, m_a2_questions)
                self.stdout.write(self.style.SUCCESS("Built M_A2 master form."))

        self.stdout.write(self.style.SUCCESS("✅ Done."))
