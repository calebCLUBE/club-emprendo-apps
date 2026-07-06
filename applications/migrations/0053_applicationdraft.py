import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("applications", "0052_gradingresponseweight")]

    operations = [
        migrations.CreateModel(
            name="ApplicationDraft",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("answers", models.JSONField(blank=True, default=dict)),
                ("name", models.CharField(blank=True, max_length=200)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("current_section", models.PositiveIntegerField(default=1)),
                ("total_sections", models.PositiveIntegerField(default=1)),
                ("answered_questions", models.PositiveIntegerField(default=0)),
                ("total_questions", models.PositiveIntegerField(default=0)),
                ("progress_percent", models.PositiveSmallIntegerField(default=0)),
                ("last_question_slug", models.CharField(blank=True, max_length=160)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("completed_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("application", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="source_draft", to="applications.application")),
                ("form", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="drafts", to="applications.formdefinition")),
            ],
            options={"ordering": ["-updated_at"]},
        ),
        migrations.AddIndex(
            model_name="applicationdraft",
            index=models.Index(fields=["form", "completed_at", "updated_at"], name="application_form_id_f4b7d4_idx"),
        ),
    ]
