import io
import re
import textwrap
from collections import defaultdict
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Avg, Count, Q
from django.db.models.functions import TruncDay, TruncMonth, TruncWeek
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from .admin_views import _load_database_encuestas_grid
from .models import Application, FormGroup, GroupParticipantList
from .participant_statuses import (
    PARTICIPANT_STATUS_CHOICES,
    PARTICIPANT_STATUS_GRADUATED,
    PARTICIPANT_STATUS_LABELS,
    PARTICIPANT_STATUS_STARTED,
)

GROUP_SLUG_RE = re.compile(r"^G(?P<num>\d+)_")
IMPACT_EMAIL_HEADERS = {"email", "correo", "correoelectronico", "mail"}
IMPACT_EMAIL_TOKENS = ("email", "correo")
IMPACT_TIMESTAMP_HEADERS = {
    "timestamp",
    "marcatemporal",
    "fecha",
    "fechadeenvio",
    "submittedat",
    "submissiondate",
    "createdat",
    "date",
}
IMPACT_METADATA_HEADERS = {
    "timestamp",
    "marcatemporal",
    "fecha",
    "fechadeenvio",
    "submittedat",
    "submissiondate",
    "createdat",
    "date",
    "email",
    "correo",
    "correoelectronico",
    "mail",
    "name",
    "nombre",
    "full_name",
    "fullname",
    "id",
    "applicationid",
    "applicacionid",
    "telefono",
    "phone",
    "whatsapp",
}
IMPACT_NPS_HEADER_TOKENS = ("nps", "recommend", "recomend", "probabilidad")
IMPACT_WELLBEING_HEADER_TOKENS = (
    "bienestar",
    "confianza",
    "ingreso",
    "ingresos",
    "ventas",
    "satisfaccion",
    "autoestima",
    "estres",
    "stress",
    "salud",
    "finanz",
    "negocio",
)
PARTICIPANT_TRACK_CONFIGS = {
    "e": {
        "label": "Emprendedoras",
        "short_label": "E",
        "rows_field": "emprendedoras_sheet_rows",
        "email_col": 5,
        "status_col": 1,
        "country_col": 7,
        "progress_cols": (9, 10, 11, 12, 13),
        "initial_survey_col": 12,
        "final_survey_col": 13,
    },
    "m": {
        "label": "Mentoras",
        "short_label": "M",
        "rows_field": "mentoras_sheet_rows",
        "email_col": 5,
        "status_col": 1,
        "country_col": 7,
        "progress_cols": (9, 10, 11, 12, 13),
        "initial_survey_col": 12,
        "final_survey_col": 13,
    },
}
IMPACT_SURVEY_SECTIONS = [
    {
        "kind": "emprendedoras",
        "title": "Initial checkpoint - Emprendedoras",
        "sheet_url_name": "admin_database_encuestas_sheet",
        "track": "e",
        "stage": "initial",
        "short_label": "E inicial",
        "color": "#3B82F6",
    },
    {
        "kind": "emprendedoras_final",
        "title": "Final checkpoint - Emprendedoras",
        "sheet_url_name": "admin_database_encuestas_final_sheet",
        "track": "e",
        "stage": "final",
        "short_label": "E final",
        "color": "#14B8A6",
    },
    {
        "kind": "mentoras",
        "title": "Initial checkpoint - Mentoras",
        "sheet_url_name": "admin_database_encuestas_mentoras_sheet",
        "track": "m",
        "stage": "initial",
        "short_label": "M inicial",
        "color": "#8B5CF6",
    },
    {
        "kind": "mentoras_final",
        "title": "Final checkpoint - Mentoras",
        "sheet_url_name": "admin_database_encuestas_mentoras_final_sheet",
        "track": "m",
        "stage": "final",
        "short_label": "M final",
        "color": "#F59E0B",
    },
]


