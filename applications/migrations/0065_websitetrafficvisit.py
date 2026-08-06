from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0064_formgroup_incomplete_reminder_template"),
    ]

    operations = [
        migrations.CreateModel(
            name="WebsiteTrafficVisit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("visitor_id", models.UUIDField(db_index=True)),
                ("visit_date", models.DateField(db_index=True)),
                ("path", models.CharField(max_length=500)),
                ("pageviews", models.PositiveIntegerField(default=0)),
                ("first_seen_at", models.DateTimeField()),
                ("last_seen_at", models.DateTimeField(db_index=True)),
            ],
            options={
                "ordering": ["-last_seen_at"],
                "indexes": [
                    models.Index(fields=["visit_date", "path"], name="application_visit_d_acf249_idx"),
                    models.Index(fields=["visit_date", "visitor_id"], name="application_visit_d_c766cc_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("visitor_id", "visit_date", "path"),
                        name="unique_website_visitor_date_path",
                    )
                ],
            },
        ),
    ]
