from django.db import transaction

from .models import Choice, FormDefinition, Question, Section


INTRO = """¡Hola de parte del equipo de Club Emprendo!

Gracias por tu interés en postularte para recibir mentoría en nuestro programa 100% virtual, diseñado específicamente para mujeres emprendedoras en América Latina. 🫶

✨ Esta aplicación está dirigida a emprendedoras que deseen participar como beneficiarias del programa de mentoría, completamente gratis gracias al trabajo voluntario de nuestras mentoras.

🗓 Formarás parte de este grupo de mentoría que durará de #(month) a #(month) de #(year).
🤝 Cada participante tendrá reuniones virtuales semanales individuales con su mentora, además de sesiones grupales periódicas.
🎁 Los beneficios incluyen mentorías personalizadas; herramientas para crear una visión clara para tu vida y negocio; acceso a recursos, cursos y una comunidad de apoyo.

Asegúrate de escribir bien tu correo electrónico y número de WhatsApp, sin errores, porque allí recibirás los pasos a seguir y toda la información importante.

⚠️ Importante: tenemos una alta tasa de selección, así que te pedimos que apliques solo si realmente estás comprometida a participar en las mentorías en caso de ser seleccionada."""

COUNTRIES = [
    ("argentina", "Argentina"), ("bolivia", "Bolivia"), ("brasil", "Brasil"),
    ("chile", "Chile"), ("colombia", "Colombia"), ("costa_rica", "Costa Rica"),
    ("cuba", "Cuba"), ("ecuador", "Ecuador"), ("el_salvador", "El Salvador"),
    ("guatemala", "Guatemala"), ("honduras", "Honduras"), ("mexico", "México"),
    ("nicaragua", "Nicaragua"), ("panama", "Panamá"), ("paraguay", "Paraguay"),
    ("peru", "Perú"), ("puerto_rico", "Puerto Rico"),
    ("republica_dominicana", "República Dominicana"), ("uruguay", "Uruguay"),
    ("venezuela", "Venezuela"), ("otro", "Otro"),
]

SCHEDULE = [
    (f"{day}_{period}", f"{day_label} - {period_label}")
    for day, day_label in (
        ("lunes", "Lunes"), ("martes", "Martes"), ("miercoles", "Miércoles"),
        ("jueves", "Jueves"), ("viernes", "Viernes"), ("sabado", "Sábado"),
        ("domingo", "Domingo"),
    )
    for period, period_label in (("manana", "Mañana"), ("tarde", "Tarde"), ("noche", "Noche"))
]

PRIVACY = """🛡 Aviso de privacidad: Club Emprendo recopila datos personales limitados, como tu nombre y número de documento, con fines administrativos relacionados con el proceso de postulación. Trataremos esta información de forma confidencial, segura y conforme a las leyes de protección de datos aplicables en América Latina. Puedes ejercer tus derechos de acceso, corrección o eliminación escribiéndonos a contacto@clubemprendo.org."""

REQUIREMENTS = """Requisitos básicos:
• Soy mujer.
• Hablo español.
• Tengo acceso a internet y un dispositivo para participar en reuniones virtuales.
• Estoy dispuesta a firmar un acta de compromiso antes de la fecha límite.
• Estoy dispuesta a completar la capacitación en línea previa al programa (1 a 2 horas).
• Estoy dispuesta a completar dos encuestas de retroalimentación durante el programa.
• Tengo un emprendimiento en funcionamiento; no es solo una idea ni un proyecto detenido.
• Revisé el PDF de introducción al programa.

Requisitos de disponibilidad:
• Estoy disponible desde #(month) hasta #(month) de #(year).
• Dedicaré un mínimo de 3 horas semanales durante 14 semanas consecutivas.
• Estoy disponible el lunes #(day) de #(month) de #(year) para la reunión de lanzamiento de una hora por la tarde."""

OPEN_QUESTION_HELP = """💡 Comparte una respuesta amplia y concreta. Estas respuestas nos ayudan a detectar alertas, comprender tus necesidades, valorar tu postulación y buscar una mentora adecuada. Evita responder solo con una o dos frases."""