def _parse_iso_date(raw: str | None) -> date | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _safe_int(raw: str | None, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(raw or "")
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _is_truthy(raw: str | None) -> bool:
    return (raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _track_from_slug(slug: str) -> str:
    s = (slug or "").upper()
    if "E_A1" in s or "E_A2" in s:
        return "E"
    if "M_A1" in s or "M_A2" in s:
        return "M"
    return "Other"


def _group_number_from_slug(slug: str) -> int | None:
    match = GROUP_SLUG_RE.match((slug or "").strip().upper())
    if not match:
        return None
    try:
        return int(match.group("num"))
    except (TypeError, ValueError):
        return None


def _pct(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((part / total) * 100, 1)


def _rate(part: int, total: int) -> float:
    return _pct(part, total)


def _metric_email(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    raw = raw.strip("<>[](){}\"'").rstrip(".,;:")
    if "@" not in raw:
        return ""
    return raw


def _metric_cell(row: list, index: int | None) -> str:
    if index is None or index < 0 or index >= len(row):
        return ""
    return str(row[index] or "").strip()


def _metric_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    raw = str(value or "").strip().lower()
    return raw in {"1", "true", "yes", "checked", "on", "x", "✓"}


def _metric_row_has_meaning(row: list, email_col: int, status_col: int) -> bool:
    email = _metric_email(_metric_cell(row, email_col))
    status = _metric_cell(row, status_col)
    name = _metric_cell(row, 3)
    return bool(email or status or name)


def _parse_metric_number(value) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    raw = str(value or "").strip()
    if not raw:
        return None
    match = re.search(r"-?\d+(?:[\.,]\d+)?", raw)
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def _status_label(raw_status: str | None) -> str:
    status = (raw_status or "").strip().upper()
    return status or "Sin estatus"


def _status_counts_to_rows(status_counts: dict[str, int]) -> list[dict]:
    return [
        {
            "status": status,
            "label": PARTICIPANT_STATUS_LABELS.get(status, status),
            "count": count,
        }
        for status, count in sorted(status_counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _participant_status_key() -> list[dict]:
    return [
        {
            "code": code,
            "label": label,
        }
        for code, label in PARTICIPANT_STATUS_CHOICES
    ]


def _track_key_from_slug(slug: str) -> str:
    track = _track_from_slug(slug)
    if track == "E":
        return "e"
    if track == "M":
        return "m"
    return "other"


def _normalized_header_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _non_empty_rows(rows: list[list[str]]) -> list[list[str]]:
    out: list[list[str]] = []
    for row in rows:
        if any(str(cell or "").strip() for cell in row):
            out.append(row)
    return out


def _safe_row_value(row: list[str], index: int | None) -> str:
    if index is None:
        return ""
    if index < 0 or index >= len(row):
        return ""
    return str(row[index] or "").strip()


def _find_header_index(
    headers: list[str],
    exact_keys: set[str],
    contains_tokens: tuple[str, ...] = (),
) -> int | None:
    for idx, header in enumerate(headers):
        normalized = _normalized_header_key(header)
        if normalized in exact_keys:
            return idx
        if contains_tokens and any(token in normalized for token in contains_tokens):
            return idx
    return None


def _extract_unique_emails(rows: list[list[str]], email_index: int | None) -> set[str]:
    emails: set[str] = set()
    if email_index is None:
        return emails
    for row in rows:
        raw = _safe_row_value(row, email_index).lower().strip("<>[](){}\"'").rstrip(".,;:")
        if "@" not in raw:
            continue
        emails.add(raw)
    return emails


def _build_question_completion(
    headers: list[str],
    rows: list[list[str]],
    metadata_indices: set[int],
) -> list[dict]:
    total_rows = len(rows)
    if total_rows <= 0:
        return []

    completion_rows: list[dict] = []
    for idx, header in enumerate(headers):
        if idx in metadata_indices:
            continue
        label = str(header or "").strip()
        if not label:
            continue
        answered = 0
        for row in rows:
            if _safe_row_value(row, idx):
                answered += 1
        completion_rows.append(
            {
                "label": label,
                "answered": answered,
                "pct": _pct(answered, total_rows),
            }
        )
    completion_rows.sort(key=lambda item: (-item["pct"], -item["answered"], item["label"]))
    return completion_rows


def _build_nps_rows(headers: list[str], rows: list[list[str]], metadata_indices: set[int]) -> list[dict]:
    nps_rows: list[dict] = []
    for idx, header in enumerate(headers):
        if idx in metadata_indices:
            continue
        normalized = _normalized_header_key(header)
        if not any(token in normalized for token in IMPACT_NPS_HEADER_TOKENS):
            continue

        values: list[float] = []
        for row in rows:
            value = _parse_metric_number(_safe_row_value(row, idx))
            if value is None:
                continue
            if 0 <= value <= 10:
                values.append(value)
        if not values:
            continue

        promoters = len([value for value in values if value >= 9])
        detractors = len([value for value in values if value <= 6])
        passives = len(values) - promoters - detractors
        score = round(((promoters - detractors) / len(values)) * 100, 1)
        nps_rows.append(
            {
                "label": str(header or "").strip(),
                "responses": len(values),
                "score": score,
                "promoters": promoters,
                "passives": passives,
                "detractors": detractors,
            }
        )
    return nps_rows


def _build_wellbeing_rows(headers: list[str], rows: list[list[str]], metadata_indices: set[int]) -> list[dict]:
    wellbeing_rows: list[dict] = []
    for idx, header in enumerate(headers):
        if idx in metadata_indices:
            continue
        normalized = _normalized_header_key(header)
        if not any(token in normalized for token in IMPACT_WELLBEING_HEADER_TOKENS):
            continue

        values: list[float] = []
        for row in rows:
            value = _parse_metric_number(_safe_row_value(row, idx))
            if value is not None:
                values.append(value)
        if not values:
            continue

        wellbeing_rows.append(
            {
                "label": str(header or "").strip(),
                "responses": len(values),
                "avg": round(sum(values) / len(values), 2),
                "min": round(min(values), 2),
                "max": round(max(values), 2),
            }
        )
    wellbeing_rows.sort(key=lambda item: (-item["responses"], item["label"]))
    return wellbeing_rows


def _build_impact_dataset(
    kind: str,
    title: str,
    sheet_url_name: str,
    scoped_emails: set[str] | None = None,
) -> tuple[dict, set[str]]:
    label, headers, raw_rows, file_name, file_id = _load_database_encuestas_grid(kind)
    rows = _non_empty_rows(raw_rows)

    email_index = _find_header_index(headers, IMPACT_EMAIL_HEADERS, IMPACT_EMAIL_TOKENS)
    if scoped_emails is not None and email_index is not None:
        rows = [
            row
            for row in rows
            if _metric_email(_safe_row_value(row, email_index)) in scoped_emails
        ]

    response_count = len(rows)
    unique_emails = _extract_unique_emails(rows, email_index)
    timestamp_index = _find_header_index(headers, IMPACT_TIMESTAMP_HEADERS)

    metadata_indices: set[int] = set()
    for idx, header in enumerate(headers):
        normalized = _normalized_header_key(header)
        if normalized in IMPACT_METADATA_HEADERS:
            metadata_indices.add(idx)
    if email_index is not None:
        metadata_indices.add(email_index)
    if timestamp_index is not None:
        metadata_indices.add(timestamp_index)

    completion_rows = _build_question_completion(headers, rows, metadata_indices)
    nps_rows = _build_nps_rows(headers, rows, metadata_indices)
    wellbeing_rows = _build_wellbeing_rows(headers, rows, metadata_indices)

    dataset = {
        "kind": kind,
        "title": title,
        "label": label,
        "sheet_url_name": sheet_url_name,
        "source_name": file_name,
        "source_file_id": file_id,
        "responses_count": response_count,
        "headers_count": len(headers),
        "question_count": len(completion_rows),
        "unique_emails_count": len(unique_emails),
        "email_column_label": headers[email_index] if email_index is not None else "",
        "completion_rows": completion_rows,
        "nps_rows": nps_rows,
        "wellbeing_rows": wellbeing_rows,
    }
    return dataset, unique_emails


def _track_impact_summary(track_label: str, initial_dataset: dict, final_dataset: dict, initial_emails: set[str], final_emails: set[str]) -> dict:
    initial_responses = int(initial_dataset.get("responses_count") or 0)
    final_responses = int(final_dataset.get("responses_count") or 0)
    initial_unique = int(initial_dataset.get("unique_emails_count") or 0)
    final_unique = int(final_dataset.get("unique_emails_count") or 0)

    matched = len(initial_emails & final_emails)
    missing = max(len(initial_emails) - matched, 0)

    return {
        "label": track_label,
        "initial_responses": initial_responses,
        "final_responses": final_responses,
        "response_growth": final_responses - initial_responses,
        "final_vs_initial_pct": _pct(final_responses, initial_responses),
        "initial_unique": initial_unique,
        "final_unique": final_unique,
        "matched_unique": matched,
        "missing_from_final_unique": missing,
        "retention_pct": _pct(matched, len(initial_emails)),
    }


def _participant_records() -> list[dict]:
    records: list[dict] = []
    participant_lists = GroupParticipantList.objects.select_related("group").order_by("group__number", "id")

    for participant_list in participant_lists:
        group = getattr(participant_list, "group", None)
        group_number = getattr(group, "number", None)
        group_year = getattr(group, "year", None)
        group_label = f"Group {group_number}" if group_number is not None else "No group"

        for track_key, cfg in PARTICIPANT_TRACK_CONFIGS.items():
            raw_rows = getattr(participant_list, cfg["rows_field"], []) or []
            if not isinstance(raw_rows, list):
                continue

            for raw_row in raw_rows:
                if not isinstance(raw_row, (list, tuple)):
                    continue
                row = list(raw_row)
                if not _metric_row_has_meaning(row, cfg["email_col"], cfg["status_col"]):
                    continue

                status = _status_label(_metric_cell(row, cfg["status_col"]))
                progress = any(_metric_bool(row[idx]) for idx in cfg["progress_cols"] if idx < len(row))
                started = progress or status in PARTICIPANT_STATUS_STARTED
                graduated = status in PARTICIPANT_STATUS_GRADUATED
                country = _metric_cell(row, cfg["country_col"]) or "Sin país"
                email = _metric_email(_metric_cell(row, cfg["email_col"]))

                records.append(
                    {
                        "track": track_key,
                        "track_label": cfg["label"],
                        "email": email,
                        "status": status,
                        "started": started,
                        "graduated": graduated,
                        "country": country,
                        "group_number": group_number,
                        "group_year": group_year,
                        "group_label": group_label,
                        "initial_survey": _metric_bool(row[cfg["initial_survey_col"]])
                        if cfg["initial_survey_col"] < len(row)
                        else False,
                        "final_survey": _metric_bool(row[cfg["final_survey_col"]])
                        if cfg["final_survey_col"] < len(row)
                        else False,
                    }
                )
    return records


def _participant_summary(records: list[dict], group_numbers: set[int] | None = None) -> dict:
    summary_by_track: dict[str, dict] = {}
    all_participant_emails: set[str] = set()
    all_started_emails: set[str] = set()
    all_graduated_emails: set[str] = set()
    country_counts: dict[str, int] = defaultdict(int)
    group_rows: dict[tuple[int | None, str], dict] = {}

    for track_key, cfg in PARTICIPANT_TRACK_CONFIGS.items():
        track_records = [record for record in records if record["track"] == track_key]
        participant_emails = {record["email"] for record in track_records if record["email"]}
        started_emails = {record["email"] for record in track_records if record["email"] and record["started"]}
        graduated_emails = {record["email"] for record in track_records if record["email"] and record["graduated"]}
        status_counts: dict[str, int] = defaultdict(int)
        track_country_counts: dict[str, int] = defaultdict(int)
        initial_responses = 0
        final_responses = 0

        for record in track_records:
            status_counts[record["status"]] += 1
            track_country_counts[record["country"]] += 1
            country_counts[record["country"]] += 1
            if record["initial_survey"]:
                initial_responses += 1
            if record["final_survey"]:
                final_responses += 1

            group_key = (record["group_number"], track_key)
            group_row = group_rows.setdefault(
                group_key,
                {
                    "group_number": record["group_number"],
                    "group_label": record["group_label"],
                    "track": cfg["short_label"],
                    "participants": 0,
                    "started": 0,
                    "graduated": 0,
                },
            )
            group_row["participants"] += 1
            if record["started"]:
                group_row["started"] += 1
            if record["graduated"]:
                group_row["graduated"] += 1

        all_participant_emails |= participant_emails
        all_started_emails |= started_emails
        all_graduated_emails |= graduated_emails

        summary_by_track[track_key] = {
            "label": cfg["label"],
            "short_label": cfg["short_label"],
            "rows": len(track_records),
            "unique": len(participant_emails),
            "started": len([record for record in track_records if record["started"]]),
            "started_unique": len(started_emails),
            "graduated": len([record for record in track_records if record["graduated"]]),
            "graduated_unique": len(graduated_emails),
            "graduation_rate": _rate(
                len([record for record in track_records if record["graduated"]]),
                len([record for record in track_records if record["started"]]),
            ),
            "initial_survey_responses": initial_responses,
            "initial_survey_rate": _rate(initial_responses, len(track_records)),
            "final_survey_responses": final_responses,
            "final_survey_rate": _rate(final_responses, len(track_records)),
            "status_rows": _status_counts_to_rows(status_counts),
            "country_rows": [
                {"country": country, "count": count}
                for country, count in sorted(track_country_counts.items(), key=lambda item: (-item[1], item[0]))[:8]
            ],
            "participant_emails": participant_emails,
            "started_emails": started_emails,
            "graduated_emails": graduated_emails,
        }

    overall_started = len([record for record in records if record["started"]])
    overall_graduated = len([record for record in records if record["graduated"]])
    groups_with_participants = {
        record["group_number"]
        for record in records
        if record.get("group_number") is not None
    }

    if group_numbers is not None:
        groups_in_system = len(group_numbers)
    else:
        try:
            groups_in_system = FormGroup.objects.count()
        except Exception:
            groups_in_system = 0

    group_summary_rows = list(group_rows.values())
    group_summary_rows.sort(
        key=lambda item: (
            item["group_number"] is None,
            -(item["group_number"] or 0),
            item["track"],
        )
    )

    return {
        "overall": {
            "rows": len(records),
            "unique": len(all_participant_emails),
            "started": overall_started,
            "started_unique": len(all_started_emails),
            "graduated": overall_graduated,
            "graduated_unique": len(all_graduated_emails),
            "graduation_rate": _rate(overall_graduated, overall_started),
            "groups_in_system": groups_in_system,
            "groups_with_participants": len(groups_with_participants),
        },
        "tracks": summary_by_track,
        "country_rows": [
            {"country": country, "count": count}
            for country, count in sorted(country_counts.items(), key=lambda item: (-item[1], item[0]))[:10]
        ],
        "group_rows": group_summary_rows[:20],
    }


def _application_group_number(app: Application) -> int | None:
    group = getattr(getattr(app, "form", None), "group", None)
    if group and getattr(group, "number", None) is not None:
        return group.number
    return _group_number_from_slug(getattr(getattr(app, "form", None), "slug", ""))


def _application_summary(group_numbers: set[int] | None = None) -> dict:
    apps = Application.objects.select_related("form", "form__group").order_by("created_at", "id")
    track_data: dict[str, dict] = {
        "e": {"label": "Emprendedoras", "raw": 0, "a1": 0, "a2": 0, "emails": set()},
        "m": {"label": "Mentoras", "raw": 0, "a1": 0, "a2": 0, "emails": set()},
    }
    group_data: dict[tuple[int | None, str], dict] = {}
    all_emails: set[str] = set()
    raw_total = 0

    for app in apps:
        slug = getattr(getattr(app, "form", None), "slug", "")
        track_key = _track_key_from_slug(slug)
        if track_key not in track_data:
            continue

        email = _metric_email(getattr(app, "email", ""))
        group = getattr(getattr(app, "form", None), "group", None)
        group_number = _application_group_number(app)
        if group_numbers is not None and group_number not in group_numbers:
            continue
        group_year = getattr(group, "year", None)
        group_label = f"Group {group_number}" if group_number is not None else "No group"
        group_key = (group_number, track_key)
        group_row = group_data.setdefault(
            group_key,
            {
                "group_number": group_number,
                "group_year": group_year,
                "group_label": group_label,
                "track": PARTICIPANT_TRACK_CONFIGS[track_key]["short_label"],
                "raw": 0,
                "emails": set(),
            },
        )

        raw_total += 1
        track_data[track_key]["raw"] += 1
        group_row["raw"] += 1
        if "_A1" in slug.upper():
            track_data[track_key]["a1"] += 1
        elif "_A2" in slug.upper():
            track_data[track_key]["a2"] += 1

        if email:
            track_data[track_key]["emails"].add(email)
            group_row["emails"].add(email)
            all_emails.add(email)

    track_rows = []
    for track_key in ("e", "m"):
        data = track_data[track_key]
        unique = len(data["emails"])
        track_rows.append(
            {
                "track": data["label"],
                "raw": data["raw"],
                "unique": unique,
                "duplicate_or_repeat": max(data["raw"] - unique, 0),
                "a1": data["a1"],
                "a2": data["a2"],
            }
        )

    group_rows = []
    for row in group_data.values():
        group_rows.append(
            {
                "group_number": row["group_number"],
                "group_year": row["group_year"],
                "group_label": row["group_label"],
                "track": row["track"],
                "raw": row["raw"],
                "unique": len(row["emails"]),
                "duplicate_or_repeat": max(row["raw"] - len(row["emails"]), 0),
            }
        )
    group_rows.sort(
        key=lambda item: (
            item["group_number"] is None,
            -(item["group_number"] or 0),
            item["track"],
        )
    )

    return {
        "overall": {
            "raw": raw_total,
            "unique": len(all_emails),
            "duplicate_or_repeat": max(raw_total - len(all_emails), 0),
        },
        "tracks": track_rows,
        "group_rows": group_rows[:20],
        "email_sets": {
            "all": all_emails,
            "e": track_data["e"]["emails"],
            "m": track_data["m"]["emails"],
        },
    }


def _conversion_summary(participant_summary: dict, application_summary: dict) -> list[dict]:
    rows: list[dict] = []
    for track_key, label in (("e", "Emprendedoras"), ("m", "Mentoras")):
        applicant_emails = application_summary["email_sets"].get(track_key, set())
        participant_track = participant_summary["tracks"].get(track_key, {})
        started_emails = participant_track.get("started_emails", set())
        graduated_emails = participant_track.get("graduated_emails", set())
        started_from_app = len(started_emails & applicant_emails)
        graduated_from_app = len(graduated_emails & applicant_emails)
        rows.append(
            {
                "track": label,
                "unique_applicants": len(applicant_emails),
                "started_from_app": started_from_app,
                "graduated_from_app": graduated_from_app,
                "app_to_start_rate": _rate(started_from_app, len(applicant_emails)),
                "app_to_grad_rate": _rate(graduated_from_app, len(applicant_emails)),
                "participants_without_app_match": len(started_emails - applicant_emails),
            }
        )

    applicant_all = application_summary["email_sets"].get("all", set())
    started_all: set[str] = set()
    graduated_all: set[str] = set()
    for track_key in ("e", "m"):
        participant_track = participant_summary["tracks"].get(track_key, {})
        started_all |= participant_track.get("started_emails", set())
        graduated_all |= participant_track.get("graduated_emails", set())
    rows.append(
        {
            "track": "All",
            "unique_applicants": len(applicant_all),
            "started_from_app": len(started_all & applicant_all),
            "graduated_from_app": len(graduated_all & applicant_all),
            "app_to_start_rate": _rate(len(started_all & applicant_all), len(applicant_all)),
            "app_to_grad_rate": _rate(len(graduated_all & applicant_all), len(applicant_all)),
            "participants_without_app_match": len(started_all - applicant_all),
        }
    )
    return rows


def _alumni_mentor_summary(records: list[dict]) -> dict:
    e_groups_by_email: dict[str, set[int]] = defaultdict(set)
    m_groups_by_email: dict[str, set[int]] = defaultdict(set)
    m_rows_by_email: dict[str, int] = defaultdict(int)

    for record in records:
        email = record.get("email") or ""
        group_number = record.get("group_number")
        if not email or group_number is None:
            continue
        if record["track"] == "e":
            e_groups_by_email[email].add(int(group_number))
        elif record["track"] == "m":
            m_groups_by_email[email].add(int(group_number))
            m_rows_by_email[email] += 1

    returnee_emails = sorted(set(e_groups_by_email) & set(m_groups_by_email))
    later_returnee_emails = [
        email
        for email in returnee_emails
        if max(m_groups_by_email[email]) > min(e_groups_by_email[email])
    ]
    repeated_mentors = [
        {
            "email": email,
            "groups": sorted(groups),
            "group_count": len(groups),
            "row_count": m_rows_by_email[email],
            "first_group": min(groups),
            "last_group": max(groups),
        }
        for email, groups in m_groups_by_email.items()
        if len(groups) > 1
    ]
    repeated_mentors.sort(key=lambda item: (-item["group_count"], item["email"]))

    return {
        "returnee_count": len(returnee_emails),
        "later_returnee_count": len(later_returnee_emails),
        "repeated_mentor_count": len(repeated_mentors),
        "returnee_preview": [
            {
                "email": email,
                "emprendedora_groups": sorted(e_groups_by_email[email]),
                "mentora_groups": sorted(m_groups_by_email[email]),
            }
            for email in returnee_emails[:8]
        ],
        "repeated_mentors": repeated_mentors[:10],
    }


def _collect_survey_metric_rows(datasets: dict[str, dict], key: str) -> list[dict]:
    rows: list[dict] = []
    for kind in ("emprendedoras", "emprendedoras_final", "mentoras", "mentoras_final"):
        dataset = datasets.get(kind, {})
        if dataset.get("error"):
            continue
        for row in dataset.get(key, []) or []:
            item = dict(row)
            item["dataset"] = dataset.get("title", kind)
            rows.append(item)
    return rows


def _survey_response_rate_data(participant_summary: dict) -> list[dict]:
    tracks = participant_summary.get("tracks", {})
    rows: list[dict] = []
    for key, prefix, initial_color, final_color in (
        ("e", "E", "#3B82F6", "#22C55E"),
        ("m", "M", "#14B8A6", "#F59E0B"),
    ):
        track = tracks.get(key, {})
        rows.extend(
            [
                {
                    "label": f"{prefix}: initial check-in",
                    "value": track.get("initial_survey_rate", 0),
                    "color": initial_color,
                },
                {
                    "label": f"{prefix}: final check-in",
                    "value": track.get("final_survey_rate", 0),
                    "color": final_color,
                },
            ]
        )
    return rows


def _impact_journey_data(participant_summary: dict, application_summary: dict, overall_summary: dict) -> list[dict]:
    participants = participant_summary.get("overall", {})
    applications = application_summary.get("overall", {})
    return [
        {
            "label": "Applicant reach",
            "value": applications.get("unique", 0),
            "color": "#F59E0B",
        },
        {
            "label": "Workbook rows",
            "value": participants.get("rows", 0),
            "color": "#3B82F6",
        },
        {
            "label": "Started",
            "value": participants.get("started", 0),
            "color": "#14B8A6",
        },
        {
            "label": "Graduated",
            "value": participants.get("graduated", 0),
            "color": "#22C55E",
        },
        {
            "label": "Final check-ins",
            "value": overall_summary.get("final_unique", 0),
            "color": "#8B5CF6",
        },
    ]


def _impact_story_rate_data(participant_summary: dict, conversion_rows: list[dict]) -> list[dict]:
    participants = participant_summary.get("overall", {})
    conversion_all = next((row for row in conversion_rows if row.get("track") == "All"), {})
    tracks = participant_summary.get("tracks", {})
    initial_checks = sum(int(track.get("initial_survey_responses") or 0) for track in tracks.values())
    final_checks = sum(int(track.get("final_survey_responses") or 0) for track in tracks.values())
    participant_rows = int(participants.get("rows") or 0)
    return [
        {
            "label": "Reach -> start",
            "value": conversion_all.get("app_to_start_rate", 0),
            "color": "#F59E0B",
        },
        {
            "label": "Reach -> graduation",
            "value": conversion_all.get("app_to_grad_rate", 0),
            "color": "#6366F1",
        },
        {
            "label": "Started -> graduation",
            "value": participants.get("graduation_rate", 0),
            "color": "#22C55E",
        },
        {
            "label": "Initial check-in complete",
            "value": _rate(initial_checks, participant_rows),
            "color": "#3B82F6",
        },
        {
            "label": "Final check-in complete",
            "value": _rate(final_checks, participant_rows),
            "color": "#14B8A6",
        },
    ]


def _load_impact_survey_datasets(
    *,
    top_n: int,
    scoped_emails: set[str] | None = None,
    request=None,
) -> tuple[dict[str, dict], dict[str, set[str]]]:
    datasets: dict[str, dict] = {}
    email_sets: dict[str, set[str]] = {}
    for section in IMPACT_SURVEY_SECTIONS:
        kind = section["kind"]
        title = section["title"]
        sheet_url_name = section["sheet_url_name"]
        try:
            if scoped_emails is None:
                dataset, email_set = _build_impact_dataset(kind, title, sheet_url_name)
            else:
                dataset, email_set = _build_impact_dataset(
                    kind,
                    title,
                    sheet_url_name,
                    scoped_emails=scoped_emails,
                )
            dataset["completion_rows"] = list(dataset.get("completion_rows") or [])[:top_n]
        except Exception as exc:
            dataset = {
                "kind": kind,
                "title": title,
                "sheet_url_name": sheet_url_name,
                "error": str(exc),
                "responses_count": 0,
                "unique_emails_count": 0,
                "question_count": 0,
                "completion_rows": [],
                "nps_rows": [],
                "wellbeing_rows": [],
            }
            email_set = set()
            if request is not None:
                messages.error(request, f"Could not load {title}: {exc}")
        datasets[kind] = dataset
        email_sets[kind] = email_set
    return datasets, email_sets


def _impact_group_options() -> list[dict]:
    return [
        {
            "number": group.number,
            "label": str(group),
        }
        for group in FormGroup.objects.order_by("-number")
    ]


def _parse_impact_group_numbers(raw_values: list[str]) -> set[int]:
    group_numbers: set[int] = set()
    for raw in raw_values:
        for part in str(raw or "").split(","):
            value = part.strip()
            if not value:
                continue
            try:
                group_numbers.add(int(value))
            except ValueError:
                continue
    return group_numbers


def _filter_records_by_groups(records: list[dict], group_numbers: set[int] | None) -> list[dict]:
    if group_numbers is None:
        return records
    return [
        record
        for record in records
        if record.get("group_number") in group_numbers
    ]


def _impact_group_short_label(group: FormGroup | None, group_number: int) -> str:
    if group is None:
        return f"Group {group_number}"
    custom_name = (getattr(group, "custom_name", "") or "").strip()
    return custom_name or f"Group {group.number}"


def _impact_group_scope_label(group_numbers: set[int] | None) -> str:
    if group_numbers is None:
        return "All groups"
    groups_by_number = {
        group.number: group
        for group in FormGroup.objects.filter(number__in=group_numbers)
    }
    labels = [
        _impact_group_short_label(groups_by_number.get(group_number), group_number)
        for group_number in sorted(group_numbers)
    ]
    if len(labels) > 8:
        return ", ".join(labels[:8]) + f" +{len(labels) - 8} more"
    return ", ".join(labels) if labels else "No groups selected"


def _impact_report_filename(group_numbers: set[int] | None) -> str:
    if not group_numbers:
        return "impact_report_all_groups.pdf"
    joined = "-".join(str(group_number) for group_number in sorted(group_numbers))
    return f"impact_report_groups_{joined}.pdf"


def _build_group_impact_report_payload(group_numbers: set[int] | None = None) -> dict:
    all_records = _participant_records()
    participant_records = _filter_records_by_groups(all_records, group_numbers)
    participant_summary = _participant_summary(participant_records, group_numbers=group_numbers)
    application_summary = _application_summary(group_numbers)
    conversion_rows = _conversion_summary(participant_summary, application_summary)
    alumni_summary = _alumni_mentor_summary(participant_records)
    participant_emails = {
        record["email"]
        for record in participant_records
        if record.get("email")
    }
    survey_scope = participant_emails if group_numbers is not None else None
    datasets, email_sets = _load_impact_survey_datasets(
        top_n=10,
        scoped_emails=survey_scope,
    )
    final_checkin_emails: set[str] = set()
    for section in IMPACT_SURVEY_SECTIONS:
        if section.get("stage") == "final":
            final_checkin_emails |= email_sets.get(section["kind"], set())
    report_overall_summary = {
        "final_unique": len(final_checkin_emails),
    }
    nps_rows = _collect_survey_metric_rows(datasets, "nps_rows")
    wellbeing_rows = _collect_survey_metric_rows(datasets, "wellbeing_rows")
    return {
        "group_numbers": group_numbers,
        "group_label": _impact_group_scope_label(group_numbers),
        "generated_at": timezone.localtime(timezone.now()).strftime("%Y-%m-%d %H:%M"),
        "participant_summary": participant_summary,
        "application_summary": application_summary,
        "conversion_rows": conversion_rows,
        "alumni_summary": alumni_summary,
        "impact_journey_data": _impact_journey_data(
            participant_summary,
            application_summary,
            report_overall_summary,
        ),
        "impact_story_rate_data": _impact_story_rate_data(participant_summary, conversion_rows),
        "survey_response_rate_data": _survey_response_rate_data(participant_summary),
        "nps_rows": nps_rows[:8],
        "wellbeing_rows": wellbeing_rows[:8],
        "participant_status_key": _participant_status_key(),
        "survey_source_note": (
            "NPS and wellbeing fields are filtered by selected participant emails when possible."
            if group_numbers is not None
            else "NPS and wellbeing fields use all loaded impact check-in rows."
        ),
    }


def _impact_pdf_value(value, suffix: str = "") -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        text = f"{value:.1f}".rstrip("0").rstrip(".")
    else:
        text = str(value)
    return f"{text}{suffix}"


def _impact_pdf_draw_cards(ax, cards: list[dict]) -> None:
    from matplotlib.patches import Rectangle

    ax.axis("off")
    columns = 4
    rows = 2
    gap_x = 0.018
    gap_y = 0.12
    card_w = (1 - gap_x * (columns - 1)) / columns
    card_h = (1 - gap_y * (rows - 1)) / rows
    for index, card in enumerate(cards[: columns * rows]):
        row = index // columns
        col = index % columns
        x = col * (card_w + gap_x)
        y = 1 - (row + 1) * card_h - row * gap_y
        ax.add_patch(
            Rectangle(
                (x, y),
                card_w,
                card_h,
                transform=ax.transAxes,
                facecolor="#f8fbff",
                edgecolor="#d7e2ef",
                linewidth=1,
            )
        )
        ax.add_patch(
            Rectangle(
                (x, y + card_h - 0.04),
                card_w,
                0.04,
                transform=ax.transAxes,
                facecolor=card.get("color", "#3B82F6"),
                edgecolor=card.get("color", "#3B82F6"),
                linewidth=0,
            )
        )
        ax.text(
            x + 0.025,
            y + card_h - 0.12,
            card["label"],
            transform=ax.transAxes,
            fontsize=8,
            color="#475569",
            weight="bold",
            va="top",
        )
        ax.text(
            x + 0.025,
            y + 0.17,
            str(card["value"]),
            transform=ax.transAxes,
            fontsize=19,
            color="#111827",
            weight="bold",
            va="bottom",
        )
        if card.get("note"):
            ax.text(
                x + 0.025,
                y + 0.06,
                card["note"],
                transform=ax.transAxes,
                fontsize=7,
                color="#64748b",
                va="bottom",
            )


def _impact_pdf_draw_barh(ax, data: list[dict], title: str, suffix: str = "", max_value: float | None = None) -> None:
    labels = [item["label"] for item in data]
    values = [float(item.get("value") or 0) for item in data]
    colors = [item.get("color") or "#3B82F6" for item in data]
    ax.set_title(title, loc="left", fontsize=10, color="#1f2937", weight="bold", pad=8)
    if not labels:
        ax.text(0.5, 0.5, "No data available", ha="center", va="center", color="#64748b")
        ax.axis("off")
        return

    y_positions = list(range(len(labels)))
    ax.barh(y_positions, values, color=colors, height=0.48)
    ax.set_yticks(y_positions)
    ax.set_yticklabels(labels, fontsize=8)
    ax.invert_yaxis()
    upper = max_value if max_value is not None else max(values + [1])
    ax.set_xlim(0, max(upper, 1))
    ax.grid(axis="x", color="#e5ecf5", linewidth=0.7)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(axis="x", labelsize=7, colors="#64748b")
    for y, value in zip(y_positions, values):
        ax.text(
            min(value + (upper * 0.02), upper),
            y,
            _impact_pdf_value(value, suffix),
            va="center",
            fontsize=8,
            color="#334155",
        )


def _impact_pdf_draw_table(ax, title: str, columns: list[str], rows: list[list], font_size: int = 7) -> None:
    ax.axis("off")
    ax.set_title(title, loc="left", fontsize=10, color="#1f2937", weight="bold", pad=8)
    if not rows:
        ax.text(0.02, 0.78, "No data available", color="#64748b", fontsize=8)
        return
    table = ax.table(
        cellText=[[str(cell) for cell in row] for row in rows],
        colLabels=columns,
        loc="upper left",
        cellLoc="left",
        colLoc="left",
        bbox=[0, 0, 1, 0.88],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(font_size)
    for (row, _col), cell in table.get_celld().items():
        cell.set_edgecolor("#d7e2ef")
        cell.set_linewidth(0.5)
        if row == 0:
            cell.set_facecolor("#eef4fb")
            cell.set_text_props(weight="bold", color="#334155")
        else:
            cell.set_facecolor("#ffffff")


def _render_group_impact_report_pdf(payload: dict) -> bytes:
    import os
    import tempfile

    os.environ.setdefault("MPLCONFIGDIR", tempfile.gettempdir())
    os.environ.setdefault("XDG_CACHE_HOME", tempfile.gettempdir())

    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    participant_summary = payload["participant_summary"]
    application_summary = payload["application_summary"]
    conversion_rows = payload["conversion_rows"]
    alumni_summary = payload["alumni_summary"]
    overall_participants = participant_summary["overall"]
    overall_apps = application_summary["overall"]
    conversion_all = next((row for row in conversion_rows if row["track"] == "All"), {})
    buffer = io.BytesIO()

    cards = [
        {
            "label": "Workbook rows",
            "value": overall_participants["rows"],
            "note": f"{overall_participants['unique']} unique emails",
            "color": "#3B82F6",
        },
        {
            "label": "Started program",
            "value": overall_participants["started"],
            "note": f"{overall_participants['started_unique']} unique emails",
            "color": "#14B8A6",
        },
        {
            "label": "Graduated",
            "value": overall_participants["graduated"],
            "note": f"{overall_participants['graduation_rate']}% graduation rate",
            "color": "#22C55E",
        },
        {
            "label": "Unique applicants",
            "value": overall_apps["unique"],
            "note": f"{overall_apps['raw']} raw intake forms",
            "color": "#F59E0B",
        },
        {
            "label": "App to start",
            "value": _impact_pdf_value(conversion_all.get("app_to_start_rate", 0), "%"),
            "note": "Email matched",
            "color": "#6366F1",
        },
        {
            "label": "App to graduation",
            "value": _impact_pdf_value(conversion_all.get("app_to_grad_rate", 0), "%"),
            "note": "Email matched",
            "color": "#8B5CF6",
        },
        {
            "label": "E alumni as mentors",
            "value": alumni_summary["returnee_count"],
            "note": "Email overlap",
            "color": "#22C55E",
        },
        {
            "label": "Repeat mentors",
            "value": alumni_summary["repeated_mentor_count"],
            "note": "Mentor in 2+ groups",
            "color": "#14B8A6",
        },
    ]

    with PdfPages(buffer) as pdf:
        fig = plt.figure(figsize=(11, 8.5), facecolor="white")
        fig.text(0.06, 0.94, "Club Emprendo Impact Report", fontsize=20, weight="bold", color="#111827")
        fig.text(0.06, 0.905, f"Groups: {payload['group_label']}", fontsize=10, color="#334155")
        fig.text(0.06, 0.882, f"Generated: {payload['generated_at']}", fontsize=8, color="#64748b")
        fig.text(
            0.06,
            0.852,
            "Group-scoped metrics from participant workbooks, intake records, and matching impact check-ins where available.",
            fontsize=8,
            color="#64748b",
        )
        grid = fig.add_gridspec(
            3,
            3,
            left=0.06,
            right=0.95,
            top=0.79,
            bottom=0.08,
            hspace=0.55,
            wspace=0.45,
        )
        _impact_pdf_draw_cards(fig.add_subplot(grid[0, :]), cards)
        _impact_pdf_draw_barh(
            fig.add_subplot(grid[1, 0]),
            payload["impact_journey_data"],
            "Impact journey",
        )
        _impact_pdf_draw_barh(
            fig.add_subplot(grid[1, 1]),
            payload["impact_story_rate_data"],
            "Outcome rates",
            suffix="%",
            max_value=100,
        )
        _impact_pdf_draw_barh(
            fig.add_subplot(grid[1, 2]),
            payload["survey_response_rate_data"],
            "Track check-ins",
            suffix="%",
            max_value=100,
        )
        notes_ax = fig.add_subplot(grid[2, :])
        notes_ax.axis("off")
        notes_ax.text(0, 0.92, "Definitions", fontsize=10, weight="bold", color="#1f2937")
        notes = [
            "Participant workbook rows: everyone listed in the participant workbook for the selected group scope.",
            "Started program: workbook rows with progress checks or Estatus in NCP, NCPP, CG, CP, D/NC, E, G, or A.",
            "Graduated: workbook rows where Estatus is G. Graduation rate is graduated divided by started.",
            "Unique applicants: intake emails deduped across repeated submissions in the selected group scope.",
            payload["survey_source_note"],
        ]
        status_key = "; ".join(
            f"{item['code']}={item['label']}" for item in payload["participant_status_key"]
        )
        status_lines = textwrap.wrap(f"Estatus key: {status_key}", width=132)
        for index, note in enumerate(notes + status_lines):
            prefix = "- " if index < len(notes) else "  "
            notes_ax.text(0, 0.78 - index * 0.09, f"{prefix}{note}", fontsize=8, color="#334155")
        pdf.savefig(fig)
        plt.close(fig)

        fig = plt.figure(figsize=(11, 8.5), facecolor="white")
        fig.text(0.06, 0.94, "Program Detail", fontsize=18, weight="bold", color="#111827")
        fig.text(0.06, 0.91, f"Groups: {payload['group_label']}", fontsize=9, color="#64748b")
        grid = fig.add_gridspec(
            2,
            2,
            left=0.06,
            right=0.95,
            top=0.86,
            bottom=0.08,
            hspace=0.35,
            wspace=0.25,
        )
        participant_rows = [
            [
                track["label"],
                track["rows"],
                track["started"],
                track["graduated"],
                f"{track['graduation_rate']}%",
                f"{track['initial_survey_rate']}%",
                f"{track['final_survey_rate']}%",
            ]
            for track in participant_summary["tracks"].values()
        ]
        _impact_pdf_draw_table(
            fig.add_subplot(grid[0, 0]),
            "Participants by track",
            ["Track", "Rows", "Started", "Grad", "Grad %", "Initial", "Final"],
            participant_rows,
        )
        conversion_table_rows = [
            [
                row["track"],
                row["unique_applicants"],
                row["started_from_app"],
                f"{row['app_to_start_rate']}%",
                row["graduated_from_app"],
                f"{row['app_to_grad_rate']}%",
            ]
            for row in conversion_rows
        ]
        _impact_pdf_draw_table(
            fig.add_subplot(grid[0, 1]),
            "Reach to outcome conversion",
            ["Track", "Apps", "Start", "Start %", "Grad", "Grad %"],
            conversion_table_rows,
        )
        country_rows = [
            [row["country"], row["count"]]
            for row in participant_summary["country_rows"][:8]
        ]
        _impact_pdf_draw_table(
            fig.add_subplot(grid[1, 0]),
            "Participant countries",
            ["Country", "Rows"],
            country_rows,
        )
        group_rows = [
            [
                row["group_label"],
                row["track"],
                row["participants"],
                row["started"],
                row["graduated"],
            ]
            for row in participant_summary["group_rows"][:10]
        ]
        _impact_pdf_draw_table(
            fig.add_subplot(grid[1, 1]),
            "Group outcomes",
            ["Group", "Track", "Rows", "Started", "Grad"],
            group_rows,
        )
        pdf.savefig(fig)
        plt.close(fig)

        fig = plt.figure(figsize=(11, 8.5), facecolor="white")
        fig.text(0.06, 0.94, "Impact Signals and Evidence Notes", fontsize=18, weight="bold", color="#111827")
        fig.text(0.06, 0.91, f"Groups: {payload['group_label']}", fontsize=9, color="#64748b")
        grid = fig.add_gridspec(
            2,
            2,
            left=0.06,
            right=0.95,
            top=0.86,
            bottom=0.08,
            hspace=0.35,
            wspace=0.25,
        )
        nps_rows = [
            [
                row["dataset"],
                row["label"][:44],
                row["score"],
                row["responses"],
                row["promoters"],
                row["detractors"],
            ]
            for row in payload["nps_rows"][:6]
        ]
        _impact_pdf_draw_table(
            fig.add_subplot(grid[0, :]),
            "NPS-like check-in fields",
            ["Checkpoint", "Column", "NPS", "Resp", "Prom", "Detr"],
            nps_rows,
            font_size=6,
        )
        wellbeing_rows = [
            [
                row["dataset"],
                row["label"][:44],
                row["avg"],
                row["responses"],
                f"{row['min']} - {row['max']}",
            ]
            for row in payload["wellbeing_rows"][:6]
        ]
        _impact_pdf_draw_table(
            fig.add_subplot(grid[1, 0]),
            "Wellbeing-like check-in fields",
            ["Checkpoint", "Column", "Avg", "Resp", "Range"],
            wellbeing_rows,
            font_size=6,
        )
        notes_ax = fig.add_subplot(grid[1, 1])
        notes_ax.axis("off")
        notes_ax.set_title("Manual/external metrics to add", loc="left", fontsize=10, color="#1f2937", weight="bold", pad=8)
        external_notes = [
            "Social media followers by platform",
            "Website traffic",
            "Facebook alumni community activity",
            "Course-only users outside mentoring",
            "Qualitative achievements",
            "Automation efficiency",
            "Finance ratios and team size",
        ]
        for index, note in enumerate(external_notes):
            notes_ax.text(0.02, 0.86 - index * 0.1, f"- {note}", fontsize=8, color="#334155")
        pdf.savefig(fig)
        plt.close(fig)

    buffer.seek(0)
    return buffer.getvalue()


@staff_member_required
def dashboards_home(request):
    return render(request, "admin_dash/dashboards_home.html")


@staff_member_required
def impact_dashboard_pdf(request):
    parsed_group_numbers = _parse_impact_group_numbers(request.GET.getlist("groups"))
    group_numbers = parsed_group_numbers or None
    payload = _build_group_impact_report_payload(group_numbers)
    pdf_bytes = _render_group_impact_report_pdf(payload)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="{_impact_report_filename(group_numbers)}"'
    )
    return response


@staff_member_required
def impact_dashboard(request):
    track_filter = (request.GET.get("track") or "all").strip().lower()
    if track_filter not in {"all", "e", "m"}:
        track_filter = "all"

    stage_filter = (request.GET.get("stage") or "all").strip().lower()
    if stage_filter not in {"all", "initial", "final"}:
        stage_filter = "all"

    top_n = _safe_int(request.GET.get("top_n"), default=10, minimum=3, maximum=40)

    customize_mode = _is_truthy(request.GET.get("customize"))
    show_cards = _is_truthy(request.GET.get("show_cards")) if customize_mode else True
    show_visuals = _is_truthy(request.GET.get("show_visuals")) if customize_mode else True
    show_volume_chart = _is_truthy(request.GET.get("show_volume_chart")) if customize_mode else True
    show_track_table = _is_truthy(request.GET.get("show_track_table")) if customize_mode else True
    show_dataset_cards = _is_truthy(request.GET.get("show_dataset_cards")) if customize_mode else True
    show_completion_e = _is_truthy(request.GET.get("show_completion_e")) if customize_mode else True
    show_completion_m = _is_truthy(request.GET.get("show_completion_m")) if customize_mode else True

    sections = IMPACT_SURVEY_SECTIONS
    datasets, email_sets = _load_impact_survey_datasets(
        top_n=top_n,
        request=request,
    )

    participant_records = _participant_records()
    participant_summary = _participant_summary(participant_records)
    application_summary = _application_summary()
    conversion_rows = _conversion_summary(participant_summary, application_summary)
    alumni_summary = _alumni_mentor_summary(participant_records)
    nps_rows = _collect_survey_metric_rows(datasets, "nps_rows")
    wellbeing_rows = _collect_survey_metric_rows(datasets, "wellbeing_rows")
    survey_response_rate_data = _survey_response_rate_data(participant_summary)

    def _in_scope(section: dict) -> bool:
        if track_filter != "all" and section["track"] != track_filter:
            return False
        if stage_filter != "all" and section["stage"] != stage_filter:
            return False
        return True

    scoped_sections = [section for section in sections if _in_scope(section)]
    visible_kinds = {section["kind"] for section in scoped_sections}

    initial_sections = [section for section in scoped_sections if section["stage"] == "initial"]
    final_sections = [section for section in scoped_sections if section["stage"] == "final"]

    initial_union: set[str] = set()
    for section in initial_sections:
        initial_union |= email_sets.get(section["kind"], set())

    final_union: set[str] = set()
    for section in final_sections:
        final_union |= email_sets.get(section["kind"], set())

    overall_summary = {
        "initial_responses": sum(datasets[section["kind"]]["responses_count"] for section in initial_sections),
        "final_responses": sum(datasets[section["kind"]]["responses_count"] for section in final_sections),
        "initial_unique": len(initial_union),
        "final_unique": len(final_union),
        "matched_unique": len(initial_union & final_union),
    }
    overall_summary["response_growth"] = overall_summary["final_responses"] - overall_summary["initial_responses"]
    overall_summary["final_vs_initial_pct"] = _pct(overall_summary["final_responses"], overall_summary["initial_responses"])
    overall_summary["retention_pct"] = _pct(overall_summary["matched_unique"], overall_summary["initial_unique"])

    track_summaries = []
    for track_key, track_label in (("e", "Emprendedoras"), ("m", "Mentoras")):
        if track_filter != "all" and track_filter != track_key:
            continue
        track_initial_sections = [
            section for section in scoped_sections
            if section["track"] == track_key and section["stage"] == "initial"
        ]
        track_final_sections = [
            section for section in scoped_sections
            if section["track"] == track_key and section["stage"] == "final"
        ]

        track_initial_emails: set[str] = set()
        for section in track_initial_sections:
            track_initial_emails |= email_sets.get(section["kind"], set())

        track_final_emails: set[str] = set()
        for section in track_final_sections:
            track_final_emails |= email_sets.get(section["kind"], set())

        track_initial_responses = sum(datasets[section["kind"]]["responses_count"] for section in track_initial_sections)
        track_final_responses = sum(datasets[section["kind"]]["responses_count"] for section in track_final_sections)
        track_matched = len(track_initial_emails & track_final_emails)
        track_summaries.append(
            {
                "label": track_label,
                "initial_responses": track_initial_responses,
                "final_responses": track_final_responses,
                "response_growth": track_final_responses - track_initial_responses,
                "final_vs_initial_pct": _pct(track_final_responses, track_initial_responses),
                "initial_unique": len(track_initial_emails),
                "final_unique": len(track_final_emails),
                "matched_unique": track_matched,
                "retention_pct": _pct(track_matched, len(track_initial_emails)),
            }
        )

    flow_points = [
        {
            "label": section["short_label"],
            "count": datasets[section["kind"]]["responses_count"],
        }
        for section in scoped_sections
    ]

    dataset_mix = [
        {
            "label": section["short_label"],
            "value": datasets[section["kind"]]["responses_count"],
            "color": section["color"],
        }
        for section in scoped_sections
    ]
    max_dataset_value = max(max([item["value"] for item in dataset_mix], default=0), 1)

    retention_mix = [
        {"label": "Retained unique", "value": overall_summary["matched_unique"], "color": "#22C55E"},
        {
            "label": "Only initial",
            "value": max(overall_summary["initial_unique"] - overall_summary["matched_unique"], 0),
            "color": "#F97316",
        },
        {
            "label": "Only final",
            "value": max(overall_summary["final_unique"] - overall_summary["matched_unique"], 0),
            "color": "#64748B",
        },
    ]

    impact_journey_data = _impact_journey_data(
        participant_summary,
        application_summary,
        overall_summary,
    )
    impact_story_rate_data = _impact_story_rate_data(participant_summary, conversion_rows)
    source_datasets = [datasets[section["kind"]] for section in scoped_sections]

    context = {
        "track_filter": track_filter,
        "stage_filter": stage_filter,
        "top_n": top_n,
        "show_cards": show_cards,
        "show_visuals": show_visuals,
        "show_volume_chart": show_volume_chart,
        "show_track_table": show_track_table,
        "show_dataset_cards": show_dataset_cards,
        "show_completion_e": show_completion_e,
        "show_completion_m": show_completion_m,
        "show_e_initial": "emprendedoras" in visible_kinds,
        "show_e_final": "emprendedoras_final" in visible_kinds,
        "show_m_initial": "mentoras" in visible_kinds,
        "show_m_final": "mentoras_final" in visible_kinds,
        "has_e_completion_cards": ("emprendedoras" in visible_kinds) or ("emprendedoras_final" in visible_kinds),
        "has_m_completion_cards": ("mentoras" in visible_kinds) or ("mentoras_final" in visible_kinds),
        "has_scoped_data": bool(scoped_sections),
        "overall": overall_summary,
        "participant_summary": participant_summary,
        "application_summary": application_summary,
        "conversion_rows": conversion_rows,
        "alumni_summary": alumni_summary,
        "nps_rows": nps_rows[:12],
        "wellbeing_rows": wellbeing_rows[:12],
        "impact_journey_data": impact_journey_data,
        "impact_story_rate_data": impact_story_rate_data,
        "survey_response_rate_data": survey_response_rate_data,
        "track_summaries": track_summaries,
        "datasets": datasets,
        "source_datasets": source_datasets,
        "flow_points": flow_points,
        "dataset_mix": dataset_mix,
        "max_dataset_value": max_dataset_value,
        "retention_mix": retention_mix,
        "impact_report_group_options": _impact_group_options(),
    }
    return render(request, "admin_dash/impact_dashboard.html", context)


@staff_member_required
def applications_dashboard(request):
    base_qs = Application.objects.select_related("form", "form__group")

    date_from = _parse_iso_date(request.GET.get("date_from"))
    date_to = _parse_iso_date(request.GET.get("date_to"))
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from

    group_filter = (request.GET.get("group") or "").strip()
    track_filter = (request.GET.get("track") or "all").strip().lower()
    if track_filter not in {"all", "e", "m"}:
        track_filter = "all"

    granularity = (request.GET.get("granularity") or "month").strip().lower()
    if granularity not in {"day", "week", "month"}:
        granularity = "month"

    top_n = _safe_int(request.GET.get("top_n"), default=8, minimum=3, maximum=30)
    recent_n = _safe_int(request.GET.get("recent_n"), default=25, minimum=5, maximum=200)

    customize_mode = _is_truthy(request.GET.get("customize"))
    show_cards = _is_truthy(request.GET.get("show_cards")) if customize_mode else True
    show_timeline = _is_truthy(request.GET.get("show_timeline")) if customize_mode else True
    show_pie_charts = _is_truthy(request.GET.get("show_pie_charts")) if customize_mode else True
    show_form_chart = _is_truthy(request.GET.get("show_form_chart")) if customize_mode else True
    show_form_table = _is_truthy(request.GET.get("show_form_table")) if customize_mode else True
    show_group_table = _is_truthy(request.GET.get("show_group_table")) if customize_mode else True
    show_recent_table = _is_truthy(request.GET.get("show_recent_table")) if customize_mode else True

    filtered_qs = base_qs
    if date_from:
        filtered_qs = filtered_qs.filter(created_at__date__gte=date_from)
    if date_to:
        filtered_qs = filtered_qs.filter(created_at__date__lte=date_to)

    if track_filter == "e":
        filtered_qs = filtered_qs.filter(Q(form__slug__contains="E_A1") | Q(form__slug__contains="E_A2"))
    elif track_filter == "m":
        filtered_qs = filtered_qs.filter(Q(form__slug__contains="M_A1") | Q(form__slug__contains="M_A2"))

    pre_group_qs = filtered_qs
    if group_filter == "ungrouped":
        filtered_qs = filtered_qs.filter(form__group__isnull=True)
    elif group_filter.isdigit():
        filtered_qs = filtered_qs.filter(form__group__number=int(group_filter))

    group_rows = (
        pre_group_qs.values("form__group__number")
        .annotate(total=Count("id"))
        .order_by("form__group__number")
    )
    group_options = []
    for row in group_rows:
        group_num = row["form__group__number"]
        total = row["total"] or 0
        if group_num is None:
            group_options.append(
                {"value": "ungrouped", "label": f"Ungrouped ({total})"}
            )
        else:
            group_options.append(
                {"value": str(group_num), "label": f"Group {group_num} ({total})"}
            )
    if not group_options:
        for group_num in FormGroup.objects.order_by("number").values_list("number", flat=True):
            group_options.append({"value": str(group_num), "label": f"Group {group_num} (0)"})

    summary = filtered_qs.aggregate(
        total=Count("id"),
        unique_emails=Count("email", distinct=True),
        invited=Count("id", filter=Q(invited_to_second_stage=True)),
        avg_overall=Avg("overall_score", filter=Q(overall_score__gt=0)),
    )
    total_apps = summary["total"] or 0
    invited = summary["invited"] or 0
    unique_emails = summary["unique_emails"] or 0
    avg_overall = summary["avg_overall"]

    a2_q = Q(form__slug__contains="A2")
    a2_total = filtered_qs.filter(a2_q).count()
    a2_graded = (
        filtered_qs.filter(a2_q)
        .exclude(Q(recommendation__isnull=True) | Q(recommendation=""))
        .count()
    )
    recent_30_days = filtered_qs.filter(
        created_at__date__gte=timezone.localdate() - timedelta(days=29)
    ).count()

    form_rows_qs = (
        filtered_qs.values("form__slug", "form__name", "form__group__number")
        .annotate(
            total=Count("id"),
            invited=Count("id", filter=Q(invited_to_second_stage=True)),
            avg_overall=Avg("overall_score", filter=Q(overall_score__gt=0)),
        )
        .order_by("-total", "form__slug")
    )

    form_rows = []
    for row in form_rows_qs:
        slug = row["form__slug"] or "—"
        group_num = row["form__group__number"] or _group_number_from_slug(slug)
        form_rows.append(
            {
                "slug": slug,
                "name": row["form__name"] or slug,
                "group_num": group_num,
                "track": _track_from_slug(slug),
                "total": row["total"] or 0,
                "invited": row["invited"] or 0,
                "avg_overall": row["avg_overall"],
            }
        )

    top_forms = form_rows[:top_n]
    max_form_total = max([r["total"] for r in top_forms], default=1)
    form_chart_points = [
        {
            "label": row["slug"],
            "count": row["total"],
            "pct": round((row["total"] / max_form_total) * 100, 1) if max_form_total else 0.0,
        }
        for row in top_forms
    ]

    track_totals = {"E": 0, "M": 0, "Other": 0}
    for row in form_rows:
        track = row["track"]
        if track not in track_totals:
            track = "Other"
        track_totals[track] += row["total"] or 0

    track_mix = [
        {"label": "Emprendedoras (E)", "value": track_totals["E"], "color": "#3B82F6"},
        {"label": "Mentoras (M)", "value": track_totals["M"], "color": "#22C55E"},
        {"label": "Other", "value": track_totals["Other"], "color": "#F59E0B"},
    ]

    stage_a1_total = filtered_qs.filter(form__slug__contains="A1").count()
    stage_a2_total = filtered_qs.filter(form__slug__contains="A2").count()
    stage_other_total = max(total_apps - stage_a1_total - stage_a2_total, 0)
    stage_mix = [
        {"label": "A1", "value": stage_a1_total, "color": "#8B5CF6"},
        {"label": "A2", "value": stage_a2_total, "color": "#14B8A6"},
        {"label": "Other", "value": stage_other_total, "color": "#F97316"},
    ]

    trunc_map = {
        "day": TruncDay,
        "week": TruncWeek,
        "month": TruncMonth,
    }
    timeline_rows = (
        filtered_qs.annotate(period=trunc_map[granularity]("created_at"))
        .values("period")
        .annotate(total=Count("id"))
        .order_by("period")
    )
    max_timeline_total = max([row["total"] for row in timeline_rows], default=1)
    timeline_points = []
    for row in timeline_rows:
        period = row["period"]
        if granularity == "month":
            label = period.strftime("%Y-%m")
        else:
            label = period.strftime("%Y-%m-%d")
        total = row["total"] or 0
        timeline_points.append(
            {
                "label": label,
                "count": total,
                "pct": round((total / max_timeline_total) * 100, 1) if max_timeline_total else 0.0,
            }
        )

    grouped = {}
    grouped_rows = (
        filtered_qs.values("form__slug", "form__group__number")
        .annotate(total=Count("id"))
        .order_by("-total")
    )
    for row in grouped_rows:
        slug = row["form__slug"] or ""
        group_num = row["form__group__number"] or _group_number_from_slug(slug)
        key = f"Group {group_num}" if group_num else "Ungrouped"
        if key not in grouped:
            grouped[key] = {
                "group_label": key,
                "total": 0,
                "e_total": 0,
                "m_total": 0,
                "other_total": 0,
            }
        grouped[key]["total"] += row["total"] or 0
        track = _track_from_slug(slug)
        if track == "E":
            grouped[key]["e_total"] += row["total"] or 0
        elif track == "M":
            grouped[key]["m_total"] += row["total"] or 0
        else:
            grouped[key]["other_total"] += row["total"] or 0

    def _group_sort_key(item):
        label = item["group_label"]
        if label == "Ungrouped":
            return (1_000_000,)
        try:
            return (int(label.replace("Group ", "").strip()),)
        except ValueError:
            return (999_999,)

    group_summary_rows = sorted(grouped.values(), key=_group_sort_key)

    recent_apps = (
        filtered_qs.select_related("form")
        .order_by("-created_at", "-id")[:recent_n]
    )

    context = {
        "date_from": date_from.isoformat() if date_from else "",
        "date_to": date_to.isoformat() if date_to else "",
        "group_filter": group_filter,
        "track_filter": track_filter,
        "granularity": granularity,
        "top_n": top_n,
        "recent_n": recent_n,
        "group_options": group_options,
        "show_cards": show_cards,
        "show_timeline": show_timeline,
        "show_pie_charts": show_pie_charts,
        "show_form_chart": show_form_chart,
        "show_form_table": show_form_table,
        "show_group_table": show_group_table,
        "show_recent_table": show_recent_table,
        "total_apps": total_apps,
        "unique_emails": unique_emails,
        "invited": invited,
        "invited_pct": _pct(invited, total_apps),
        "a2_total": a2_total,
        "a2_graded": a2_graded,
        "a2_graded_pct": _pct(a2_graded, a2_total),
        "recent_30_days": recent_30_days,
        "avg_overall": avg_overall,
        "timeline_points": timeline_points,
        "form_chart_points": form_chart_points,
        "track_mix": track_mix,
        "stage_mix": stage_mix,
        "form_rows": form_rows,
        "group_summary_rows": group_summary_rows,
        "recent_apps": recent_apps,
    }
    return render(request, "admin_dash/applications_dashboard.html", context)
