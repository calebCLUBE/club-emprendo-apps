# applications/management/commands/bootstrap_forms.py
from django.core.management.base import BaseCommand
from applications.models import FormDefinition, Question, Choice


class Command(BaseCommand):
    help = "Create FormDefinitions and Questions for E_A1, E_A2, M_A1, M_A2"

    def handle(self, *args, **options):
        self.stdout.write("Creating forms and questions...")

        # Helper to create or update forms
        def get_form(slug, name, description="", is_public=True):
            form, _ = FormDefinition.objects.get_or_create(
                slug=slug,
                defaults={"name": name, "description": description, "is_public": is_public},
            )
            form.name = name
            form.description = description
            form.is_public = is_public
            form.save()
            return form

        # ---------- E_A1: Emprendedora – Aplicación 1 ----------
        e_a1 = get_form(
            "E_A1",
            "Emprendedora – Aplicación 1",
            "Primera aplicación para emprendedoras (requisitos y disponibilidad).",
            is_public=True,
        )

        # clear old questions if re-running
        e_a1.questions.all().delete()

        q = Question.objects.create(
            form=e_a1,
            position=1,
            slug="e1_email",
            text="Correo electrónico",
            field_type=Question.SHORT_TEXT,
            required=True,
        )

        Question.objects.create(
            form=e_a1,
            position=2,
            slug="e1_full_name",
            text="Nombre completo",
            field_type=Question.SHORT_TEXT,
            required=True,
        )
        Question.objects.create(
            form=e_a1,
            position=3,
            slug="e1_country",
            text="País donde resides",
            field_type=Question.SHORT_TEXT,
            required=True,
        )
        Question.objects.create(
            form=e_a1,
            position=4,
            slug="e1_whatsapp",
            text="Número de Whatsapp (con indicativo de país)",
            field_type=Question.SHORT_TEXT,
            required=True,
        )

        q = Question.objects.create(
            form=e_a1,
            position=5,
            slug="e1_meet_requirements",
            text="¿Cumples todos los requisitos enumerados anteriormente?",
            field_type=Question.CHOICE,
            required=True,
        )
        Choice.objects.bulk_create(
            [
                Choice(question=q, label="Sí, cumplo con todos", value="yes", position=1),
                Choice(question=q, label="No, no cumplo con todos", value="no", position=2),
            ]
        )

        q = Question.objects.create(
            form=e_a1,
            position=6,
            slug="e1_available_period",
            text="¿Estás de acuerdo y disponible para participar de septiembre a diciembre 2025, por 3 horas a la semana?",
            field_type=Question.CHOICE,
            required=True,
        )
        Choice.objects.bulk_create(
            [
                Choice(question=q, label="Sí, estoy de acuerdo y disponible", value="yes", position=1),
                Choice(question=q, label="No, no puedo comprometerme", value="no", position=2),
            ]
        )

        q = Question.objects.create(
            form=e_a1,
            position=7,
            slug="e1_has_running_business",
            text="¿Actualmente tienes un emprendimiento en funcionamiento?",
            field_type=Question.CHOICE,
            required=True,
        )
        Choice.objects.bulk_create(
            [
                Choice(question=q, label="Sí, mi emprendimiento está activo", value="yes", position=1),
                Choice(question=q, label="No, solo tengo una idea o está detenido", value="no", position=2),
            ]
        )

        Question.objects.create(
            form=e_a1,
            position=8,
            slug="e1_disqualify_reason",
            text="Si no cumples algún requisito, cuéntanos cuál(es) y cualquier otro comentario.",
            field_type=Question.LONG_TEXT,
            required=False,
        )

        # ---------- E_A2: Emprendedora – Aplicación 2 ----------
        e_a2 = get_form(
            "E_A2",
            "Emprendedora – Aplicación 2",
            "Segunda aplicación para emprendedoras (negocio, motivación, horarios).",
            is_public=False,
        )
        e_a2.questions.all().delete()

        Question.objects.create(
            form=e_a2,
            position=1,
            slug="e2_id_number",
            text="¿Cuál es tu número de cédula? (documento de identidad)",
            field_type=Question.SHORT_TEXT,
            required=True,
        )
        Question.objects.create(
            form=e_a2,
            position=2,
            slug="e2_full_name",
            text="Nombre completo",
            field_type=Question.SHORT_TEXT,
            required=True,
        )
        Question.objects.create(
            form=e_a2,
            position=3,
            slug="e2_email",
            text="Correo electrónico",
            field_type=Question.SHORT_TEXT,
            required=True,
        )
        Question.objects.create(
            form=e_a2,
            position=4,
            slug="e2_whatsapp",
            text="Número de Whatsapp (con indicativo de país)",
            field_type=Question.SHORT_TEXT,
            required=True,
        )
        Question.objects.create(
            form=e_a2,
            position=5,
            slug="e2_city",
            text="Ciudad de residencia",
            field_type=Question.SHORT_TEXT,
            required=True,
        )
        Question.objects.create(
            form=e_a2,
            position=6,
            slug="e2_country",
            text="País de residencia",
            field_type=Question.SHORT_TEXT,
            required=True,
        )

        q = Question.objects.create(
            form=e_a2,
            position=7,
            slug="e2_age_range",
            text="Edad",
            field_type=Question.CHOICE,
            required=True,
        )
        Choice.objects.bulk_create(
            [
                Choice(question=q, label="18-24", value="18_24", position=1),
                Choice(question=q, label="25-34", value="25_34", position=2),
                Choice(question=q, label="35-44", value="35_44", position=3),
                Choice(question=q, label="45-54", value="45_54", position=4),
                Choice(question=q, label="55+", value="55_plus", position=5),
                Choice(question=q, label="Otra", value="other", position=6),
            ]
        )

        q = Question.objects.create(
            form=e_a2,
            position=8,
            slug="e2_prior_ce_participation",
            text="¿Has participado anteriormente en Club Emprendo?",
            field_type=Question.MULTI_CHOICE,
            required=True,
        )
        Choice.objects.bulk_create(
            [
                Choice(question=q, label="Sí, como emprendedora", value="as_emprendedora", position=1),
                Choice(question=q, label="Sí, como mentora", value="as_mentora", position=2),
                Choice(question=q, label="No, sería mi primera vez", value="first_time", position=3),
            ]
        )

        Question.objects.create(
            form=e_a2,
            position=9,
            slug="e2_privacy_accept",
            text="Acepto que los datos proporcionados sean tratados conforme al aviso de privacidad.",
            field_type=Question.BOOLEAN,
            required=True,
        )

        Question.objects.create(
            form=e_a2,
            position=10,
            slug="e2_business_name",
            text="Nombre de tu emprendimiento",
            field_type=Question.SHORT_TEXT,
            required=True,
        )

        q = Question.objects.create(
            form=e_a2,
            position=11,
            slug="e2_business_industry",
            text="Industria de tu emprendimiento",
            field_type=Question.CHOICE,
            required=True,
        )
        Choice.objects.bulk_create(
            [
                Choice(question=q, label="Productos", value="products", position=1),
                Choice(question=q, label="Servicios", value="services", position=2),
                Choice(question=q, label="Tecnología", value="technology", position=3),
                Choice(question=q, label="Otros", value="other", position=4),
            ]
        )

        Question.objects.create(
            form=e_a2,
            position=12,
            slug="e2_business_description",
            text="Descripción del negocio",
            field_type=Question.LONG_TEXT,
            required=True,
        )

        q = Question.objects.create(
            form=e_a2,
            position=13,
            slug="e2_business_age",
            text="Edad del negocio",
            field_type=Question.CHOICE,
            required=True,
        )
        Choice.objects.bulk_create(
            [
                Choice(question=q, label="Idea en desarrollo", value="idea", position=1),
                Choice(question=q, label="Recién lanzado (menos de 1 año)", value="less_1", position=2),
                Choice(question=q, label="En crecimiento (1-3 años)", value="1_3", position=3),
                Choice(question=q, label="Establecido (4-6 años)", value="4_6", position=4),
                Choice(question=q, label="Maduro (7-10 años)", value="7_10", position=5),
                Choice(question=q, label="Más de 10 años", value="more_10", position=6),
            ]
        )

        q = Question.objects.create(
            form=e_a2,
            position=14,
            slug="e2_has_employees",
            text="¿Tienes empleados?",
            field_type=Question.CHOICE,
            required=True,
        )
        Choice.objects.bulk_create(
            [
                Choice(question=q, label="Sí, empleo a una o más personas", value="yes", position=1),
                Choice(question=q, label="No, trabajo sola", value="no", position=2),
            ]
        )

        Question.objects.create(
            form=e_a2,
            position=15,
            slug="e2_growth_how_help",
            text="¿Cómo crees que este programa puede ayudarte a crecer como emprendedora?",
            field_type=Question.LONG_TEXT,
            required=True,
        )
        Question.objects.create(
            form=e_a2,
            position=16,
            slug="e2_main_challenge",
            text="¿Cuál es tu mayor desafío actualmente como emprendedora y cómo lo estás abordando?",
            field_type=Question.LONG_TEXT,
            required=True,
        )

        q = Question.objects.create(
            form=e_a2,
            position=17,
            slug="e2_commitment_program",
            text="¿Estás dispuesta a comprometerte a asistir a reuniones semanales durante 3 meses?",
            field_type=Question.CHOICE,
            required=True,
        )
        Choice.objects.bulk_create(
            [
                Choice(question=q, label="Sí, estoy comprometida", value="yes", position=1),
                Choice(question=q, label="No estoy segura", value="not_sure", position=2),
                Choice(question=q, label="No, no puedo comprometerme", value="no", position=3),
            ]
        )

        q = Question.objects.create(
            form=e_a2,
            position=18,
            slug="e2_hours_per_week",
            text="¿Cuánto tiempo puedes dedicar al programa semanalmente?",
            field_type=Question.CHOICE,
            required=True,
        )
        Choice.objects.bulk_create(
            [
                Choice(question=q, label="Menos de 2 horas", value="lt2", position=1),
                Choice(question=q, label="Entre 2 y 4 horas", value="2_4", position=2),
                Choice(question=q, label="Más de 4 horas", value="gt4", position=3),
            ]
        )

        q = Question.objects.create(
            form=e_a2,
            position=19,
            slug="e2_mentor_experience",
            text="¿Tienes alguna experiencia previa con mentoría para tu empresa?",
            field_type=Question.CHOICE,
            required=True,
        )
        Choice.objects.bulk_create(
            [
                Choice(question=q, label="Sí", value="yes", position=1),
                Choice(question=q, label="No", value="no", position=2),
            ]
        )

        Question.objects.create(
            form=e_a2,
            position=20,
            slug="e2_intro_pdf_read",
            text="¿Revisaste el PDF con la introducción al programa?",
            field_type=Question.BOOLEAN,
            required=True,
        )

        q = Question.objects.create(
            form=e_a2,
            position=21,
            slug="e2_internet_access",
            text="¿Tienes acceso a internet y dispositivo para reuniones virtuales?",
            field_type=Question.CHOICE,
            required=True,
        )
        Choice.objects.bulk_create(
            [
                Choice(question=q, label="Sí, sin problemas", value="good", position=1),
                Choice(question=q, label="Sí, pero con algunas dificultades", value="some_difficulties", position=2),
                Choice(question=q, label="No, tengo problemas de acceso", value="no_access", position=3),
            ]
        )

        q = Question.objects.create(
            form=e_a2,
            position=22,
            slug="e2_preferred_schedule",
            text="¿En qué horario te resulta más conveniente participar en sesiones virtuales?",
            field_type=Question.MULTI_CHOICE,
            required=False,
        )
        # compress the big grid into codes
        schedules = [
            ("mon_morning", "Lunes - Mañana"),
            ("mon_afternoon", "Lunes - Tarde"),
            ("mon_night", "Lunes - Noche"),
            ("tue_morning", "Martes - Mañana"),
            ("tue_afternoon", "Martes - Tarde"),
            ("tue_night", "Martes - Noche"),
            ("wed_morning", "Miércoles - Mañana"),
            ("wed_afternoon", "Miércoles - Tarde"),
            ("wed_night", "Miércoles - Noche"),
            ("thu_morning", "Jueves - Mañana"),
            ("thu_afternoon", "Jueves - Tarde"),
            ("thu_night", "Jueves - Noche"),
            ("fri_morning", "Viernes - Mañana"),
            ("fri_afternoon", "Viernes - Tarde"),
            ("fri_night", "Viernes - Noche"),
            ("sat_morning", "Sábado - Mañana"),
            ("sat_afternoon", "Sábado - Tarde"),
            ("sat_night", "Sábado - Noche"),
            ("sun_morning", "Domingo - Mañana"),
            ("sun_afternoon", "Domingo - Tarde"),
            ("sun_night", "Domingo - Noche"),
        ]
        Choice.objects.bulk_create(
            [Choice(question=q, value=val, label=lab, position=i + 1) for i, (val, lab) in enumerate(schedules)]
        )

        Question.objects.create(
            form=e_a2,
            position=23,
            slug="e2_additional_comments",
            text="¿Te gustaría dejarnos algún comentario, duda o sugerencia adicional?",
            field_type=Question.LONG_TEXT,
            required=False,
        )

        # ---------- M_A1: Mentora – Aplicación 1 ----------
        m_a1 = get_form(
            "M_A1",
            "Mentora – Aplicación 1",
            "Primera aplicación para mentoras (requisitos y disponibilidad).",
            is_public=True,
        )
        m_a1.questions.all().delete()

        Question.objects.create(
            form=m_a1,
            position=1,
            slug="m1_email",
            text="Correo electrónico",
            field_type=Question.SHORT_TEXT,
            required=True,
        )
        Question.objects.create(
            form=m_a1,
            position=2,
            slug="m1_full_name",
            text="Nombre completo",
            field_type=Question.SHORT_TEXT,
            required=True,
        )
        Question.objects.create(
            form=m_a1,
            position=3,
            slug="m1_country",
            text="País donde resides",
            field_type=Question.SHORT_TEXT,
            required=True,
        )
        Question.objects.create(
            form=m_a1,
            position=4,
            slug="m1_whatsapp",
            text="Número de Whatsapp (con indicativo de país)",
            field_type=Question.SHORT_TEXT,
            required=True,
        )
        q = Question.objects.create(
            form=m_a1,
            position=5,
            slug="m1_meet_requirements",
            text="¿Cumples todos los requisitos enumerados anteriormente para ser mentora?",
            field_type=Question.CHOICE,
            required=True,
        )
        Choice.objects.bulk_create(
            [
                Choice(question=q, label="Sí, cumplo con todos", value="yes", position=1),
                Choice(question=q, label="No, no cumplo con todos", value="no", position=2),
            ]
        )
        q = Question.objects.create(
            form=m_a1,
            position=6,
            slug="m1_available_period",
            text="¿Estás de acuerdo y disponible para participar de septiembre a diciembre 2025, por 2 horas a la semana?",
            field_type=Question.CHOICE,
            required=True,
        )
        Choice.objects.bulk_create(
            [
                Choice(question=q, label="Sí, estoy de acuerdo y disponible", value="yes", position=1),
                Choice(question=q, label="No, no puedo comprometerme", value="no", position=2),
            ]
        )
        Question.objects.create(
            form=m_a1,
            position=7,
            slug="m1_disqualify_reason",
            text="Si no cumples algún requisito, cuéntanos cuál(es) y cualquier otro comentario.",
            field_type=Question.LONG_TEXT,
            required=False,
        )

        # ---------- M_A2: Mentora – Aplicación 2 ----------
        m_a2 = get_form(
            "M_A2",
            "Mentora – Aplicación 2",
            "Segunda aplicación para mentoras (experiencia, motivación, horarios).",
            is_public=False,
        )
        m_a2.questions.all().delete()

        Question.objects.create(
            form=m_a2,
            position=1,
            slug="m2_id_number",
            text="¿Cuál es tu número de cédula? (documento de identidad)",
            field_type=Question.SHORT_TEXT,
            required=True,
        )
        Question.objects.create(
            form=m_a2,
            position=2,
            slug="m2_full_name",
            text="Nombre completo",
            field_type=Question.SHORT_TEXT,
            required=True,
        )
        Question.objects.create(
            form=m_a2,
            position=3,
            slug="m2_preferred_name",
            text="Nombre de preferencia",
            field_type=Question.SHORT_TEXT,
            required=True,
        )
        Question.objects.create(
            form=m_a2,
            position=4,
            slug="m2_certificate_name",
            text="Nombre para certificado de voluntariado",
            field_type=Question.SHORT_TEXT,
            required=True,
        )
        Question.objects.create(
            form=m_a2,
            position=5,
            slug="m2_email",
            text="Correo electrónico",
            field_type=Question.SHORT_TEXT,
            required=True,
        )
        Question.objects.create(
            form=m_a2,
            position=6,
            slug="m2_whatsapp",
            text="Número de Whatsapp (con indicativo de país)",
            field_type=Question.SHORT_TEXT,
            required=True,
        )
        Question.objects.create(
            form=m_a2,
            position=7,
            slug="m2_city",
            text="Ciudad de residencia",
            field_type=Question.SHORT_TEXT,
            required=True,
        )
        Question.objects.create(
            form=m_a2,
            position=8,
            slug="m2_country_residence",
            text="País de residencia",
            field_type=Question.SHORT_TEXT,
            required=True,
        )
        Question.objects.create(
            form=m_a2,
            position=9,
            slug="m2_country_birth",
            text="País de nacimiento",
            field_type=Question.SHORT_TEXT,
            required=True,
        )
        q = Question.objects.create(
            form=m_a2,
            position=10,
            slug="m2_age_range",
            text="Edad",
            field_type=Question.CHOICE,
            required=True,
        )
        Choice.objects.bulk_create(
            [
                Choice(question=q, label="18-24", value="18_24", position=1),
                Choice(question=q, label="25-34", value="25_34", position=2),
                Choice(question=q, label="35-44", value="35_44", position=3),
                Choice(question=q, label="45-54", value="45_54", position=4),
                Choice(question=q, label="55+", value="55_plus", position=5),
                Choice(question=q, label="Otra", value="other", position=6),
            ]
        )
        q = Question.objects.create(
            form=m_a2,
            position=11,
            slug="m2_prior_ce_participation",
            text="¿Has participado anteriormente en Club Emprendo?",
            field_type=Question.MULTI_CHOICE,
            required=True,
        )
        Choice.objects.bulk_create(
            [
                Choice(question=q, label="Como emprendedora", value="as_emprendedora", position=1),
                Choice(question=q, label="Como mentora", value="as_mentora", position=2),
                Choice(question=q, label="Sería mi primera vez", value="first_time", position=3),
            ]
        )
        Question.objects.create(
            form=m_a2,
            position=12,
            slug="m2_privacy_accept",
            text="Acepto que los datos proporcionados sean tratados conforme al aviso de privacidad.",
            field_type=Question.BOOLEAN,
            required=True,
        )

        # For brevity we won't split every requirement checkbox into scoring,
        # but they could be separate BOOLEAN or MULTI_CHOICE questions.
        Question.objects.create(
            form=m_a2,
            position=13,
            slug="m2_requirements_comments",
            text="Si no cumples alguno de los requisitos básicos o de disponibilidad, especifica cuál(es) y motivo(s).",
            field_type=Question.LONG_TEXT,
            required=False,
        )

        Question.objects.create(
            form=m_a2,
            position=14,
            slug="m2_understand_volunteer",
            text="Entiendo que ofrezco estos servicios como voluntaria y que no recibiré pago.",
            field_type=Question.BOOLEAN,
            required=True,
        )

        Question.objects.create(
            form=m_a2,
            position=15,
            slug="m2_intro_pdf_read",
            text="¿Revisaste el PDF con la introducción al programa?",
            field_type=Question.BOOLEAN,
            required=True,
        )

        q = Question.objects.create(
            form=m_a2,
            position=16,
            slug="m2_has_run_business",
            text="¿Has dirigido tu propio negocio?",
            field_type=Question.CHOICE,
            required=True,
        )
        Choice.objects.bulk_create(
            [
                Choice(question=q, label="Sí", value="yes", position=1),
                Choice(question=q, label="No", value="no", position=2),
            ]
        )

        Question.objects.create(
            form=m_a2,
            position=17,
            slug="m2_business_name",
            text="Nombre de tu emprendimiento",
            field_type=Question.SHORT_TEXT,
            required=False,
        )

        q = Question.objects.create(
            form=m_a2,
            position=18,
            slug="m2_business_industry",
            text="Industria de tu emprendimiento",
            field_type=Question.CHOICE,
            required=False,
        )
        industries = [
            ("agri", "Agricultura"),
            ("food_drink", "Alimentos y bebidas"),
            ("crafts", "Artesanías"),
            ("beauty", "Belleza y cuidado personal"),
            ("retail", "Comercio minorista"),
            ("construction", "Construcción y remodelación"),
            ("education", "Educación y capacitación"),
            ("finance_legal", "Finanzas y servicios legales"),
            ("real_estate", "Inmobiliaria"),
            ("media", "Medios y comunicaciones"),
            ("health", "Salud y bienestar"),
            ("services", "Servicios (limpieza, cuidado de niños, turismo, etc.)"),
            ("tech", "Tecnología"),
            ("textiles", "Textiles y ropa"),
            ("transport", "Transporte y logística"),
            ("other", "Otros"),
        ]
        Choice.objects.bulk_create(
            [Choice(question=q, value=val, label=lab, position=i + 1) for i, (val, lab) in enumerate(industries)]
        )

        Question.objects.create(
            form=m_a2,
            position=19,
            slug="m2_business_description",
            text="Descripción del negocio",
            field_type=Question.LONG_TEXT,
            required=False,
        )
        Question.objects.create(
            form=m_a2,
            position=20,
            slug="m2_business_location",
            text="¿Dónde operas tu negocio? (ciudad, país)",
            field_type=Question.SHORT_TEXT,
            required=False,
        )

        q = Question.objects.create(
            form=m_a2,
            position=21,
            slug="m2_business_age",
            text="¿Cuánto tiempo has estado operando?",
            field_type=Question.CHOICE,
            required=False,
        )
        Choice.objects.bulk_create(
            [
                Choice(question=q, label="0-1 año", value="0_1", position=1),
                Choice(question=q, label="1-5 años", value="1_5", position=2),
                Choice(question=q, label="5-10 años", value="5_10", position=3),
                Choice(question=q, label="10+ años", value="10_plus", position=4),
            ]
        )

        q = Question.objects.create(
            form=m_a2,
            position=22,
            slug="m2_has_employees",
            text="¿Tienes (o tuviste) empleados?",
            field_type=Question.CHOICE,
            required=False,
        )
        Choice.objects.bulk_create(
            [
                Choice(question=q, label="Sí, empleo a una o más personas", value="yes", position=1),
                Choice(question=q, label="No, trabajo sola", value="no", position=2),
            ]
        )

        Question.objects.create(
            form=m_a2,
            position=23,
            slug="m2_expertise_area",
            text="¿Cuál es tu área de experiencia profesional más relevante para la mentoría?",
            field_type=Question.SHORT_TEXT,
            required=True,
        )
        Question.objects.create(
            form=m_a2,
            position=24,
            slug="m2_motivation",
            text="¿Qué te motiva a ser mentora en este programa de Club Emprendo?",
            field_type=Question.LONG_TEXT,
            required=True,
        )
        Question.objects.create(
            form=m_a2,
            position=25,
            slug="m2_why_good_mentor",
            text="¿Por qué crees que serías una buena mentora para una emprendedora?",
            field_type=Question.LONG_TEXT,
            required=True,
        )

        # mentoring/coaching experience matrix
        q = Question.objects.create(
            form=m_a2,
            position=26,
            slug="m2_coach_experience",
            text="¿Tienes experiencia previa como mentora o coach?",
            field_type=Question.CHOICE,
            required=False,
        )
        Choice.objects.bulk_create(
            [
                Choice(question=q, label="Sí", value="yes", position=1),
                Choice(question=q, label="No", value="no", position=2),
            ]
        )
        q = Question.objects.create(
            form=m_a2,
            position=27,
            slug="m2_student_experience",
            text="¿Has recibido mentoría o coaching como estudiante/emprendedora?",
            field_type=Question.CHOICE,
            required=False,
        )
        Choice.objects.bulk_create(
            [
                Choice(question=q, label="Sí", value="yes", position=1),
                Choice(question=q, label="No", value="no", position=2),
            ]
        )

        Question.objects.create(
            form=m_a2,
            position=28,
            slug="m2_coach_experience_details",
            text="Si has tenido experiencia con la mentoría o el coaching, descríbela brevemente.",
            field_type=Question.LONG_TEXT,
            required=False,
        )

        q = Question.objects.create(
            form=m_a2,
            position=29,
            slug="m2_hours_per_week",
            text="¿Cuánto tiempo puedes dedicar al programa semanalmente?",
            field_type=Question.CHOICE,
            required=True,
        )
        Choice.objects.bulk_create(
            [
                Choice(question=q, label="Menos de 2 horas", value="lt2", position=1),
                Choice(question=q, label="2-3 horas", value="2_3", position=2),
                Choice(question=q, label="3-4 horas", value="3_4", position=3),
                Choice(question=q, label="Más de 4 horas", value="gt4", position=4),
            ]
        )

        q = Question.objects.create(
            form=m_a2,
            position=30,
            slug="m2_preferred_schedule",
            text="¿En qué horario te resulta más conveniente participar en sesiones virtuales?",
            field_type=Question.MULTI_CHOICE,
            required=False,
        )
        Choice.objects.bulk_create(
            [Choice(question=q, value=val, label=lab, position=i + 1) for i, (val, lab) in enumerate(schedules)]
        )

        Question.objects.create(
            form=m_a2,
            position=31,
            slug="m2_additional_comments",
            text="¿Hay algo más que te gustaría compartir con nosotras?",
            field_type=Question.LONG_TEXT,
            required=False,
        )

        self.stdout.write(self.style.SUCCESS("Forms and questions created."))
