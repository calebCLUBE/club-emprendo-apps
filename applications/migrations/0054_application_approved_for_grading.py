from django.db import migrations, models


def backfill_approved(apps, schema_editor):
    Application = apps.get_model("applications", "Application")
    Application.objects.filter(invited_to_second_stage=True).update(approved_for_grading=True)


class Migration(migrations.Migration):
    dependencies = [("applications", "0053_applicationdraft")]

    operations = [
        migrations.AddField(
            model_name="application",
            name="approved_for_grading",
            field=models.BooleanField(
                db_index=True,
                default=False,
                help_text="True only when the applicant completed the form on an approved outcome path.",
            ),
        ),
        migrations.RunPython(backfill_approved, migrations.RunPython.noop),
    ]
