from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("applications", "0058_formdefinition_page_images"),
    ]

    operations = [
        migrations.AddField(
            model_name="formdefinition",
            name="intro_image_alignment",
            field=models.CharField(
                choices=[
                    ("left", "Left"),
                    ("center", "Center"),
                    ("right", "Right"),
                ],
                default="center",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="formdefinition",
            name="intro_image_position",
            field=models.CharField(
                choices=[
                    ("above", "Above page content"),
                    ("below", "Below page content"),
                ],
                default="above",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="formdefinition",
            name="intro_image_width",
            field=models.CharField(
                choices=[
                    ("small", "Small"),
                    ("medium", "Medium"),
                    ("full", "Full width"),
                ],
                default="full",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="formdefinition",
            name="thanks_approved_image_alignment",
            field=models.CharField(
                choices=[
                    ("left", "Left"),
                    ("center", "Center"),
                    ("right", "Right"),
                ],
                default="center",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="formdefinition",
            name="thanks_approved_image_position",
            field=models.CharField(
                choices=[
                    ("above", "Above page content"),
                    ("below", "Below page content"),
                ],
                default="above",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="formdefinition",
            name="thanks_approved_image_width",
            field=models.CharField(
                choices=[
                    ("small", "Small"),
                    ("medium", "Medium"),
                    ("full", "Full width"),
                ],
                default="full",
                max_length=10,
            ),
        ),
    ]
