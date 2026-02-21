from django.db import migrations, models
import json


def backfill_legacy(apps, schema_editor):
    Question = apps.get_model("applications", "Question")
    for q in Question.objects.all():
        conds = []
        if getattr(q, "show_if_question_id", None) and getattr(q, "show_if_value", ""):
            conds.append({"question_id": q.show_if_question_id, "value": q.show_if_value})
        q.show_if_conditions = conds
        q.save(update_fields=["show_if_conditions"])


class Migration(migrations.Migration):

    dependencies = [
        ("applications", "0020_fix_missing_show_if_conditions"),
    ]

    operations = [
        migrations.AddField(
            model_name="question",
            name="show_if_conditions",
            field=models.JSONField(
                default=list,
                blank=True,
                help_text="Lista de condiciones [{'question_id':..., 'value':...}]. Usa lógica OR (se muestra si cualquiera coincide).",
            ),
        ),
        migrations.RunPython(backfill_legacy, migrations.RunPython.noop, elidable=True),
    ]
