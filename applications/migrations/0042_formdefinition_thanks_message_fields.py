from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0041_formgroup_is_active"),
    ]

    operations = [
        migrations.AddField(
            model_name="formdefinition",
            name="thanks_approved_message",
            field=models.TextField(
                blank=True,
                default="",
                help_text=(
                    "Optional custom body shown on pass/approved thank-you page. "
                    "Supports placeholders like {{ group_num }}, {{ group_label }}, {{ track_label }}, {{ form_name }}."
                ),
            ),
        ),
        migrations.AddField(
            model_name="formdefinition",
            name="thanks_approved_title",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "Optional custom title shown on pass/approved thank-you page. "
                    "Supports placeholders like {{ group_num }}, {{ group_label }}, {{ track_label }}, {{ form_name }}."
                ),
                max_length=200,
            ),
        ),
        migrations.AddField(
            model_name="formdefinition",
            name="thanks_rejected_message",
            field=models.TextField(
                blank=True,
                default="",
                help_text=(
                    "Optional custom body shown on rejected/disqualified page. "
                    "Supports placeholders like {{ group_num }}, {{ group_label }}, {{ track_label }}, {{ form_name }}."
                ),
            ),
        ),
        migrations.AddField(
            model_name="formdefinition",
            name="thanks_rejected_title",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "Optional custom title shown on rejected/disqualified page. "
                    "Supports placeholders like {{ group_num }}, {{ group_label }}, {{ track_label }}, {{ form_name }}."
                ),
                max_length=200,
            ),
        ),
    ]
