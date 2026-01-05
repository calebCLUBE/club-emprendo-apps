# applications/surveys_seed.py
from __future__ import annotations

from django.db import transaction

from applications.models import FormDefinition, Question, Choice


def _upsert_form(*, slug: str, name: str, description: str = "") -> FormDefinition:
    fd, created = FormDefinition.objects.get_or_create(
        slug=slug,
        defaults={"name": name},
    )

    changed = False
    if fd.name != name:
        fd.name = name
        changed = True

    # Only set description if your model has it
    if hasattr(fd, "description"):
        if (fd.description or "") != (description or ""):
            fd.description = description or ""
            changed = True

    if changed:
        fd.save()

    return fd


def _upsert_question(
    *,
    fd: FormDefinition,
    slug: str,
    text: str,
    field_type: str,
    position: int,
    required: bool = False,
    help_text: str = "",
    active: bool = True,
) -> Question:
    q, _ = Question.objects.get_or_create(
        slug=slug,
        defaults={
            "text": text,
            "field_type": field_type,
            "position": position,
            "required": required,
            "help_text": help_text,
            "active": active,
        },
    )

    changed = False
    for attr, val in [
        ("text", text),
        ("field_type", field_type),
        ("position", position),
        ("required", required),
        ("help_text", help_text),
        ("active", active),
    ]:
        if getattr(q, attr) != val:
            setattr(q, attr, val)
            changed = True

    if changed:
        q.save()

    # attach to form (M2M)
    if hasattr(fd, "questions") and not fd.questions.filter(pk=q.pk).exists():
        fd.questions.add(q)

    return q


def _replace_choices(q: Question, choices: list[tuple[str, str]]):
    """
    choices = [(label, value), ...]
    Fully replaces choices for this question.
    """
    q.choices.all().delete()
    for idx, (label, value) in enumerate(choices, start=1):
        Choice.objects.create(question=q, label=label, value=value, position=idx)


# -------------------------
# SURVEY BUILDERS (EDIT THESE)
# -------------------------

@transaction.atomic
def build_survey_primer_e() -> FormDefinition:
    fd = _upsert_form(
        slug="PRIMER_E",
        name="PRIMER · Emprendedoras",
        description="Encuesta inicial (antes del programa).",
    )

    _upsert_question(
        fd=fd,
        slug="full_name",
        text="Nombre completo",
        field_type=Question.SHORT_TEXT,
        position=1,
        required=True,
    )

    _upsert_question(
        fd=fd,
        slug="email",
        text="Correo electrónico",
        field_type=Question.SHORT_TEXT,
        position=2,
        required=True,
    )

    q3 = _upsert_question(
        fd=fd,
        slug="expectations",
        text="¿Qué esperas aprender o mejorar con el programa?",
        field_type=Question.LONG_TEXT,
        position=3,
        required=True,
    )

    q4 = _upsert_question(
        fd=fd,
        slug="confidence",
        text="En este momento, ¿qué tan segura te sientes liderando tu emprendimiento?",
        field_type=Question.CHOICE,
        position=4,
        required=True,
    )
    _replace_choices(q4, [
        ("Nada segura", "not_confident"),
        ("Poco segura", "low"),
        ("Neutral", "neutral"),
        ("Segura", "confident"),
        ("Muy segura", "very_confident"),
    ])

    return fd


@transaction.atomic
def build_survey_primer_m() -> FormDefinition:
    fd = _upsert_form(
        slug="PRIMER_M",
        name="PRIMER · Mentoras",
        description="Encuesta inicial (antes del programa).",
    )

    _upsert_question(
        fd=fd,
        slug="full_name",
        text="Nombre completo",
        field_type=Question.SHORT_TEXT,
        position=1,
        required=True,
    )

    _upsert_question(
        fd=fd,
        slug="email",
        text="Correo electrónico",
        field_type=Question.SHORT_TEXT,
        position=2,
        required=True,
    )

    _upsert_question(
        fd=fd,
        slug="mentor_goals",
        text="¿Qué esperas aportar como mentora en este grupo?",
        field_type=Question.LONG_TEXT,
        position=3,
        required=True,
    )

    q4 = _upsert_question(
        fd=fd,
        slug="time_commitment_ok",
        text="¿Te sientes cómoda con el compromiso de tiempo semanal?",
        field_type=Question.CHOICE,
        position=4,
        required=True,
    )
    _replace_choices(q4, [
        ("Sí", "yes"),
        ("No", "no"),
    ])

    return fd


@transaction.atomic
def build_survey_final_e() -> FormDefinition:
    fd = _upsert_form(
        slug="FINAL_E",
        name="FINAL · Emprendedoras",
        description="Encuesta final (después del programa).",
    )

    _upsert_question(
        fd=fd,
        slug="full_name",
        text="Nombre completo",
        field_type=Question.SHORT_TEXT,
        position=1,
        required=True,
    )

    _upsert_question(
        fd=fd,
        slug="email",
        text="Correo electrónico",
        field_type=Question.SHORT_TEXT,
        position=2,
        required=True,
    )

    q3 = _upsert_question(
        fd=fd,
        slug="rating",
        text="¿Qué calificación le darías al programa?",
        field_type=Question.CHOICE,
        position=3,
        required=True,
    )
    _replace_choices(q3, [
        ("1", "1"),
        ("2", "2"),
        ("3", "3"),
        ("4", "4"),
        ("5", "5"),
    ])

    _upsert_question(
        fd=fd,
        slug="most_valuable",
        text="¿Qué fue lo más valioso que te llevas del programa?",
        field_type=Question.LONG_TEXT,
        position=4,
        required=True,
    )

    return fd


@transaction.atomic
def build_survey_final_m() -> FormDefinition:
    fd = _upsert_form(
        slug="FINAL_M",
        name="FINAL · Mentoras",
        description="Encuesta final (después del programa).",
    )

    _upsert_question(
        fd=fd,
        slug="full_name",
        text="Nombre completo",
        field_type=Question.SHORT_TEXT,
        position=1,
        required=True,
    )

    _upsert_question(
        fd=fd,
        slug="email",
        text="Correo electrónico",
        field_type=Question.SHORT_TEXT,
        position=2,
        required=True,
    )

    q3 = _upsert_question(
        fd=fd,
        slug="experience",
        text="¿Cómo calificarías tu experiencia como mentora en esta cohorte?",
        field_type=Question.CHOICE,
        position=3,
        required=True,
    )
    _replace_choices(q3, [
        ("Muy mala", "very_bad"),
        ("Mala", "bad"),
        ("Neutral", "neutral"),
        ("Buena", "good"),
        ("Muy buena", "very_good"),
    ])

    _upsert_question(
        fd=fd,
        slug="improvements",
        text="¿Qué mejorarías para la próxima cohorte?",
        field_type=Question.LONG_TEXT,
        position=4,
        required=False,
    )

    return fd


@transaction.atomic
def seed_surveys():
    """
    Call this to create/update all 4 surveys.
    """
    build_survey_primer_e()
    build_survey_primer_m()
    build_survey_final_e()
    build_survey_final_m()
