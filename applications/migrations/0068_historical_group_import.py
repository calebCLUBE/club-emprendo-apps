from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0067_backfill_all_intro_image_positions"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="HistoricalGroupImport",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("group_number", models.PositiveIntegerField(db_index=True)),
                ("group_name", models.CharField(blank=True, default="", max_length=120)),
                ("start_day", models.PositiveIntegerField(default=1)),
                ("start_month", models.CharField(max_length=30)),
                ("end_month", models.CharField(max_length=30)),
                ("year", models.PositiveIntegerField()),
                ("mentoras_filename", models.CharField(blank=True, default="", max_length=255)),
                ("emprendedoras_filename", models.CharField(blank=True, default="", max_length=255)),
                ("mentoras_data", models.JSONField(blank=True, default=dict)),
                ("emprendedoras_data", models.JSONField(blank=True, default=dict)),
                ("field_mapping", models.JSONField(blank=True, default=dict)),
                ("import_summary", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(choices=[("preview", "Preview ready"), ("imported", "Imported")], db_index=True, default="preview", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("imported_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="historical_group_imports", to=settings.AUTH_USER_MODEL)),
                ("group", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="historical_imports", to="applications.formgroup")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.CreateModel(
            name="HistoricalParticipant",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("track", models.CharField(choices=[("mentoras", "Mentoras"), ("emprendedoras", "Emprendedoras")], db_index=True, max_length=24)),
                ("name", models.CharField(blank=True, default="", max_length=255)),
                ("email", models.EmailField(blank=True, db_index=True, default="", max_length=254)),
                ("document_id", models.CharField(blank=True, db_index=True, default="", max_length=120)),
                ("whatsapp", models.CharField(blank=True, default="", max_length=120)),
                ("country", models.CharField(blank=True, default="", max_length=120)),
                ("age", models.CharField(blank=True, default="", max_length=80)),
                ("status", models.CharField(blank=True, default="", max_length=120)),
                ("source_filename", models.CharField(blank=True, default="", max_length=255)),
                ("source_row_number", models.PositiveIntegerField(default=0)),
                ("answers", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("group", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="historical_participants", to="applications.formgroup")),
                ("source_import", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="participants", to="applications.historicalgroupimport")),
            ],
            options={"ordering": ["group__number", "track", "source_row_number", "id"]},
        ),
        migrations.AddIndex(
            model_name="historicalparticipant",
            index=models.Index(fields=["group", "track", "email"], name="applicatio_group_i_14fc62_idx"),
        ),
        migrations.AddIndex(
            model_name="historicalparticipant",
            index=models.Index(fields=["group", "track", "document_id"], name="applicatio_group_i_e22a1c_idx"),
        ),
    ]
