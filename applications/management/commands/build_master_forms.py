from django.core.management.base import BaseCommand
from django.db import transaction

from applications.models import FormDefinition, Question, Choice


MASTER_SLUGS = ["E_A1", "E_A2", "M_A1", "M_A2"]


def upsert_form(slug: str, name: str, description: str = "", is_public: bool = True, is_master: bool = True):
    fd, _ = FormDefinition.objects.get_or_create(slug=slug, defaults={
        "name": name,
        "description": description,
        "is_public": is_public,
        "is_master": is_master,
        "group": None,
    })
    # Update in case you changed text later
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
    This avoids duplicate slug conflicts and makes the command safe to rerun.
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


class Command(BaseCommand):
    help = "Build/refresh master forms (E_A1, E_A2, M_A1, M_A2). Safe to rerun."

    def add_arguments(self, parser):
        parser.add_argument(
            "--only",
            choices=MASTER_SLUGS,
            help="Only rebuild one master form slug (e.g., M_A1).",
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        only = opts.get("only")

        # -------------------------
        # M_A1 MASTER (Mentoras A1)
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

        # IMPORTANT:
        # - Only ONE correo question
        # - Keep Nombre + Correo because your Application model expects name/email anyway
        # - Country + WhatsApp + the two eligibility questions + comments
        m_a1_questions = [
            {
                "text": "Nombre completo",
                "slug": "full_name",
                "field_type": Question.SHORT_TEXT,
                "required": True,
                "position": 2,
            },
            {
                "text": "Correo electrónico",
                "slug": "email",
                "field_type": Question.SHORT_TEXT,
                "required": True,
                "position": 3,
            },
            {
                "text": "País donde resides",
                "slug": "country_residence",
                "field_type": Question.SHORT_TEXT,
                "required": True,
                "position": 4,
            },
            {
                "text": "Numero de Whatsapp (con indicativo de país ej: +57 para Colombia)",
                "slug": "whatsapp_number",
                "field_type": Question.SHORT_TEXT,
                "required": True,
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
                "text": "¿Estás de acuerdo y disponible para participar en el periodo de #(month) a #(month) #(year), por 2 horas a la semana?",
                "slug": "availability_ok",
                "field_type": Question.CHOICE,
                "required": True,
                "position": 7,
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
                "position": 8,
            },
        ]

        # You can build the other masters later; for now, at least make M_A1 correct
        forms_to_build = []

        if only:
            forms_to_build = [only]
        else:
            # Build all masters if no --only flag
            forms_to_build = MASTER_SLUGS

        for slug in forms_to_build:
            if slug == "M_A1":
                fd = upsert_form(
                    slug="M_A1",
                    name="Aplicación para mentoras voluntarias (A1)",
                    description=m_a1_description,
                    is_public=True,
                    is_master=True,
                )
                rebuild_questions(fd, m_a1_questions)
                self.stdout.write(self.style.SUCCESS("Built M_A1 master form."))
            else:
                # Placeholder so command doesn’t crash if you run without --only.
                # You can fill these out next from the PDFs.
                fd = upsert_form(
                    slug=slug,
                    name=f"{slug} (MASTER) – TODO",
                    description="TODO: build from PDF",
                    is_public=True,
                    is_master=True,
                )
                Question.objects.filter(form=fd).delete()
                self.stdout.write(self.style.WARNING(f"Created stub for {slug} (fill later)."))

        self.stdout.write(self.style.SUCCESS("Done."))