SELECTION_PROCESS = """1. Seré notificada por correo electrónico si fui seleccionada el #(respond_day) de #(respond_month).
2. Si soy seleccionada, firmaré el Acta de Compromiso antes de la fecha límite.
3. Después de firmarla, recibiré los pasos para completar la capacitación virtual.
4. Completaré la capacitación antes del inicio del programa para poder ser emparejada durante las 14 semanas.

Los próximos pasos llegarán desde contacto@clubemprendo.org o por WhatsApp. Si Club Emprendo me escribe por WhatsApp, agregaré el contacto y responderé para poder recibir información importante."""


def _q(slug, text, field_type="short_text", *, required=True, help_text="", choices=(), confirm=False):
    return {
        "slug": slug, "text": text, "field_type": field_type, "required": required,
        "help_text": help_text, "choices": choices, "confirm_value": confirm,
    }


A1_SECTIONS = [
    {
        "title": "Datos personales",
        "description": "Cuéntanos quién eres y cómo podemos contactarte. Verifica cuidadosamente tus datos antes de continuar.",
        "questions": [
            _q("privacy_accept", "Acepto que los datos proporcionados sean tratados conforme al aviso de privacidad.", "multi_choice", help_text=PRIVACY, choices=(("accept", "Acepto"),)),
            _q("full_name", "Nombre completo"),
            _q("cedula", "¿Cuál es tu número de documento de identidad? (cédula, DNI, pasaporte u otro)", help_text="Usa siempre este mismo número en formularios de Club Emprendo. Lo solicitamos para identificar tu postulación y evitar duplicados.", confirm=True),
            _q("email", "Correo electrónico", confirm=True),
            _q("whatsapp", "Número de WhatsApp (con indicativo de país, por ejemplo 57 para Colombia)", help_text="Escribe el número que manejas personalmente y donde puedes responder rápido. Al proporcionarlo, autorizas a Club Emprendo a contactarte por WhatsApp.", confirm=True),
            _q("country_residence", "País donde vives actualmente", "choice", choices=COUNTRIES),
            _q("age_range", "Edad", "integer"),
            _q("participated_before", "¿Has participado anteriormente en Club Emprendo? (Puedes seleccionar más de una opción)", "multi_choice", help_text="Solo cuenta si participaste activamente y te graduaste en un grupo anterior.", choices=(("entrepreneur", "Sí, como emprendedora"), ("mentor", "Sí, como mentora"), ("first_time", "No, esta sería mi primera vez"))),
            _q("prior_mentoring", "¿Tienes alguna experiencia previa con mentoría para tu empresa?", "choice", choices=(("yes", "Sí"), ("no", "No"))),
        ],
    },
    {
        "title": "Confirmación de cumplimiento de requisitos",
        "description": "Lee todos los requisitos antes de responder. Tus respuestas determinan si puedes participar en esta convocatoria.",
        "questions": [
            _q("meets_requirements", "¿Cumples todos los requisitos básicos indicados?", "choice", help_text=REQUIREMENTS, choices=(("yes", "Sí, cumplo todos los requisitos"), ("no", "No cumplo uno o más requisitos"))),
            _q("available_period", "¿Confirmas tu disponibilidad y compromiso de mínimo 3 horas semanales durante las 14 semanas?", "choice", choices=(("yes", "Sí, confirmo mi disponibilidad"), ("no", "No puedo comprometerme en este momento"))),
            _q("business_active", "¿Actualmente tienes un emprendimiento en funcionamiento?", "choice", help_text="No se considera una idea de negocio o un proyecto detenido hace tiempo.", choices=(("yes", "Sí, mi emprendimiento está funcionando"), ("no", "No, es una idea o no está funcionando actualmente"))),
        ],
    },
]

