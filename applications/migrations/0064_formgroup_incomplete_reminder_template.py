from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0063_applicationdraft_visitor_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="formgroup",
            name="incomplete_reminder_subject",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "Subject used for incomplete-application reminders. Leave blank to use the default. "
                    "Supports {{ group_label }}, {{ form_name }}, and {{ applicant_name }}."
                ),
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="formgroup",
            name="incomplete_reminder_body",
            field=models.TextField(
                blank=True,
                default="",
                help_text=(
                    "Plain-text template used for incomplete-application reminders. Leave blank to use "
                    "the default. Supports {{ greeting }}, {{ applicant_name }}, {{ group_label }}, "
                    "{{ form_name }}, and {{ resume_url }}."
                ),
            ),
        ),
    ]
