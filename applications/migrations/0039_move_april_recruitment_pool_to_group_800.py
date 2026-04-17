from django.db import migrations


OLD_TO_NEW_SLUGS = {
    "G8_E_A1": "G800_E_A1",
    "G8_E_A2": "G800_E_A2",
    "G8_M_A1": "G800_M_A1",
    "G8_M_A2": "G800_M_A2",
}


def _merge_email_text(a: str, b: str) -> str:
    out = []
    seen = set()
    for raw in [a or "", b or ""]:
        for line in str(raw).splitlines():
            v = line.strip().lower()
            if not v or v in seen:
                continue
            seen.add(v)
            out.append(v)
    return "\n".join(out)


def move_pool_to_group_800(apps, schema_editor):
    FormGroup = apps.get_model("applications", "FormGroup")
    FormDefinition = apps.get_model("applications", "FormDefinition")
    GroupParticipantList = apps.get_model("applications", "GroupParticipantList")
    GradedFile = apps.get_model("applications", "GradedFile")
    GradingJob = apps.get_model("applications", "GradingJob")

    old_forms = {
        old_slug: FormDefinition.objects.filter(slug=old_slug).first()
        for old_slug in OLD_TO_NEW_SLUGS
    }
    if not any(old_forms.values()):
        # Already migrated or source forms not present.
        return

    source_group = None
    for fd in old_forms.values():
        if fd and getattr(fd, "group_id", None):
            source_group = FormGroup.objects.filter(id=fd.group_id).first()
            if source_group:
                break
    if source_group is None:
        source_group = FormGroup.objects.filter(number=8).first()
    if source_group is None:
        return

    target_group = FormGroup.objects.filter(number=800).first()
    if target_group is None:
        target_group = FormGroup.objects.create(
            number=800,
            start_day=getattr(source_group, "start_day", 1) or 1,
            start_month=getattr(source_group, "start_month", "abril") or "abril",
            end_month=getattr(source_group, "end_month", "junio") or "junio",
            year=getattr(source_group, "year", 2026) or 2026,
            a2_deadline=getattr(source_group, "a2_deadline", None),
            open_at=getattr(source_group, "open_at", None),
            close_at=getattr(source_group, "close_at", None),
            reminder_1_at=getattr(source_group, "reminder_1_at", None),
            reminder_2_at=getattr(source_group, "reminder_2_at", None),
            reminder_3_at=getattr(source_group, "reminder_3_at", None),
            reminder_1_sent_at=getattr(source_group, "reminder_1_sent_at", None),
            reminder_2_sent_at=getattr(source_group, "reminder_2_sent_at", None),
            reminder_3_sent_at=getattr(source_group, "reminder_3_sent_at", None),
            use_combined_application=bool(
                getattr(source_group, "use_combined_application", True)
            ),
        )

    if source_group.id != target_group.id:
        src_list = GroupParticipantList.objects.filter(group_id=source_group.id).first()
        if src_list:
            tgt_list = GroupParticipantList.objects.filter(group_id=target_group.id).first()
            if tgt_list is None:
                src_list.group_id = target_group.id
                src_list.save(update_fields=["group"])
            else:
                tgt_list.mentoras_emails_text = _merge_email_text(
                    getattr(tgt_list, "mentoras_emails_text", ""),
                    getattr(src_list, "mentoras_emails_text", ""),
                )
                tgt_list.emprendedoras_emails_text = _merge_email_text(
                    getattr(tgt_list, "emprendedoras_emails_text", ""),
                    getattr(src_list, "emprendedoras_emails_text", ""),
                )
                tgt_list.save(
                    update_fields=[
                        "mentoras_emails_text",
                        "emprendedoras_emails_text",
                        "updated_at",
                    ]
                )

    for old_slug, new_slug in OLD_TO_NEW_SLUGS.items():
        fd = FormDefinition.objects.filter(slug=old_slug).first()
        if not fd:
            continue
        conflict = FormDefinition.objects.filter(slug=new_slug).exclude(id=fd.id).exists()
        if conflict:
            continue
        fd.group_id = target_group.id
        fd.slug = new_slug
        fd.save(update_fields=["group", "slug"])

        GradedFile.objects.filter(form_slug=old_slug).update(form_slug=new_slug)
        GradingJob.objects.filter(form_slug=old_slug).update(form_slug=new_slug)

    FormDefinition.objects.filter(slug__in=["G800_E_A1", "G800_M_A1"]).update(
        name="April Recruitment"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0038_rename_group8_april_recruitment"),
    ]

    operations = [
        migrations.RunPython(move_pool_to_group_800, migrations.RunPython.noop),
    ]

