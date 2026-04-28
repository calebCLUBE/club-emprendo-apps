import csv
import io
import json
import os
import re
import zipfile
import hashlib
import subprocess
import sys
import tempfile
from collections import defaultdict
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

import httpx
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.validators import validate_email
from django.db import connection
from django.http import HttpResponse, JsonResponse, HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import (
    Answer,
    Application,
    DropboxSignWebhookEvent,
    FormGroup,
    GradedFile,
    GroupParticipantList,
    ParticipantEmailStatus,
)

GROUP_SLUG_RE = re.compile(r"^G(?P<num>\d+)_")

IDENTITY_SLUGS = ("cedula", "id_number")
EMAIL_SLUGS = ("email",)
CSV_IDENTITY_KEYS = (
    "cedula",
    "idnumber",
    "documento",
    "documentnumber",
    "numeroidentidad",
    "numerodedocumento",
)
CSV_EMAIL_KEYS = ("email", "correo", "correoelectronico", "correoelectrnico")
CSV_RECOMMENDATION_KEYS = ("recommendation", "recomendacion", "calificacion", "status", "estado")
CSV_OVERALL_SCORE_KEYS = ("overallscore", "totalscore", "score")
CSV_TABLESTAKES_KEYS = ("tablestakesscore",)
CSV_COMMITMENT_KEYS = ("commitmentscore",)
CSV_NICE_TO_HAVE_KEYS = ("nicetohavescore",)
EMAIL_SPLIT_RE = re.compile(r"[,\s;]+")
IDENTITY_SPLIT_RE = re.compile(r"[,\s;]+")
DBS_SIGN_EVENT_TYPES = {"signature_request_signed", "signature_request_all_signed"}
_FORM_SIGNER_EMAIL_RE = re.compile(
    r"^signature_request\[signatures\]\[(\d+)\]\[signer_email_address\]$"
)
_FORM_METADATA_RE = re.compile(r"^signature_request\[metadata\]\[(.+?)\]$")
_FORM_CUSTOM_FIELD_NAME_RE = re.compile(
    r"^signature_request\[custom_fields\]\[(\d+)\]\[name\]$"
)
_FORM_CUSTOM_FIELD_VALUE_RE = re.compile(
    r"^signature_request\[custom_fields\]\[(\d+)\]\[value\]$"
)
_EMPRENDEDORA_GROUP_TITLE_RE = re.compile(
    r"acta\s+de\s+compromiso.*emprendedora.*\b(?:g|grupo)\s*[-_.:#\s]*([0-9]{1,4})\b",
    re.IGNORECASE,
)
_MENTORA_TITLE_RE = re.compile(
    r"acta\s+de\s+compromiso.*mentora",
    re.IGNORECASE,
)
WIX_CAPACITACION_EMPRENDEDORAS_PROGRAM_NAME = "Capacitacion programa de mentorias (Emprendedoras)"
WIX_CAPACITACION_MENTORAS_PROGRAM_NAME = "Capacitacion de Mentoras"

PROFILE_OVERVIEW_FIELDS = [
    ("full_name", "Full name"),
    ("preferred_name", "Preferred name"),
    ("email", "Email"),
    ("whatsapp", "WhatsApp"),
    ("city_residence", "City"),
    ("country_residence", "Country"),
    ("country_birth", "Country of birth"),
    ("age_range", "Age range"),
    ("business_name", "Business"),
    ("industry", "Industry"),
    ("professional_expertise", "Expertise"),
]

MENTORAS_HEADERS = [
    "Info",
    "Estatus",
    "#",
    "Nombre",
    "Id",
    "Email",
    "WhatsApp",
    "Recide",
    "Edad",
    "Acta",
    "Website ",
    "Capacitacion ",
    "Plazo extra ",
    "Lanzamiento",
    "W/M",
    "W/E",
]

EMPRENDEDORAS_HEADERS = [
    "Info",
    "Estatus",
    "#",
    "Nombre",
    "ID",
    "Correo",
    "WhatsApp",
    "Reside",
    "Edad",
    "Acta",
    "Website ",
    "Capacitacion ",
    "Plazo extra Cap",
    "Lanzamiento",
    "W/E",
]

MENTORAS_COL_WIDTHS = [6.88, 14.38, 5.63, 31.5, 13.63, 28.13, 14.25, 12.5, 10.5, 7, 9, 12, 12, 12, 8, 8]
EMPRENDEDORAS_COL_WIDTHS = [7.25, 14.38, 5.75, 18.38, 17.75, 32.13, 15.25, 12.5, 10.5, 7, 9, 12, 14, 12, 8]
MENTORAS_EMAIL_COL = 5
EMPRENDEDORAS_EMAIL_COL = 5
MENTORAS_ID_COL = 4
EMPRENDEDORAS_ID_COL = 4
MENTORAS_ACTA_COL = 9
EMPRENDEDORAS_ACTA_COL = 9
MENTORAS_CAPACITACION_COL = 11
EMPRENDEDORAS_CAPACITACION_COL = 11
MENTORAS_PROGRESS_DEFAULT_FALSE_COLS = [10, 11]  # Website, Capacitacion
EMPRENDEDORAS_PROGRESS_DEFAULT_FALSE_COLS = [10, 11]  # Website, Capacitacion
MENTORAS_BOOLEAN_COLS = [9, 10, 11, 12, 13, 14, 15]
EMPRENDEDORAS_BOOLEAN_COLS = [9, 10, 11, 12, 13, 14]
MENTORAS_STATUS_OPTIONS = ["NFA", "NCC", "INCP", "INCPP", "CP", "DC", "D", "P", "E/T", "G", "SG"]
EMPRENDEDORAS_STATUS_OPTIONS = ["NFA", "NCC", "INCP", "INCPP", "CP", "DC", "P", "E/T", "G", "SG"]
MENTORAS_COLUMN_TYPES = [
    "text",          # info
    "select",        # Estatus
    "readonly_num",  # #
    "text",          # Nombre
    "text",          # Id
    "email",         # Email
    "text",          # WhatsApp
    "text",          # Recide
    "text",          # Edad
    "checkbox",      # Acta
    "checkbox",      # Website
    "checkbox",      # Capacitacion
    "checkbox",      # Plazo extra
    "checkbox",      # Lanzamiento
    "checkbox",      # W/M
    "checkbox",      # W/E
]
EMPRENDEDORAS_COLUMN_TYPES = [
    "text",          # Info
    "select",        # Estatus
    "readonly_num",  # #
    "text",          # Nombre
    "text",          # ID
    "email",         # Correo
    "text",          # WhatsApp
    "text",          # Reside
    "text",          # Edad
    "checkbox",      # Acta
    "checkbox",      # Website
    "checkbox",      # Capacitacion
    "checkbox",      # Plazo extra Cap
    "checkbox",      # Lanzamiento
    "checkbox",      # W/E
]


def _model_has_field(model, field_name: str) -> bool:
    try:
        model._meta.get_field(field_name)
        return True
    except Exception:
        return False


@lru_cache(maxsize=1)
def _formgroup_is_active_column_exists() -> bool:
    if not _model_has_field(FormGroup, "is_active"):
        return False
    try:
        with connection.cursor() as cursor:
            columns = connection.introspection.get_table_description(cursor, FormGroup._meta.db_table)
        return any(getattr(col, "name", "") == "is_active" for col in columns)
    except Exception:
        return False


def _formgroup_safe_queryset():
    # Keep FormGroup reads resilient if local DB is behind migrations and misses is_active.
    base_fields = ["id", "number"]
    if _formgroup_is_active_column_exists():
        base_fields.append("is_active")
    return FormGroup.objects.only(*base_fields)


def _formgroup_active_queryset():
    qs = _formgroup_safe_queryset().order_by("number")
    if _formgroup_is_active_column_exists():
        qs = qs.filter(is_active=True)
    return qs


