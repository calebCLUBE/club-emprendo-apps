import csv
import io
import json
import re
import zipfile
from collections import defaultdict
from io import BytesIO
from xml.sax.saxutils import escape

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_datetime

from .models import (
    Answer,
    Application,
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
    return raw in {"1", "true", "yes", "y", "si", "sí", "checked", "on"}


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
        return getattr(app.form.group, "number", None)
    return _group_number_from_slug(getattr(app.form, "slug", ""))


def _latest_apps_by_email_for_group_track(group_num: int, track: str) -> dict[str, dict]:
    target_track = (track or "").strip().upper()
    if target_track not in {"E", "M"}:
        return {}

    apps = (
        Application.objects.select_related("form", "form__group")
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
            True,
            True,
            True,
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
            True,
            True,
            True,
            False,
            False,
            True,
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
        Application.objects.select_related("form", "form__group")
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
        group_num = getattr(getattr(latest_app, "form", None), "group_id", None)
        if group_num:
            group_num = getattr(latest_app.form.group, "number", None)
        else:
            group_num = _group_number_from_slug(getattr(latest_app.form, "slug", ""))
        if group_num:
            target_groups.add(group_num)

    latest_by_group_track, latest_by_group, rows_by_file_id = _build_grading_lookup(target_groups)

    profiles = []
    for root, app_ids in clusters.items():
        latest_payload = app_data_by_id[latest_app_id_by_cluster[root]]
        app = latest_payload["app"]
        answer_map = latest_payload["answer_map"]

        group_num = getattr(getattr(app, "form", None), "group_id", None)
        if group_num:
            group_num = getattr(app.form.group, "number", None)
        else:
            group_num = _group_number_from_slug(getattr(app.form, "slug", ""))
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


@staff_member_required
def profiles_list(request):
    profiles = _build_profiles()
    participation_map = {
        _email_status_key(row.email): row.participated
        for row in ParticipantEmailStatus.objects.only("email", "participated")
    }
    participant_list_email_keys = _participant_list_email_keys()
    for email_key in participant_list_email_keys:
        participation_map[email_key] = True
    for profile in profiles:
        profile_email_key = _email_status_key(profile.get("email"))
        profile["participated"] = bool(participation_map.get(profile_email_key, False))

    query = (request.GET.get("q") or "").strip()
    query_lower = query.lower()
    group_filter = (request.GET.get("group") or "").strip()
    status_filter = (request.GET.get("grading") or "").strip()
    view_mode = (request.GET.get("view") or "list").strip().lower()
    show_sheet = view_mode == "sheet"
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

    sheet_headers: list[str] = []
    sheet_rows: list[list[str]] = []
    if show_sheet:
        sheet_headers = [
            "Cedula",
            "Email",
            "Applicant",
            "Group",
            "Form",
            "Calificacion status",
            "Score",
            "Participated",
            "Profile URL",
        ]
        for p in filtered:
            group_label = f"Group {p['group_num']}" if p.get("group_num") else "Ungrouped"
            form_label = str(p.get("form_slug") or "—")
            track = str(p.get("track") or "").strip()
            if track and track != "—":
                form_label = f"{form_label} · {track}"
            profile_url = reverse("admin_profile_detail", args=[p["profile_key"]])
            sheet_rows.append(
                [
                    str(p.get("identity_display") or ""),
                    str(p.get("email") or ""),
                    str(p.get("applicant_name") or ""),
                    group_label,
                    form_label,
                    str(p.get("calificacion_status") or ""),
                    str(p.get("overall_score") or "—"),
                    "Yes" if p.get("participated") else "No",
                    profile_url,
                ]
            )

    group_options = sorted(
        {p["group_num"] for p in profiles if p["group_num"] is not None},
        reverse=True,
    )

    context = {
        "profiles": filtered,
        "query": query,
        "group_filter": group_filter,
        "status_filter": status_filter,
        "group_options": group_options,
        "total_profiles": len(profiles),
        "visible_profiles": len(filtered),
        "graded_profiles": sum(1 for p in profiles if p["is_graded"]),
        "participated_profiles": sum(1 for p in profiles if p["participated"]),
        "show_sheet": show_sheet,
        "profiles_sheet_headers": sheet_headers,
        "profiles_sheet_rows": sheet_rows,
    }
    return render(request, "admin_dash/profiles_list.html", context)


@staff_member_required
def profiles_participants(request):
    groups = list(FormGroup.objects.order_by("number"))
    group_raw = (request.GET.get("group") or request.POST.get("group") or "").strip()
    selected_group = None
    participant_list = None

    if group_raw.isdigit():
        selected_group = FormGroup.objects.filter(number=int(group_raw)).first()
        if selected_group:
            participant_list = GroupParticipantList.objects.filter(group=selected_group).first()

    if request.method == "POST":
        posted_group = (request.POST.get("group") or "").strip()
        if not posted_group.isdigit():
            messages.error(request, "Please select a valid group.")
            return redirect(reverse("admin_profiles_participants"))

        selected_group = FormGroup.objects.filter(number=int(posted_group)).first()
        if not selected_group:
            messages.error(request, "Selected group does not exist.")
            return redirect(reverse("admin_profiles_participants"))

        action = (request.POST.get("action") or "save_sheet").strip()
        mentoras_raw = (request.POST.get("mentoras_emails") or "").strip()
        emprendedoras_raw = (request.POST.get("emprendedoras_emails") or "").strip()
        mentoras_valid: list[str] = []
        emprendedoras_valid: list[str] = []
        mentoras_invalid: list[str] = []
        emprendedoras_invalid: list[str] = []
        mentoras_rows: list[list] = []
        emprendedoras_rows: list[list] = []

        if action == "build_from_emails":
            mentoras_valid, mentoras_invalid = _parse_email_list(mentoras_raw)
            emprendedoras_valid, emprendedoras_invalid = _parse_email_list(emprendedoras_raw)
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
                mentoras_rows = _build_mentoras_rows(selected_group.number, mentoras_valid)
            if not emprendedoras_rows and emprendedoras_raw:
                emprendedoras_valid, emprendedoras_invalid = _parse_email_list(emprendedoras_raw)
                emprendedoras_rows = _build_emprendedoras_rows(selected_group.number, emprendedoras_valid)

            if not mentoras_valid:
                mentoras_valid = _emails_from_sheet_rows(mentoras_rows, MENTORAS_EMAIL_COL)
            if not emprendedoras_valid:
                emprendedoras_valid = _emails_from_sheet_rows(
                    emprendedoras_rows,
                    EMPRENDEDORAS_EMAIL_COL,
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
                f"Saved participants for Group {selected_group.number}. "
                f"Mentoras: {len(mentoras_rows)} rows · Emprendedoras: {len(emprendedoras_rows)} rows. "
                f"Profile participation set to Yes: {participation_created} new, "
                f"{participation_updated} changed, {participation_unchanged} already yes."
            ),
        )
        invalid_total = len(mentoras_invalid) + len(emprendedoras_invalid)
        if invalid_total:
            invalid_preview = mentoras_invalid + emprendedoras_invalid
            preview = ", ".join(invalid_preview[:8])
            suffix = "" if invalid_total <= 8 else ", ..."
            messages.warning(
                request,
                f"Ignored invalid emails ({invalid_total}): {preview}{suffix}",
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

    mentoras_emails = _emails_from_sheet_rows(mentoras_rows, MENTORAS_EMAIL_COL)
    emprendedoras_emails = _emails_from_sheet_rows(emprendedoras_rows, EMPRENDEDORAS_EMAIL_COL)
    has_list = bool(participant_list and (mentoras_rows or emprendedoras_rows))

    context = {
        "groups": groups,
        "selected_group": selected_group,
        "participant_list": participant_list,
        "mentoras_headers": MENTORAS_HEADERS,
        "emprendedoras_headers": EMPRENDEDORAS_HEADERS,
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
def profiles_participants_download(request, group_num: int):
    group = FormGroup.objects.filter(number=group_num).first()
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


@staff_member_required
def profile_detail(request, identity_key: str):
    requested_key = _normalize_profile_key(identity_key)
    if not requested_key:
        return render(request, "admin_dash/profile_detail.html", {"profile": None})

    profiles = _build_profiles()
    profile = next((p for p in profiles if p["profile_key"] == requested_key), None)
    if profile:
        email_key = _email_status_key(profile.get("email"))
        participation_value = None
        if email_key:
            participation_value = (
                ParticipantEmailStatus.objects.filter(email=email_key)
                .values_list("participated", flat=True)
                .first()
            )
        from_participant_list = email_key in _participant_list_email_keys() if email_key else False
        profile["participated"] = (
            bool(participation_value) if participation_value is not None else from_participant_list
        )
    return render(request, "admin_dash/profile_detail.html", {"profile": profile})
