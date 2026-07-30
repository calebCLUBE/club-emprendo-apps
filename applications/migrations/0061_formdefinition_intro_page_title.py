from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("applications", "0060_formdefinition_rejection_email_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="formdefinition",
            name="intro_page_title",
            field=models.CharField(
                blank=True,
                default="Antes de comenzar",
                help_text=(
                    "Título mostrado encima del contenido de la página de introducción."
                ),
                max_length=200,
            ),
        ),
    ]