def _normalize_identity(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    return re.sub(r"[^0-9A-Za-z]", "", raw).upper()


def _normalize_email(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    return raw


def _clean_valid_emails(raw_emails: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for raw in raw_emails:
        email = _normalize_email(raw)
        if not email or email in seen:
            continue
        try:
            validate_email(email)
        except ValidationError:
            continue
        seen.add(email)
        deduped.append(email)
    return deduped


def _signature_status_is_signed(raw_status: str | None) -> bool:
    status = (raw_status or "").strip().lower()
    if not status:
        return False
    negative_tokens = (
        "await",
        "pending",
        "request",
        "declin",
        "cancel",
        "expire",
        "invalid",
        "error",
    )
    if any(tok in status for tok in negative_tokens):
        return False
    return ("signed" in status) or (status in {"complete", "completed"})


def _participant_emails_from_list(group_list: GroupParticipantList, track: str) -> set[str]:
    wanted = (track or "").strip().upper()
    if wanted == "M":
        rows = _normalize_sheet_rows(
            getattr(group_list, "mentoras_sheet_rows", []),
            MENTORAS_HEADERS,
        )
        emails = _emails_from_sheet_rows(rows, MENTORAS_EMAIL_COL)
        if not emails:
            emails = _norm_email_list(getattr(group_list, "mentoras_emails_text", ""))
        return {_normalize_email(v) for v in emails if _normalize_email(v)}

    if wanted == "E":
        rows = _normalize_sheet_rows(
            getattr(group_list, "emprendedoras_sheet_rows", []),
            EMPRENDEDORAS_HEADERS,
        )
        emails = _emails_from_sheet_rows(rows, EMPRENDEDORAS_EMAIL_COL)
        if not emails:
            emails = _norm_email_list(getattr(group_list, "emprendedoras_emails_text", ""))
        return {_normalize_email(v) for v in emails if _normalize_email(v)}

    return set()


def _resolve_mentora_group_by_signer_pool(signer_pool: set[str]) -> tuple[int | None, str]:
    if not signer_pool:
        return None, "No signer emails available to resolve mentora group."

    exact_matches: list[int] = []
    participant_lists = GroupParticipantList.objects.select_related("group")
    for row in participant_lists:
        pool = _participant_emails_from_list(row, "M")
        if pool and pool == signer_pool:
            exact_matches.append(int(row.group.number))

    if len(exact_matches) == 1:
        return exact_matches[0], "Matched mentora group by exact participant email list."
    if len(exact_matches) > 1:
        return None, "Multiple mentora groups matched the exact signer email list."

    # Fallback for payloads that only carry one signer email.
    if len(signer_pool) == 1:
        only_email = next(iter(signer_pool))
        contains_matches: list[int] = []
        for row in participant_lists:
            pool = _participant_emails_from_list(row, "M")
            if only_email in pool:
                contains_matches.append(int(row.group.number))
        if len(contains_matches) == 1:
            return contains_matches[0], "Matched mentora group by unique signer email."
        if len(contains_matches) > 1:
            return None, "Signer email exists in multiple mentora groups."

    return None, "No mentora group matched signer emails."


def _mark_participant_sheet_acta_signed(
    *,
    group_num: int,
    track: str,
    signed_emails: list[str],
) -> tuple[int, int, str]:
    track_key = (track or "").strip().upper()
    if track_key not in {"E", "M"}:
        return 0, 0, "Invalid track."

    signed_set = set(_clean_valid_emails(signed_emails))
    if not signed_set:
        return 0, 0, "No valid signer emails to apply."

    group = _formgroup_safe_queryset().filter(number=int(group_num)).first()
    if not group:
        return 0, 0, f"Group {group_num} not found."

    participant_list = GroupParticipantList.objects.filter(group=group).first()
    if not participant_list:
        return 0, 0, f"Group {group_num} has no participant list."

    if track_key == "M":
        headers = MENTORAS_HEADERS
        bool_cols = MENTORAS_BOOLEAN_COLS
        email_col = MENTORAS_EMAIL_COL
        acta_col = MENTORAS_ACTA_COL
        rows_field = "mentoras_sheet_rows"
        text_field = "mentoras_emails_text"
        build_rows = _build_mentoras_rows
    else:
        headers = EMPRENDEDORAS_HEADERS
        bool_cols = EMPRENDEDORAS_BOOLEAN_COLS
        email_col = EMPRENDEDORAS_EMAIL_COL
        acta_col = EMPRENDEDORAS_ACTA_COL
        rows_field = "emprendedoras_sheet_rows"
        text_field = "emprendedoras_emails_text"
        build_rows = _build_emprendedoras_rows

    rows = _normalize_sheet_rows(getattr(participant_list, rows_field, []), headers)
    rows = _coerce_bool_columns(rows, bool_cols)
    if not rows:
        seed_emails = _norm_email_list(getattr(participant_list, text_field, ""))
        if seed_emails:
            rows = build_rows(group.number, seed_emails)
    if not rows:
        return 0, 0, f"Group {group_num} has no {track_key} participant rows."

    matched = 0
    changed = 0
    updated_rows: list[list] = []
    for row in rows:
        row_copy = list(row)
        if email_col < len(row_copy):
            email_norm = _normalize_email(row_copy[email_col])
            if email_norm in signed_set:
                matched += 1
                if acta_col < len(row_copy) and not _as_checkbox_bool(row_copy[acta_col]):
                    row_copy[acta_col] = True
                    changed += 1
        updated_rows.append(row_copy)

    if changed:
        setattr(
            participant_list,
            rows_field,
            _number_sheet_rows(updated_rows, number_col=2),
        )
        participant_list.save(update_fields=[rows_field, "updated_at"])

    return matched, changed, f"Updated {changed} row(s), matched {matched} signer email(s)."


def _email_status_key(value: str | None) -> str:
    normalized = _normalize_email(value)
    if not normalized or "@" not in normalized:
        return ""
    return normalized


def _normalize_header(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    return re.sub(r"[^a-z0-9]+", "", raw)


def _pick_value(row: dict[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = (row.get(key) or "").strip()
        if value:
            return value
    return ""


def _id_token(value: str | None) -> str:
    normalized = _normalize_identity(value)
    if not normalized:
        return ""
    return f"id:{normalized}"


def _email_token(value: str | None) -> str:
    normalized = _normalize_email(value)
    if not normalized:
        return ""
    return f"email:{normalized}"


def _normalize_profile_key(value: str | None) -> str:
    raw = (value or "").strip().lower()
    return re.sub(r"[^a-z0-9_]+", "", raw)


def _parse_email_list(raw_value: str) -> tuple[list[str], list[str]]:
    seen = set()
    valid: list[str] = []
    invalid: list[str] = []
    for part in EMAIL_SPLIT_RE.split(raw_value or ""):
        candidate = _normalize_email(part)
        if not candidate:
            continue
        try:
            validate_email(candidate)
        except ValidationError:
            invalid.append(part.strip() or candidate)
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        valid.append(candidate)
    return valid, invalid


def _parse_identity_list(raw_value: str) -> tuple[list[str], list[str]]:
    seen = set()
    valid: list[str] = []
    invalid: list[str] = []
    for part in IDENTITY_SPLIT_RE.split(raw_value or ""):
        candidate_raw = (part or "").strip()
        if not candidate_raw:
            continue
        candidate = _normalize_identity(candidate_raw)
        if not candidate:
            invalid.append(candidate_raw)
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        valid.append(candidate)
    return valid, invalid


def _row_identity_email_tokens(row: list, id_col: int, email_col: int) -> set[str]:
    tokens: set[str] = set()
    if 0 <= id_col < len(row):
        id_norm = _normalize_identity(str(row[id_col] or ""))
        if id_norm:
            tokens.add(f"id:{id_norm}")
    if 0 <= email_col < len(row):
        email_norm = _normalize_email(str(row[email_col] or ""))
        if email_norm:
            tokens.add(f"email:{email_norm}")
    return tokens


def _append_unique_participant_rows(
    existing_rows: list[list],
    incoming_rows: list[list],
    *,
    id_col: int,
    email_col: int,
) -> tuple[list[list], int, int]:
    merged_rows: list[list] = [list(row) for row in existing_rows]
    seen_tokens: set[str] = set()
    for row in merged_rows:
        seen_tokens.update(_row_identity_email_tokens(row, id_col=id_col, email_col=email_col))

    added = 0
    skipped_duplicates = 0
    for row in incoming_rows:
        row_tokens = _row_identity_email_tokens(row, id_col=id_col, email_col=email_col)
        if row_tokens and any(token in seen_tokens for token in row_tokens):
            skipped_duplicates += 1
            continue
        merged_rows.append(list(row))
        added += 1
        seen_tokens.update(row_tokens)
    return merged_rows, added, skipped_duplicates


def _columns_uniformly_checked(rows: list[list], cols: list[int]) -> bool:
    if not rows or not cols:
        return False
    for col_idx in cols:
        has_any = False
        for row in rows:
            if col_idx >= len(row):
                return False
            has_any = True
            if not _as_checkbox_bool(row[col_idx]):
                return False
        if not has_any:
            return False
    return True


def _set_checkbox_columns(rows: list[list], cols: list[int], value: bool) -> tuple[list[list], int]:
    changed = 0
    out: list[list] = []
    for row in rows:
        row_copy = list(row)
        for col_idx in cols:
            if col_idx >= len(row_copy):
                continue
            current = _as_checkbox_bool(row_copy[col_idx])
            if current != bool(value):
                row_copy[col_idx] = bool(value)
                changed += 1
            else:
                row_copy[col_idx] = bool(value) if isinstance(row_copy[col_idx], bool) else row_copy[col_idx]
        out.append(row_copy)
    return out, changed


def _repair_progress_defaults_if_legacy(
    rows: list[list],
    reset_cols: list[int],
) -> tuple[list[list], int]:
    if not _columns_uniformly_checked(rows, reset_cols):
        return rows, 0
    return _set_checkbox_columns(rows, reset_cols, False)


def _normalize_dropbox_sign_payload(request) -> dict:
    raw_body = request.body or b""
    decoded = raw_body.decode("utf-8", errors="replace")
    payload_json: dict | list | str | None = None
    metadata: dict[str, str] = {}
    signer_emails: list[str] = []
    signed_signer_emails: list[str] = []
    custom_fields: dict[str, str] = {}

    # 1) JSON body / json form field
    try:
        if decoded.strip():
            parsed = json.loads(decoded)
            if isinstance(parsed, dict):
                payload_json = parsed
    except Exception:
        payload_json = None

    if payload_json is None:
        json_field = (request.POST.get("json") or "").strip()
        if json_field:
            try:
                parsed = json.loads(json_field)
                if isinstance(parsed, dict):
                    payload_json = parsed
            except Exception:
                payload_json = None

    event_type = ""
    event_time = ""
    event_hash = ""
    signature_request_id = ""
    signature_title = ""

    if isinstance(payload_json, dict):
        event = payload_json.get("event") or {}
        if isinstance(event, dict):
            event_type = str(event.get("event_type") or "")
            event_time = str(event.get("event_time") or "")
            event_hash = str(event.get("event_hash") or "")
        if not event_type:
            event_type = str(payload_json.get("event_type") or "")
        if not event_time:
            event_time = str(payload_json.get("event_time") or "")
        if not event_hash:
            event_hash = str(payload_json.get("event_hash") or "")
        sig_req = payload_json.get("signature_request") or {}
        if isinstance(sig_req, dict):
            signature_request_id = str(sig_req.get("signature_request_id") or "")
            signature_title = str(sig_req.get("title") or "")
            raw_metadata = sig_req.get("metadata") or {}
            if isinstance(raw_metadata, dict):
                metadata = {
                    str(k).strip(): str(v).strip()
                    for k, v in raw_metadata.items()
                    if str(v or "").strip()
                }
            raw_signatures = sig_req.get("signatures") or []
            if isinstance(raw_signatures, list):
                for row in raw_signatures:
                    if not isinstance(row, dict):
                        continue
                    email = _normalize_email(
                        row.get("signer_email_address")
                        or row.get("email_address")
                        or row.get("email")
                    )
                    if email:
                        signer_emails.append(email)
                        status_raw = (
                            row.get("status_code")
                            or row.get("status")
                            or row.get("signer_status_code")
                            or row.get("state")
                        )
                        signed_at_raw = str(row.get("signed_at") or "").strip()
                        if _signature_status_is_signed(status_raw) or bool(signed_at_raw):
                            signed_signer_emails.append(email)
            raw_custom_fields = sig_req.get("custom_fields") or []
            if isinstance(raw_custom_fields, list):
                for row in raw_custom_fields:
                    if not isinstance(row, dict):
                        continue
                    name = str(row.get("name") or "").strip()
                    value = str(row.get("value") or "").strip()
                    if name and value:
                        custom_fields[name] = value
            elif isinstance(raw_custom_fields, dict):
                for k, v in raw_custom_fields.items():
                    name = str(k or "").strip()
                    value = str(v or "").strip()
                    if name and value:
                        custom_fields[name] = value

    # 2) Form encoded fallback
    if not event_type:
        event_type = str(request.POST.get("event[event_type]") or "").strip()
    if not event_type:
        event_type = str(request.POST.get("event_type") or "").strip()
    if not event_time:
        event_time = str(request.POST.get("event[event_time]") or "").strip()
    if not event_time:
        event_time = str(request.POST.get("event_time") or "").strip()
    if not event_hash:
        event_hash = str(request.POST.get("event[event_hash]") or "").strip()
    if not event_hash:
        event_hash = str(request.POST.get("event_hash") or "").strip()
    if not signature_request_id:
        signature_request_id = str(
            request.POST.get("signature_request[signature_request_id]") or ""
        ).strip()
    if not signature_title:
        signature_title = str(request.POST.get("signature_request[title]") or "").strip()

    if not signer_emails:
        indexed: dict[int, str] = {}
        for key, value in request.POST.items():
            m = _FORM_SIGNER_EMAIL_RE.match(key)
            if not m:
                continue
            idx = int(m.group(1))
            email = _normalize_email(value)
            if email:
                indexed[idx] = email
        signer_emails = [indexed[idx] for idx in sorted(indexed)]

    if not metadata:
        for key, value in request.POST.items():
            m = _FORM_METADATA_RE.match(key)
            if not m:
                continue
            k = str(m.group(1) or "").strip()
            v = str(value or "").strip()
            if k and v:
                metadata[k] = v

    if not custom_fields:
        field_names: dict[int, str] = {}
        field_values: dict[int, str] = {}
        for key, value in request.POST.items():
            name_match = _FORM_CUSTOM_FIELD_NAME_RE.match(key)
            if name_match:
                field_names[int(name_match.group(1))] = str(value or "").strip()
                continue
            value_match = _FORM_CUSTOM_FIELD_VALUE_RE.match(key)
            if value_match:
                field_values[int(value_match.group(1))] = str(value or "").strip()
        for idx, name in field_names.items():
            val = field_values.get(idx, "")
            if name and val:
                custom_fields[name] = val

    deduped_emails = _clean_valid_emails(signer_emails)
    signed_set = set(_clean_valid_emails(signed_signer_emails))
    deduped_signed_emails = [email for email in deduped_emails if email in signed_set]

    return {
        "event_type": event_type,
        "event_time": event_time,
        "event_hash": event_hash,
        "signature_request_id": signature_request_id,
        "signature_title": signature_title,
        "signer_emails": deduped_emails,
        "signed_signer_emails": deduped_signed_emails,
        "metadata": metadata,
        "custom_fields": custom_fields,
        "payload_json": payload_json if isinstance(payload_json, dict) else {},
        "raw_body_text": decoded[:10000],
    }


def _dropbox_sign_hash_is_valid(event_time: str, event_type: str, event_hash: str) -> bool:
    api_key = (getattr(settings, "DROPBOX_SIGN_API_KEY", "") or "").strip()
    if not api_key:
        return True
    if not event_time or not event_type or not event_hash:
        return False
    candidate = hashlib.sha256(f"{api_key}{event_time}{event_type}".encode("utf-8")).hexdigest()
    return candidate == event_hash


def _resolve_dropbox_signature_scope(
    *,
    signature_title: str,
    signer_pool: set[str],
) -> tuple[str | None, int | None, str]:
    title = (signature_title or "").strip()
    if not title:
        return None, None, "Missing signature title."

    empr_match = _EMPRENDEDORA_GROUP_TITLE_RE.search(title)
    if empr_match:
        try:
            group_num = int(empr_match.group(1))
        except (TypeError, ValueError):
            return None, None, "Could not parse group number from emprendedora document title."
        return "E", group_num, "Resolved from emprendedora document title."

    if _MENTORA_TITLE_RE.search(title):
        group_num, reason = _resolve_mentora_group_by_signer_pool(signer_pool)
        if group_num is None:
            return None, None, reason
        return "M", int(group_num), reason

    return None, None, "Document title did not match mentora/emprendedora patterns."


def _candidate_identity_values(metadata: dict, custom_fields: dict) -> list[str]:
    out: list[str] = []
    keys = [*metadata.keys(), *custom_fields.keys()]
    for key in keys:
        key_norm = _normalize_header(str(key or ""))
        if not key_norm:
            continue
        if not any(token in key_norm for token in ("cedula", "id", "document", "identidad")):
            continue
        value = metadata.get(key) if key in metadata else custom_fields.get(key)
        raw = str(value or "").strip()
        if raw:
            out.append(raw)
    # preserve order; dedupe
    seen = set()
    deduped = []
    for v in out:
        n = _normalize_identity(v)
        if not n or n in seen:
            continue
        seen.add(n)
        deduped.append(v)
    return deduped


def _emails_for_identity_value(identity_value: str) -> list[str]:
    identity_norm = _normalize_identity(identity_value)
    if not identity_norm:
        return []

    matched_app_ids: set[int] = set()
    for app_id, raw_value in Answer.objects.filter(
        question__slug__in=IDENTITY_SLUGS
    ).values_list("application_id", "value"):
        if _normalize_identity(raw_value) == identity_norm:
            matched_app_ids.add(int(app_id))
    if not matched_app_ids:
        return []

    emails: list[str] = []
    seen: set[str] = set()

    for raw_email in Application.objects.filter(id__in=matched_app_ids).values_list("email", flat=True):
        email = _normalize_email(raw_email)
        if not email or email in seen:
            continue
        try:
            validate_email(email)
        except ValidationError:
            continue
        seen.add(email)
        emails.append(email)

    if emails:
        return emails

    # Fallback: email answer field in matched applications.
    for app_id, raw_value in Answer.objects.filter(
        application_id__in=matched_app_ids,
        question__slug__in=EMAIL_SLUGS,
    ).values_list("application_id", "value"):
        email = _normalize_email(raw_value)
        if not email or email in seen:
            continue
        try:
            validate_email(email)
        except ValidationError:
            continue
        seen.add(email)
        emails.append(email)
    return emails


def _build_profile_key(identity_norm: str, email_norm: str, app_id: int) -> str:
    if identity_norm:
        return _normalize_profile_key(f"id_{identity_norm.lower()}")
    if email_norm:
        email_fragment = re.sub(r"[^a-z0-9]+", "", email_norm.lower())
        if email_fragment:
            return _normalize_profile_key(f"email_{email_fragment}")
    return _normalize_profile_key(f"app_{app_id}")


def _norm_email_list(raw_text: str) -> list[str]:
    valid, _invalid = _parse_email_list(raw_text or "")
    return valid


def _as_checkbox_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    raw = (str(value or "")).strip().lower()
    # Keep checkbox parsing strict to avoid accidental mass-checking from free-text values.
    return raw in {"1", "true", "checked", "on", "x", "✓"}


def _normalize_sheet_rows(raw_rows, headers: list[str]) -> list[list]:
    if not isinstance(raw_rows, list):
        return []
    width = len(headers)
    out: list[list] = []
    for raw_row in raw_rows:
        if not isinstance(raw_row, (list, tuple)):
            continue
        row = []
        for idx in range(width):
            value = raw_row[idx] if idx < len(raw_row) else ""
            if value is None:
                row.append("")
            elif isinstance(value, bool):
                row.append(value)
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                row.append(value)
            else:
                row.append(str(value))
        has_meaningful = False
        for v in row:
            if isinstance(v, bool):
                if v:
                    has_meaningful = True
                    break
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                if float(v) != 0:
                    has_meaningful = True
                    break
            elif str(v).strip():
                has_meaningful = True
                break
        if has_meaningful:
            out.append(row)
    return out


def _coerce_bool_columns(rows: list[list], bool_cols: list[int]) -> list[list]:
    bool_set = set(bool_cols or [])
    out: list[list] = []
    for row in rows:
        row_copy = list(row)
        for idx in bool_set:
            if idx < len(row_copy):
                row_copy[idx] = _as_checkbox_bool(row_copy[idx])
        out.append(row_copy)
    return out


def _emails_from_sheet_rows(rows: list[list], email_col: int) -> list[str]:
    seen = set()
    out: list[str] = []
    for row in rows:
        if email_col >= len(row):
            continue
        email_raw = row[email_col]
        email_norm = _normalize_email(email_raw)
        if not email_norm or email_norm in seen:
            continue
        try:
            validate_email(email_norm)
        except ValidationError:
            continue
        seen.add(email_norm)
        out.append(email_norm)
    return out


def _contract_signed_email_map(emails: list[str]) -> dict[str, bool]:
    normalized_emails: list[str] = []
    seen: set[str] = set()
    for raw in emails:
        email = _normalize_email(raw)
        if not email or email in seen:
            continue
        seen.add(email)
        normalized_emails.append(email)

    if not normalized_emails:
        return {}

    signed_rows = ParticipantEmailStatus.objects.filter(
        email__in=normalized_emails,
        contract_signed=True,
    ).values_list("email", flat=True)
    signed_set = {_normalize_email(v) for v in signed_rows if _normalize_email(v)}
    return {email: (email in signed_set) for email in normalized_emails}


def _apply_contract_signed_to_rows(
    rows: list[list],
    email_col: int,
    acta_col: int,
) -> list[list]:
    if not rows:
        return []
    emails = _emails_from_sheet_rows(rows, email_col)
    signed_map = _contract_signed_email_map(emails)
    out: list[list] = []
    for row in rows:
        row_copy = list(row)
        if email_col >= len(row_copy):
            out.append(row_copy)
            continue
        email_norm = _normalize_email(row_copy[email_col])
        signed = bool(signed_map.get(email_norm, False))
        if acta_col < len(row_copy):
            row_copy[acta_col] = signed
        out.append(row_copy)
    return out


def _mark_participated_yes(emails: list[str]) -> tuple[int, int, int]:
    created_count = 0
    updated_count = 0
    unchanged_count = 0
    for email in emails:
        email_key = _email_status_key(email)
        if not email_key:
            continue
        obj, created = ParticipantEmailStatus.objects.get_or_create(
            email=email_key,
            defaults={"participated": True},
        )
        if created:
            created_count += 1
            continue
        if obj.participated:
            unchanged_count += 1
            continue
        obj.participated = True
        obj.save(update_fields=["participated", "updated_at"])
        updated_count += 1
    return created_count, updated_count, unchanged_count


def _run_dropbox_reconcile_for_group(
    *,
    group_num: int,
    track: str = "both",
    days_back: int = 730,
    max_pages: int = 60,
    background: bool = True,
) -> tuple[bool, str]:
    filters = _dropbox_group_file_filters(group_num=group_num, track=track)
    cli_args = [
        sys.executable,
        str(Path(settings.BASE_DIR) / "manage.py"),
        "reconcile_dropbox_sign_bulk",
        "--group",
        str(int(group_num)),
        "--track",
        str(track or "both"),
        "--days-back",
        str(int(days_back)),
        "--max-pages",
        str(int(max_pages)),
    ]
    for title in filters.get("title_exact", []):
        cli_args.extend(["--title-exact", str(title)])
    for job_id in filters.get("bulk_send_job_id", []):
        cli_args.extend(["--bulk-send-job-id", str(job_id)])
    for req_id in filters.get("signature_request_id", []):
        cli_args.extend(["--signature-request-id", str(req_id)])

    if background:
        ts = timezone.now().strftime("%Y%m%d_%H%M%S")
        log_path = Path(tempfile.gettempdir()) / f"dropbox_reconcile_g{group_num}_{track}_{ts}.log"
        try:
            with open(log_path, "ab", buffering=0) as log_file:
                subprocess.Popen(
                    cli_args,
                    cwd=str(settings.BASE_DIR),
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )
            return True, f"Dropbox check started in background. Refresh in 1-2 minutes."
        except Exception as exc:
            return False, f"Could not start background Dropbox check: {exc}"

    output = io.StringIO()
    try:
        cmd_kwargs = dict(
            group=int(group_num),
            track=str(track or "both"),
            days_back=int(days_back),
            max_pages=int(max_pages),
            stdout=output,
            stderr=output,
            verbosity=1,
        )
        if filters.get("title_exact"):
            cmd_kwargs["title_exact"] = list(filters["title_exact"])
        if filters.get("bulk_send_job_id"):
            cmd_kwargs["bulk_send_job_id"] = list(filters["bulk_send_job_id"])
        if filters.get("signature_request_id"):
            cmd_kwargs["signature_request_id"] = list(filters["signature_request_id"])
        call_command(
            "reconcile_dropbox_sign_bulk",
            **cmd_kwargs,
        )
    except Exception as exc:
        details = output.getvalue().strip()
        if details:
            tail = " | ".join(line.strip() for line in details.splitlines()[-3:] if line.strip())
            if tail:
                return False, f"{exc} ({tail})"
        return False, str(exc)

    lines = [line.strip() for line in output.getvalue().splitlines() if line.strip()]
    if not lines:
        return True, "Dropbox check finished."
    summary_line = " | ".join(lines[-4:])
    return True, summary_line


def _dropbox_group_file_filters(*, group_num: int, track: str) -> dict[str, list[str]]:
    raw_cfg = getattr(settings, "DROPBOX_SIGN_GROUP_FILE_FILTERS", {})
    if isinstance(raw_cfg, str):
        text = raw_cfg.strip()
        if not text:
            return {}
        try:
            raw_cfg = json.loads(text)
        except Exception:
            return {}
    if not isinstance(raw_cfg, dict):
        return {}

    group_cfg = raw_cfg.get(str(group_num))
    if group_cfg is None:
        group_cfg = raw_cfg.get(group_num)
    if not isinstance(group_cfg, dict):
        return {}

    track_key = str(track or "both").strip().upper()

    def _values(section: dict, *keys: str) -> list[str]:
        vals: list[str] = []
        for key in keys:
            raw_val = section.get(key)
            if isinstance(raw_val, str):
                candidate = raw_val.strip()
                if candidate:
                    vals.append(candidate)
            elif isinstance(raw_val, list):
                for item in raw_val:
                    candidate = str(item or "").strip()
                    if candidate:
                        vals.append(candidate)
        # de-dup preserve order
        seen: set[str] = set()
        out: list[str] = []
        for v in vals:
            if v in seen:
                continue
            seen.add(v)
            out.append(v)
        return out

    sections: list[dict] = []
    both_section = group_cfg.get("both") or group_cfg.get("BOTH")
    if isinstance(both_section, dict):
        sections.append(both_section)
    track_section = group_cfg.get(track_key) or group_cfg.get(track_key.lower())
    if isinstance(track_section, dict):
        sections.append(track_section)

    if not sections:
        sections = [group_cfg]

    title_exact: list[str] = []
    bulk_send_job_id: list[str] = []
    signature_request_id: list[str] = []
    for section in sections:
        title_exact.extend(_values(section, "title_exact"))
        bulk_send_job_id.extend(_values(section, "bulk_send_job_id", "bulk_send_job_ids"))
        signature_request_id.extend(_values(section, "signature_request_id", "signature_request_ids"))

    out: dict[str, list[str]] = {}
    if title_exact:
        out["title_exact"] = title_exact
    if bulk_send_job_id:
        out["bulk_send_job_id"] = bulk_send_job_id
    if signature_request_id:
        out["signature_request_id"] = signature_request_id
    return out


def _setting_or_env(name: str, default: str = "") -> str:
    raw = getattr(settings, name, None)
    if raw is not None:
        text = str(raw).strip()
        if text:
            return text
    return str(os.environ.get(name, default) or default).strip()


def _wix_capacitacion_program_name(track_slug: str) -> str:
    if track_slug == "mentoras":
        return _setting_or_env(
            "WIX_CAPACITACION_PROGRAM_MENTORAS",
            WIX_CAPACITACION_MENTORAS_PROGRAM_NAME,
        )
    return _setting_or_env(
        "WIX_CAPACITACION_PROGRAM_EMPRENDEDORAS",
        WIX_CAPACITACION_EMPRENDEDORAS_PROGRAM_NAME,
    )


def _iter_nested_values(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_nested_values(child)
        return
    if isinstance(value, (list, tuple, set)):
        for child in value:
            yield from _iter_nested_values(child)
        return
    yield value


def _extract_emails_from_any(value) -> list[str]:
    raw_values: list[str] = []
    for node in _iter_nested_values(value):
        if isinstance(node, str):
            if "@" in node:
                raw_values.extend(EMAIL_SPLIT_RE.split(node))
            continue
        if isinstance(node, (int, float, bool)) or node is None:
            continue
        text = str(node or "").strip()
        if "@" in text:
            raw_values.extend(EMAIL_SPLIT_RE.split(text))
    return _clean_valid_emails(raw_values)


def _record_is_completed(record: dict) -> bool:
    bool_keys = (
        "completed",
        "is_completed",
        "isComplete",
        "is_complete",
        "done",
        "finished",
        "passed",
    )
    for key in bool_keys:
        if key in record:
            return _as_checkbox_bool(record.get(key))

    date_keys = (
        "completed_at",
        "completedAt",
        "completionDate",
        "completion_date",
        "finishedAt",
        "finished_at",
    )
    for key in date_keys:
        raw = str(record.get(key, "") or "").strip()
        if raw:
            return True

    status_keys = (
        "status",
        "completion_status",
        "completionStatus",
        "progress_status",
        "progressStatus",
        "state",
    )
    positive_tokens = ("complete", "completed", "finished", "done", "passed")
    negative_tokens = (
        "incomplete",
        "pending",
        "started",
        "in progress",
        "in_progress",
        "enrolled",
        "invited",
        "await",
        "cancel",
    )
    for key in status_keys:
        status = str(record.get(key, "") or "").strip().lower()
        if not status:
            continue
        if any(token in status for token in negative_tokens):
            return False
        if any(token in status for token in positive_tokens):
            return True
    return False


def _extract_completed_emails_from_wix_payload(payload) -> set[str]:
    out: set[str] = set()
    explicit_keys = {
        "completedemails",
        "completed_emails",
        "signedemails",
        "signed_emails",
        "completedparticipants",
        "completed_participants",
    }
    for node in _iter_nested_values(payload):
        if not isinstance(node, dict):
            continue
        for key, value in node.items():
            if str(key or "").strip().lower() in explicit_keys:
                for email in _extract_emails_from_any(value):
                    out.add(email)

    for node in _iter_nested_values(payload):
        if not isinstance(node, dict):
            continue
        if not _record_is_completed(node):
            continue
        candidate_values = []
        for key, value in node.items():
            if "email" in str(key or "").strip().lower():
                candidate_values.append(value)
        if not candidate_values:
            continue
        for email in _extract_emails_from_any(candidate_values):
            out.add(email)
    return out


def _fetch_wix_capacitacion_completed_emails(
    *,
    program_name: str,
    group_num: int,
    track_slug: str,
    participant_pool: set[str],
) -> tuple[bool, set[str], str]:
    completions_url = _setting_or_env("WIX_CAPACITACION_COMPLETIONS_URL", "")
    if not completions_url:
        return (
            False,
            set(),
            "WIX_CAPACITACION_COMPLETIONS_URL is not configured.",
        )

    raw_timeout = _setting_or_env("WIX_CAPACITACION_TIMEOUT_SECONDS", "30")
    try:
        timeout = max(5.0, float(raw_timeout))
    except Exception:
        timeout = 30.0

    api_key = _setting_or_env("WIX_CAPACITACION_API_KEY", "") or _setting_or_env("WIX_API_KEY", "")
    auth_token = _setting_or_env("WIX_CAPACITACION_AUTH_TOKEN", "")
    site_id = _setting_or_env("WIX_CAPACITACION_SITE_ID", "") or _setting_or_env("WIX_SITE_ID", "")
    method = (_setting_or_env("WIX_CAPACITACION_HTTP_METHOD", "GET") or "GET").strip().upper()
    if method not in {"GET", "POST"}:
        method = "GET"

    headers: dict[str, str] = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    elif auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    if site_id:
        headers["wix-site-id"] = site_id

    params = {
        "programName": program_name,
        "program_name": program_name,
        "group": str(group_num),
        "track": str(track_slug),
    }
    body = {
        "programName": program_name,
        "group": int(group_num),
        "track": str(track_slug),
    }
    if participant_pool:
        body["participantEmails"] = sorted(participant_pool)

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            if method == "POST":
                resp = client.post(
                    completions_url,
                    headers=headers,
                    params=params,
                    json=body,
                )
            else:
                resp = client.get(
                    completions_url,
                    headers=headers,
                    params=params,
                )
    except Exception as exc:
        return False, set(), f"Wix request failed: {exc}"

    if resp.status_code >= 400:
        return (
            False,
            set(),
            f"Wix request failed (HTTP {resp.status_code}): {resp.text[:220]}",
        )

    try:
        payload = resp.json() if resp.content else {}
    except Exception:
        return False, set(), "Wix response was not valid JSON."

    completed = _extract_completed_emails_from_wix_payload(payload)
    if participant_pool:
        completed = {email for email in completed if email in participant_pool}

    return (
        True,
        completed,
        (
            f"Wix completions fetched for '{program_name}'. "
            f"Matched participant emails: {len(completed)}."
        ),
    )


def _apply_capacitacion_to_rows(
    *,
    rows: list[list],
    email_col: int,
    capacitacion_col: int,
    completed_emails: set[str],
) -> tuple[list[list], int, int]:
    out: list[list] = []
    matched = 0
    changed = 0
    for row in rows:
        row_copy = list(row)
        email = ""
        if email_col < len(row_copy):
            email = _normalize_email(row_copy[email_col])
        if email and email in completed_emails:
            matched += 1
            if capacitacion_col < len(row_copy):
                if not _as_checkbox_bool(row_copy[capacitacion_col]):
                    row_copy[capacitacion_col] = True
                    changed += 1
        out.append(row_copy)
    return out, matched, changed


def _run_wix_capacitacion_check_for_track(
    *,
    group: FormGroup,
    track_slug: str,
    headers: list[str],
    bool_cols: list[int],
    email_col: int,
    capacitacion_col: int,
    text_field: str,
    rows_field: str,
    build_rows,
) -> tuple[bool, str]:
    participant_list = GroupParticipantList.objects.filter(group=group).first()
    if not participant_list:
        return False, f"Group {group.number} has no participant list."

    stored_rows = _normalize_sheet_rows(getattr(participant_list, rows_field, []), headers)
    stored_rows = _coerce_bool_columns(stored_rows, bool_cols)
    if stored_rows:
        rows = _number_sheet_rows(stored_rows, number_col=2)
    else:
        emails_seed = _norm_email_list(getattr(participant_list, text_field, ""))
        rows = _number_sheet_rows(build_rows(group.number, emails_seed), number_col=2)

    participant_emails = _emails_from_sheet_rows(rows, email_col)
    participant_pool = set(participant_emails)
    if not participant_pool:
        return False, "No participant emails found in this sheet."

    program_name = _wix_capacitacion_program_name(track_slug)
    ok, completed_emails, fetch_note = _fetch_wix_capacitacion_completed_emails(
        program_name=program_name,
        group_num=group.number,
        track_slug=track_slug,
        participant_pool=participant_pool,
    )
    if not ok:
        return False, fetch_note

    next_rows, matched_count, changed_count = _apply_capacitacion_to_rows(
        rows=rows,
        email_col=email_col,
        capacitacion_col=capacitacion_col,
        completed_emails=completed_emails,
    )
    next_rows = _number_sheet_rows(next_rows, number_col=2)

    if getattr(participant_list, rows_field) != next_rows:
        setattr(participant_list, rows_field, next_rows)
        participant_list.save(update_fields=[rows_field, "updated_at"])

    return (
        True,
        (
            f"{fetch_note} "
            f"Capacitacion rows matched: {matched_count}; changed: {changed_count}."
        ),
    )


def _participant_list_email_keys() -> set[str]:
    out: set[str] = set()
    participant_lists = GroupParticipantList.objects.only(
        "mentoras_emails_text",
        "emprendedoras_emails_text",
        "mentoras_sheet_rows",
        "emprendedoras_sheet_rows",
    )
    for row in participant_lists:
        mentoras_emails = _norm_email_list(getattr(row, "mentoras_emails_text", ""))
        emprendedoras_emails = _norm_email_list(getattr(row, "emprendedoras_emails_text", ""))

        if not mentoras_emails:
            mentoras_rows = _normalize_sheet_rows(getattr(row, "mentoras_sheet_rows", []), MENTORAS_HEADERS)
            mentoras_emails = _emails_from_sheet_rows(mentoras_rows, MENTORAS_EMAIL_COL)
        if not emprendedoras_emails:
            emprendedoras_rows = _normalize_sheet_rows(
                getattr(row, "emprendedoras_sheet_rows", []),
                EMPRENDEDORAS_HEADERS,
            )
            emprendedoras_emails = _emails_from_sheet_rows(
                emprendedoras_rows,
                EMPRENDEDORAS_EMAIL_COL,
            )

        for email in mentoras_emails + emprendedoras_emails:
            email_key = _email_status_key(email)
            if email_key:
                out.add(email_key)
    return out


def _number_sheet_rows(rows: list[list], number_col: int = 2) -> list[list]:
    out: list[list] = []
    for idx, row in enumerate(rows, start=1):
        row_copy = list(row)
        if number_col < len(row_copy):
            row_copy[number_col] = idx
        out.append(row_copy)
    return out


def _app_group_number(app: Application) -> int | None:
    gnum = getattr(getattr(app, "form", None), "group_id", None)
    if gnum:
        return _formgroup_number_from_id(gnum)
    return _group_number_from_slug(getattr(app.form, "slug", ""))


@lru_cache(maxsize=4096)
def _formgroup_number_from_id(group_id: int | None) -> int | None:
    if not group_id:
        return None
    try:
        obj = _formgroup_safe_queryset().filter(id=int(group_id)).first()
    except Exception:
        return None
    return getattr(obj, "number", None) if obj else None


def _latest_apps_by_email_for_group_track(group_num: int, track: str) -> dict[str, dict]:
    target_track = (track or "").strip().upper()
    if target_track not in {"E", "M"}:
        return {}

    apps = (
        Application.objects.select_related("form")
        .prefetch_related("answers__question")
        .order_by("-created_at", "-id")
    )

    out: dict[str, dict] = {}
    for app in apps:
        if _app_group_number(app) != int(group_num):
            continue
        if _track_from_slug(getattr(app.form, "slug", "")) != target_track:
            continue

        email_norm = _normalize_email(getattr(app, "email", "") or "")
        if not email_norm:
            for ans in app.answers.all():
                if getattr(ans.question, "slug", "") == "email":
                    email_norm = _normalize_email(ans.value)
                    if email_norm:
                        break
        if not email_norm or email_norm in out:
            continue

        answer_map = {}
        for ans in app.answers.all():
            slug = getattr(ans.question, "slug", "")
            if slug:
                answer_map[slug] = (ans.value or "").strip()

        out[email_norm] = {
            "app": app,
            "answers": answer_map,
            "name": answer_map.get("full_name") or app.name or "",
            "id_value": answer_map.get("cedula") or answer_map.get("id_number") or "",
            "email": answer_map.get("email") or app.email or "",
            "whatsapp": answer_map.get("whatsapp") or "",
            "country": answer_map.get("country_residence") or "",
            "age": answer_map.get("age_range") or "",
        }
    return out


def _build_mentoras_rows(group_num: int, emails: list[str]) -> list[list]:
    data = _latest_apps_by_email_for_group_track(group_num, "M")
    rows: list[list] = []
    for idx, raw_email in enumerate(emails, start=1):
        email_norm = _normalize_email(raw_email)
        found = data.get(email_norm, {})
        row = [
            "",
            "",
            idx,
            found.get("name", ""),
            found.get("id_value", ""),
            found.get("email", raw_email),
            found.get("whatsapp", ""),
            found.get("country", ""),
            found.get("age", ""),
            False,
            False,
            False,
            False,
            False,
            False,
            False,
        ]
        rows.append(row)
    return rows


def _build_emprendedoras_rows(group_num: int, emails: list[str]) -> list[list]:
    data = _latest_apps_by_email_for_group_track(group_num, "E")
    rows: list[list] = []
    for idx, raw_email in enumerate(emails, start=1):
        email_norm = _normalize_email(raw_email)
        found = data.get(email_norm, {})
        row = [
            "",
            "",
            idx,
            found.get("name", ""),
            found.get("id_value", ""),
            found.get("email", raw_email),
            found.get("whatsapp", ""),
            found.get("country", ""),
            found.get("age", ""),
            False,
            False,
            False,
            False,
            False,
            False,
        ]
        rows.append(row)
    return rows


def _excel_col_name(col_idx: int) -> str:
    n = int(col_idx)
    out = []
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out.append(chr(65 + rem))
    return "".join(reversed(out))


def _cell_xml(col: int, row: int, value, style_id: int = 0) -> str:
    ref = f"{_excel_col_name(col)}{row}"
    if value is None:
        return f'<c r="{ref}" s="{style_id}"/>'
    if isinstance(value, bool):
        return f'<c r="{ref}" s="{style_id}" t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}" s="{style_id}"><v>{value}</v></c>'
    text = str(value)
    text_escaped = escape(text)
    preserve = ' xml:space="preserve"' if text.startswith(" ") or text.endswith(" ") or ("\n" in text) else ""
    return (
        f'<c r="{ref}" s="{style_id}" t="inlineStr">'
        f"<is><t{preserve}>{text_escaped}</t></is>"
        f"</c>"
    )


def _sheet_xml(
    headers: list[str],
    rows: list[list],
    col_widths: list[float] | None = None,
    freeze_cols: int = 0,
    freeze_rows: int = 1,
) -> bytes:
    total_cols = max(len(headers), 1)
    top_left_col = _excel_col_name(freeze_cols + 1)
    top_left_row = freeze_rows + 1
    top_left_cell = f"{top_left_col}{top_left_row}"
    col_defs = []
    for idx, width in enumerate(col_widths or [], start=1):
        if width is None:
            continue
        col_defs.append(
            f'<col min="{idx}" max="{idx}" width="{float(width):.2f}" customWidth="1"/>'
        )

    body_rows = []
    header_cells = "".join(_cell_xml(col=i + 1, row=1, value=val, style_id=1) for i, val in enumerate(headers))
    body_rows.append(f'<row r="1" ht="22.5" customHeight="1">{header_cells}</row>')

    for r_idx, values in enumerate(rows, start=2):
        padded = list(values) + [""] * max(0, total_cols - len(values))
        cells = "".join(
            _cell_xml(col=c_idx + 1, row=r_idx, value=val, style_id=0)
            for c_idx, val in enumerate(padded[:total_cols])
        )
        body_rows.append(f'<row r="{r_idx}">{cells}</row>')

    auto_filter_ref = f"A1:{_excel_col_name(total_cols)}1"
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetViews>"
        '<sheetView workbookViewId="0">'
        f'<pane xSplit="{float(freeze_cols):.1f}" ySplit="{float(freeze_rows):.1f}" '
        f'topLeftCell="{top_left_cell}" activePane="bottomRight" state="frozen"/>'
        "</sheetView>"
        "</sheetViews>"
        '<sheetFormatPr defaultRowHeight="15"/>'
        + (f"<cols>{''.join(col_defs)}</cols>" if col_defs else "")
        + f"<sheetData>{''.join(body_rows)}</sheetData>"
        + f'<autoFilter ref="{auto_filter_ref}"/>'
        + "</worksheet>"
    )
    return xml.encode("utf-8")


def _participants_workbook_bytes(mentoras_rows: list[list], emprendedoras_rows: list[list]) -> bytes:
    mentoras_rows = _normalize_sheet_rows(mentoras_rows, MENTORAS_HEADERS)
    emprendedoras_rows = _normalize_sheet_rows(emprendedoras_rows, EMPRENDEDORAS_HEADERS)
    mentoras_rows = _coerce_bool_columns(mentoras_rows, MENTORAS_BOOLEAN_COLS)
    emprendedoras_rows = _coerce_bool_columns(emprendedoras_rows, EMPRENDEDORAS_BOOLEAN_COLS)

    sheets = [
        ("Mentoras", MENTORAS_HEADERS, mentoras_rows, MENTORAS_COL_WIDTHS, 4),
        ("Emprendedoras", EMPRENDEDORAS_HEADERS, emprendedoras_rows, EMPRENDEDORAS_COL_WIDTHS, 3),
    ]

    content_types_overrides = [
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
    ]
    workbook_sheets = []
    workbook_rels = []

    bio = BytesIO()
    with zipfile.ZipFile(bio, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for idx, (name, headers, rows, widths, freeze_cols) in enumerate(sheets, start=1):
            worksheet_path = f"xl/worksheets/sheet{idx}.xml"
            worksheet_xml = _sheet_xml(
                headers=headers,
                rows=rows,
                col_widths=widths,
                freeze_cols=freeze_cols,
                freeze_rows=1,
            )
            zf.writestr(worksheet_path, worksheet_xml)

            content_types_overrides.append(
                f'<Override PartName="/xl/worksheets/sheet{idx}.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            )
            workbook_sheets.append(
                f'<sheet name="{escape(name)}" sheetId="{idx}" r:id="rId{idx}"/>'
            )
            workbook_rels.append(
                f'<Relationship Id="rId{idx}" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                f'Target="worksheets/sheet{idx}.xml"/>'
            )

        workbook_rels.append(
            f'<Relationship Id="rId{len(sheets) + 1}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
            'Target="styles.xml"/>'
        )

        workbook_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{''.join(workbook_sheets)}</sheets>"
            "</workbook>"
        )
        zf.writestr("xl/workbook.xml", workbook_xml)

        workbook_rels_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{''.join(workbook_rels)}"
            "</Relationships>"
        )
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)

        styles_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="2">'
            '<font><sz val="11"/><color rgb="FF000000"/><name val="Calibri"/><family val="2"/></font>'
            '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/><family val="2"/></font>'
            "</fonts>"
            '<fills count="3">'
            '<fill><patternFill patternType="none"/></fill>'
            '<fill><patternFill patternType="gray125"/></fill>'
            '<fill><patternFill patternType="solid"><fgColor rgb="FF223413"/><bgColor indexed="64"/></patternFill></fill>'
            "</fills>"
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="2">'
            '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1">'
            '<alignment horizontal="center" vertical="center"/>'
            "</xf>"
            "</cellXfs>"
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
            "</styleSheet>"
        )
        zf.writestr("xl/styles.xml", styles_xml)

        root_rels_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/>'
            "</Relationships>"
        )
        zf.writestr("_rels/.rels", root_rels_xml)

        content_types_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            f"{''.join(content_types_overrides)}"
            "</Types>"
        )
        zf.writestr("[Content_Types].xml", content_types_xml)

    return bio.getvalue()


def _group_number_from_slug(slug: str | None) -> int | None:
    match = GROUP_SLUG_RE.match((slug or "").strip().upper())
    if not match:
        return None
    try:
        return int(match.group("num"))
    except (TypeError, ValueError):
        return None


def _track_from_slug(slug: str | None) -> str | None:
    s = (slug or "").upper()
    if "E_A" in s:
        return "E"
    if "M_A" in s:
        return "M"
    return None


def _parse_created_at(value: str) -> tuple:
    raw = (value or "").strip()
    parsed = parse_datetime(raw)
    if parsed:
        # Keep comparisons deterministic across naive/aware mixes by using the raw string.
        return (1, raw)
    return (0, raw)


def _parse_graded_file_rows(gf: GradedFile) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not gf.csv_text:
        return out

    reader = csv.DictReader(io.StringIO(gf.csv_text))
    for row in reader:
        normalized = {
            _normalize_header(col): (val or "").strip()
            for col, val in row.items()
            if col is not None
        }
        identity_raw = _pick_value(normalized, CSV_IDENTITY_KEYS)
        email_raw = _pick_value(normalized, CSV_EMAIL_KEYS)
        tokens = {token for token in (_id_token(identity_raw), _email_token(email_raw)) if token}
        if not tokens:
            continue

        created_marker = _parse_created_at(normalized.get("createdat", ""))
        row_data = {
            "identity_raw": identity_raw,
            "email_raw": email_raw,
            "created_marker": created_marker,
            "recommendation": _pick_value(normalized, CSV_RECOMMENDATION_KEYS),
            "overall_score": _pick_value(normalized, CSV_OVERALL_SCORE_KEYS),
            "tablestakes_score": _pick_value(normalized, CSV_TABLESTAKES_KEYS),
            "commitment_score": _pick_value(normalized, CSV_COMMITMENT_KEYS),
            "nice_to_have_score": _pick_value(normalized, CSV_NICE_TO_HAVE_KEYS),
        }
        for token in tokens:
            current = out.get(token)
            if current and current["created_marker"] > created_marker:
                continue
            out[token] = row_data

    return out


def _build_grading_lookup(target_groups: set[int]) -> tuple[dict[tuple[int, str], GradedFile], dict[int, GradedFile], dict[int, dict[str, dict]]]:
    if not target_groups:
        return {}, {}, {}

    latest_by_group_track: dict[tuple[int, str], GradedFile] = {}
    latest_by_group: dict[int, GradedFile] = {}

    graded_files = GradedFile.objects.exclude(form_slug__startswith="PAIR_G").order_by("-created_at", "-id")
    for gf in graded_files:
        group_num = _group_number_from_slug(gf.form_slug)
        if group_num is None or (target_groups and group_num not in target_groups):
            continue

        track = _track_from_slug(gf.form_slug)
        if track and (group_num, track) not in latest_by_group_track:
            latest_by_group_track[(group_num, track)] = gf
        if group_num not in latest_by_group:
            latest_by_group[group_num] = gf

    selected_files = {}
    for gf in latest_by_group_track.values():
        selected_files[gf.id] = gf
    for gf in latest_by_group.values():
        selected_files[gf.id] = gf

    rows_by_file_id = {gf_id: _parse_graded_file_rows(gf) for gf_id, gf in selected_files.items()}
    return latest_by_group_track, latest_by_group, rows_by_file_id


def _build_profiles():
    apps = list(
        Application.objects.select_related("form")
        .prefetch_related("answers__question")
        .order_by("-created_at", "-id")
    )
    if not apps:
        return []

    app_data_by_id: dict[int, dict] = {}
    for app in apps:
        answer_map = {}
        for ans in app.answers.all():
            slug = getattr(ans.question, "slug", "")
            if slug:
                answer_map[slug] = (ans.value or "").strip()

        id_values = [answer_map.get(slug, "") for slug in IDENTITY_SLUGS]
        email_values = [app.email or ""] + [answer_map.get(slug, "") for slug in EMAIL_SLUGS]

        id_display = next((val for val in id_values if (val or "").strip()), "")
        email_display = next((val for val in email_values if (val or "").strip()), "")

        id_norm = _normalize_identity(id_display)
        email_norm = _normalize_email(email_display)
        tokens = {
            token
            for token in (
                *(_id_token(value) for value in id_values),
                *(_email_token(value) for value in email_values),
            )
            if token
        }

        app_data_by_id[app.id] = {
            "app": app,
            "answer_map": answer_map,
            "id_display": id_display,
            "email_display": email_display,
            "id_norm": id_norm,
            "email_norm": email_norm,
            "tokens": tokens,
        }

    # Union applications by shared identity token (cedula or email).
    parent = {app_id: app_id for app_id in app_data_by_id.keys()}

    def _find(app_id: int) -> int:
        while parent[app_id] != app_id:
            parent[app_id] = parent[parent[app_id]]
            app_id = parent[app_id]
        return app_id

    def _union(a: int, b: int) -> None:
        ra = _find(a)
        rb = _find(b)
        if ra != rb:
            parent[rb] = ra

    token_owner: dict[str, int] = {}
    for app_id, payload in app_data_by_id.items():
        for token in payload["tokens"]:
            owner = token_owner.get(token)
            if owner is None:
                token_owner[token] = app_id
            else:
                _union(app_id, owner)

    clusters: dict[int, list[int]] = defaultdict(list)
    for app_id in app_data_by_id.keys():
        clusters[_find(app_id)].append(app_id)

    latest_app_id_by_cluster: dict[int, int] = {}
    target_groups: set[int] = set()
    for root, app_ids in clusters.items():
        latest_app_id = max(
            app_ids,
            key=lambda app_id: (
                app_data_by_id[app_id]["app"].created_at,
                app_id,
            ),
        )
        latest_app_id_by_cluster[root] = latest_app_id
        latest_app = app_data_by_id[latest_app_id]["app"]
        group_num = _app_group_number(latest_app)
        if group_num:
            target_groups.add(group_num)

    latest_by_group_track, latest_by_group, rows_by_file_id = _build_grading_lookup(target_groups)

    profiles = []
    for root, app_ids in clusters.items():
        latest_payload = app_data_by_id[latest_app_id_by_cluster[root]]
        app = latest_payload["app"]
        answer_map = latest_payload["answer_map"]

        group_num = _app_group_number(app)
        track = _track_from_slug(getattr(app.form, "slug", ""))

        cluster_tokens: set[str] = set()
        cluster_id_values: list[str] = []
        cluster_email_values: list[str] = []
        for app_id in app_ids:
            payload = app_data_by_id[app_id]
            cluster_tokens.update(payload["tokens"])
            if payload["id_display"]:
                cluster_id_values.append(payload["id_display"])
            if payload["email_display"]:
                cluster_email_values.append(payload["email_display"])

        grade_file = None
        if group_num and track:
            grade_file = latest_by_group_track.get((group_num, track))
        if not grade_file and group_num:
            grade_file = latest_by_group.get(group_num)

        grade_row = {}
        if grade_file:
            token_map = rows_by_file_id.get(grade_file.id, {})
            for token in cluster_tokens:
                row = token_map.get(token)
                if not row:
                    continue
                if not grade_row or row["created_marker"] > grade_row["created_marker"]:
                    grade_row = row
            if not grade_row and group_num:
                fallback_file = latest_by_group.get(group_num)
                if fallback_file and fallback_file.id != grade_file.id:
                    token_map = rows_by_file_id.get(fallback_file.id, {})
                    for token in cluster_tokens:
                        row = token_map.get(token)
                        if not row:
                            continue
                        if not grade_row or row["created_marker"] > grade_row["created_marker"]:
                            grade_row = row
                    if grade_row:
                        grade_file = fallback_file

        recommendation = (
            (grade_row.get("recommendation") or "").strip()
            or (app.recommendation or "").strip()
        )
        overall_score = (
            (grade_row.get("overall_score") or "").strip()
            or (f"{app.overall_score:g}" if app.overall_score else "")
        )
        tablestakes_score = (
            (grade_row.get("tablestakes_score") or "").strip()
            or (f"{app.tablestakes_score:g}" if app.tablestakes_score else "")
        )
        commitment_score = (
            (grade_row.get("commitment_score") or "").strip()
            or (f"{app.commitment_score:g}" if app.commitment_score else "")
        )
        nice_to_have_score = (
            (grade_row.get("nice_to_have_score") or "").strip()
            or (f"{app.nice_to_have_score:g}" if app.nice_to_have_score else "")
        )

        calificacion_status = recommendation or ("Scored" if overall_score else "Not graded")

        display_identity = (
            latest_payload["id_display"]
            or (cluster_id_values[0] if cluster_id_values else "")
            or (grade_row.get("identity_raw") or "").strip()
            or "—"
        )
        display_email = (
            latest_payload["email_display"]
            or (cluster_email_values[0] if cluster_email_values else "")
            or (grade_row.get("email_raw") or "").strip()
            or "—"
        )

        identity_norm = (
            latest_payload["id_norm"]
            or _normalize_identity(cluster_id_values[0] if cluster_id_values else "")
            or _normalize_identity(grade_row.get("identity_raw"))
        )
        email_norm = (
            latest_payload["email_norm"]
            or _normalize_email(cluster_email_values[0] if cluster_email_values else "")
            or _normalize_email(grade_row.get("email_raw"))
        )
        profile_key = _build_profile_key(identity_norm, email_norm, app.id)

        overview_rows = []
        for slug, label in PROFILE_OVERVIEW_FIELDS:
            value = answer_map.get(slug, "")
            if value:
                overview_rows.append({"label": label, "value": value})

        profile = {
            "profile_key": profile_key,
            "identity_key": identity_norm or email_norm or profile_key,
            "identity_display": display_identity,
            "applicant_name": answer_map.get("full_name") or app.name or "—",
            "email": display_email,
            "group_num": group_num,
            "track": track or "—",
            "form_slug": getattr(app.form, "slug", "—"),
            "form_name": getattr(app.form, "name", "—"),
            "applied_at": app.created_at,
            "application_id": app.id,
            "application_count": len(app_ids),
            "calificacion_status": calificacion_status,
            "recommendation": recommendation,
            "overall_score": overall_score,
            "tablestakes_score": tablestakes_score,
            "commitment_score": commitment_score,
            "nice_to_have_score": nice_to_have_score,
            "is_graded": bool(recommendation or overall_score),
            "graded_file_slug": getattr(grade_file, "form_slug", ""),
            "graded_file_created_at": getattr(grade_file, "created_at", None),
            "overview_rows": overview_rows,
        }
        profile["search_text"] = " ".join(
            [
                str(profile["identity_display"]),
                str(profile["identity_key"]),
                str(profile["applicant_name"]),
                str(profile["email"]),
                str(profile["form_slug"]),
                str(profile["group_num"] or ""),
                str(profile["calificacion_status"]),
                " ".join(cluster_email_values),
                " ".join(cluster_id_values),
            ]
        ).lower()
        profiles.append(profile)

    profiles.sort(key=lambda p: (p["applied_at"], p["application_id"]), reverse=True)
    return profiles


def _profiles_filtered_payload(request):
    profiles = _build_profiles()
    status_map = {
        _email_status_key(row.email): row
        for row in ParticipantEmailStatus.objects.only(
            "email",
            "participated",
            "contract_signed",
            "contract_signed_at",
        )
    }
    participant_list_email_keys = _participant_list_email_keys()
    for email_key in participant_list_email_keys:
        row = status_map.get(email_key)
        if row is None:
            status_map[email_key] = ParticipantEmailStatus(
                email=email_key,
                participated=True,
            )
            continue
        row.participated = True
    for profile in profiles:
        profile_email_key = _email_status_key(profile.get("email"))
        row = status_map.get(profile_email_key)
        profile["participated"] = bool(getattr(row, "participated", False))
        profile["contract_signed"] = bool(getattr(row, "contract_signed", False))
        profile["contract_signed_at"] = getattr(row, "contract_signed_at", None)

    query = (request.GET.get("q") or "").strip()
    query_lower = query.lower()
    group_filter = (request.GET.get("group") or "").strip()
    status_filter = (request.GET.get("grading") or "").strip()
    if status_filter not in {"all", "graded", "not_graded"}:
        status_filter = "all"

    filtered = profiles
    if query_lower:
        filtered = [p for p in filtered if query_lower in p["search_text"]]

    if group_filter.isdigit():
        filtered = [p for p in filtered if p["group_num"] == int(group_filter)]

    if status_filter == "graded":
        filtered = [p for p in filtered if p["is_graded"]]
    elif status_filter == "not_graded":
        filtered = [p for p in filtered if not p["is_graded"]]

    group_options = sorted(
        {p["group_num"] for p in profiles if p["group_num"] is not None},
        reverse=True,
    )
    return {
        "profiles": profiles,
        "filtered": filtered,
        "query": query,
        "group_filter": group_filter,
        "status_filter": status_filter,
        "group_options": group_options,
        "total_profiles": len(profiles),
        "visible_profiles": len(filtered),
        "graded_profiles": sum(1 for p in profiles if p["is_graded"]),
        "participated_profiles": sum(1 for p in profiles if p["participated"]),
        "contract_signed_profiles": sum(1 for p in profiles if p.get("contract_signed")),
    }


def _build_profiles_sheet_data(filtered_profiles):
    headers = [
        "Cedula",
        "Email",
        "Applicant",
        "Group",
        "Form",
        "Calificacion status",
        "Score",
        "Participated",
        "Contract signed",
        "Contract signed at",
        "Profile URL",
    ]
    rows = []
    for p in filtered_profiles:
        group_label = f"Group {p['group_num']}" if p.get("group_num") else "Ungrouped"
        form_label = str(p.get("form_slug") or "—")
        track = str(p.get("track") or "").strip()
        if track and track != "—":
            form_label = f"{form_label} · {track}"
        profile_url = reverse("admin_profile_detail", args=[p["profile_key"]])
        rows.append(
            [
                str(p.get("identity_display") or ""),
                str(p.get("email") or ""),
                str(p.get("applicant_name") or ""),
                group_label,
                form_label,
                str(p.get("calificacion_status") or ""),
                str(p.get("overall_score") or "—"),
                "Yes" if p.get("participated") else "No",
                "Yes" if p.get("contract_signed") else "No",
                str(p.get("contract_signed_at") or ""),
                profile_url,
            ]
        )
    return headers, rows


@staff_member_required
def profiles_list(request):
    view_mode = (request.GET.get("view") or "").strip().lower()
    if view_mode == "sheet":
        params = request.GET.copy()
        params.pop("view", None)
        params.pop("sheet_page", None)
        target = reverse("admin_profiles_sheet")
        query = params.urlencode()
        if query:
            target = f"{target}?{query}"
        return redirect(target)

    payload = _profiles_filtered_payload(request)
    context = {
        "profiles": payload["filtered"],
        "query": payload["query"],
        "group_filter": payload["group_filter"],
        "status_filter": payload["status_filter"],
        "group_options": payload["group_options"],
        "total_profiles": payload["total_profiles"],
        "visible_profiles": payload["visible_profiles"],
        "graded_profiles": payload["graded_profiles"],
        "participated_profiles": payload["participated_profiles"],
        "contract_signed_profiles": payload["contract_signed_profiles"],
    }
    return render(request, "admin_dash/profiles_list.html", context)


@staff_member_required
def profiles_sheet(request):
    payload = _profiles_filtered_payload(request)
    sheet_headers, sheet_rows = _build_profiles_sheet_data(payload["filtered"])
    context = {
        "query": payload["query"],
        "group_filter": payload["group_filter"],
        "status_filter": payload["status_filter"],
        "group_options": payload["group_options"],
        "rows_count": len(sheet_rows),
        "sheet_headers": sheet_headers,
        "sheet_rows": sheet_rows,
    }
    return render(request, "admin_dash/profiles_sheet.html", context)


@staff_member_required
def profiles_participants(request):
    groups_qs = _formgroup_active_queryset()
    groups = list(groups_qs)
    group_raw = (request.GET.get("group") or request.POST.get("group") or "").strip()
    selected_group = None
    participant_list = None

    if group_raw.isdigit():
        selected_group = groups_qs.filter(number=int(group_raw)).first()
        if selected_group:
            participant_list = GroupParticipantList.objects.filter(group=selected_group).first()

    if request.method == "POST":
        posted_group = (request.POST.get("group") or "").strip()
        if not posted_group.isdigit():
            messages.error(request, "Please select a valid group.")
            return redirect(reverse("admin_profiles_participants"))

        post_group_qs = _formgroup_active_queryset()
        selected_group = post_group_qs.filter(number=int(posted_group)).first()
        if not selected_group:
            messages.error(request, "Selected group does not exist or is archived.")
            return redirect(reverse("admin_profiles_participants"))

        action = (request.POST.get("action") or "save_sheet").strip()
        if action in {"delete_group", "force_delete_group", "delete_group_force"}:
            messages.error(
                request,
                (
                    "Group deletion is disabled on the Participants page. "
                    "This page only manages participant lists and never deletes application database records."
                ),
            )
            return redirect(f"{reverse('admin_profiles_participants')}?group={selected_group.number}")

        if action == "check_dropbox":
            try:
                ok, summary = _run_dropbox_reconcile_for_group(
                    group_num=selected_group.number,
                    track="both",
                    days_back=730,
                    max_pages=60,
                )
            except Exception as exc:
                ok, summary = False, f"Unexpected error starting Dropbox check: {exc}"
            if ok:
                messages.success(
                    request,
                    f"Dropbox check started for Group {selected_group.number}. {summary}",
                )
            else:
                messages.error(
                    request,
                    f"Dropbox check failed for Group {selected_group.number}: {summary}",
                )
            return redirect(f"{reverse('admin_profiles_participants')}?group={selected_group.number}")

        if action in {"delete_group_participants", "clear_group_participants"}:
            group_number = selected_group.number
            try:
                participant_list = GroupParticipantList.objects.filter(group=selected_group).first()
                if participant_list:
                    participant_list.mentoras_emails_text = ""
                    participant_list.emprendedoras_emails_text = ""
                    participant_list.mentoras_sheet_rows = []
                    participant_list.emprendedoras_sheet_rows = []
                    participant_list.save(
                        update_fields=[
                            "mentoras_emails_text",
                            "emprendedoras_emails_text",
                            "mentoras_sheet_rows",
                            "emprendedoras_sheet_rows",
                            "updated_at",
                        ]
                    )
                    messages.success(
                        request,
                        f"Cleared participant list data for Group {group_number}.",
                    )
                else:
                    messages.info(
                        request,
                        f"Group {group_number} has no participant list data to clear.",
                    )
                if _formgroup_is_active_column_exists() and getattr(selected_group, "is_active", True):
                    selected_group.is_active = False
                    selected_group.save(update_fields=["is_active"])
                    messages.success(
                        request,
                        (
                            f"Group {group_number} was archived from the Participants page. "
                            "It is hidden there now."
                        ),
                    )
                messages.info(
                    request,
                    (
                        f"Group {group_number} application/database records were not deleted. "
                        "Submissions, answers, and forms were preserved."
                    ),
                )
                return redirect(reverse("admin_profiles_participants"))
            except Exception as exc:
                messages.error(
                    request,
                    f"Could not clear participant list for Group {group_number}: {exc}",
                )
                return redirect(f"{reverse('admin_profiles_participants')}?group={group_number}")

        success_action_text = "Saved"
        action_detail_text = ""
        mentoras_raw = (request.POST.get("mentoras_emails") or "").strip()
        emprendedoras_raw = (request.POST.get("emprendedoras_emails") or "").strip()
        mentoras_cedulas_raw = (request.POST.get("mentoras_cedulas") or "").strip()
        emprendedoras_cedulas_raw = (request.POST.get("emprendedoras_cedulas") or "").strip()
        mentoras_valid: list[str] = []
        emprendedoras_valid: list[str] = []
        mentoras_invalid: list[str] = []
        emprendedoras_invalid: list[str] = []
        mentoras_rows: list[list] = []
        emprendedoras_rows: list[list] = []
        invalid_entries: list[str] = []
        unmatched_cedulas: list[str] = []
        skipped_duplicates_total = 0
        repaired_legacy_checks = 0

        if action == "build_from_emails":
            success_action_text = "Created"
            mentoras_valid, mentoras_invalid = _parse_email_list(mentoras_raw)
            emprendedoras_valid, emprendedoras_invalid = _parse_email_list(emprendedoras_raw)
            invalid_entries.extend(mentoras_invalid)
            invalid_entries.extend(emprendedoras_invalid)
            mentoras_rows = _build_mentoras_rows(selected_group.number, mentoras_valid)
            emprendedoras_rows = _build_emprendedoras_rows(selected_group.number, emprendedoras_valid)
        elif action == "add_from_cedulas":
            success_action_text = "Updated"
            mentoras_cedulas, mentoras_invalid_cedulas = _parse_identity_list(mentoras_cedulas_raw)
            emprendedoras_cedulas, emprendedoras_invalid_cedulas = _parse_identity_list(
                emprendedoras_cedulas_raw
            )
            invalid_entries.extend(mentoras_invalid_cedulas)
            invalid_entries.extend(emprendedoras_invalid_cedulas)

            existing_list = GroupParticipantList.objects.filter(group=selected_group).first()

            mentoras_existing_rows = _normalize_sheet_rows(
                getattr(existing_list, "mentoras_sheet_rows", []),
                MENTORAS_HEADERS,
            )
            emprendedoras_existing_rows = _normalize_sheet_rows(
                getattr(existing_list, "emprendedoras_sheet_rows", []),
                EMPRENDEDORAS_HEADERS,
            )
            mentoras_existing_rows = _coerce_bool_columns(
                mentoras_existing_rows,
                MENTORAS_BOOLEAN_COLS,
            )
            emprendedoras_existing_rows = _coerce_bool_columns(
                emprendedoras_existing_rows,
                EMPRENDEDORAS_BOOLEAN_COLS,
            )

            if not mentoras_existing_rows and existing_list:
                mentoras_seed = _norm_email_list(getattr(existing_list, "mentoras_emails_text", ""))
                if mentoras_seed:
                    mentoras_existing_rows = _build_mentoras_rows(selected_group.number, mentoras_seed)
            if not emprendedoras_existing_rows and existing_list:
                emprendedoras_seed = _norm_email_list(
                    getattr(existing_list, "emprendedoras_emails_text", "")
                )
                if emprendedoras_seed:
                    emprendedoras_existing_rows = _build_emprendedoras_rows(
                        selected_group.number,
                        emprendedoras_seed,
                    )

            mentoras_latest_by_email = _latest_apps_by_email_for_group_track(selected_group.number, "M")
            mentoras_by_id: dict[str, str] = {}
            for email_norm, item in mentoras_latest_by_email.items():
                id_norm = _normalize_identity(item.get("id_value"))
                if id_norm and id_norm not in mentoras_by_id:
                    mentoras_by_id[id_norm] = email_norm

            emprendedoras_latest_by_email = _latest_apps_by_email_for_group_track(
                selected_group.number,
                "E",
            )
            emprendedoras_by_id: dict[str, str] = {}
            for email_norm, item in emprendedoras_latest_by_email.items():
                id_norm = _normalize_identity(item.get("id_value"))
                if id_norm and id_norm not in emprendedoras_by_id:
                    emprendedoras_by_id[id_norm] = email_norm

            mentoras_matched_emails: list[str] = []
            seen_mentoras_emails: set[str] = set()
            for id_norm in mentoras_cedulas:
                matched_email = mentoras_by_id.get(id_norm)
                if not matched_email:
                    unmatched_cedulas.append(id_norm)
                    continue
                if matched_email in seen_mentoras_emails:
                    continue
                seen_mentoras_emails.add(matched_email)
                mentoras_matched_emails.append(matched_email)

            emprendedoras_matched_emails: list[str] = []
            seen_emprendedoras_emails: set[str] = set()
            for id_norm in emprendedoras_cedulas:
                matched_email = emprendedoras_by_id.get(id_norm)
                if not matched_email:
                    unmatched_cedulas.append(id_norm)
                    continue
                if matched_email in seen_emprendedoras_emails:
                    continue
                seen_emprendedoras_emails.add(matched_email)
                emprendedoras_matched_emails.append(matched_email)

            mentoras_incoming_rows = _build_mentoras_rows(selected_group.number, mentoras_matched_emails)
            emprendedoras_incoming_rows = _build_emprendedoras_rows(
                selected_group.number,
                emprendedoras_matched_emails,
            )

            mentoras_rows, mentoras_added, mentoras_skipped = _append_unique_participant_rows(
                mentoras_existing_rows,
                mentoras_incoming_rows,
                id_col=MENTORAS_ID_COL,
                email_col=MENTORAS_EMAIL_COL,
            )
            emprendedoras_rows, emprendedoras_added, emprendedoras_skipped = (
                _append_unique_participant_rows(
                    emprendedoras_existing_rows,
                    emprendedoras_incoming_rows,
                    id_col=EMPRENDEDORAS_ID_COL,
                    email_col=EMPRENDEDORAS_EMAIL_COL,
                )
            )
            skipped_duplicates_total = mentoras_skipped + emprendedoras_skipped
            action_detail_text = (
                f"Added by cedula: {mentoras_added} mentoras, {emprendedoras_added} emprendedoras."
            )

            mentoras_valid = _emails_from_sheet_rows(mentoras_rows, MENTORAS_EMAIL_COL)
            emprendedoras_valid = _emails_from_sheet_rows(
                emprendedoras_rows,
                EMPRENDEDORAS_EMAIL_COL,
            )
        elif action == "sync_from_group_assignments":
            success_action_text = "Synced"
            mentoras_valid = sorted(_latest_apps_by_email_for_group_track(selected_group.number, "M").keys())
            emprendedoras_valid = sorted(_latest_apps_by_email_for_group_track(selected_group.number, "E").keys())
            mentoras_rows = _build_mentoras_rows(selected_group.number, mentoras_valid)
            emprendedoras_rows = _build_emprendedoras_rows(selected_group.number, emprendedoras_valid)
        else:
            mentoras_sheet_raw = (request.POST.get("mentoras_sheet_data") or "").strip()
            emprendedoras_sheet_raw = (request.POST.get("emprendedoras_sheet_data") or "").strip()

            mentoras_payload = []
            emprendedoras_payload = []
            decode_error = False
            if mentoras_sheet_raw:
                try:
                    mentoras_payload = json.loads(mentoras_sheet_raw)
                except json.JSONDecodeError:
                    decode_error = True
            if emprendedoras_sheet_raw:
                try:
                    emprendedoras_payload = json.loads(emprendedoras_sheet_raw)
                except json.JSONDecodeError:
                    decode_error = True
            if decode_error:
                messages.error(request, "Could not read sheet edits. Please try again.")

            mentoras_rows = _normalize_sheet_rows(mentoras_payload, MENTORAS_HEADERS)
            emprendedoras_rows = _normalize_sheet_rows(emprendedoras_payload, EMPRENDEDORAS_HEADERS)
            mentoras_rows = _coerce_bool_columns(mentoras_rows, MENTORAS_BOOLEAN_COLS)
            emprendedoras_rows = _coerce_bool_columns(emprendedoras_rows, EMPRENDEDORAS_BOOLEAN_COLS)

            if not mentoras_rows and mentoras_raw:
                mentoras_valid, mentoras_invalid = _parse_email_list(mentoras_raw)
                invalid_entries.extend(mentoras_invalid)
                mentoras_rows = _build_mentoras_rows(selected_group.number, mentoras_valid)
            if not emprendedoras_rows and emprendedoras_raw:
                emprendedoras_valid, emprendedoras_invalid = _parse_email_list(emprendedoras_raw)
                invalid_entries.extend(emprendedoras_invalid)
                emprendedoras_rows = _build_emprendedoras_rows(selected_group.number, emprendedoras_valid)

            if not mentoras_valid:
                mentoras_valid = _emails_from_sheet_rows(mentoras_rows, MENTORAS_EMAIL_COL)
            if not emprendedoras_valid:
                emprendedoras_valid = _emails_from_sheet_rows(
                    emprendedoras_rows,
                    EMPRENDEDORAS_EMAIL_COL,
                )

        # Acta is source-of-truth from Dropbox Sign contract status.
        mentoras_rows, repaired_m = _repair_progress_defaults_if_legacy(
            mentoras_rows,
            MENTORAS_PROGRESS_DEFAULT_FALSE_COLS,
        )
        emprendedoras_rows, repaired_e = _repair_progress_defaults_if_legacy(
            emprendedoras_rows,
            EMPRENDEDORAS_PROGRESS_DEFAULT_FALSE_COLS,
        )
        repaired_legacy_checks += (repaired_m + repaired_e)

        mentoras_rows = _apply_contract_signed_to_rows(
            mentoras_rows,
            email_col=MENTORAS_EMAIL_COL,
            acta_col=MENTORAS_ACTA_COL,
        )
        emprendedoras_rows = _apply_contract_signed_to_rows(
            emprendedoras_rows,
            email_col=EMPRENDEDORAS_EMAIL_COL,
            acta_col=EMPRENDEDORAS_ACTA_COL,
        )

        mentoras_rows = _number_sheet_rows(mentoras_rows, number_col=2)
        emprendedoras_rows = _number_sheet_rows(emprendedoras_rows, number_col=2)

        GroupParticipantList.objects.update_or_create(
            group=selected_group,
            defaults={
                "mentoras_emails_text": "\n".join(mentoras_valid),
                "emprendedoras_emails_text": "\n".join(emprendedoras_valid),
                "mentoras_sheet_rows": mentoras_rows,
                "emprendedoras_sheet_rows": emprendedoras_rows,
            },
        )
        participant_emails = []
        participant_emails.extend(mentoras_valid)
        participant_emails.extend(emprendedoras_valid)
        if not participant_emails:
            participant_emails.extend(_emails_from_sheet_rows(mentoras_rows, MENTORAS_EMAIL_COL))
            participant_emails.extend(
                _emails_from_sheet_rows(emprendedoras_rows, EMPRENDEDORAS_EMAIL_COL)
            )
        participation_created, participation_updated, participation_unchanged = _mark_participated_yes(
            list(dict.fromkeys(participant_emails))
        )

        messages.success(
            request,
            (
                f"{success_action_text} participants for Group {selected_group.number}. "
                f"Mentoras: {len(mentoras_rows)} rows · Emprendedoras: {len(emprendedoras_rows)} rows. "
                f"Profile participation set to Yes: {participation_created} new, "
                f"{participation_updated} changed, {participation_unchanged} already yes."
                f"{' ' + action_detail_text if action_detail_text else ''}"
            ),
        )
        if skipped_duplicates_total:
            messages.info(
                request,
                f"Skipped {skipped_duplicates_total} duplicate participant rows already present in the sheet.",
            )

        if invalid_entries:
            preview = ", ".join(invalid_entries[:8])
            suffix = "" if len(invalid_entries) <= 8 else ", ..."
            messages.warning(
                request,
                f"Ignored invalid values ({len(invalid_entries)}): {preview}{suffix}",
            )

        if unmatched_cedulas:
            preview = ", ".join(unmatched_cedulas[:8])
            suffix = "" if len(unmatched_cedulas) <= 8 else ", ..."
            messages.warning(
                request,
                (
                    f"Cedulas not found in Group {selected_group.number} applications "
                    f"({len(unmatched_cedulas)}): {preview}{suffix}"
                ),
            )
        if repaired_legacy_checks:
            messages.info(
                request,
                "Auto-repaired legacy default checks in Website/Capacitacion columns.",
            )

        return redirect(f"{reverse('admin_profiles_participants')}?group={selected_group.number}")

    mentoras_rows: list[list] = []
    emprendedoras_rows: list[list] = []
    if selected_group:
        stored_mentoras = _normalize_sheet_rows(
            getattr(participant_list, "mentoras_sheet_rows", []),
            MENTORAS_HEADERS,
        )
        stored_emprendedoras = _normalize_sheet_rows(
            getattr(participant_list, "emprendedoras_sheet_rows", []),
            EMPRENDEDORAS_HEADERS,
        )
        stored_mentoras = _coerce_bool_columns(stored_mentoras, MENTORAS_BOOLEAN_COLS)
        stored_emprendedoras = _coerce_bool_columns(stored_emprendedoras, EMPRENDEDORAS_BOOLEAN_COLS)
        if stored_mentoras or stored_emprendedoras:
            mentoras_rows = _number_sheet_rows(stored_mentoras, number_col=2)
            emprendedoras_rows = _number_sheet_rows(stored_emprendedoras, number_col=2)
        else:
            mentoras_emails_seed = _norm_email_list(getattr(participant_list, "mentoras_emails_text", ""))
            emprendedoras_emails_seed = _norm_email_list(
                getattr(participant_list, "emprendedoras_emails_text", "")
            )
            mentoras_rows = _number_sheet_rows(
                _build_mentoras_rows(selected_group.number, mentoras_emails_seed),
                number_col=2,
            )
            emprendedoras_rows = _number_sheet_rows(
                _build_emprendedoras_rows(selected_group.number, emprendedoras_emails_seed),
                number_col=2,
            )

    repaired_on_load = 0
    if selected_group:
        mentoras_rows, repaired_m = _repair_progress_defaults_if_legacy(
            mentoras_rows,
            MENTORAS_PROGRESS_DEFAULT_FALSE_COLS,
        )
        emprendedoras_rows, repaired_e = _repair_progress_defaults_if_legacy(
            emprendedoras_rows,
            EMPRENDEDORAS_PROGRESS_DEFAULT_FALSE_COLS,
        )
        repaired_on_load = repaired_m + repaired_e
        if repaired_on_load:
            participant_obj, _ = GroupParticipantList.objects.get_or_create(group=selected_group)
            participant_obj.mentoras_sheet_rows = _number_sheet_rows(mentoras_rows, number_col=2)
            participant_obj.emprendedoras_sheet_rows = _number_sheet_rows(emprendedoras_rows, number_col=2)
            participant_obj.save(update_fields=["mentoras_sheet_rows", "emprendedoras_sheet_rows", "updated_at"])

    # Acta is source-of-truth from Dropbox Sign contract status.
    mentoras_rows = _apply_contract_signed_to_rows(
        mentoras_rows,
        email_col=MENTORAS_EMAIL_COL,
        acta_col=MENTORAS_ACTA_COL,
    )
    emprendedoras_rows = _apply_contract_signed_to_rows(
        emprendedoras_rows,
        email_col=EMPRENDEDORAS_EMAIL_COL,
        acta_col=EMPRENDEDORAS_ACTA_COL,
    )

    mentoras_emails = _emails_from_sheet_rows(mentoras_rows, MENTORAS_EMAIL_COL)
    emprendedoras_emails = _emails_from_sheet_rows(emprendedoras_rows, EMPRENDEDORAS_EMAIL_COL)
    has_list = bool(participant_list and (mentoras_rows or emprendedoras_rows))

    context = {
        "groups": groups,
        "selected_group": selected_group,
        "participant_list": participant_list,
        "mentoras_headers": MENTORAS_HEADERS,
        "emprendedoras_headers": EMPRENDEDORAS_HEADERS,
        "mentoras_column_types": MENTORAS_COLUMN_TYPES,
        "emprendedoras_column_types": EMPRENDEDORAS_COLUMN_TYPES,
        "mentoras_column_types_json": json.dumps(MENTORAS_COLUMN_TYPES),
        "emprendedoras_column_types_json": json.dumps(EMPRENDEDORAS_COLUMN_TYPES),
        "mentoras_status_options_json": json.dumps(MENTORAS_STATUS_OPTIONS),
        "emprendedoras_status_options_json": json.dumps(EMPRENDEDORAS_STATUS_OPTIONS),
        "mentoras_rows": mentoras_rows,
        "emprendedoras_rows": emprendedoras_rows,
        "mentoras_rows_json": json.dumps(mentoras_rows),
        "emprendedoras_rows_json": json.dumps(emprendedoras_rows),
        "mentoras_emails_text": "\n".join(mentoras_emails),
        "emprendedoras_emails_text": "\n".join(emprendedoras_emails),
        "mentoras_count": len(mentoras_rows),
        "emprendedoras_count": len(emprendedoras_rows),
        "has_list": has_list,
    }
    return render(request, "admin_dash/profiles_participants.html", context)


@staff_member_required
def profiles_participants_track_sheet(request, group_num: int, track: str):
    group = _formgroup_safe_queryset().filter(number=group_num).first()
    if not group:
        messages.error(request, "Group not found.")
        return redirect(reverse("admin_profiles_participants"))

    track_key = (track or "").strip().lower()
    if track_key.startswith("m"):
        track_slug = "mentoras"
        track_label = "Mentoras"
        headers = MENTORAS_HEADERS
        column_types = MENTORAS_COLUMN_TYPES
        bool_cols = MENTORAS_BOOLEAN_COLS
        email_col = MENTORAS_EMAIL_COL
        acta_col = MENTORAS_ACTA_COL
        capacitacion_col = MENTORAS_CAPACITACION_COL
        progress_default_false_cols = MENTORAS_PROGRESS_DEFAULT_FALSE_COLS
        text_field = "mentoras_emails_text"
        rows_field = "mentoras_sheet_rows"
        build_rows = _build_mentoras_rows
    elif track_key.startswith("e"):
        track_slug = "emprendedoras"
        track_label = "Emprendedoras"
        headers = EMPRENDEDORAS_HEADERS
        column_types = EMPRENDEDORAS_COLUMN_TYPES
        bool_cols = EMPRENDEDORAS_BOOLEAN_COLS
        email_col = EMPRENDEDORAS_EMAIL_COL
        acta_col = EMPRENDEDORAS_ACTA_COL
        capacitacion_col = EMPRENDEDORAS_CAPACITACION_COL
        progress_default_false_cols = EMPRENDEDORAS_PROGRESS_DEFAULT_FALSE_COLS
        text_field = "emprendedoras_emails_text"
        rows_field = "emprendedoras_sheet_rows"
        build_rows = _build_emprendedoras_rows
    else:
        messages.error(request, "Invalid participant track.")
        return redirect(f"{reverse('admin_profiles_participants')}?group={group.number}")

    participant_list = GroupParticipantList.objects.filter(group=group).first()

    if request.method == "POST":
        is_async_save = request.headers.get("x-requested-with") == "XMLHttpRequest"
        action = (request.POST.get("action") or "save_sheet").strip()
        if action == "check_dropbox":
            target_track = "M" if track_slug == "mentoras" else "E"
            try:
                ok, summary = _run_dropbox_reconcile_for_group(
                    group_num=group.number,
                    track=target_track,
                    days_back=730,
                    max_pages=60,
                )
            except Exception as exc:
                ok, summary = False, f"Unexpected error starting Dropbox check: {exc}"
            if ok:
                messages.success(
                    request,
                    f"Dropbox check started for Group {group.number} {track_label}. {summary}",
                )
            else:
                messages.error(
                    request,
                    f"Dropbox check failed for Group {group.number} {track_label}: {summary}",
                )
            return redirect(
                reverse(
                    "admin_profiles_participants_track_sheet",
                    args=[group.number, track_slug],
                )
            )
        if action == "check_capacitacion":
            try:
                ok, summary = _run_wix_capacitacion_check_for_track(
                    group=group,
                    track_slug=track_slug,
                    headers=headers,
                    bool_cols=bool_cols,
                    email_col=email_col,
                    capacitacion_col=capacitacion_col,
                    text_field=text_field,
                    rows_field=rows_field,
                    build_rows=build_rows,
                )
            except Exception as exc:
                ok, summary = False, f"Unexpected error running Wix capacitacion check: {exc}"
            if ok:
                messages.success(
                    request,
                    f"Wix capacitacion check completed for Group {group.number} {track_label}. {summary}",
                )
            else:
                messages.error(
                    request,
                    f"Wix capacitacion check failed for Group {group.number} {track_label}: {summary}",
                )
            return redirect(
                reverse(
                    "admin_profiles_participants_track_sheet",
                    args=[group.number, track_slug],
                )
            )

        emails_raw = (request.POST.get("emails") or "").strip()
        valid_emails: list[str] = []
        invalid_emails: list[str] = []
        track_rows: list[list] = []

        if action == "build_from_emails":
            valid_emails, invalid_emails = _parse_email_list(emails_raw)
            track_rows = build_rows(group.number, valid_emails)
        else:
            sheet_raw = (request.POST.get("sheet_data") or "").strip()
            payload = []
            if sheet_raw:
                try:
                    payload = json.loads(sheet_raw)
                except json.JSONDecodeError:
                    if is_async_save:
                        return JsonResponse(
                            {"ok": False, "error": "Could not read sheet edits."},
                            status=400,
                        )
                    messages.error(request, "Could not read sheet edits. Please try again.")

            track_rows = _normalize_sheet_rows(payload, headers)
            track_rows = _coerce_bool_columns(track_rows, bool_cols)
            if not track_rows and emails_raw:
                valid_emails, invalid_emails = _parse_email_list(emails_raw)
                track_rows = build_rows(group.number, valid_emails)
            if not valid_emails:
                valid_emails = _emails_from_sheet_rows(track_rows, email_col)

        track_rows, repaired_track = _repair_progress_defaults_if_legacy(
            track_rows,
            progress_default_false_cols,
        )

        track_rows = _apply_contract_signed_to_rows(
            track_rows,
            email_col=email_col,
            acta_col=acta_col,
        )
        track_rows = _number_sheet_rows(track_rows, number_col=2)

        participant_obj, _ = GroupParticipantList.objects.get_or_create(group=group)
        updates: list[str] = []
        next_emails_text = "\n".join(valid_emails)
        if getattr(participant_obj, text_field) != next_emails_text:
            setattr(participant_obj, text_field, next_emails_text)
            updates.append(text_field)
        if getattr(participant_obj, rows_field) != track_rows:
            setattr(participant_obj, rows_field, track_rows)
            updates.append(rows_field)
        if updates:
            participant_obj.save(update_fields=updates + ["updated_at"])

        participant_emails = valid_emails or _emails_from_sheet_rows(track_rows, email_col)
        created_count, updated_count, unchanged_count = _mark_participated_yes(
            list(dict.fromkeys(participant_emails))
        )
        if is_async_save:
            return JsonResponse(
                {
                    "ok": True,
                    "rows": len(track_rows),
                    "track": track_slug,
                    "group": group.number,
                }
            )
        messages.success(
            request,
            (
                f"Saved {track_label} participants for Group {group.number}. "
                f"Rows: {len(track_rows)}. Profile participation set to Yes: "
                f"{created_count} new, {updated_count} changed, {unchanged_count} already yes."
            ),
        )
        if repaired_track:
            messages.info(
                request,
                "Auto-repaired legacy default checks in Website/Capacitacion columns.",
            )
        if invalid_emails:
            preview = ", ".join(invalid_emails[:8])
            suffix = "" if len(invalid_emails) <= 8 else ", ..."
            messages.warning(
                request,
                f"Ignored invalid emails ({len(invalid_emails)}): {preview}{suffix}",
            )
        return redirect(
            reverse(
                "admin_profiles_participants_track_sheet",
                args=[group.number, track_slug],
            )
        )

    rows: list[list] = []
    if participant_list:
        stored_rows = _normalize_sheet_rows(getattr(participant_list, rows_field, []), headers)
        stored_rows = _coerce_bool_columns(stored_rows, bool_cols)
        if stored_rows:
            rows = _number_sheet_rows(stored_rows, number_col=2)
        else:
            emails_seed = _norm_email_list(getattr(participant_list, text_field, ""))
            rows = _number_sheet_rows(
                build_rows(group.number, emails_seed),
                number_col=2,
            )

    rows, repaired_on_load = _repair_progress_defaults_if_legacy(
        rows,
        progress_default_false_cols,
    )
    if repaired_on_load:
        participant_obj, _ = GroupParticipantList.objects.get_or_create(group=group)
        if getattr(participant_obj, rows_field) != rows:
            setattr(participant_obj, rows_field, rows)
            participant_obj.save(update_fields=[rows_field, "updated_at"])

    rows = _apply_contract_signed_to_rows(
        rows,
        email_col=email_col,
        acta_col=acta_col,
    )
    emails_text = "\n".join(_emails_from_sheet_rows(rows, email_col))

    context = {
        "group": group,
        "track_slug": track_slug,
        "track_label": track_label,
        "sheet_headers": headers,
        "sheet_column_types": column_types,
        "sheet_rows": rows,
        "sheet_rows_json": json.dumps(rows),
        "emails_text": emails_text,
        "rows_count": len(rows),
    }
    return render(request, "admin_dash/profiles_participants_track_sheet.html", context)


@staff_member_required
def profiles_participants_download(request, group_num: int):
    group = _formgroup_safe_queryset().filter(number=group_num).first()
    if not group:
        messages.error(request, "Group not found.")
        return redirect(reverse("admin_profiles_participants"))

    participant_list = GroupParticipantList.objects.filter(group=group).first()
    if not participant_list:
        messages.error(request, f"No participants list found for Group {group.number}.")
        return redirect(f"{reverse('admin_profiles_participants')}?group={group.number}")

    mentoras_rows = _normalize_sheet_rows(
        getattr(participant_list, "mentoras_sheet_rows", []),
        MENTORAS_HEADERS,
    )
    emprendedoras_rows = _normalize_sheet_rows(
        getattr(participant_list, "emprendedoras_sheet_rows", []),
        EMPRENDEDORAS_HEADERS,
    )
    mentoras_rows = _coerce_bool_columns(mentoras_rows, MENTORAS_BOOLEAN_COLS)
    emprendedoras_rows = _coerce_bool_columns(emprendedoras_rows, EMPRENDEDORAS_BOOLEAN_COLS)
    if not mentoras_rows:
        mentoras_emails = _norm_email_list(participant_list.mentoras_emails_text or "")
        mentoras_rows = _build_mentoras_rows(group.number, mentoras_emails)
    if not emprendedoras_rows:
        emprendedoras_emails = _norm_email_list(participant_list.emprendedoras_emails_text or "")
        emprendedoras_rows = _build_emprendedoras_rows(group.number, emprendedoras_emails)

    mentoras_rows = _apply_contract_signed_to_rows(
        mentoras_rows,
        email_col=MENTORAS_EMAIL_COL,
        acta_col=MENTORAS_ACTA_COL,
    )
    emprendedoras_rows = _apply_contract_signed_to_rows(
        emprendedoras_rows,
        email_col=EMPRENDEDORAS_EMAIL_COL,
        acta_col=EMPRENDEDORAS_ACTA_COL,
    )

    workbook_bytes = _participants_workbook_bytes(
        mentoras_rows=_number_sheet_rows(mentoras_rows, number_col=2),
        emprendedoras_rows=_number_sheet_rows(emprendedoras_rows, number_col=2),
    )

    response = HttpResponse(
        workbook_bytes,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="G{group.number}_Participantes.xlsx"'
    )
    return response


@csrf_exempt
def dropbox_sign_webhook(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    raw_body = request.body or b""
    payload_digest = hashlib.sha256(raw_body).hexdigest()
    existing = DropboxSignWebhookEvent.objects.filter(payload_digest=payload_digest).first()
    if existing:
        return JsonResponse(
            {
                "ok": True,
                "duplicate": True,
                "event_id": existing.id,
                "marked": existing.marked_count,
            },
            status=200,
        )

    normalized = _normalize_dropbox_sign_payload(request)
    event_type = str(normalized.get("event_type") or "").strip()
    event_time = str(normalized.get("event_time") or "").strip()
    event_hash = str(normalized.get("event_hash") or "").strip()
    signature_request_id = str(normalized.get("signature_request_id") or "").strip()
    signature_title = str(normalized.get("signature_title") or "").strip()
    signer_emails = list(normalized.get("signer_emails") or [])
    signed_signer_emails = list(normalized.get("signed_signer_emails") or [])
    metadata = dict(normalized.get("metadata") or {})
    custom_fields = dict(normalized.get("custom_fields") or {})
    payload_json = dict(normalized.get("payload_json") or {})

    hash_ok = _dropbox_sign_hash_is_valid(event_time, event_type, event_hash)
    event = DropboxSignWebhookEvent.objects.create(
        event_type=event_type or "unknown",
        event_time=event_time,
        event_hash=event_hash,
        signature_request_id=signature_request_id,
        signer_emails_text=", ".join(signer_emails),
        payload_json={
            "event_type": event_type,
            "event_time": event_time,
            "signature_request_id": signature_request_id,
            "signature_title": signature_title,
            "signer_emails": signer_emails,
            "signed_signer_emails": signed_signer_emails,
            "metadata": metadata,
            "custom_fields": custom_fields,
            "payload": payload_json,
        },
        payload_digest=payload_digest,
        hash_verified=hash_ok,
        processed=False,
    )

    # Dropbox Sign callback tests should always return success.
    # Keep strict hash enforcement for real production events.
    if event_type in {"account_callback_test", "callback_test"}:
        event.processed = True
        event.process_note = (
            "Dropbox Sign callback test acknowledged."
            if hash_ok
            else "Dropbox Sign callback test acknowledged (hash not enforced for test event)."
        )
        event.save(update_fields=["processed", "process_note"])
        return HttpResponse("Hello API Event Received", status=200, content_type="text/plain")

    if event_type not in DBS_SIGN_EVENT_TYPES:
        event.processed = True
        event.process_note = f"Ignored event type: {event_type or 'unknown'}"
        event.save(update_fields=["processed", "process_note"])
        return JsonResponse({"ok": True, "ignored": True, "event_type": event_type}, status=200)

    if not hash_ok:
        event.process_note = "Rejected: event hash verification failed."
        event.save(update_fields=["process_note"])
        return JsonResponse({"ok": False, "error": "invalid_event_hash"}, status=403)

    resolved_emails: list[str] = list(signed_signer_emails)
    if not resolved_emails and event_type == "signature_request_all_signed":
        resolved_emails = list(signer_emails)
    if not resolved_emails and event_type == "signature_request_signed" and len(signer_emails) == 1:
        resolved_emails = list(signer_emails)
    if not resolved_emails:
        for identity_candidate in _candidate_identity_values(metadata, custom_fields):
            resolved_emails.extend(_emails_for_identity_value(identity_candidate))

    # Final dedupe + validation
    clean_emails = _clean_valid_emails(resolved_emails)

    if not clean_emails:
        event.processed = True
        event.process_note = "No signer emails resolved from webhook payload."
        event.save(update_fields=["processed", "process_note"])
        return JsonResponse({"ok": True, "marked": 0, "note": event.process_note}, status=200)

    signer_pool = set(_clean_valid_emails(signer_emails))
    if not signer_pool:
        signer_pool = set(clean_emails)

    now = timezone.now()
    marked_count = 0
    for email in clean_emails:
        row, created = ParticipantEmailStatus.objects.get_or_create(
            email=email,
            defaults={
                "participated": False,
            },
        )
        changed = False
        if not row.contract_signed:
            row.contract_signed = True
            changed = True
        if not row.contract_signed_at:
            row.contract_signed_at = now
            changed = True
        if signature_request_id and row.contract_signature_request_id != signature_request_id:
            row.contract_signature_request_id = signature_request_id
            changed = True
        if row.contract_source != "dropbox_sign":
            row.contract_source = "dropbox_sign"
            changed = True
        if changed or created:
            row.save(
                update_fields=[
                    "contract_signed",
                    "contract_signed_at",
                    "contract_signature_request_id",
                    "contract_source",
                    "updated_at",
                ]
            )
            marked_count += 1

    scoped_track, scoped_group_num, scope_reason = _resolve_dropbox_signature_scope(
        signature_title=signature_title,
        signer_pool=signer_pool,
    )
    matched_rows = 0
    changed_rows = 0
    scope_sheet_note = "Participant sheet scope not resolved."
    if scoped_track and scoped_group_num:
        matched_rows, changed_rows, scope_sheet_note = _mark_participant_sheet_acta_signed(
            group_num=scoped_group_num,
            track=scoped_track,
            signed_emails=clean_emails,
        )
    else:
        scope_sheet_note = scope_reason

    event.processed = True
    event.marked_count = marked_count
    event.signer_emails_text = ", ".join(clean_emails)
    base_note = (
        f"Marked contract signed for {marked_count} email(s)."
        if marked_count
        else "All resolved emails were already marked as signed."
    )
    if scoped_track and scoped_group_num:
        scope_note = (
            f" Scope={scoped_track}{scoped_group_num}. {scope_reason} "
            f"{scope_sheet_note}"
        )
    else:
        scope_note = f" Scope unresolved. {scope_reason}"
    event.process_note = f"{base_note}{scope_note}"
    event.save(update_fields=["processed", "marked_count", "signer_emails_text", "process_note"])
    return JsonResponse(
        {
            "ok": True,
            "event_id": event.id,
            "marked": marked_count,
            "emails": clean_emails,
            "scope_track": scoped_track,
            "scope_group_num": scoped_group_num,
            "scope_reason": scope_reason,
            "participant_rows_matched": matched_rows,
            "participant_rows_changed": changed_rows,
        },
        status=200,
    )


@staff_member_required
def profile_detail(request, identity_key: str):
    requested_key = _normalize_profile_key(identity_key)
    if not requested_key:
        return render(request, "admin_dash/profile_detail.html", {"profile": None})

    profiles = _build_profiles()
    profile = next((p for p in profiles if p["profile_key"] == requested_key), None)
    if profile:
        email_key = _email_status_key(profile.get("email"))
        status_row = None
        if email_key:
            status_row = ParticipantEmailStatus.objects.filter(email=email_key).first()
        participation_value = getattr(status_row, "participated", None)
        from_participant_list = email_key in _participant_list_email_keys() if email_key else False
        profile["participated"] = (
            bool(participation_value) if participation_value is not None else from_participant_list
        )
        profile["contract_signed"] = bool(getattr(status_row, "contract_signed", False))
        profile["contract_signed_at"] = getattr(status_row, "contract_signed_at", None)
        profile["contract_signature_request_id"] = getattr(
            status_row, "contract_signature_request_id", ""
        )
    return render(request, "admin_dash/profile_detail.html", {"profile": profile})
