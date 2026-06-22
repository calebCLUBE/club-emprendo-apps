from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0046_participantsheetversion"),
    ]

    operations = [
        migrations.AddField(
            model_name="question",
            name="end_form_rules",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Terminal answer rules. Each rule may show a final page and send a named stored email.",
            ),
        ),
        migrations.CreateModel(
            name="StoredEmailTemplate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("subject", models.CharField(max_length=255)),
                ("body", models.TextField(help_text="Plain text only. Line breaks are preserved; HTML is not required.")),
                ("position", models.PositiveIntegerField(default=0)),
                (
                    "form",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="stored_emails",
                        to="applications.formdefinition",
                    ),
                ),
            ],
            options={
                "ordering": ["position", "id"],
                "unique_together": {("form", "name")},
            },
        ),
    ]
