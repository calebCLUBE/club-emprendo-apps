import logging

from django.db import migrations
from django.db.models import Q
from django.utils import timezone


logger = logging.getLogger(__name__)
PREVIOUS_SPREADSHEET_ID = "1atqd03PIUxE98NNIkLWwlT0SPPZjZVP_4BpIZpKtO_g"
SPREADSHEET_ID = "1JA5s-IxNzPNGNPLADAwwdw45KBZHa5ZQbeQrT2ExCzc"
SPREADSHEET_URL = (
    f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit"
    "?gid=1806697888#gid=1806697888"
)


def relink_group_10(apps, schema_editor):
    """Repair the confirmed obsolete link without replacing later admin edits."""
    participant_model = apps.get_model("applications", "GroupParticipantList")
    database = schema_editor.connection.alias
    participants = participant_model.objects.using(database).filter(group__number=10).filter(
        Q(google_sheet_url="", google_sheet_id=PREVIOUS_SPREADSHEET_ID)
        | Q(google_sheet_url__startswith=(
            f"https://docs.google.com/spreadsheets/d/{PREVIOUS_SPREADSHEET_ID}/"
        ))
    )
    participant_ids = list(participants.values_list("pk", flat=True))
    if not participant_ids:
        return
    participants.update(
        google_sheet_url=SPREADSHEET_URL,
        google_sheet_id=SPREADSHEET_ID,
        google_sheet_tabs=[],
        google_sheet_last_synced_at=None,
        google_sheet_sync_error="Participant source corrected; first refresh pending.",
        updated_at=timezone.now(),
    )

    # The link repair commits independently. If Google is temporarily
    # unavailable, keep the corrected link and expose the error for the next
    # normal refresh instead of reverting to the obsolete workbook.
    from applications.admin_profiles_views import _sync_group_from_linked_google_sheet
    from applications.models import GroupParticipantList

    for participant_id in participant_ids:
        participant = GroupParticipantList.objects.using(database).select_related("group").get(pk=participant_id)
        try:
            _sync_group_from_linked_google_sheet(
                group=participant.group, participant_list=participant,
            )
        except Exception as exc:
            participant_model.objects.using(database).filter(pk=participant_id).update(
                google_sheet_sync_error=f"Participant source corrected, but its first refresh failed: {exc}",
                updated_at=timezone.now(),
            )
            logger.warning("Group 10 participant source corrected; first refresh failed: %s", exc)


class Migration(migrations.Migration):
    atomic = False

    dependencies = [("applications", "0069_group_end_year")]

    operations = [migrations.RunPython(relink_group_10, migrations.RunPython.noop)]
