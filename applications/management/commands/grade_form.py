# applications/management/commands/grade_form.py
import re
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from applications.grading import grade_from_answers
from applications.models import Application, FormDefinition


A2_RE = re.compile(r"^(G\d+_)?(E_A2|M_A2)$")


class Command(BaseCommand):
    help = "Grade all submissions for a given form slug (typically A2)."

    def add_arguments(self, parser):
        parser.add_argument("--form-slug", required=True, help="e.g. G6_E_A2 or G6_M_A2")
        parser.add_argument(
            "--only-pending",
            action="store_true",
            help="Only grade apps with blank/NULL recommendation (safe default).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compute grades but do not write to DB.",
        )

    def handle(self, *args, **options):
        form_slug = options["form_slug"].strip()
        only_pending = bool(options["only_pending"])
        dry_run = bool(options["dry_run"])

        fd = FormDefinition.objects.filter(slug=form_slug).first()
        if not fd:
            raise CommandError(f"FormDefinition not found for slug={form_slug}")

        if not A2_RE.match(form_slug):
            self.stdout.write(self.style.WARNING(
                f"Note: {form_slug} does not look like an A2 slug. Continuing anyway."
            ))

        qs = (
            Application.objects
            .filter(form=fd)
            .select_related("form")
            .prefetch_related("answers__question")
            .order_by("created_at", "id")
        )

        if only_pending:
            qs = qs.filter(recommendation__isnull=True) | qs.filter(recommendation="")

        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.WARNING("No applications to grade."))
            return

        self.stdout.write(f"Grading {total} applications for {form_slug} "
                          f"({'pending only' if only_pending else 'all'}) "
                          f"{'(DRY RUN)' if dry_run else ''}")

        updated = 0
        with transaction.atomic():
            for app in qs:
                scores = grade_from_answers(app)

                if not dry_run:
                    app.tablestakes_score = scores.get("tablestakes_score")
                    app.commitment_score = scores.get("commitment_score")
                    app.nice_to_have_score = scores.get("nice_to_have_score")
                    app.overall_score = scores.get("overall_score")
                    app.recommendation = scores.get("recommendation")

                    app.save(update_fields=[
                        "tablestakes_score",
                        "commitment_score",
                        "nice_to_have_score",
                        "overall_score",
                        "recommendation",
                    ])

                updated += 1

        self.stdout.write(self.style.SUCCESS(f"Done. Graded {updated} applications."))
