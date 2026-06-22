from .emprendedora_application_schema import (
    A1_SECTIONS,
    COUNTRIES,
    OPEN_QUESTION_HELP,
    PRIVACY,
    SCHEDULE,
    SELECTION_PROCESS,
    _q,
    apply_two_part_application_schema,
    gate_later_sections,
)


INTRO = """¡Hola de parte del equipo de Club Emprendo!

Gracias por tu interés en postularte como mentora voluntaria en nuestro programa 100% virtual para mujeres emprendedoras en América Latina. 🫶

✨ Como mentora, ofrecerás acompañamiento personalizado a una emprendedora. Tu capacidad de escuchar, hacer preguntas y ser una aliada responsable es más importante que tener todas las respuestas.

🗓 Formarás parte de este grupo de mentoría que durará de #(month) a #(month) de #(year).
🤝 Tendrás reuniones virtuales semanales individuales con tu emprendedora, además de sesiones grupales periódicas.

Tu participación es voluntaria y no remunerada. Asegúrate de escribir bien tu correo electrónico y número de WhatsApp, porque allí recibirás los pasos a seguir y toda la información importante."""

REQUIREMENTS = """Requisitos básicos:
• Soy mujer.
• Hablo español.
• Tengo acceso a internet y un dispositivo (computadora o celular) para participar en reuniones virtuales.
• Tengo experiencia en emprender o trabajar en negocios de alguna forma.
• Estoy dispuesta a firmar un acta de compromiso antes de la fecha límite.
• Estoy dispuesta a completar la capacitación en línea previa al programa (2 a 3 horas).
• Estoy dispuesta a completar dos encuestas de retroalimentación durante el programa.
• Entiendo que ofreceré estos servicios como voluntaria y no recibiré ningún pago por ser mentora en Club Emprendo.

Requisitos de disponibilidad:
• Estoy disponible desde #(month) hasta #(month) de #(year).
• Dedicaré un mínimo de 2 horas semanales durante 14 semanas consecutivas."""

# The DOCX says personal data remains the same for both tracks.
PERSONAL_SECTION = A1_SECTIONS[0]

A1_SECTIONS_M = [
    PERSONAL_SECTION,
    {
        "title": "Confirmación de cumplimiento de requisitos",
        "description": "Lee todos los requisitos antes de responder. Tus respuestas determinan si puedes participar en esta convocatoria.",
        "questions": [
            _q("meets_requirements", "¿Cumples todos los requisitos básicos indicados?", "choice", help_text=REQUIREMENTS, choices=(("yes", "Sí, cumplo todos los requisitos"), ("no", "No cumplo uno o más requisitos"))),
            _q("available_period", "¿Confirmas tu disponibilidad y compromiso de mínimo 2 horas semanales durante las 14 semanas?", "choice", choices=(("yes", "Sí, confirmo mi disponibilidad"), ("no", "No puedo comprometerme en este momento"))),
        ],
    },
]

BUSINESS_DETAILS = [
    _q("business_name", "Nombre de tu emprendimiento", show_if_slug="owned_business", show_if_value="yes"),
    _q("industry", "Industria de tu emprendimiento", "choice", choices=(("products", "Productos"), ("services", "Servicios"), ("technology", "Tecnología"), ("food", "Alimentos y bebidas"), ("education", "Educación"), ("health", "Salud y bienestar"), ("other", "Otra")), show_if_slug="owned_business", show_if_value="yes"),
    _q("business_description", "Descripción de tu emprendimiento", "long_text", help_text=OPEN_QUESTION_HELP, show_if_slug="owned_business", show_if_value="yes"),
    _q("business_age", "Edad del emprendimiento", "choice", choices=(("lt_1y", "Menos de 1 año"), ("1_3y", "1 a 3 años"), ("4_6y", "4 a 6 años"), ("7_10y", "7 a 10 años"), ("gt_10y", "Más de 10 años")), show_if_slug="owned_business", show_if_value="yes"),
    _q("has_employees", "¿Tienes empleados?", "choice", choices=(("yes", "Sí"), ("no", "No, trabajo sola")), show_if_slug="owned_business", show_if_value="yes"),
]

A2_SECTIONS_M = [
    {
        "title": "Información sobre tu emprendimiento",
        "description": "Primero dinos si tienes o has tenido un emprendimiento. Si respondes que sí, completa los detalles para ayudarnos con el emparejamiento.",
        "questions": [
            _q("owned_business", "¿Tienes o has tenido un emprendimiento?", "choice", choices=(("yes", "Sí"), ("no", "No"))),
            *BUSINESS_DETAILS,
        ],
    },
    {
        "title": "Motivación y compromiso",
        "description": "Estas respuestas nos ayudan a valorar tu experiencia, detectar alertas y encontrar un emparejamiento adecuado.",
        "questions": [
            _q("professional_expertise", "¿Cuál es tu área de experiencia profesional más relevante para la mentoría de mujeres microempresarias? (Ej. Marketing, Finanzas, etc.)"),
            _q("motivation", "¿Qué te motiva a ser mentora en este programa de Club Emprendo?", "long_text", help_text=OPEN_QUESTION_HELP),
            _q("why_good_mentor", "¿Por qué crees que serías una buena mentora para una emprendedora en su proceso de crecimiento personal y profesional?", "long_text", help_text=OPEN_QUESTION_HELP),
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


def apply_mentora_schema(form_a1, form_a2, transform=None):
    apply_two_part_application_schema(
        form_a1,
        form_a2,
        name="Aplicación para mentoras",
        intro=INTRO,
        a1_sections=A1_SECTIONS_M,
        a2_sections=A2_SECTIONS_M,
        transform=transform,
    )
    gate_later_sections(form_a1, form_a2, ("meets_requirements", "available_period"))
