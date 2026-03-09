from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("applications", "0022_formdefinition_manual_open_override"),
    ]

    operations = [
        migrations.AddField(
            model_name="formgroup",
            name="reminder_1_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Fecha/hora para el recordatorio automático #1 de A2.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="formgroup",
            name="reminder_1_sent_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="formgroup",
            name="reminder_2_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Fecha/hora para el recordatorio automático #2 de A2.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="formgroup",
            name="reminder_2_sent_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="formgroup",
            name="reminder_3_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Fecha/hora para el recordatorio automático #3 de A2.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="formgroup",
            name="reminder_3_sent_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
    ]
