"""Participant-page cohort outcomes, using the dashboard's source normalization."""

from django.utils import timezone


def participant_group_summary(group):
    from .admin_dashboard_views import (
        IMPACT_STATUS_STARTED,
        _dedupe_participant_records,
        _impact_combined_group_summary,
        _impact_group_is_completed,
        _impact_group_start_date,
        _participant_records,
    )

    history = _dedupe_participant_records(_participant_records(include_unlinked=True))
    records = [r for r in history if r["group_number"] == group.number]
    summary = _impact_combined_group_summary(records, history)
    # Always show both roles, even if the group has no rows for one role.
    tracks = [key for key in ("m", "e") if any(r["track"] == key for r in records)]
    for row in summary.get("rows", []):
        by_track = dict(zip(tracks, row["cells"]))
        row["cells"] = [by_track.get(key, {"count": 0, "pct": None}) for key in ("m", "e")]
        if row["key"] == "active":
            row["label"] = "Activa status in this group"
            for key, cell in zip(("m", "e"), row["cells"]):
                cohort = [r for r in records if r["track"] == key and r.get("started")]
                cell["count"] = sum(r["status"] == "A" and not r.get("graduated") for r in cohort)
                cell["pct"] = round(100 * cell["count"] / len(cohort), 1) if cohort else None

    def count_row(key, label, predicate):
        return {"key": key, "label": label, "cells": [
            {"count": sum(predicate(r) for r in records if r["track"] == track), "pct": None}
            for track in ("m", "e")
        ]}

    rows = [
        count_row("selected", "Selected participants", lambda r: True),
        count_row("not_started", "Did not start / start not confirmed", lambda r: not r.get("started")),
    ] + summary.get("rows", [])
    if not summary:
        rows.append(count_row("started", "Started program", lambda r: r.get("started")))

    # Future enrollments are not evidence that someone has already served again.
    today = timezone.localdate()
    mentors = {}
    for record in history:
        start = record.get("group_start_date")
        if record["track"] == "m" and record["status"] in IMPACT_STATUS_STARTED and start and start <= today:
            mentors.setdefault(record["person_key"], []).append(record)
    later = {"m": [], "e": []}
    unknown_dates = 0
    for record in records:
        if not record.get("started"):
            continue
        start = record.get("group_start_date")
        if not start:
            unknown_dates += 1
            continue
        later_groups = sorted({
            r["group_number"] for r in mentors.get(record["person_key"], [])
            if r["group_number"] != group.number and r["group_start_date"] > start
        })
        if later_groups:
            later[record["track"]].append({
                "name": record.get("name") or record.get("email") or record.get("document_id") or "Participant",
                "email": record.get("email", ""),
                "groups": later_groups,
            })
    for row in rows:
        if row["key"] == "later_mentor":
            for track, cell in zip(("m", "e"), row["cells"]):
                base = sum(r.get("started", False) for r in records if r["track"] == track)
                cell.update(count=len(later[track]), pct=round(100 * len(later[track]) / base, 1) if base else None)

    start = _impact_group_start_date(group)
    state = "Completed" if _impact_group_is_completed(group) else "Upcoming" if start and start > today else "In progress"
    return {
        "state": state,
        "columns": ["Mentoras", "Emprendedoras"],
        "rows": rows,
        "later_entrepreneurs": later["e"],
        "repeat_mentors": later["m"],
        "unknown_dates": unknown_dates,
        "has_participants": bool(records),
    }
