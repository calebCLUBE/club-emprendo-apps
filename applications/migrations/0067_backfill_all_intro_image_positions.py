from django.db import migrations
from django.db.models import Q


def move_all_existing_intro_images_below(apps, schema_editor):
    FormDefinition = apps.get_model("applications", "FormDefinition")
    FormDefinition.objects.filter(
        Q(description__icontains="<img")
        | (Q(intro_image_data__isnull=False) & ~Q(intro_image_data=""))
    ).update(intro_image_position="below")


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0066_move_inline_intro_images_below"),
    ]

    operations = [
        migrations.RunPython(
            move_all_existing_intro_images_below,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
