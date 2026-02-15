from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("applications", "0011_section"),
    ]

    operations = [
        migrations.AddField(
            model_name="formdefinition",
            name="default_section_title",
            field=models.CharField(
                default="Preguntas generales",
                help_text="Título para las preguntas sin sección asignada.",
                max_length=200,
            ),
        ),
    ]
