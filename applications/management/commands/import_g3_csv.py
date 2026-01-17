# applications/management/commands/import_g3_csv.py
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Dict, Optional

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from applications.models import Application, Answer, FormDefinition, Question


def norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def pick_col(headers, contains_any):
    """
    Find first header whose normalized text contains ALL tokens in contains_any list.
    """
    for h in headers:
        nh = norm(h)
        ok = True
        for token in contains_any:
            if token not in nh:
                ok = False
                break
        if ok:
            return h
    return None


class Command(BaseCommand):
    help = "Import Club Emprendo Group 3 Google Forms CSV exports into a target FormDefinition (A1)."

    def add_arguments(self, parser):
        parser.add_argument("--form", required=True, help="Target form slug in DB (e.g. G6_E_A1 or G3_E_A1).")
        parser.add_argument("--csv", required=True, help="Path to CSV file to import.")
        parser.add_argument("--limit", type=int, default=0, help="Optional limit rows for testing (0 = no limit).")
        parser.add_argument("--dry-run", action="store_true", help="Parse and report only; do not write to DB.")

    def handle(self, *args, **opts):
        form_slug = opts["form"].strip()
        csv_path = Path(opts["csv"]).expanduser()
        limit = int(opts["limit"] or 0)
        dry = bool(opts["dry_run"])

        if not csv_path.exists():
            raise CommandError(f"CSV not found: {csv_path}")

        form = FormDefinition.objects.filter(slug=form_slug).first()
        if not form:
            raise CommandError(f"FormDefinition not found for slug={form_slug}")

        # We’ll map CSV columns -> question slugs (A1 only)
        # This matches your G3 CSVs you uploaded.
        # If your question slugs differ, adjust these target slugs.
        # Common A1 slugs we’ve seen in your project:
        # E_A1: email, full_name, country_residence, whatsapp, meets_requirements, available_period, business_active, comments
        # M_A1: full_name, email, country_residence, whatsapp_number/whatsapp, meets_requirements, availability_ok, comments_if_not_eligible
        qslugs = set(form.questions.values_list("slug", flat=True))

        # Candidate slug choices by role:
        def first_existing(*cands) -> Optional[str]:
            for c in cands:
                if c in qslugs:
                    return c
            return None

        slug_email = first_existing("email", "e1_email", "m1_email")
        slug_name = first_existing("full_name", "e1_full_name", "m1_full_name", "name")
        slug_country = first_existing("country_residence", "e1_country", "m1_country")
        slug_whatsapp = first_existing("whatsapp", "whatsapp_number", "m1_whatsapp", "e1_whatsapp")
        slug_meets = first_existing("meets_requirements", "e1_meet_requirements", "m1_meet_requirements")
        slug_available = first_existing("available_period", "availability_ok", "e1_available_period", "m1_available_period")
        slug_business = first_existing("business_active", "has_running_business", "e1_has_running_business")
        slug_comments = first_existing("comments", "comments_if_not_eligible", "e1_disqualify_reason", "m1_disqualify_reason", "req_explain")

        required = [slug_name, slug_email, slug_country, slug_whatsapp, slug_meets, slug_available]
        if any(x is None for x in required):
            raise CommandError(
                "Could not find required question slugs on the target form. "
                f"Found: name={slug_name}, email={slug_email}, country={slug_country}, whatsapp={slug_whatsapp}, "
                f"meets={slug_meets}, available={slug_available}. "
                "Open your FormDefinition questions and confirm their slugs."
            )

        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            if not headers:
                raise CommandError("CSV has no headers.")

            col_timestamp = pick_col(headers, ["timestamp"])
            col_name = pick_col(headers, ["nombre completo"])
            col_email = pick_col(headers, ["correo"])
            col_country = pick_col(headers, ["país"])
            col_whatsapp = pick_col(headers, ["whatsapp"])

            col_meets = pick_col(headers, ["cumples", "requisitos"])
            col_available = pick_col(headers, ["disponible", "periodo"])
            col_business = pick_col(headers, ["emprendimiento", "funcionamiento"]) or pick_col(headers, ["emprendimiento", "funcionando"])
            col_comments = pick_col(headers, ["indícanos", "requisito"]) or pick_col(headers, ["comentario"])

            rows = list(reader)
            if limit and limit > 0:
                rows = rows[:limit]

        self.stdout.write(self.style.NOTICE(f"Target form: {form_slug}"))
        self.stdout.write(self.style.NOTICE(f"Rows to import: {len(rows)}"))
        self.stdout.write(self.style.NOTICE("Detected columns:"))
        for k, v in [
            ("timestamp", col_timestamp),
            ("name", col_name),
            ("email", col_email),
            ("country", col_country),
            ("whatsapp", col_whatsapp),
            ("meets_requirements", col_meets),
            ("available_period", col_available),
            ("business_active", col_business),
            ("comments", col_comments),
        ]:
            self.stdout.write(f"  - {k}: {v}")

        if dry:
            self.stdout.write(self.style.WARNING("Dry run: no DB writes."))
            return

        # Build Question objects once
        q_by_slug: Dict[str, Question] = {
            q.slug: q for q in form.questions.all()
        }

        created = 0
        with transaction.atomic():
            for r in rows:
                app = Application.objects.create(
                    form=form,
                    created_at=timezone.now(),
                    name=(r.get(col_name) or "").strip() if col_name else "",
                    email=(r.get(col_email) or "").strip() if col_email else "",
                )

                def add_answer(qslug: Optional[str], colname: Optional[str]):
                    if not qslug or not colname:
                        return
                    q = q_by_slug.get(qslug)
                    if not q:
                        return
                    val = (r.get(colname) or "").strip()
                    Answer.objects.create(application=app, question=q, value=val)

                add_answer(slug_name, col_name)
                add_answer(slug_email, col_email)
                add_answer(slug_country, col_country)
                add_answer(slug_whatsapp, col_whatsapp)
                add_answer(slug_meets, col_meets)
                add_answer(slug_available, col_available)
                add_answer(slug_business, col_business)
                add_answer(slug_comments, col_comments)

                created += 1

        self.stdout.write(self.style.SUCCESS(f"Imported {created} applications into {form_slug}."))
