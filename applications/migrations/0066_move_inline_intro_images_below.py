from django.db import migrations


def move_inline_intro_images_below(apps, schema_editor):
    FormDefinition = apps.get_model("applications", "FormDefinition")
    FormDefinition.objects.filter(description__icontains="<img").update(
        intro_image_position="below"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0065_websitetrafficvisit"),
    ]

    operations = [
        migrations.RunPython(
            move_inline_intro_images_below,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
