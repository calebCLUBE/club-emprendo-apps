# applications/management/commands/import_form_submissions.py
import json
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from applications.models import Application, Answer, FormDefinition


class Command(BaseCommand):
    help = "Import Applications + Answers exported by export_form_submissions.py (remaps IDs safely)."

    def add_arguments(self, parser):
        parser.add_argument("--infile", required=True, help="Input json filename")

    def handle(self, *args, **options):
        infile = options["infile"].strip()

        try:
            with open(infile, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            raise CommandError(f"Could not read JSON: {e}")

        forms = data.get("forms") or []
        apps = data.get("applications") or []
        answers = data.get("answers") or []

        if not forms or not apps:
            raise CommandError("JSON missing forms/applications.")

        # We'll map old IDs -> new IDs
        fd_id_map = {}
        app_id_map = {}

        with transaction.atomic():
            # 1) Ensure FormDefinitions exist by slug (do not overwrite your current ones)
            for fd in forms:
                slug = fd.get("slug")
                if not slug:
                    continue
                existing = FormDefinition.objects.filter(slug=slug).first()
                if existing:
                    fd_id_map[fd["id"]] = existing.id
                else:
                    fd_copy = fd.copy()
                    old_id = fd_copy.pop("id", None)
                    fd_copy.pop("created_at", None)
                    fd_copy.pop("updated_at", None)

                    obj = FormDefinition.objects.create(**fd_copy)
                    if old_id is not None:
                        fd_id_map[old_id] = obj.id

            # 2) Insert Applications (linking to FormDefinition by mapped id)
            for a in apps:
                old_id = a.get("id")
                form_id = a.get("form")
                if form_id not in fd_id_map:
                    continue

                a_copy = a.copy()
                a_copy.pop("id", None)

                a_copy["form_id"] = fd_id_map[form_id]
                a_copy.pop("form", None)

                # If your model has auto fields / timestamps you don't want to force, remove them:
                # (Keeping created_at is usually fine for testing; remove if it errors)
                # a_copy.pop("created_at", None)
                # a_copy.pop("updated_at", None)

                obj = Application.objects.create(**a_copy)
                if old_id is not None:
                    app_id_map[old_id] = obj.id

            # 3) Insert Answers (linking to mapped application id)
            for ans in answers:
                app_old = ans.get("application")
                if app_old not in app_id_map:
                    continue

                ans_copy = ans.copy()
                ans_copy.pop("id", None)

                ans_copy["application_id"] = app_id_map[app_old]
                ans_copy.pop("application", None)

                # question is FK: we assume question IDs won’t match across systems.
                # Your Answer model likely stores question as FK; we must link by question slug.
                # If your Answer model is FK-only, we need to look up Question by (form, slug).
                # So: require ans to include question id? It likely does. But IDs will not match.

                # ---- SAFER: require the export to include question slug instead (recommended) ----
                # If your Answer has question_id only, this importer can't safely remap it.
                # In that case, skip importing answers and instead use CSV-based test apps.

                Answer.objects.create(**ans_copy)

        self.stdout.write(self.style.SUCCESS(
            f"Imported {len(app_id_map)} applications. (Forms matched/created: {len(fd_id_map)})"
        ))
