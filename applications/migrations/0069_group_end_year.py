from django.db import migrations, models


def copy_start_year_to_end_year(apps, schema_editor):
    FormGroup = apps.get_model("applications", "FormGroup")
    HistoricalGroupImport = apps.get_model("applications", "HistoricalGroupImport")
    for group in FormGroup.objects.filter(end_year__isnull=True).iterator():
        group.end_year = group.year
        group.save(update_fields=["end_year"])
    for item in HistoricalGroupImport.objects.filter(end_year__isnull=True).iterator():
        item.end_year = item.year
        item.save(update_fields=["end_year"])


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0068_historical_group_import"),
    ]

    operations = [
        migrations.AddField(
            model_name="formgroup",
            name="end_year",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Year in which the group ends. Defaults to the start year.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="historicalgroupimport",
            name="end_year",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.RunPython(copy_start_year_to_end_year, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="historicalgroupimport",
            name="end_year",
            field=models.PositiveIntegerField(),
        ),
    ]
