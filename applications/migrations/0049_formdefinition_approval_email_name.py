from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0048_question_grid_rows"),
    ]

    operations = [
        migrations.AddField(
            model_name="formdefinition",
            name="approval_email_name",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "Stored email sent when an applicant reaches the end and submits without "
                    "triggering an end-application rule."
                ),
                max_length=120,
            ),
        ),
    ]
