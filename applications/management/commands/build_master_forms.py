# applications/management/commands/build_master_forms.py

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from applications.models import FormDefinition, Question, Choice
from applications.master_form_specs import MASTER_FORMS


def _pick(obj, names, default=None):
    for n in names:
        if hasattr(obj, n):
            return n
    return default


class Command(BaseCommand):
    help = "Build/replace the 4 master forms (E_A1, E_A2, M_A1, M_A2) from code specs."

    @transaction.atomic
    def handle(self, *args, **options):
        # Infer your field names (since your models evolved a lot)
        fd_slug_field = "slug"
        fd_name_field = _pick(FormDefinition, ["name", "title"], "name")

        q_label_field = _pick(Question, ["label", "text", "prompt", "question_text"], "label")
        q_type_field = _pick(Question, ["type", "kind", "field_type", "question_type"], "type")
        q_slug_field = _pick(Question, ["slug", "key", "code"], None)

        q_required_field = _pick(Question, ["required", "is_required"], "required")
        q_order_field = _pick(Question, ["order", "position", "sort_order"], "order")

        c_label_field = _pick(Choice, ["label", "text", "value"], "label")
        c_order_field = _pick(Choice, ["order", "position", "sort_order"], "order")

        # Sanity: your Question likely has FK named "form"
        q_form_field = "form"

        for slug, spec in MASTER_FORMS.items():
            fd, _ = FormDefinition.objects.get_or_create(**{fd_slug_field: slug})
            setattr(fd, fd_name_field, spec.get("name", slug))
            fd.save()

            # Delete old questions/choices for this master
            Question.objects.filter(**{q_form_field: fd}).delete()

            skip = set(spec.get("skip_labels") or [])
            created_q = 0

            for idx, q in enumerate(spec["questions"], start=1):
                label = q["label"].strip()
                if label in skip:
                    continue

                qobj = Question(**{q_form_field: fd})
                setattr(qobj, q_label_field, label)

                # ✅ ensure unique per-form slug if the model has it
                if q_slug_field:
                    base = slugify(label)[:40] or "q"
                    # make it unique even if labels repeat
                    setattr(qobj, q_slug_field, f"{base}-{idx:02d}")

                if q_type_field:
                    setattr(qobj, q_type_field, q["type"])
                if q_required_field:
                    setattr(qobj, q_required_field, bool(q.get("required", False)))
                if q_order_field:
                    setattr(qobj, q_order_field, idx)

                qobj.save()

                created_q += 1

                # Create choices if any
                opts = q.get("options") or []
                for c_idx, opt in enumerate(opts, start=1):
                    cobj = Choice(question=qobj) if hasattr(Choice, "question") else Choice()
                    if hasattr(cobj, "question"):
                        cobj.question = qobj
                    setattr(cobj, c_label_field, opt.strip())
                    if c_order_field:
                        setattr(cobj, c_order_field, c_idx)
                    cobj.save()

            self.stdout.write(self.style.SUCCESS(f"{slug}: rebuilt ({created_q} questions)."))

        self.stdout.write(self.style.SUCCESS("Done."))
