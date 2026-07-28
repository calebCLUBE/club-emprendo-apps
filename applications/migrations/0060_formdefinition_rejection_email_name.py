from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("applications", "0059_formdefinition_image_placement"),
    ]

    operations = [
        migrations.AddField(
            model_name="formdefinition",
            name="rejection_email_name",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "Default stored email sent when an answer ends the application. "
                    "An individual ending rule may override it."
                ),
                max_length=120,
            ),
        ),
    ]
