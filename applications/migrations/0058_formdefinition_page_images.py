from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("applications", "0057_question_terms_content"),
    ]

    operations = [
        migrations.AddField(
            model_name="formdefinition",
            name="intro_image_data",
            field=models.TextField(
                blank=True,
                default="",
                editable=False,
                help_text="Optimized intro-page image stored as a data URI.",
            ),
        ),
        migrations.AddField(
            model_name="formdefinition",
            name="thanks_approved_image_data",
            field=models.TextField(
                blank=True,
                default="",
                editable=False,
                help_text="Optimized approval-page image stored as a data URI.",
            ),
        ),
    ]
