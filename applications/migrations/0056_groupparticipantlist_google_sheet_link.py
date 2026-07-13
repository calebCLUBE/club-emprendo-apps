from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0055_backfill_managed_approval_outcomes"),
    ]

    operations = [
        migrations.AddField(
            model_name="groupparticipantlist",
            name="google_sheet_id",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="groupparticipantlist",
            name="google_sheet_last_synced_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="groupparticipantlist",
            name="google_sheet_sync_error",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="groupparticipantlist",
            name="google_sheet_tabs",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="groupparticipantlist",
            name="google_sheet_url",
            field=models.URLField(blank=True, default="", max_length=1000),
        ),
    ]
