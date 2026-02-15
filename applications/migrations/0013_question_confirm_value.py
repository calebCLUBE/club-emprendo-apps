from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("applications", "0012_formdefinition_default_section_title"),
    ]

    operations = [
        migrations.AddField(
            model_name="question",
            name="confirm_value",
            field=models.BooleanField(
                default=False,
                help_text="If true, render a second confirmation input that must match.",
            ),
        ),
    ]
