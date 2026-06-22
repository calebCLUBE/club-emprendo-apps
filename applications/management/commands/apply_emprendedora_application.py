from django.core.management.base import BaseCommand
from django.db.models import Count

from applications.emprendedora_application_schema import apply_emprendedora_schema
from applications.mentora_application_schema import apply_mentora_schema
from applications.admin_views import MONTH_NUM_TO_ES, _fill_placeholders
from applications.models import FormDefinition


class Command(BaseCommand):
    help = "Apply the current DOCX application schemas to masters and unused group forms."

    def handle(self, *args, **options):
        updated = []
        skipped = []

        for track, schema, label in (
            ("E", apply_emprendedora_schema, "Emprendedoras"),
            ("M", apply_mentora_schema, "Mentoras"),
        ):
            master_a1 = FormDefinition.objects.filter(slug=f"{track}_A1").first()
            master_a2 = FormDefinition.objects.filter(slug=f"{track}_A2").first()
            if master_a1 and master_a2:
                schema(master_a1, master_a2)
                updated.append(f"master {label}")

            groups = (
                FormDefinition.objects.filter(group__isnull=False, slug__endswith=f"{track}_A1")
                .select_related("group")
                .annotate(submission_count=Count("applications"))
            )
            for a1 in groups:
                a2 = (
                    FormDefinition.objects.filter(group=a1.group, slug__endswith=f"{track}_A2")
                    .annotate(submission_count=Count("applications"))
                    .first()
                )
                if not a2:
                    continue
                if a1.submission_count or a2.submission_count:
                    skipped.append(f"{a1.group} — {label}")
                    continue
                group = a1.group
                respond_day = str(group.a2_deadline.day) if group.a2_deadline else ""
                respond_month = MONTH_NUM_TO_ES.get(group.a2_deadline.month, "") if group.a2_deadline else ""
                schema(
                    a1,
                    a2,
                    transform=lambda value: _fill_placeholders(
                        value,
                        group.number,
                        group.start_day,
                        group.start_month,
                        group.end_month,
                        group.year,
                        respond_day=respond_day,
                        respond_month=respond_month,
                    ) or "",
                )
                updated.append(f"{a1.group} — {label}")

        self.stdout.write(self.style.SUCCESS("Updated: " + (", ".join(updated) or "none")))
        if skipped:
            self.stdout.write("Preserved forms with historical submissions: " + ", ".join(skipped))
