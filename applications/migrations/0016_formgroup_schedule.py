from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("applications", "0015_formgroup_start_day_deadline"),
    ]

    operations = [
        migrations.AddField(
            model_name="formgroup",
            name="open_at",
            field=models.DateTimeField(
                null=True,
                blank=True,
                help_text="Fecha/hora en la que las aplicaciones del grupo se abrirán automáticamente.",
            ),
        ),
        migrations.AddField(
            model_name="formgroup",
            name="close_at",
            field=models.DateTimeField(
                null=True,
                blank=True,
                help_text="Fecha/hora en la que las aplicaciones del grupo se cerrarán automáticamente.",
            ),
        ),
    ]
