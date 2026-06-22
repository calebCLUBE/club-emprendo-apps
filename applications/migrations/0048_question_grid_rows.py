from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0047_stored_email_template_and_terminal_rules"),
    ]

    operations = [
        migrations.AddField(
            model_name="question",
            name="grid_rows",
            field=models.TextField(
                blank=True,
                help_text="For multiple choice grids: enter one row label per line.",
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
                    ("choice", "Single choice"),
                    ("multi_choice", "Multiple choice"),
                    ("choice_grid", "Multiple choice grid"),
                ],
                max_length=20,
            ),
        ),
    ]
