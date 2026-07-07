from django.db import migrations


def is_yes(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "si", "sí", "on"}


def criterion(answers, aggregate_slugs, prefixes):
    for slug in aggregate_slugs:
        if str(answers.get(slug, "") or "").strip():
            return is_yes(answers[slug])
    values = [
        value
        for slug, value in answers.items()
        if any(slug.startswith(prefix) for prefix in prefixes)
        and str(value or "").strip()
    ]
    return all(is_yes(value) for value in values) if values else None


def inferred_eligible(slug, answers):
    requirements = criterion(
        answers,
        ("meets_requirements", "m1_meet_requirements", "m1_meets_requirements", "m1_requirements_ok", "e1_meet_requirements"),
        ("req_basic_", "m1_req_basic_", "e1_req_basic_"),
    )
    availability = criterion(
        answers,
        ("available_period", "availability_ok", "m1_availability_ok", "m1_available_period", "m1_available", "e1_available_period"),
        ("req_avail_", "m1_req_avail_", "e1_req_avail_"),
    )
    recognized = [result for result in (requirements, availability) if result is not None]
    if slug.endswith("E_A1"):
        active_business = criterion(
            answers,
            ("business_active", "e1_has_running_business"),
            ("req_business_active", "e1_req_business_active"),
        )
        if active_business is None:
            active_business = requirements
        if active_business is not None:
            recognized.append(active_business)
    return all(recognized) if recognized else None


def backfill_managed_outcomes(apps, schema_editor):
    Application = apps.get_model("applications", "Application")
    FormDefinition = apps.get_model("applications", "FormDefinition")
    Question = apps.get_model("applications", "Question")
    Answer = apps.get_model("applications", "Answer")

    for form in FormDefinition.objects.all().iterator():
        slug = str(form.slug or "")
        if not (slug.endswith("E_A1") or slug.endswith("M_A1")):
            continue
        terminal_questions = {}
        for question in Question.objects.filter(form_id=form.id).exclude(end_form_rules=[]):
            expected = {
                str(rule.get("value") or "").strip().lower()
                for rule in list(question.end_form_rules or [])
                if str(rule.get("value") or "").strip()
            }
            if expected:
                terminal_questions[question.id] = expected

        for application in Application.objects.filter(form_id=form.id).iterator():
            answer_rows = list(
                Answer.objects.filter(application_id=application.id).values_list(
                    "question_id", "question__slug", "value"
                )
            )
            answers = {question_slug: value for _, question_slug, value in answer_rows}
            if terminal_questions:
                values_by_question = {
                    question_id: str(value or "").strip().lower()
                    for question_id, _, value in answer_rows
                }
                approved = not any(
                    values_by_question.get(question_id, "") in expected_values
                    for question_id, expected_values in terminal_questions.items()
                )
            else:
                inferred = inferred_eligible(slug, answers)
                approved = bool(application.invited_to_second_stage) if inferred is None else inferred
            Application.objects.filter(id=application.id).update(
                approved_for_grading=approved
            )


class Migration(migrations.Migration):
    dependencies = [("applications", "0054_application_approved_for_grading")]

    operations = [
        migrations.RunPython(backfill_managed_outcomes, migrations.RunPython.noop),
    ]
