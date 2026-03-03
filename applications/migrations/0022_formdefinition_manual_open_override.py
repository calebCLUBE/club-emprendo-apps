from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("applications", "0021_question_show_if_conditions"),
    ]

    operations = [
        migrations.AddField(
            model_name="formdefinition",
            name="manual_open_override",
            field=models.BooleanField(
                blank=True,
                default=None,
                help_text="Optional manual override for open/closed state. Leave blank to follow the group schedule.",
                null=True,
            ),
        ),
    ]
