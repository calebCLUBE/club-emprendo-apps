from django.db import migrations


def rename_group8_a1_forms(apps, schema_editor):
    FormDefinition = apps.get_model("applications", "FormDefinition")
    FormDefinition.objects.filter(slug__in=["G8_E_A1", "G8_M_A1"]).update(name="April Recruitment")


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0037_dropboxsignwebhookevent_and_more"),
    ]

    operations = [
        migrations.RunPython(rename_group8_a1_forms, migrations.RunPython.noop),
    ]