A2_SECTIONS = [
    {
        "title": "Información sobre tu emprendimiento",
        "description": "Esta información es clave para revisar tu postulación y encontrar una mentora compatible con tu sector y necesidades.",
        "questions": [
            _q("business_name", "Nombre de tu emprendimiento"),
            _q("industry", "Industria de tu emprendimiento", "choice", choices=(("products", "Productos"), ("services", "Servicios"), ("technology", "Tecnología"), ("food", "Alimentos y bebidas"), ("education", "Educación"), ("health", "Salud y bienestar"), ("other", "Otra"))),
            _q("business_description", "Descripción de tu emprendimiento", "long_text", help_text=OPEN_QUESTION_HELP),
            _q("business_age", "Edad del emprendimiento", "choice", choices=(("lt_1y", "Menos de 1 año"), ("1_3y", "1 a 3 años"), ("4_6y", "4 a 6 años"), ("7_10y", "7 a 10 años"), ("gt_10y", "Más de 10 años"))),
            _q("has_employees", "¿Tienes empleados?", "choice", choices=(("yes", "Sí"), ("no", "No, trabajo sola"))),
        ],
    },
    {
        "title": "Motivación y compromiso",
        "description": "Queremos comprender tus metas, necesidades y forma de contribuir a la comunidad. Estas respuestas apoyan la selección y el emparejamiento.",
        "questions": [
            _q("growth_how", "¿Cómo crees que este programa puede ayudarte a crecer como emprendedora?", "long_text", help_text=OPEN_QUESTION_HELP),
            _q("business_goal", "¿Qué estás tratando de lograr con tu emprendimiento y cómo crees que la mentoría de Club Emprendo te ayudará a llegar ahí?", "long_text", help_text=OPEN_QUESTION_HELP),
            _q("biggest_challenge", "¿Cuál es tu mayor desafío actualmente como emprendedora y cómo lo estás abordando?", "long_text", help_text=OPEN_QUESTION_HELP),
            _q("community_contribution", "¿Qué experiencia, perspectiva o fortaleza te gustaría compartir con la comunidad de emprendedoras si eres aceptada?", "long_text", help_text="Esta respuesta nos ayuda a conocerte y orientar el emparejamiento; no determina por sí sola si eres seleccionada."),
        ],
    },
    {
        "title": "Disponibilidad",
        "description": "Selecciona todos los horarios en los que normalmente podrías reunirte de forma virtual.",
        "questions": [_q("preferred_schedule", "¿En qué horario te resulta más conveniente participar en sesiones virtuales?", "multi_choice", choices=SCHEDULE)],
    },
    {
        "title": "Comentarios adicionales",
        "description": "Este espacio es opcional, pero siempre estamos felices de leerte.",
        "questions": [_q("additional_comments", "¿Te gustaría dejarnos algún comentario, duda o sugerencia adicional?", "long_text", required=False)],
    },
    {
        "title": "Confirmación del proceso de selección",
        "description": SELECTION_PROCESS,
        "questions": [_q("selection_process_accept", "Declaro que he leído y entendido los cuatro pasos del proceso de selección.", "multi_choice", choices=(("accept", "Entiendo y acepto el proceso descrito"),))],
    },
]


@transaction.atomic
def apply_emprendedora_schema(form_a1: FormDefinition, form_a2: FormDefinition, transform=None):
    render = transform or (lambda value: value)
    for form_def, sections, description in ((form_a1, A1_SECTIONS, INTRO), (form_a2, A2_SECTIONS, "")):
        form_def.name = "Aplicación para emprendedoras"
        form_def.description = render(description)
        form_def.save(update_fields=["name", "description"])
        form_def.questions.all().delete()
        form_def.sections.all().delete()
        position = 1
        for section_position, section_spec in enumerate(sections, start=1):
            section = Section.objects.create(
                form=form_def,
                title=render(section_spec["title"]),
                description=render(section_spec.get("description", "")),
                position=section_position,
            )
            for question_spec in section_spec["questions"]:
                question = Question.objects.create(
                    form=form_def,
                    section=section,
                    position=position,
                    active=True,
                    slug=question_spec["slug"],
                    text=render(question_spec["text"]),
                    field_type=question_spec["field_type"],
                    required=question_spec["required"],
                    help_text=render(question_spec["help_text"]),
                    confirm_value=question_spec["confirm_value"],
                )
                for choice_position, (value, label) in enumerate(question_spec["choices"], start=1):
                    Choice.objects.create(
                        question=question,
                        value=value,
                        label=render(label),
                        position=choice_position,
                    )
                position += 1
