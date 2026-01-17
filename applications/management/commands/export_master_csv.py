# applications/management/commands/export_master_csv.py
import csv
from django.core.management.base import BaseCommand, CommandError

from applications.models import Application, FormDefinition


class Command(BaseCommand):
    help = "Export a master CSV for a form slug, including grade columns."

    def add_arguments(self, parser):
        parser.add_argument("--form-slug", required=True, help="e.g. G6_E_A2")
        parser.add_argument("--out", default="", help="Output filename (optional)")

    def handle(self, *args, **options):
        form_slug = options["form_slug"].strip()
        out = (options["out"] or "").strip()

        fd = FormDefinition.objects.filter(slug=form_slug).first()
        if not fd:
            raise CommandError(f"FormDefinition not found for slug={form_slug}")

        questions = list(fd.questions.filter(active=True).order_by("position", "id"))

        # Grade columns first, then answer columns (you can reorder if you prefer)
        headers = [
            "created_at",
            "application_id",
            "name",
            "email",
            "tablestakes_score",
            "commitment_score",
            "nice_to_have_score",
            "overall_score",
            "recommendation",
        ] + [q.slug for q in questions]

        apps = (
            Application.objects.filter(form=fd)
            .select_related("form")
            .prefetch_related("answers__question")
            .order_by("created_at", "id")
        )

        filename = out or f"{form_slug}_MASTER_GRADED.csv"

        with open(filename, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(headers)

            for app in apps:
                amap = {a.question.slug: (a.value or "") for a in app.answers.all()}

                row = [
                    app.created_at.isoformat(),
                    str(app.id),
                    app.name or "",
                    app.email or "",
                    app.tablestakes_score or "",
                    app.commitment_score or "",
                    app.nice_to_have_score or "",
                    app.overall_score or "",
                    app.recommendation or "",
                ] + [amap.get(q.slug, "") for q in questions]

                w.writerow(row)

        self.stdout.write(self.style.SUCCESS(f"Wrote {filename}"))
