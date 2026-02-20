from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("applications", "0016_formgroup_schedule"),
    ]

    operations = [
        migrations.AddField(
            model_name="question",
            name="show_if_question",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="dependent_questions",
                to="applications.question",
                help_text="Optional: only show this question when another question has the expected value.",
            ),
        ),
        migrations.AddField(
            model_name="question",
            name="show_if_value",
            field=models.CharField(
                max_length=200,
                blank=True,
                help_text="Case-insensitive match. For yes/no, use 'yes' or 'no'. For choices, use the stored choice value.",
            ),
        ),
    ]
