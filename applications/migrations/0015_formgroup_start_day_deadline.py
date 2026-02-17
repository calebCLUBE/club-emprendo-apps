from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("applications", "0014_section_conditions"),
    ]

    operations = [
        migrations.AddField(
            model_name="formgroup",
            name="start_day",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="formgroup",
            name="a2_deadline",
            field=models.DateField(
                blank=True,
                null=True,
                help_text="Fecha límite para completar la aplicación 2.",
            ),
        ),
    ]
