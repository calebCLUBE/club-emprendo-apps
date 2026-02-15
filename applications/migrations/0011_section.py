from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("applications", "0010_pairingjob"),
    ]

    operations = [
        migrations.CreateModel(
            name="Section",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("title", models.CharField(max_length=200)),
                ("description", models.TextField(blank=True)),
                ("position", models.PositiveIntegerField(default=0)),
                (
                    "form",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sections",
                        to="applications.formdefinition",
                    ),
                ),
            ],
            options={
                "ordering": ["position", "id"],
                "unique_together": {("form", "title")},
            },
        ),
        migrations.AddField(
            model_name="question",
            name="section",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="questions",
                to="applications.section",
            ),
        ),
        migrations.RunSQL(
            "UPDATE applications_question SET field_type='choice' WHERE field_type='single_choice';",
            reverse_sql="UPDATE applications_question SET field_type='single_choice' WHERE field_type='choice';",
        ),
    ]
