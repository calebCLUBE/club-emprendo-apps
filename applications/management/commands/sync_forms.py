# applications/management/commands/sync_forms.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from applications.models import FormDefinition, Question, Choice


# ----------------------------
# Helpers
# ----------------------------

def _replace_placeholders(s: str, mapping: Dict[str, str]) -> str:
    if not s:
        return s
    for k, v in mapping.items():
        s = s.replace(k, v)
    return s


@dataclass
class FormSpec:
    slug: str
    name: str
    description: str
    questions: List[Dict[str, Any]]  # each has slug, text, help_text, required, field_type, position, choices(optional)


def upsert_form(spec: FormSpec, placeholder_map: Optional[Dict[str, str]] = None) -> None:
    placeholder_map = placeholder_map or {}

    # Replace placeholders in form definition
    name = _replace_placeholders(spec.name, placeholder_map)
    description = _replace_placeholders(spec.description, placeholder_map)

    fd, _ = FormDefinition.objects.update_or_create(
        slug=spec.slug,
        defaults={"name": name, "description": description},
    )

    # Upsert questions
    for qd in spec.questions:
        q_slug = qd["slug"]
        q_text = _replace_placeholders(qd.get("text", ""), placeholder_map)
        q_help = _replace_placeholders(qd.get("help_text", ""), placeholder_map)

        q, _ = Question.objects.update_or_create(
            form=fd,
            slug=q_slug,
            defaults={
                "text": q_text,
                "help_text": q_help,
                "required": bool(qd.get("required", False)),
                "active": bool(qd.get("active", True)),
                "field_type": qd["field_type"],
                "position": int(qd.get("position", 0)),
            },
        )

        # If question has choices, replace all choices for consistency
        if qd.get("choices") is not None:
            Choice.objects.filter(question=q).delete()
            choices = qd.get("choices", [])
            for idx, c in enumerate(choices, start=1):
                Choice.objects.create(
                    question=q,
                    label=_replace_placeholders(c["label"], placeholder_map),
                    value=c["value"],
                    position=int(c.get("position", idx)),
                )


# ----------------------------
# You MUST adapt this import section to your codebase
# ----------------------------
# These should return python dicts describing the form
# Example expected return format shown below in form_from_builder()

def form_from_builder(builder_func: Callable[[], Dict[str, Any]], slug_override: Optional[str] = None) -> FormSpec:
    data = builder_func()
    # data should include: slug, name, description, questions[]
    # questions[] entries: slug, text, help_text, required, field_type, position, choices[]
    slug = slug_override or data["slug"]
    return FormSpec(
        slug=slug,
        name=data.get("name", slug),
        description=data.get("description", ""),
        questions=data.get("questions", []),
    )


# ----------------------------
# Command
# ----------------------------

class Command(BaseCommand):
    help = "Sync master/group forms from builder functions into the database (updates descriptions + help_text)."

    def add_arguments(self, parser):
        parser.add_argument("--group", type=int, help="Group number to sync (e.g., 6). If omitted, only masters sync.")
        parser.add_argument("--months", type=str, default="", help='Display months string, e.g. "Septiembre a Diciembre 2025"')
        parser.add_argument("--year", type=str, default="", help='Year string, e.g. "2025"')

    @transaction.atomic
    def handle(self, *args, **opts):
        # ✅ ADAPT THESE IMPORTS TO YOUR ACTUAL BUILDER LOCATIONS
        try:
            from applications.form_builders import (
                build_master_e_a1,
                build_master_e_a2,
                build_master_m_a1,
                build_master_m_a2,
            )
        except Exception as e:
            raise CommandError(f"Could not import builder functions. Update sync_forms.py imports. Error: {e}")

        group_num = opts.get("group")
        months = (opts.get("months") or "").strip()
        year = (opts.get("year") or "").strip()

        placeholder_map = {}
        if group_num:
            placeholder_map["#(group)"] = str(group_num)
        if months:
            placeholder_map["#(month)"] = months
        if year:
            placeholder_map["#(year)"] = year

        # 1) Sync master forms
        masters = [
            form_from_builder(build_master_e_a1),
            form_from_builder(build_master_e_a2),
            form_from_builder(build_master_m_a1),
            form_from_builder(build_master_m_a2),
        ]
        for spec in masters:
            upsert_form(spec, placeholder_map={})  # masters usually keep generic placeholders or already static
            self.stdout.write(self.style.SUCCESS(f"Synced master: {spec.slug}"))

        # 2) Sync group clones if requested
        if group_num:
            # You can either:
            # A) have explicit "clone builder" functions, or
            # B) clone by re-using master specs but overriding slug and applying placeholders
            # Here we do (B)

            # Build from master but override slugs
            group_specs = [
                form_from_builder(build_master_e_a1, slug_override=f"G{group_num}_E_A1"),
                form_from_builder(build_master_e_a2, slug_override=f"G{group_num}_E_A2"),
                form_from_builder(build_master_m_a1, slug_override=f"G{group_num}_M_A1"),
                form_from_builder(build_master_m_a2, slug_override=f"G{group_num}_M_A2"),
            ]

            for spec in group_specs:
                upsert_form(spec, placeholder_map=placeholder_map)
                self.stdout.write(self.style.SUCCESS(f"Synced group: {spec.slug}"))

        self.stdout.write(self.style.SUCCESS("Done."))
