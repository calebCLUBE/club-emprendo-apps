from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("applications", "0062_repair_cloned_conditional_logic"),
    ]

    operations = [
        migrations.AddField(
            model_name="applicationdraft",
            name="visitor_id",
            field=models.UUIDField(
                blank=True,
                db_index=True,
                editable=False,
                help_text="First-party browser identifier used to reconnect return visits.",
                null=True,
            ),
        ),
    ]
