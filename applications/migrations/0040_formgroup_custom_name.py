from django.db import migrations, models


def seed_april_recruitment_name(apps, schema_editor):
    FormGroup = apps.get_model("applications", "FormGroup")
    g = FormGroup.objects.filter(number=800).first()
    if g and not (getattr(g, "custom_name", "") or "").strip():
        g.custom_name = "April Recruitment"
        g.save(update_fields=["custom_name"])


class Migration(migrations.Migration):

    dependencies = [
        ("applications", "0039_move_april_recruitment_pool_to_group_800"),
    ]

    operations = [
        migrations.AddField(
            model_name="formgroup",
            name="custom_name",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Optional custom label for this group in admin pages.",
                max_length=120,
            ),
        ),
        migrations.RunPython(seed_april_recruitment_name, migrations.RunPython.noop),
    ]

