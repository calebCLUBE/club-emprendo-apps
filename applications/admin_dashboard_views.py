import io
import re
import textwrap
from collections import Counter, defaultdict
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Avg, Count, Q
from django.db.models.functions import TruncDay, TruncMonth, TruncWeek
from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils import timezone

from .admin_views import _group_label_for_number, _load_database_encuestas_grid
from .models import Application, FormGroup, GroupParticipantList
from .participant_statuses import (
    PARTICIPANT_STATUS_CHOICES,
    PARTICIPANT_STATUS_COLORS,
    PARTICIPANT_STATUS_GRADUATED,
    PARTICIPANT_STATUS_SHEET_LABELS,
    PARTICIPANT_STATUS_STARTED,
    normalize_participant_status,
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
IMPACT_NPS_HEADER_TOKENS = (
    "nps",
    "recommend",
    "recomend",
    "recomiend",
    "probabilidad",
    "probable",
)
IMPACT_NPS_CONTEXT_HEADER_TOKENS = (
    "amiga",
    "amigo",
    "friend",
    "likely",
    "probable",
    "probabilidad",
    "programa",
    "program",
    "mentoria",
    "mentorship",
    "clubemprendo",
)
IMPACT_NPS_EXCLUDE_HEADER_TOKENS = (
    "cambiar",
    "cambio",
    "change",
    "comentario",
    "feedback",
    "mejorar",
    "sugerencia",
)
IMPACT_WELLBEING_HEADER_TOKENS = (
    "satisfechacontuvida",
    "satisfechaconlavida",
    "satisfechaconmivida",
    "vidaengeneral",
    "calidaddevida",
    "qualityoflife",
    "lifesatisfaction",
    "satisfactionwithlife",
)
IMPACT_WELLBEING_EXCLUDE_HEADER_TOKENS = (
    "financ",
    "ingreso",
    "ingresos",
    "ventas",
    "negocio",
    "emprendimiento",
    "contabilidad",
    "salario",
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


def _impact_group_label(group_number: int | None, group_map: dict[int, FormGroup] | None = None) -> str:
    if group_number is None:
        return "No group"
    try:
        return _group_label_for_number(int(group_number), group_map)
    except Exception:
        return f"Group {int(group_number)}"


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
    status = normalize_participant_status(raw_status)
    return status or "Sin estatus"


def _status_counts_to_rows(status_counts: dict[str, int]) -> list[dict]:
    return [
        {
            "status": status,
            "label": PARTICIPANT_STATUS_SHEET_LABELS.get(status, status),
            "count": count,
        }
        for status, count in sorted(status_counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _participant_status_key() -> list[dict]:
    return [
        {
            "code": code,
            "label": PARTICIPANT_STATUS_SHEET_LABELS.get(code, label),
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


def _is_nps_header(normalized_header: str) -> bool:
    if not any(token in normalized_header for token in IMPACT_NPS_HEADER_TOKENS):
        return False
    if any(token in normalized_header for token in IMPACT_NPS_EXCLUDE_HEADER_TOKENS):
        return False
    if "nps" in normalized_header:
        return True
    has_recommend_token = any(
        token in normalized_header for token in ("recommend", "recomend", "recomiend")
    )
    has_context_token = any(
        token in normalized_header for token in IMPACT_NPS_CONTEXT_HEADER_TOKENS
    )
    return has_recommend_token and has_context_token


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
        if not _is_nps_header(normalized):
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
        if any(token in normalized for token in IMPACT_WELLBEING_EXCLUDE_HEADER_TOKENS):
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
    group_map = {group.number: group for group in FormGroup.objects.all()}

    for participant_list in participant_lists:
        group = getattr(participant_list, "group", None)
        group_number = getattr(group, "number", None)
        group_year = getattr(group, "year", None)
        group_label = _impact_group_label(group_number, group_map)

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
    completed_group_numbers = {
        record["group_number"]
        for record in records
        if record.get("group_number") is not None and record.get("graduated")
    }

    for track_key, cfg in PARTICIPANT_TRACK_CONFIGS.items():
        track_records = [record for record in records if record["track"] == track_key]
        graduation_scope_records = [
            record
            for record in track_records
            if record.get("group_number") in completed_group_numbers
        ]
        participant_emails = {record["email"] for record in track_records if record["email"]}
        started_emails = {record["email"] for record in track_records if record["email"] and record["started"]}
        graduated_emails = {record["email"] for record in track_records if record["email"] and record["graduated"]}
        graduation_started = len([record for record in graduation_scope_records if record["started"]])
        graduation_graduated = len([record for record in graduation_scope_records if record["graduated"]])
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
            "graduation_started": graduation_started,
            "graduation_graduated": graduation_graduated,
            "graduation_rate": _rate(
                graduation_graduated,
                graduation_started,
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
    overall_graduation_scope_records = [
        record
        for record in records
        if record.get("group_number") in completed_group_numbers
    ]
    overall_graduation_started = len(
        [record for record in overall_graduation_scope_records if record["started"]]
    )
    overall_graduation_graduated = len(
        [record for record in overall_graduation_scope_records if record["graduated"]]
    )
    overall_initial_survey = len([record for record in records if record["initial_survey"]])
    overall_final_survey = len([record for record in records if record["final_survey"]])
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
            "graduation_started": overall_graduation_started,
            "graduation_graduated": overall_graduation_graduated,
            "graduation_completed_groups": len(completed_group_numbers),
            "graduation_rate": _rate(overall_graduation_graduated, overall_graduation_started),
            "initial_survey_responses": overall_initial_survey,
            "initial_survey_rate": _rate(overall_initial_survey, len(records)),
            "final_survey_responses": overall_final_survey,
            "final_survey_rate": _rate(overall_final_survey, len(records)),
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
    group_map = {group.number: group for group in FormGroup.objects.all()}
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
        group_label = _impact_group_label(group_number, group_map)
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
        participant_emails = participant_track.get("participant_emails", set())
        graduated_emails = participant_track.get("graduated_emails", set())
        listed_from_app = len(participant_emails & applicant_emails)
        graduated_from_app = len(graduated_emails & applicant_emails)
        rows.append(
            {
                "track": label,
                "unique_applicants": len(applicant_emails),
                "started_from_app": listed_from_app,
                "listed_from_app": listed_from_app,
                "graduated_from_app": graduated_from_app,
                "app_to_start_rate": _rate(listed_from_app, len(applicant_emails)),
                "app_to_listed_rate": _rate(listed_from_app, len(applicant_emails)),
                "app_to_grad_rate": _rate(graduated_from_app, len(applicant_emails)),
                "participants_without_app_match": len(participant_emails - applicant_emails),
            }
        )

    applicant_all = application_summary["email_sets"].get("all", set())
    participant_all: set[str] = set()
    graduated_all: set[str] = set()
    for track_key in ("e", "m"):
        participant_track = participant_summary["tracks"].get(track_key, {})
        participant_all |= participant_track.get("participant_emails", set())
        graduated_all |= participant_track.get("graduated_emails", set())
    listed_all_from_app = len(participant_all & applicant_all)
    graduated_all_from_app = len(graduated_all & applicant_all)
    rows.append(
        {
            "track": "All",
            "unique_applicants": len(applicant_all),
            "started_from_app": listed_all_from_app,
            "listed_from_app": listed_all_from_app,
            "graduated_from_app": graduated_all_from_app,
            "app_to_start_rate": _rate(listed_all_from_app, len(applicant_all)),
            "app_to_listed_rate": _rate(listed_all_from_app, len(applicant_all)),
            "app_to_grad_rate": _rate(graduated_all_from_app, len(applicant_all)),
            "participants_without_app_match": len(participant_all - applicant_all),
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


def _group_recruitment_source_rows(records: list[dict], group_numbers: set[int] | None = None) -> list[dict]:
    groups: dict[int, dict] = {}
    for record in records:
        group_number = record.get("group_number")
        email = record.get("email") or ""
        if group_number is None:
            continue
        if group_numbers is not None and int(group_number) not in group_numbers:
            continue
        group_row = groups.setdefault(
            int(group_number),
            {
                "group_number": int(group_number),
                "group_label": record.get("group_label") or _impact_group_label(int(group_number)),
                "emails": set(),
                "participants": 0,
            },
        )
        group_row["participants"] += 1
        if email:
            group_row["emails"].add(email)

    if not groups:
        return []

    group_map = {group.number: group for group in FormGroup.objects.all()}
    app_groups_by_email: dict[str, Counter] = defaultdict(Counter)
    app_names_by_group: dict[int | None, Counter] = defaultdict(Counter)
    for app in Application.objects.select_related("form", "form__group"):
        email = _metric_email(getattr(app, "email", ""))
        if not email:
            continue
        app_group_number = _application_group_number(app)
        app_groups_by_email[email][app_group_number] += 1
        form_name = str(getattr(getattr(app, "form", None), "name", "") or "").strip()
        if form_name:
            app_names_by_group[app_group_number][form_name] += 1

    rows: list[dict] = []
    for group_number, group_row in groups.items():
        source_counts: Counter = Counter()
        for email in group_row["emails"]:
            source_counts.update(app_groups_by_email.get(email, Counter()))

        if len(source_counts) > 1 and group_number in source_counts:
            self_count = source_counts.get(group_number, 0)
            strongest_external_count = max(
                [
                    count
                    for source_group_number, count in source_counts.items()
                    if source_group_number != group_number
                ],
                default=0,
            )
            if strongest_external_count >= self_count:
                source_counts.pop(group_number, None)
            else:
                source_counts = Counter({group_number: self_count})

        top_sources = source_counts.most_common(3)
        source_group_numbers = [
            int(source_group_number)
            for source_group_number, _count in top_sources
            if source_group_number is not None
        ]
        if top_sources:
            source_labels = []
            source_details = []
            for source_group_number, count in top_sources:
                label = _impact_group_label(source_group_number, group_map)
                source_labels.append(label)
                top_form = app_names_by_group.get(source_group_number, Counter()).most_common(1)
                form_label = top_form[0][0] if top_form else label
                source_details.append(f"{label} ({count} matches; {form_label})")
            source_label = ", ".join(source_labels)
            source_detail = "; ".join(source_details)
        else:
            source_label = "No matched intake source"
            source_detail = "No participant emails matched an intake form email."

        rows.append(
            {
                "group_number": group_number,
                "group_label": group_row["group_label"],
                "participants": group_row["participants"],
                "source_label": source_label,
                "source_detail": source_detail,
                "source_group_numbers": source_group_numbers,
            }
        )

    rows.sort(key=lambda item: -item["group_number"])
    return rows


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


def _collect_survey_metric_rows_for_kinds(
    datasets: dict[str, dict],
    key: str,
    kinds: tuple[str, ...],
) -> list[dict]:
    rows: list[dict] = []
    for kind in kinds:
        dataset = datasets.get(kind, {})
        if dataset.get("error"):
            continue
        for row in dataset.get(key, []) or []:
            item = dict(row)
            item["dataset"] = dataset.get("title", kind)
            item["kind"] = kind
            rows.append(item)
    return rows


def _completed_group_participant_emails(records: list[dict]) -> set[str]:
    completed_group_numbers = {
        record["group_number"]
        for record in records
        if record.get("group_number") is not None and record.get("graduated")
    }
    return {
        record["email"]
        for record in records
        if record.get("email") and record.get("group_number") in completed_group_numbers
    }


def _final_completed_wellbeing_rows(
    *,
    top_n: int,
    completed_emails: set[str],
    request=None,
) -> list[dict]:
    if not completed_emails:
        return []
    datasets, _email_sets = _load_impact_survey_datasets(
        top_n=top_n,
        scoped_emails=completed_emails,
        request=request,
    )
    return _collect_survey_metric_rows_for_kinds(
        datasets,
        "wellbeing_rows",
        ("emprendedoras_final", "mentoras_final"),
    )


def _wellbeing_comparison_summary(initial_rows: list[dict], final_rows: list[dict]) -> dict:
    initial = _wellbeing_metric_summary(initial_rows)
    final = _wellbeing_metric_summary(final_rows)
    change = None
    if initial.get("avg") is not None and final.get("avg") is not None:
        change = round(float(final["avg"]) - float(initial["avg"]), 2)
    chart_data = []
    if initial.get("avg") is not None:
        chart_data.append(
            {
                "label": "Initial",
                "value": initial["avg"],
                "color": "#3B82F6",
            }
        )
    if final.get("avg") is not None:
        chart_data.append(
            {
                "label": "Final completed groups",
                "value": final["avg"],
                "color": "#22C55E",
            }
        )
    return {
        "initial": initial,
        "final": final,
        "change": change,
        "chart_data": chart_data,
    }


def _survey_response_rate_data(participant_summary: dict) -> list[dict]:
    tracks = participant_summary.get("tracks", {})
    overall = participant_summary.get("overall", {})
    rows: list[dict] = []
    rows.extend(
        [
            {
                "label": "All: initial check-in",
                "value": overall.get("initial_survey_rate", 0),
                "color": "#6366F1",
            },
            {
                "label": "All: final check-in",
                "value": overall.get("final_survey_rate", 0),
                "color": "#8B5CF6",
            },
        ]
    )
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


def _chart_rows(rows: list[dict], label_key: str, value_key: str = "count", colors: list[str] | None = None) -> list[dict]:
    palette = colors or ["#3B82F6", "#14B8A6", "#F59E0B", "#8B5CF6", "#22C55E", "#64748B", "#EF4444", "#06B6D4"]
    data: list[dict] = []
    for index, row in enumerate(rows or []):
        label = str(row.get(label_key) or "").strip()
        if not label:
            continue
        value = row.get(value_key, 0) or 0
        data.append(
            {
                "label": label,
                "value": value,
                "color": row.get("color") or palette[index % len(palette)],
            }
        )
    return data


def _participant_country_chart_data(participant_summary: dict) -> dict:
    tracks = participant_summary.get("tracks", {})
    return {
        "e": _chart_rows(tracks.get("e", {}).get("country_rows", []), "country"),
        "m": _chart_rows(tracks.get("m", {}).get("country_rows", []), "country"),
    }


def _participant_status_chart_data(participant_summary: dict) -> dict:
    tracks = participant_summary.get("tracks", {})
    data: dict[str, list[dict]] = {}
    for track_key in ("e", "m"):
        rows = []
        for row in tracks.get(track_key, {}).get("status_rows", []) or []:
            status = str(row.get("status") or "").strip()
            label = str(row.get("label") or "").strip() or status
            rows.append(
                {
                    "label": label,
                    "value": row.get("count", 0) or 0,
                    "color": PARTICIPANT_STATUS_COLORS.get(status, "#64748B"),
                }
            )
        data[track_key] = rows
    return data


def _graduation_rate_chart_data(participant_summary: dict) -> list[dict]:
    tracks = participant_summary.get("tracks", {})
    overall = participant_summary.get("overall", {})
    return [
        {
            "label": "Emprendedoras",
            "value": tracks.get("e", {}).get("graduation_rate", 0),
            "color": "#3B82F6",
        },
        {
            "label": "Mentoras",
            "value": tracks.get("m", {}).get("graduation_rate", 0),
            "color": "#14B8A6",
        },
        {
            "label": "All",
            "value": overall.get("graduation_rate", 0),
            "color": "#22C55E",
        },
    ]


def _application_conversion_chart_data(conversion_rows: list[dict]) -> list[dict]:
    colors = {
        "Emprendedoras": ("#3B82F6", "#22C55E"),
        "Mentoras": ("#14B8A6", "#F59E0B"),
        "All": ("#6366F1", "#8B5CF6"),
    }
    data: list[dict] = []
    for row in conversion_rows:
        track = row.get("track")
        start_color, grad_color = colors.get(track, ("#64748B", "#94A3B8"))
        data.append(
            {
                "label": f"{track}: listed",
                "value": row.get("app_to_listed_rate", row.get("app_to_start_rate", 0)),
                "color": start_color,
            }
        )
        data.append(
            {
                "label": f"{track}: graduated",
                "value": row.get("app_to_grad_rate", 0),
                "color": grad_color,
            }
        )
    return data


def _alumni_engagement_chart_data(alumni_summary: dict) -> list[dict]:
    return [
        {
            "label": "E alumni as mentors",
            "value": alumni_summary.get("returnee_count", 0) or 0,
            "color": "#22C55E",
        },
        {
            "label": "Later mentor group",
            "value": alumni_summary.get("later_returnee_count", 0) or 0,
            "color": "#3B82F6",
        },
        {
            "label": "Repeat mentoras",
            "value": alumni_summary.get("repeated_mentor_count", 0) or 0,
            "color": "#14B8A6",
        },
    ]


def _alumni_returnee_chart_data(alumni_summary: dict) -> list[dict]:
    return [
        {
            "label": "E alumni as mentors",
            "value": alumni_summary.get("returnee_count", 0) or 0,
            "color": "#22C55E",
        },
        {
            "label": "Mentor group is later",
            "value": alumni_summary.get("later_returnee_count", 0) or 0,
            "color": "#3B82F6",
        },
    ]


def _repeat_mentor_chart_data(alumni_summary: dict) -> list[dict]:
    return [
        {
            "label": "Repeat mentoras",
            "value": alumni_summary.get("repeated_mentor_count", 0) or 0,
            "color": "#14B8A6",
        }
    ]


def _nps_metric_summary(nps_rows: list[dict]) -> dict:
    rows = list(nps_rows or [])
    total_responses = sum(int(row.get("responses") or 0) for row in rows)
    weighted_score = 0.0
    if total_responses:
        weighted_score = sum(
            float(row.get("score") or 0) * int(row.get("responses") or 0)
            for row in rows
        ) / total_responses
    return {
        "score": round(weighted_score, 1) if rows else None,
        "responses": total_responses,
        "rows": rows,
        "chart_data": [
            {
                "label": str(row.get("dataset") or row.get("label") or "Checkpoint")[:36],
                "value": row.get("score", 0),
                "color": "#6366F1",
            }
            for row in rows[:6]
        ],
    }


def _wellbeing_metric_summary(wellbeing_rows: list[dict]) -> dict:
    rows = list(wellbeing_rows or [])
    total_responses = sum(int(row.get("responses") or 0) for row in rows)
    weighted_avg = 0.0
    if total_responses:
        weighted_avg = sum(
            float(row.get("avg") or 0) * int(row.get("responses") or 0)
            for row in rows
        ) / total_responses
    return {
        "avg": round(weighted_avg, 2) if rows else None,
        "responses": total_responses,
        "rows": rows,
        "chart_data": [
            {
                "label": str(row.get("label") or row.get("dataset") or "Field")[:36],
                "value": row.get("avg", 0),
                "color": "#22C55E",
            }
            for row in rows[:6]
        ],
    }


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
            "label": "Reach -> listed",
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
    group_map = {group.number: group for group in FormGroup.objects.order_by("-number")}
    return [
        {
            "number": group.number,
            "label": _impact_group_label(group.number, group_map),
        }
        for group in group_map.values()
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
    return _impact_group_label(group_number, {group.number: group} if group is not None else None)


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


def _impact_dashboard_format_value(value, suffix: str = "") -> str:
    try:
        numeric = float(value or 0)
    except (TypeError, ValueError):
        return f"{value}{suffix}"
    rounded = round(numeric, 1)
    text = f"{rounded:.1f}".rstrip("0").rstrip(".")
    return f"{text}{suffix}"


def _impact_dashboard_bar_rows(
    data: list[dict],
    *,
    min_value: float = 0,
    max_value: float | None = None,
    suffix: str = "",
) -> list[dict]:
    values = []
    for item in data or []:
        try:
            values.append(float(item.get("value") or 0))
        except (TypeError, ValueError):
            values.append(0.0)
    upper = float(max_value) if max_value is not None else max(values + [1])
    lower = float(min_value)
    value_range = max(upper - lower, 1)
    has_zero = lower < 0 < upper
    zero_pct = ((0 - lower) / value_range) * 100 if has_zero else 0
    rows = []
    for item, raw_value in zip(data or [], values):
        label = str(item.get("label") or "")
        if not label:
            continue
        clamped = max(lower, min(upper, raw_value))
        value_pct = ((clamped - lower) / value_range) * 100
        left = min(zero_pct, value_pct) if has_zero else 0
        width = abs(value_pct - zero_pct) if has_zero else max(1, value_pct)
        rows.append(
            {
                **item,
                "bar_left": round(left, 3),
                "bar_width": round(width, 3),
                "bar_zero_pct": round(zero_pct, 3),
                "bar_has_zero": has_zero,
                "bar_display_value": item.get("display_value")
                if item.get("display_value") is not None
                else _impact_dashboard_format_value(raw_value, suffix),
            }
        )
    return rows


def _impact_dashboard_donut(data: list[dict]) -> dict:
    rows = [
        {
            "label": str(item.get("label") or ""),
            "value": float(item.get("value") or 0),
            "color": item.get("color") or "#94a3b8",
        }
        for item in data or []
        if item.get("value")
    ]
    total = sum(item["value"] for item in rows)
    if total <= 0:
        return {"total": 0, "background": "#e5e7eb", "legend": []}
    current = 0.0
    parts = []
    legend = []
    for item in rows:
        start = (current / total) * 360
        current += item["value"]
        end = (current / total) * 360
        parts.append(f"{item['color']} {start:.3f}deg {end:.3f}deg")
        legend.append(
            {
                **item,
                "value_display": _impact_dashboard_format_value(item["value"]),
                "pct": round((item["value"] / total) * 100, 1),
            }
        )
    return {
        "total": _impact_dashboard_format_value(total),
        "background": f"conic-gradient({', '.join(parts)})",
        "legend": legend,
    }


def _prepare_impact_dashboard_chart_context(context: dict) -> dict:
    country_data = context.get("participant_country_chart_data") or {}
    status_data = context.get("participant_status_chart_data") or {}
    context.update(
        {
            "country_e_donut": _impact_dashboard_donut(country_data.get("e", [])),
            "country_m_donut": _impact_dashboard_donut(country_data.get("m", [])),
            "status_e_bar_rows": _impact_dashboard_bar_rows(status_data.get("e", [])),
            "status_m_bar_rows": _impact_dashboard_bar_rows(status_data.get("m", [])),
            "graduation_rate_bar_rows": _impact_dashboard_bar_rows(
                context.get("graduation_rate_chart_data") or [],
                max_value=100,
                suffix="%",
            ),
            "application_conversion_bar_rows": _impact_dashboard_bar_rows(
                context.get("application_conversion_chart_data") or [],
                max_value=100,
                suffix="%",
            ),
            "alumni_returnee_bar_rows": _impact_dashboard_bar_rows(
                context.get("alumni_returnee_chart_data") or []
            ),
            "repeat_mentor_bar_rows": _impact_dashboard_bar_rows(
                context.get("repeat_mentor_chart_data") or []
            ),
            "nps_bar_rows": _impact_dashboard_bar_rows(
                (context.get("nps_summary") or {}).get("chart_data") or [],
                min_value=-100,
                max_value=100,
            ),
            "wellbeing_bar_rows": _impact_dashboard_bar_rows(
                (context.get("wellbeing_summary") or {}).get("chart_data") or []
            ),
            "survey_response_rate_bar_rows": _impact_dashboard_bar_rows(
                context.get("survey_response_rate_data") or [],
                max_value=100,
                suffix="%",
            ),
        }
    )
    return context


def _build_group_impact_report_payload(group_numbers: set[int] | None = None) -> dict:
    all_records = _participant_records()
    participant_records = _filter_records_by_groups(all_records, group_numbers)
    participant_summary = _participant_summary(participant_records, group_numbers=group_numbers)
    group_source_rows = _group_recruitment_source_rows(participant_records, group_numbers)
    application_group_numbers = group_numbers
    if group_numbers is not None:
        inferred_source_groups = {
            int(source_group_number)
            for row in group_source_rows
            for source_group_number in row.get("source_group_numbers", [])
        }
        if inferred_source_groups:
            application_group_numbers = inferred_source_groups
    application_summary = _application_summary(application_group_numbers)
    conversion_rows = _conversion_summary(participant_summary, application_summary)
    alumni_summary = _alumni_mentor_summary(participant_records)
    participant_emails = {
        record["email"]
        for record in participant_records
        if record.get("email")
    }
    completed_participant_emails = _completed_group_participant_emails(participant_records)
    survey_scope = participant_emails if group_numbers is not None else None
    datasets, _email_sets = _load_impact_survey_datasets(
        top_n=10,
        scoped_emails=survey_scope,
    )
    nps_rows = _collect_survey_metric_rows(datasets, "nps_rows")
    initial_wellbeing_rows = _collect_survey_metric_rows_for_kinds(
        datasets,
        "wellbeing_rows",
        ("emprendedoras", "mentoras"),
    )
    final_wellbeing_rows = _final_completed_wellbeing_rows(
        top_n=10,
        completed_emails=completed_participant_emails,
    )
    wellbeing_summary = _wellbeing_comparison_summary(
        initial_wellbeing_rows,
        final_wellbeing_rows,
    )
    return {
        "group_numbers": group_numbers,
        "group_label": _impact_group_scope_label(group_numbers),
        "generated_at": timezone.localtime(timezone.now()).strftime("%Y-%m-%d %H:%M"),
        "participant_summary": participant_summary,
        "application_summary": application_summary,
        "conversion_rows": conversion_rows,
        "alumni_summary": alumni_summary,
        "group_source_rows": group_source_rows,
        "participant_country_chart_data": _participant_country_chart_data(participant_summary),
        "participant_status_chart_data": _participant_status_chart_data(participant_summary),
        "graduation_rate_chart_data": _graduation_rate_chart_data(participant_summary),
        "application_conversion_chart_data": _application_conversion_chart_data(conversion_rows),
        "alumni_engagement_chart_data": _alumni_engagement_chart_data(alumni_summary),
        "alumni_returnee_chart_data": _alumni_returnee_chart_data(alumni_summary),
        "repeat_mentor_chart_data": _repeat_mentor_chart_data(alumni_summary),
        "survey_response_rate_data": _survey_response_rate_data(participant_summary),
        "nps_rows": nps_rows[:8],
        "wellbeing_rows": (initial_wellbeing_rows + final_wellbeing_rows)[:8],
        "nps_summary": _nps_metric_summary(nps_rows),
        "wellbeing_summary": wellbeing_summary,
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


def _impact_pdf_draw_cards(ax, cards: list[dict], columns: int = 5, rows: int = 2) -> None:
    from matplotlib.patches import Rectangle

    ax.axis("off")
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
            textwrap.fill(str(card["label"]), width=18),
            transform=ax.transAxes,
            fontsize=7.5,
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
                textwrap.fill(str(card["note"]), width=24),
                transform=ax.transAxes,
                fontsize=7,
                color="#64748b",
                va="bottom",
            )


def _impact_pdf_draw_barh(
    ax,
    data: list[dict],
    title: str,
    suffix: str = "",
    max_value: float | None = None,
    min_value: float | None = 0,
) -> None:
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
    lower = min_value if min_value is not None else min(values + [0])
    upper = max_value if max_value is not None else max(values + [1])
    if lower == upper:
        upper = lower + 1
    if lower < 0 < upper:
        ax.axvline(0, color="#94a3b8", linewidth=0.8)
    ax.set_xlim(lower, upper)
    ax.grid(axis="x", color="#e5ecf5", linewidth=0.7)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(axis="x", labelsize=7, colors="#64748b")
    range_width = max(upper - lower, 1)
    for y, value in zip(y_positions, values):
        if value < 0:
            text_x = max(value - (range_width * 0.02), lower)
            ha = "right"
        else:
            text_x = min(value + (range_width * 0.02), upper)
            ha = "left"
        ax.text(
            text_x,
            y,
            _impact_pdf_value(value, suffix),
            va="center",
            ha=ha,
            fontsize=8,
            color="#334155",
        )


def _impact_pdf_draw_pie(ax, data: list[dict], title: str) -> None:
    ax.set_title(title, loc="left", fontsize=10, color="#1f2937", weight="bold", pad=8)
    labels = [str(item.get("label") or "") for item in data if (item.get("value") or 0)]
    values = [float(item.get("value") or 0) for item in data if (item.get("value") or 0)]
    colors = [item.get("color") or "#3B82F6" for item in data if (item.get("value") or 0)]
    if not values:
        ax.text(0.5, 0.5, "No data available", ha="center", va="center", color="#64748b")
        ax.axis("off")
        return
    total = sum(values)
    chart_labels = [
        f"{label} ({int(value) if value.is_integer() else value:g})"
        for label, value in zip(labels, values)
    ]
    ax.pie(
        values,
        labels=chart_labels,
        colors=colors,
        startangle=90,
        counterclock=False,
        textprops={"fontsize": 7, "color": "#334155"},
        wedgeprops={"linewidth": 0.8, "edgecolor": "white"},
    )
    ax.text(0, 0, _impact_pdf_value(total), ha="center", va="center", fontsize=10, weight="bold", color="#111827")
    ax.axis("equal")


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
    nps_summary = payload.get("nps_summary", {})
    wellbeing_summary = payload.get("wellbeing_summary", {})
    country_data = payload.get("participant_country_chart_data", {})
    status_data = payload.get("participant_status_chart_data", {})
    buffer = io.BytesIO()

    cards = [
        {
            "label": "Number of Participants",
            "value": overall_participants["rows"],
            "note": (
                f"E {participant_summary['tracks'].get('e', {}).get('rows', 0)} / "
                f"M {participant_summary['tracks'].get('m', {}).get('rows', 0)}"
            ),
            "color": "#3B82F6",
        },
        {
            "label": "Graduation Rate",
            "value": _impact_pdf_value(overall_participants["graduation_rate"], "%"),
            "note": (
                f"{overall_participants.get('graduation_graduated', 0)} graduated of "
                f"{overall_participants.get('graduation_started', 0)} began in completed groups"
            ),
            "color": "#22C55E",
        },
        {
            "label": "Application -> Listed",
            "value": _impact_pdf_value(conversion_all.get("app_to_listed_rate", 0), "%"),
            "note": f"{conversion_all.get('listed_from_app', 0)} of {overall_apps['unique']} applicants",
            "color": "#F59E0B",
        },
        {
            "label": "Application -> Graduated",
            "value": _impact_pdf_value(conversion_all.get("app_to_grad_rate", 0), "%"),
            "note": f"{conversion_all.get('graduated_from_app', 0)} applicant matches",
            "color": "#6366F1",
        },
        {
            "label": "Number of Groups",
            "value": overall_participants["groups_with_participants"],
            "note": f"{overall_participants['groups_in_system']} groups in system",
            "color": "#8B5CF6",
        },
        {
            "label": "E Returned as Mentoras",
            "value": alumni_summary["returnee_count"],
            "note": "Email overlap",
            "color": "#22C55E",
        },
        {
            "label": "Repeat Mentoras",
            "value": alumni_summary["repeated_mentor_count"],
            "note": "Mentora in 2+ groups",
            "color": "#14B8A6",
        },
        {
            "label": "NPS",
            "value": _impact_pdf_value(nps_summary.get("score")),
            "note": f"{nps_summary.get('responses', 0)} responses",
            "color": "#6366F1",
        },
        {
            "label": "Quality of Life",
            "value": _impact_pdf_value((wellbeing_summary.get("final") or {}).get("avg")),
            "note": (
                f"Initial {_impact_pdf_value((wellbeing_summary.get('initial') or {}).get('avg'))}; "
                f"change {_impact_pdf_value(wellbeing_summary.get('change'))}"
            ),
            "color": "#22C55E",
        },
        {
            "label": "Survey Response Rate",
            "value": _impact_pdf_value(overall_participants.get("final_survey_rate", 0), "%"),
            "note": f"{overall_participants.get('final_survey_responses', 0)} final responses",
            "color": "#F59E0B",
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
            "Metrics from participant workbooks, intake records, and matching impact surveys where available.",
            fontsize=8,
            color="#64748b",
        )
        grid = fig.add_gridspec(
            2,
            1,
            left=0.06,
            right=0.95,
            top=0.79,
            bottom=0.08,
            hspace=0.25,
            height_ratios=[2.2, 1],
        )
        _impact_pdf_draw_cards(fig.add_subplot(grid[0, 0]), cards, columns=5, rows=2)
        notes_ax = fig.add_subplot(grid[1, 0])
        notes_ax.axis("off")
        notes_ax.text(0, 0.92, "Definitions", fontsize=10, weight="bold", color="#1f2937")
        notes = [
            "Number of participants: rows listed on the Participants page workbook.",
            "Application -> listed: applicant emails that appear on the Participants page.",
            "Graduation rate: Estatus Graduada divided by began rows in groups with at least one Graduada.",
            "Group source: inferred from matching participant emails back to intake/application emails.",
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
        fig.text(0.06, 0.94, "Participants by Country and Estatus", fontsize=18, weight="bold", color="#111827")
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
        _impact_pdf_draw_pie(
            fig.add_subplot(grid[0, 0]),
            country_data.get("e", []),
            "Emprendedoras by country",
        )
        _impact_pdf_draw_pie(
            fig.add_subplot(grid[0, 1]),
            country_data.get("m", []),
            "Mentoras by country",
        )
        _impact_pdf_draw_barh(
            fig.add_subplot(grid[1, 0]),
            status_data.get("e", []),
            "Emprendedoras by Estatus",
        )
        _impact_pdf_draw_barh(
            fig.add_subplot(grid[1, 1]),
            status_data.get("m", []),
            "Mentoras by Estatus",
        )
        pdf.savefig(fig)
        plt.close(fig)

        fig = plt.figure(figsize=(11, 8.5), facecolor="white")
        fig.text(0.06, 0.94, "Conversion, Graduation, Groups, and Survey Response", fontsize=18, weight="bold", color="#111827")
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
        _impact_pdf_draw_barh(
            fig.add_subplot(grid[0, 0]),
            payload["graduation_rate_chart_data"],
            "Graduation rate",
            suffix="%",
            max_value=100,
        )
        _impact_pdf_draw_barh(
            fig.add_subplot(grid[0, 1]),
            payload["application_conversion_chart_data"],
            "Application conversion",
            suffix="%",
            max_value=100,
        )
        _impact_pdf_draw_barh(
            fig.add_subplot(grid[1, 0]),
            payload["survey_response_rate_data"],
            "Survey response rate",
            suffix="%",
            max_value=100,
        )
        group_source_rows = [
            [
                row["group_label"][:32],
                row["participants"],
                row["source_label"][:42],
            ]
            for row in payload.get("group_source_rows", [])[:10]
        ]
        _impact_pdf_draw_table(
            fig.add_subplot(grid[1, 1]),
            "Participant groups and matched intake source",
            ["Group", "Rows", "Intake source"],
            group_source_rows,
            font_size=6,
        )
        pdf.savefig(fig)
        plt.close(fig)

        fig = plt.figure(figsize=(11, 8.5), facecolor="white")
        fig.text(0.06, 0.94, "Alumni Engagement, NPS, and Quality of Life", fontsize=18, weight="bold", color="#111827")
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
        _impact_pdf_draw_barh(
            fig.add_subplot(grid[0, 0]),
            payload["alumni_returnee_chart_data"],
            "Emprendedoras returning as mentoras",
        )
        _impact_pdf_draw_barh(
            fig.add_subplot(grid[0, 1]),
            payload["repeat_mentor_chart_data"],
            "Repeat mentoras",
        )
        _impact_pdf_draw_barh(
            fig.add_subplot(grid[1, 0]),
            nps_summary.get("chart_data", []),
            "NPS",
            min_value=-100,
            max_value=100,
        )
        _impact_pdf_draw_barh(
            fig.add_subplot(grid[1, 1]),
            wellbeing_summary.get("chart_data", []),
            "Quality of life",
        )
        pdf.savefig(fig)
        plt.close(fig)

    buffer.seek(0)
    return buffer.getvalue()


def _impact_dashboard_context_from_payload(payload: dict) -> dict:
    return _prepare_impact_dashboard_chart_context(
        {
            "top_n": 10,
            "participant_summary": payload["participant_summary"],
            "application_summary": payload["application_summary"],
            "conversion_rows": payload["conversion_rows"],
            "alumni_summary": payload["alumni_summary"],
            "group_source_rows": payload["group_source_rows"],
            "nps_rows": payload.get("nps_rows", [])[:12],
            "wellbeing_rows": payload.get("wellbeing_rows", [])[:12],
            "nps_summary": payload["nps_summary"],
            "wellbeing_summary": payload["wellbeing_summary"],
            "participant_country_chart_data": payload["participant_country_chart_data"],
            "participant_status_chart_data": payload["participant_status_chart_data"],
            "graduation_rate_chart_data": payload["graduation_rate_chart_data"],
            "application_conversion_chart_data": payload["application_conversion_chart_data"],
            "alumni_engagement_chart_data": payload["alumni_engagement_chart_data"],
            "alumni_returnee_chart_data": payload["alumni_returnee_chart_data"],
            "repeat_mentor_chart_data": payload["repeat_mentor_chart_data"],
            "survey_response_rate_data": payload["survey_response_rate_data"],
            "impact_report_group_options": _impact_group_options(),
            "impact_report_group_label": payload["group_label"],
        }
    )


def _render_impact_dashboard_html_pdf(request, context: dict) -> bytes | None:
    try:
        from contextlib import redirect_stderr, redirect_stdout

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            from weasyprint import HTML
    except (ImportError, OSError):
        return None

    pdf_context = {
        **context,
        "is_pdf_export": True,
        "impact_dashboard_base_template": "admin_dash/impact_dashboard_pdf_base.html",
    }
    html = render_to_string("admin_dash/impact_dashboard.html", pdf_context, request=request)
    return HTML(
        string=html,
        base_url=request.build_absolute_uri("/"),
    ).write_pdf()


@staff_member_required
def dashboards_home(request):
    return render(request, "admin_dash/dashboards_home.html")


@staff_member_required
def impact_dashboard_pdf(request):
    parsed_group_numbers = _parse_impact_group_numbers(request.GET.getlist("groups"))
    group_numbers = parsed_group_numbers or None
    payload = _build_group_impact_report_payload(group_numbers)
    dashboard_context = _impact_dashboard_context_from_payload(payload)
    pdf_bytes = _render_impact_dashboard_html_pdf(request, dashboard_context)
    if pdf_bytes is None:
        pdf_bytes = _render_group_impact_report_pdf(payload)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="{_impact_report_filename(group_numbers)}"'
    )
    return response


@staff_member_required
def impact_dashboard(request):
    top_n = _safe_int(request.GET.get("top_n"), default=10, minimum=3, maximum=40)

    datasets, _email_sets = _load_impact_survey_datasets(
        top_n=top_n,
        request=request,
    )

    participant_records = _participant_records()
    participant_summary = _participant_summary(participant_records)
    completed_participant_emails = _completed_group_participant_emails(participant_records)
    application_summary = _application_summary()
    conversion_rows = _conversion_summary(participant_summary, application_summary)
    alumni_summary = _alumni_mentor_summary(participant_records)
    group_source_rows = _group_recruitment_source_rows(participant_records)
    nps_rows = _collect_survey_metric_rows(datasets, "nps_rows")
    initial_wellbeing_rows = _collect_survey_metric_rows_for_kinds(
        datasets,
        "wellbeing_rows",
        ("emprendedoras", "mentoras"),
    )
    final_wellbeing_rows = _final_completed_wellbeing_rows(
        top_n=top_n,
        completed_emails=completed_participant_emails,
        request=request,
    )
    survey_response_rate_data = _survey_response_rate_data(participant_summary)
    nps_summary = _nps_metric_summary(nps_rows)
    wellbeing_summary = _wellbeing_comparison_summary(
        initial_wellbeing_rows,
        final_wellbeing_rows,
    )

    context = _prepare_impact_dashboard_chart_context(
        {
            "top_n": top_n,
            "participant_summary": participant_summary,
            "application_summary": application_summary,
            "conversion_rows": conversion_rows,
            "alumni_summary": alumni_summary,
            "group_source_rows": group_source_rows,
            "nps_rows": nps_rows[:12],
            "wellbeing_rows": (initial_wellbeing_rows + final_wellbeing_rows)[:12],
            "nps_summary": nps_summary,
            "wellbeing_summary": wellbeing_summary,
            "participant_country_chart_data": _participant_country_chart_data(participant_summary),
            "participant_status_chart_data": _participant_status_chart_data(participant_summary),
            "graduation_rate_chart_data": _graduation_rate_chart_data(participant_summary),
            "application_conversion_chart_data": _application_conversion_chart_data(conversion_rows),
            "alumni_engagement_chart_data": _alumni_engagement_chart_data(alumni_summary),
            "alumni_returnee_chart_data": _alumni_returnee_chart_data(alumni_summary),
            "repeat_mentor_chart_data": _repeat_mentor_chart_data(alumni_summary),
            "survey_response_rate_data": survey_response_rate_data,
            "impact_report_group_options": _impact_group_options(),
        }
    )
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
