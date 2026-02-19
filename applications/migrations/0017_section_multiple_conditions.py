from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("applications", "0016_formgroup_schedule"),
    ]

    operations = [
        migrations.AddField(
            model_name="section",
            name="show_if_logic",
            field=models.CharField(
                choices=[("AND", "AND"), ("OR", "OR")],
                default="AND",
                help_text="Cómo combinar las 2 condiciones (si ambas existen).",
                max_length=3,
            ),
        ),
        migrations.AddField(
            model_name="section",
            name="show_if_question_2",
            field=models.ForeignKey(
                blank=True,
                help_text="Segunda condición opcional.",
                null=True,
                on_delete=models.SET_NULL,
                related_name="sections_conditioned_second",
                to="applications.question",
            ),
        ),
        migrations.AddField(
            model_name="section",
            name="show_if_value_2",
            field=models.CharField(blank=True, max_length=200),
        ),
    ]
