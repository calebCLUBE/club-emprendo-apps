from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("applications", "0024_application_second_stage_reminder_due_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="formgroup",
            name="use_combined_application",
            field=models.BooleanField(
                default=False,
                help_text="If enabled, this group uses the combined application flow/database view.",
            ),
        ),
    ]

