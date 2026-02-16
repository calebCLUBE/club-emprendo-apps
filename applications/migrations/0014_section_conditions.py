from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("applications", "0013_question_confirm_value"),
    ]

    operations = [
        migrations.AddField(
            model_name="section",
            name="show_if_question",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional: only show this section if the question equals the expected value.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="sections_conditioned",
                to="applications.question",
            ),
        ),
        migrations.AddField(
            model_name="section",
            name="show_if_value",
            field=models.CharField(
                blank=True,
                help_text="Match is case-insensitive; works for short_text/choice/boolean. Leave blank for no condition.",
                max_length=200,
            ),
        ),
    ]
