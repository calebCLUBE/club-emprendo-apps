from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("applications", "0017_section_multiple_conditions"),
    ]

    operations = [
        migrations.AddField(
            model_name="section",
            name="show_if_conditions",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Lista de condiciones [{'question_id':..., 'value':...}]. Usa lógica AND/OR.",
            ),
        ),
    ]
