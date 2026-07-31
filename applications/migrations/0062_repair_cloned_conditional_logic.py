from django.db import migrations


ACTIVE_MASTER_SLUGS = ("E_A1", "M_A1")


def _remap_conditions(conditions, question_id_map):
    remapped = []
    for raw_condition in list(conditions or []):
        if not isinstance(raw_condition, dict):
            continue
        try:
            source_id = int(raw_condition.get("question_id") or 0)
        except (TypeError, ValueError):
            continue
        target_id = question_id_map.get(source_id)
        if not target_id:
            continue
        condition = dict(raw_condition)
        condition["question_id"] = target_id
        remapped.append(condition)
    return remapped


def repair_missing_clone_conditions(apps, schema_editor):
    FormDefinition = apps.get_model("applications", "FormDefinition")
    Question = apps.get_model("applications", "Question")
    Section = apps.get_model("applications", "Section")

    masters = {
        form.slug.upper(): form
        for form in FormDefinition.objects.filter(
            is_master=True,
            slug__in=ACTIVE_MASTER_SLUGS,
        )
    }
    if not masters:
        return

    clones = FormDefinition.objects.filter(
        is_master=False,
        group_id__isnull=False,
    ).iterator()
    for clone in clones:
        clone_slug = str(clone.slug or "").upper()
        master_slug = next(
            (
                suffix
                for suffix in ACTIVE_MASTER_SLUGS
                if clone_slug.endswith(f"_{suffix}")
            ),
            "",
        )
        master = masters.get(master_slug)
        if not master:
            continue

        master_questions = list(
            Question.objects.filter(form_id=master.id).order_by("position", "id")
        )
        clone_questions = list(
            Question.objects.filter(form_id=clone.id).order_by("position", "id")
        )
        master_by_slug = {question.slug: question for question in master_questions}
        clone_by_slug = {question.slug: question for question in clone_questions}
        if not master_by_slug or set(master_by_slug) != set(clone_by_slug):
            continue

        master_sections = list(
            Section.objects.filter(form_id=master.id).order_by("position", "id")
        )
        clone_sections = list(
            Section.objects.filter(form_id=clone.id).order_by("position", "id")
        )
        if len(master_sections) != len(clone_sections):
            continue
        if [row.position for row in master_sections] != [
            row.position for row in clone_sections
        ]:
            continue

        clone_has_logic = any(
            question.show_if_question_id
            or question.show_if_value
            or list(question.show_if_conditions or [])
            for question in clone_questions
        ) or any(
            section.show_if_question_id
            or section.show_if_question_2_id
            or section.show_if_value
            or section.show_if_value_2
            or list(section.show_if_conditions or [])
            for section in clone_sections
        )
        if clone_has_logic:
            # Preserve any group-specific edits rather than partially overwriting them.
            continue

        master_has_logic = any(
            question.show_if_question_id
            or question.show_if_value
            or list(question.show_if_conditions or [])
            for question in master_questions
        ) or any(
            section.show_if_question_id
            or section.show_if_question_2_id
            or section.show_if_value
            or section.show_if_value_2
            or list(section.show_if_conditions or [])
            for section in master_sections
        )
        if not master_has_logic:
            continue

        question_id_map = {
            master_question.id: clone_by_slug[slug].id
            for slug, master_question in master_by_slug.items()
        }
        for slug, master_question in master_by_slug.items():
            clone_question = clone_by_slug[slug]
            clone_question.show_if_question_id = question_id_map.get(
                master_question.show_if_question_id
            )
            clone_question.show_if_value = master_question.show_if_value
            clone_question.show_if_conditions = _remap_conditions(
                master_question.show_if_conditions,
                question_id_map,
            )
            clone_question.save(
                update_fields=[
                    "show_if_question",
                    "show_if_value",
                    "show_if_conditions",
                ]
            )

        for master_section, clone_section in zip(master_sections, clone_sections):
            clone_section.show_if_question_id = question_id_map.get(
                master_section.show_if_question_id
            )
            clone_section.show_if_value = master_section.show_if_value
            clone_section.show_if_question_2_id = question_id_map.get(
                master_section.show_if_question_2_id
            )
            clone_section.show_if_value_2 = master_section.show_if_value_2
            clone_section.show_if_logic = master_section.show_if_logic
            clone_section.show_if_conditions = _remap_conditions(
                master_section.show_if_conditions,
                question_id_map,
            )
            clone_section.save(
                update_fields=[
                    "show_if_question",
                    "show_if_value",
                    "show_if_question_2",
                    "show_if_value_2",
                    "show_if_logic",
                    "show_if_conditions",
                ]
            )


class Migration(migrations.Migration):

    dependencies = [
        ("applications", "0061_formdefinition_intro_page_title"),
    ]

    operations = [
        migrations.RunPython(
            repair_missing_clone_conditions,
            migrations.RunPython.noop,
        ),
    ]
