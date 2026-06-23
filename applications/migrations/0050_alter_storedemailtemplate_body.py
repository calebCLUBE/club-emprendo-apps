from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0049_formdefinition_approval_email_name"),
    ]

    operations = [
        migrations.AlterField(
            model_name="storedemailtemplate",
            name="body",
            field=models.TextField(
                help_text="Use the formatting toolbar; HTML knowledge is not required."
            ),
        ),
    ]
