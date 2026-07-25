from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("applications", "0056_groupparticipantlist_google_sheet_link"),
    ]

    operations = [
        migrations.AddField(
            model_name="question",
            name="terms_content",
            field=models.TextField(
                blank=True,
                help_text=(
                    "Full terms shown on the linked details page when this is a "
                    "Terms and conditions question."
                ),
            ),
        ),
        migrations.AlterField(
            model_name="question",
            name="field_type",
            field=models.CharField(
                choices=[
                    ("short_text", "Short text"),
                    ("long_text", "Long text"),
                    ("integer", "Integer"),
                    ("boolean", "Yes/No (checkbox)"),
                    ("terms_acceptance", "Terms and conditions acceptance"),
                    ("choice", "Single choice"),
                    ("multi_choice", "Multiple choice"),
                    ("choice_grid", "Multiple choice grid"),
                ],
                max_length=20,
            ),
        ),
    ]
