"""Resolve the A1 application dataset used by a group's pairing workflow.

Participant groups created from a shared recruitment round do not always own the
source ``FormDefinition``.  Their selected participants still live in the group
sheet, while the applications can remain attached to the recruitment group.
This module keeps the pairing editor and pairing job on the same resolution
rules.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from django.db.models.functions import Lower
from django.utils.text import slugify

from .models import Application, FormDefinition, FormGroup, GroupParticipantList


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class PairingFormResolution:
    form: FormDefinition | None
    source: str
    matched_email_count: int = 0
    participant_email_count: int = 0

    @property
    def uses_shared_form(self) -> bool:
        return bool(self.form and self.source != "group")


def _normalized_emails(values: Iterable[object] | str | None) -> set[str]:
    if values is None:
        return set()
    if isinstance(values, str):
        values = re.split(r"[,;\s]+", values)

    out: set[str] = set()
    for value in values:
        email = str(value or "").strip().lower()
        if email and _EMAIL_RE.match(email):
            out.add(email)
    return out


def _participant_emails(group: FormGroup, track: str) -> set[str]:
    participant_list = (
        GroupParticipantList.objects.filter(group_id=group.id)
        .only(
            "mentoras_emails_text",
            "emprendedoras_emails_text",
            "mentoras_sheet_rows",
            "emprendedoras_sheet_rows",
        )
        .first()
    )
    if not participant_list:
        return set()

    if track == "M":
        raw_text = participant_list.mentoras_emails_text
        rows = participant_list.mentoras_sheet_rows
    else:
        raw_text = participant_list.emprendedoras_emails_text
        rows = participant_list.emprendedoras_sheet_rows

    row_emails: set[str] = set()
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                row_emails.update(_normalized_emails(row.values()))
            elif isinstance(row, (list, tuple)):
                # Imported and linked participant sheets keep email at index 5.
                # Scanning all cells also supports older flexible imports.
                row_emails.update(_normalized_emails(row))
    # Match the pairing page: linked sheet rows are authoritative when present,
    # while the legacy pasted-email field remains a fallback.
    return row_emails or _normalized_emails(raw_text)


def _expected_group_slugs(group: FormGroup, track: str) -> set[str]:
    suffix = f"{track}_A1"
    slugs = {f"G{group.number}_{suffix}".lower()}
    custom_name = str(getattr(group, "custom_name", "") or "").strip()
    compact_name = re.sub(r"\s+", "", custom_name.lower())
    if custom_name and compact_name not in {
        f"g{group.number}",
        f"group{group.number}",
        f"grupo{group.number}",
    }:
        token = re.sub(r"_+", "_", slugify(custom_name).replace("-", "_")).strip("_")
        if token:
            slugs.add(f"{token}_{suffix}".lower())
    return slugs


def _sibling_slugs(group: FormGroup, track: str) -> set[str]:
    other_track = "E" if track == "M" else "M"
    other_suffix = re.compile(rf"_{other_track}_A1$", flags=re.IGNORECASE)
    sibling_slugs: set[str] = set()
    anchors = FormDefinition.objects.filter(
        group_id=group.id,
        is_master=False,
        slug__iendswith=f"_{other_track}_A1",
    ).only("slug")
    for anchor in anchors:
        sibling_slugs.add(other_suffix.sub(f"_{track}_A1", anchor.slug).lower())
    return sibling_slugs


def _match_stats(
    form_ids: list[int],
    participant_emails: set[str],
) -> tuple[dict[int, set[str]], dict[int, tuple[object, int]]]:
    matched_by_form: dict[int, set[str]] = {}
    latest_by_form: dict[int, tuple[object, int]] = {}
    if not form_ids or not participant_emails:
        return matched_by_form, latest_by_form

    rows = (
        Application.objects.filter(form_id__in=form_ids)
        .exclude(email__isnull=True)
        .exclude(email__exact="")
        .annotate(_email_norm=Lower("email"))
        .filter(_email_norm__in=participant_emails)
        .values_list("form_id", "_email_norm", "created_at", "id")
    )
    for form_id, email, created_at, application_id in rows:
        matched_by_form.setdefault(int(form_id), set()).add(str(email).lower())
        rank = (created_at, int(application_id))
        if rank > latest_by_form.get(int(form_id), rank):
            latest_by_form[int(form_id)] = rank
        else:
            latest_by_form.setdefault(int(form_id), rank)
    return matched_by_form, latest_by_form


def resolve_pairing_application_form(
    group: FormGroup | None,
    track: str,
    participant_emails: Iterable[object] | str | None = None,
) -> PairingFormResolution:
    """Return the current A1 form for a pairing track.

    A directly linked group form always wins.  When one is missing, the resolver
    first recognizes a detached exact group form or the opposite track's shared
    recruitment sibling, then ranks all A1 forms by distinct selected-email
    overlap.  This prevents a merely newer, unrelated form from being selected.
    """
    normalized_track = str(track or "").strip().upper()
    if not group or normalized_track not in {"E", "M"}:
        return PairingFormResolution(None, "missing")

    suffix = f"_{normalized_track}_A1"
    direct_forms = list(
        FormDefinition.objects.filter(
            group_id=group.id,
            is_master=False,
            slug__iendswith=suffix,
        ).order_by("-id")
    )
    if direct_forms:
        return PairingFormResolution(direct_forms[0], "group")

    emails = (
        _normalized_emails(participant_emails)
        if participant_emails is not None
        else _participant_emails(group, normalized_track)
    )
    candidates = list(
        FormDefinition.objects.filter(
            is_master=False,
            slug__iendswith=suffix,
        ).order_by("id")
    )
    if not candidates:
        return PairingFormResolution(
            None,
            "missing",
            participant_email_count=len(emails),
        )

    expected_slugs = _expected_group_slugs(group, normalized_track)
    sibling_slugs = _sibling_slugs(group, normalized_track)
    matched_by_form, latest_by_form = _match_stats(
        [candidate.id for candidate in candidates],
        emails,
    )

    def rank(candidate: FormDefinition):
        matched_count = len(matched_by_form.get(candidate.id, set()))
        candidate_slug = str(candidate.slug or "").lower()
        # Datetimes are awkward to compare against a synthetic minimum across
        # database backends, so the application id supplies the recency tie-break.
        latest_application_id = latest_by_form.get(candidate.id, (None, 0))[1]
        return (
            matched_count,
            int(candidate_slug in expected_slugs),
            int(candidate_slug in sibling_slugs),
            latest_application_id,
            candidate.id,
        )

    best = max(candidates, key=rank)
    matched_count = len(matched_by_form.get(best.id, set()))
    best_slug = str(best.slug or "").lower()

    if matched_count:
        if best_slug in expected_slugs:
            source = "detached_group_form"
        elif best_slug in sibling_slugs:
            source = "shared_application_sibling"
        else:
            source = "participant_email_overlap"
        return PairingFormResolution(
            best,
            source,
            matched_email_count=matched_count,
            participant_email_count=len(emails),
        )

    # With no usable participant emails, only accept a form whose identity is
    # anchored to this group.  Never fall back to an arbitrary global M/E form.
    anchored = [
        candidate
        for candidate in candidates
        if str(candidate.slug or "").lower() in expected_slugs | sibling_slugs
    ]
    if anchored:
        best = max(anchored, key=rank)
        source = (
            "detached_group_form"
            if str(best.slug or "").lower() in expected_slugs
            else "shared_application_sibling"
        )
        return PairingFormResolution(
            best,
            source,
            participant_email_count=len(emails),
        )

    return PairingFormResolution(
        None,
        "missing",
        participant_email_count=len(emails),
    )
