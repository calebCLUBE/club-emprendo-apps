from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("applications", "0040_formgroup_custom_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="formgroup",
            name="is_active",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "If disabled, the group is archived/hidden in participant-management views "
                    "while preserving application database records."
                ),
            ),
        ),
    ]

