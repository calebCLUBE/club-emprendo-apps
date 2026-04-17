# applications/admin_views.py
import openai
import calendar
import csv
import io
import json
import re
import zipfile
from typing import List, Tuple
from urllib.parse import urlparse
from xml.sax.saxutils import escape
from django.core.mail import get_connection
import time
from datetime import datetime, date
from applications.grading import grade_from_answers
from openai import OpenAI
from django.core.files.base import ContentFile
import threading
import traceback
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.files.storage import default_storage
from django.core.mail import EmailMultiAlternatives, get_connection
from django.core.cache import cache
from django.db import transaction, DatabaseError
from django.db.models import Model, Count, Q
from django.db.models.functions import Lower
from django.http import HttpResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
import os
from django.utils import timezone
from applications.grader_e import grade_single_row, grade_from_dataframe
from django.db import connection
from applications.grader_e import grade_from_dataframe as grade_e_df
from applications.grader_m import grade_from_dataframe as grade_m_df
from applications.drive_sync import (
    ensure_group_drive_tree,
    sync_generated_csv_artifact,
    sync_group_track_responses_csv,
)
import math
from django.db import connection
from applications.models import (
    Application,
    Answer,
    Choice,
    FormDefinition,
    FormGroup,
    GroupParticipantList,
    Section,
    Question,
    GradedFile,
    GradingJob,
    PairingJob,
    scheduled_group_open_state,
)
import logging

logger = logging.getLogger(__name__)
MASTER_SLUGS = ["E_A1", "E_A2", "M_A1", "M_A2"]
GROUP_SLUG_RE = re.compile(r"^G(?P<num>\d+)_(?P<master>E_A1|E_A2|M_A1|M_A2)$")
GRADED_GROUP_RE = re.compile(r"^G(?P<num>\d+)_")
TEST_E_A2_SLUG = "TEST_E_A2"
TEST_M_A2_SLUG = "TEST_M_A2"
A2_FORM_RE = re.compile(r"^(G\d+_)?(E_A2|M_A2)$")
TEST_A2_FORM_RE = re.compile(r"^TEST_(E_A2|M_A2)$")
REMINDER_LOCK_TTL_SECONDS = 60 * 60
AUTO_REMINDER_CHECK_THROTTLE_SECONDS = 45
TRACK_COMPLETION_FILTER_ALL = ""
TRACK_COMPLETION_FILTER_A1_ONLY = "a1_only"
TRACK_COMPLETION_FILTER_A1_A2 = "a1_a2"
TRACK_COMPLETION_FILTER_EXCLUDE_A2_ONLY = "exclude_a2_only"
IDENTITY_EMAIL_SLUGS = {"email", "correo", "correo_electronico"}
IDENTITY_DOCUMENT_SLUGS = {
    "cedula",
    "id_number",
    "documento",
    "document_number",
    "numero_de_documento",
    "numerodedocumento",
}
PREVIOUS_APPLICATION_DOC_SLUGS = (
    "cedula",
    "id_number",
    "documento",
    "document_number",
    "numero_de_documento",
    "numerodedocumento",
)
PERCENT_SCORE_HEADERS = {
    "score",
    "totalscore",
    "totalpts",
    "overallscore",
}
RECRUITMENT_POOL_SOURCES = {
    "april_recruitment": {
        "label": "April Recruitment",
        "group_num": 8,
    },
}


def _reminder_lock_key(form_slug: str) -> str:
    return f"admin:reminders:lock:{(form_slug or '').strip().lower()}"

@staff_member_required
def download_graded_csv(request, graded_file_id: int):
    """
    Download a graded CSV stored in the database.
    """
    gf = get_object_or_404(GradedFile, id=graded_file_id)

    response = HttpResponse(
        gf.csv_text,
        content_type="text/csv; charset=utf-8",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="{gf.form_slug}_app_{gf.application_id}_graded.csv"'
    )

    return response

@staff_member_required
@require_POST
def grading_upload_test_csv(request):
    """
    Upload CSV into sandbox A2 forms only:
      - role=E -> TEST_E_A2
      - role=M -> TEST_M_A2

    This is independent from real group applications.
    """
    role = (request.POST.get("role") or "E").strip().upper()
    sandbox_slug = "TEST_E_A2" if role == "E" else "TEST_M_A2"

    fd = FormDefinition.objects.filter(slug=sandbox_slug).first()
    if not fd:
        messages.error(
            request,
            f"Sandbox form {sandbox_slug} does not exist. Create it (or clone it) first."
        )
        return _redirect_back_to_grading(request)

    f = request.FILES.get("csv_file")
    if not f:
        messages.error(request, "No CSV file uploaded.")
        return _redirect_back_to_grading(request)

    try:
        raw = f.read().decode("utf-8-sig")
    except Exception:
        raw = f.read().decode("latin-1")

    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames:
        messages.error(request, "CSV appears to have no header row.")
        return _redirect_back_to_grading(request)

    qmap = {q.slug: q for q in fd.questions.all()}
    created = 0

    with transaction.atomic():
        for row in reader:
            name = (row.get("name") or row.get("full_name") or "").strip()
            email = (row.get("email") or "").strip()

            app = Application.objects.create(
                form=fd,
                name=name,
                email=email,
            )

            for col, val in row.items():
                if col not in qmap:
                    continue
                v = (val or "").strip()
                Answer.objects.create(
                    application=app,
                    question=qmap[col],
                    value=v,
                )
            created += 1

    messages.success(request, f"Imported {created} submissions into sandbox form {sandbox_slug}.")
    return _redirect_back_to_grading(request)


def _redirect_back_to_grading(request):
    group = (request.GET.get("group") or "").strip()
    url = reverse("admin_grading_home")
    if group:
        url = f"{url}?group={group}"
    return redirect(url)

def _job_log(job: GradingJob, line: str):
    job.log_text = (job.log_text or "") + line + "\n"
    job.save(update_fields=["log_text", "updated_at"])


def _track_from_form_slug(form_slug: str) -> str | None:
    slug = (form_slug or "").strip().upper()
    if slug.endswith("E_A2"):
        return "E"
    if slug.endswith("M_A2"):
        return "M"
    return None


def _emails_from_participant_sheet_rows(rows) -> set[str]:
    if not isinstance(rows, list):
        return set()

    out: set[str] = set()
    for row in rows:
        if not isinstance(row, list):
            continue
        for cell in row:
            raw = (str(cell or "").strip().lower())
            if not raw or "@" not in raw:
                continue
            if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", raw):
                out.add(raw)
    return out


def _month_name_to_number(month_name: str) -> int | None:
    raw = (month_name or "").strip().lower()
    if not raw:
        return None

    month_num = RESPOND_BY_MONTH_TO_NUM.get(raw)
    if month_num:
        return int(month_num)

    month_map_en = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }
    return month_map_en.get(raw)


def _safe_add_months(base_date: date, months: int) -> date:
    month_index = (base_date.month - 1) + int(months)
    year = base_date.year + (month_index // 12)
    month = (month_index % 12) + 1
    day = min(base_date.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _group_start_date(group: FormGroup | None) -> date | None:
    if not group:
        return None
    try:
        day = int(getattr(group, "start_day", 0) or 0)
        year = int(getattr(group, "year", 0) or 0)
    except (TypeError, ValueError):
        return None

    month_num = _month_name_to_number(getattr(group, "start_month", ""))
    if not day or not year or not month_num:
        return None
    try:
        return date(year, month_num, day)
    except ValueError:
        return None


def _is_group_currently_active(group: FormGroup | None, today: date | None = None) -> bool:
    start_date = _group_start_date(group)
    if not start_date:
        return False
    today = today or timezone.localdate()
    active_until = _safe_add_months(start_date, 3)
    return start_date <= today < active_until


def _active_participant_emails_for_track(track: str | None) -> set[str]:
    wanted = (track or "").strip().upper()
    include_e = wanted in {"", "E"}
    include_m = wanted in {"", "M"}

    out: set[str] = set()
    participant_lists = GroupParticipantList.objects.select_related("group").only(
        "group__start_day",
        "group__start_month",
        "group__year",
        "mentoras_emails_text",
        "emprendedoras_emails_text",
        "mentoras_sheet_rows",
        "emprendedoras_sheet_rows",
    )
    today = timezone.localdate()
    for row in participant_lists:
        if not _is_group_currently_active(getattr(row, "group", None), today=today):
            continue

        if include_e:
            emps = _norm_email_list(getattr(row, "emprendedoras_emails_text", ""))
            if emps:
                out.update(emps)
            else:
                out.update(
                    _emails_from_participant_sheet_rows(getattr(row, "emprendedoras_sheet_rows", []))
                )

        if include_m:
            mentors = _norm_email_list(getattr(row, "mentoras_emails_text", ""))
            if mentors:
                out.update(mentors)
            else:
                out.update(
                    _emails_from_participant_sheet_rows(getattr(row, "mentoras_sheet_rows", []))
                )
    return out


def _active_participant_emails_for_form_slug(form_slug: str) -> set[str]:
    return _active_participant_emails_for_track(_track_from_form_slug(form_slug))


def _normalize_document_id(raw_value: str) -> str:
    value = str(raw_value or "").strip().lower()
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", "", value)


def _group_number_from_form(form_slug: str, form_def: FormDefinition | None = None) -> int | None:
    if form_def and getattr(form_def, "group_id", None):
        try:
            return int(getattr(form_def.group, "number"))
        except Exception:
            return None
    match = re.match(r"^G(?P<num>\d+)_", (form_slug or "").strip(), flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group("num"))
    except (TypeError, ValueError):
        return None


def _mentor_dual_applicant_identifiers(
    mentor_form_slug: str,
    mentor_form: FormDefinition | None = None,
) -> tuple[set[str], set[str]]:
    if not (mentor_form_slug or "").strip().upper().endswith("M_A2"):
        return set(), set()

    group_num = _group_number_from_form(mentor_form_slug, mentor_form)
    if not group_num:
        return set(), set()

    empr_form = FormDefinition.objects.filter(slug=f"G{group_num}_E_A2").first()
    if not empr_form:
        return set(), set()

    empre_apps = (
        Application.objects
        .filter(form=empr_form)
        .prefetch_related("answers__question")
        .order_by("-created_at", "-id")
    )

    email_keys: set[str] = set()
    doc_keys: set[str] = set()
    for app in empre_apps:
        app_email = (app.email or "").strip().lower()
        if app_email:
            email_keys.add(app_email)

        answer_map = {
            getattr(ans.question, "slug", ""): (ans.value or "")
            for ans in app.answers.all()
        }
        if not app_email:
            fallback_email = (answer_map.get("email") or "").strip().lower()
            if fallback_email:
                email_keys.add(fallback_email)

        doc_raw = (
            answer_map.get("cedula")
            or answer_map.get("id_number")
            or ""
        )
        doc_key = _normalize_document_id(doc_raw)
        if doc_key:
            doc_keys.add(doc_key)

    return email_keys, doc_keys


def _application_answer_map(app: Application) -> dict[str, str]:
    return {
        (getattr(getattr(ans, "question", None), "slug", "") or "").strip().lower(): (ans.value or "")
        for ans in app.answers.all()
    }


def _application_doc_id_key(answer_map: dict[str, str]) -> str:
    for slug in PREVIOUS_APPLICATION_DOC_SLUGS:
        value = answer_map.get(slug, "")
        normalized = _normalize_document_id(value)
        if normalized:
            return normalized
    return ""


def _prior_application_ids_for_track(
    track: str,
    current_apps: list[Application],
) -> set[int]:
    wanted = (track or "").strip().upper()
    if wanted not in {"E", "M"} or not current_apps:
        return set()

    historical_apps = list(
        Application.objects.filter(
            Q(form__slug__iendswith=f"{wanted}_A1") | Q(form__slug__iendswith=f"{wanted}_A2")
        )
        .prefetch_related("answers__question")
        .order_by("created_at", "id")
    )
    if not historical_apps:
        return set()

    email_earliest: dict[str, tuple[datetime, int]] = {}
    doc_earliest: dict[str, tuple[datetime, int]] = {}
    app_keys: dict[int, tuple[str, str]] = {}

    for app in historical_apps:
        when = (app.created_at, int(app.id))
        email_key = (app.email or "").strip().lower()
        answer_map = _application_answer_map(app)
        doc_key = _application_doc_id_key(answer_map)
        app_keys[app.id] = (email_key, doc_key)

        if email_key:
            prev = email_earliest.get(email_key)
            if prev is None or when < prev:
                email_earliest[email_key] = when
        if doc_key:
            prev = doc_earliest.get(doc_key)
            if prev is None or when < prev:
                doc_earliest[doc_key] = when

    out: set[int] = set()
    for app in current_apps:
        key_now = (app.created_at, int(app.id))
        email_key, doc_key = app_keys.get(app.id, ("", ""))

        has_previous = False
        if email_key:
            first_seen = email_earliest.get(email_key)
            has_previous = bool(first_seen and first_seen < key_now)

        if not has_previous and doc_key:
            first_seen = doc_earliest.get(doc_key)
            has_previous = bool(first_seen and first_seen < key_now)

        if has_previous:
            out.add(app.id)

    return out


@staff_member_required
def grading_job_status(request, job_id: int):
    job = get_object_or_404(GradingJob, id=job_id)

    # super simple auto-refresh every 2 seconds
    return render(request, "admin_dash/grading_job_status.html", {"job": job})
def _run_grade_job(job_id: int):
    job = GradingJob.objects.get(id=job_id)
    job.status = GradingJob.STATUS_RUNNING
    job.save(update_fields=["status", "updated_at"])

    try:
        _job_log(job, "✅ Starting grading job...")
        priority_emails = set(_norm_email_list(job.priority_emails_text))
        if priority_emails:
            _job_log(
                job,
                f"⭐ Priority status override enabled for {len(priority_emails)} email(s).",
            )
        active_participant_emails = _active_participant_emails_for_form_slug(job.form_slug)
        if active_participant_emails:
            _job_log(
                job,
                f"🧷 Active participant filter enabled for {len(active_participant_emails)} email(s).",
            )

        # ----------------------------------
        # Validate form type
        # ----------------------------------
        if not (job.form_slug.endswith("E_A2") or job.form_slug.endswith("M_A2")):
            raise RuntimeError(f"Unsupported form type: {job.form_slug}")

        fd = FormDefinition.objects.get(slug=job.form_slug)
        dual_applicant_emails: set[str] = set()
        dual_applicant_doc_ids: set[str] = set()
        if job.form_slug.endswith("M_A2"):
            dual_applicant_emails, dual_applicant_doc_ids = _mentor_dual_applicant_identifiers(
                mentor_form_slug=job.form_slug,
                mentor_form=fd,
            )
            if dual_applicant_emails or dual_applicant_doc_ids:
                _job_log(
                    job,
                    (
                        "🔁 Dual-applicant filter enabled for mentor grading: "
                        f"{len(dual_applicant_emails)} email(s), "
                        f"{len(dual_applicant_doc_ids)} document id(s)."
                    ),
                )

        apps = (
            Application.objects
            .filter(form=fd)
            .prefetch_related("answers__question")
            .order_by("created_at", "id")
        )

        if not apps.exists():
            raise RuntimeError("No applications to grade.")

        app_list = list(apps)
        previous_application_ids = _prior_application_ids_for_track(
            "E" if job.form_slug.endswith("E_A2") else "M",
            app_list,
        )
        if previous_application_ids:
            _job_log(
                job,
                f"🕘 Previous-application status enabled for {len(previous_application_ids)} application(s).",
            )

        _job_log(job, f"📦 Building master dataset ({len(app_list)} applications)")

        # ----------------------------------
        # BUILD MASTER DATAFRAME (MATCHES MASTER CSV)
        # ----------------------------------
        questions = list(
            fd.questions
            .filter(active=True)
            .order_by("position", "id")
        )

        headers = [
            "created_at",
            "application_id",
            "full_name",
            "email",
        ] + [q.slug for q in questions]

        rows = []
        for app in app_list:
            answer_map = {
                a.question.slug: (a.value or "")
                for a in app.answers.all()
            }

            rows.append([
                app.created_at.isoformat(),
                app.id,
                app.name or "",
                app.email or "",
            ] + [answer_map.get(q.slug, "") for q in questions])

        import pandas as pd
        master_df = pd.DataFrame(rows, columns=headers)

        _job_log(job, "🤖 Running grader on full dataset")

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not set")

        client = OpenAI(api_key=api_key)

        # ----------------------------------
        # Run correct grader
        # ----------------------------------
        if job.form_slug.endswith("E_A2"):
            from applications.grader_e import grade_from_dataframe
            graded_df = grade_from_dataframe(
                master_df,
                client,
                log_fn=lambda msg: _job_log(job, msg),
                priority_emails=priority_emails,
                active_participant_emails=active_participant_emails,
                previous_application_ids=previous_application_ids,
            )

        else:  # M_A2
            from applications.grader_m import grade_from_dataframe
            graded_df = grade_from_dataframe(
                master_df,
                client,
                log_fn=lambda msg: _job_log(job, msg),
                priority_emails=priority_emails,
                active_participant_emails=active_participant_emails,
                previous_application_ids=previous_application_ids,
                dual_applicant_emails=dual_applicant_emails,
                dual_applicant_doc_ids=dual_applicant_doc_ids,
            )

        if graded_df is None or graded_df.empty:
            raise RuntimeError("Grader returned empty output")

        normalized_cols = {
            _normalized_header_key(col): col
            for col in graded_df.columns
        }
        if "status" not in normalized_cols:
            fallback_status = ""
            rec_col = normalized_cols.get("recommendation")
            if rec_col:
                fallback_status = graded_df[rec_col].fillna("").astype(str)
            graded_df.insert(0, "Status", fallback_status)
            _job_log(job, "⚠️ Grader output had no status column. Inserted fallback Status column before saving.")

        # ----------------------------------
        # STORE GRADED FILE (keep latest per form slug)
        # ----------------------------------
        csv_text = graded_df.to_csv(index=False)

        existing_qs = GradedFile.objects.filter(form_slug=job.form_slug)
        replaced_count = existing_qs.count()
        with transaction.atomic():
            existing_qs.delete()
            gf = GradedFile.objects.create(
                form_slug=job.form_slug,
                csv_text=csv_text,
            )

        drive_sync = sync_generated_csv_artifact(job.form_slug, csv_text)
        _job_log(job, f"☁️ Drive sync: {drive_sync.status} - {drive_sync.detail}")

        _job_log(job, f"📄 Saved graded file (id={gf.id}, bytes={len(csv_text)})")
        if replaced_count:
            _job_log(job, f"♻️ Removed {replaced_count} older graded file(s) for {job.form_slug}")

        _job_log(job, "✅ Grading completed successfully")

        job.status = GradingJob.STATUS_DONE
        job.save(update_fields=["status", "updated_at"])

    except Exception:
        _job_log(job, "❌ Grading failed")
        _job_log(job, traceback.format_exc())
        job.status = GradingJob.STATUS_FAILED
        job.save(update_fields=["status", "updated_at"])


@staff_member_required
def download_graded_csv(request, graded_file_id: int):
    gf = get_object_or_404(GradedFile, id=graded_file_id)

    response = HttpResponse(
        gf.csv_text,
        content_type="text/csv; charset=utf-8"
    )
    response["Content-Disposition"] = (
        f'attachment; filename="{gf.form_slug}_graded.csv"'
    )
    return response


@staff_member_required
def download_graded_excel(request, graded_file_id: int):
    gf = get_object_or_404(GradedFile, id=graded_file_id)
    headers, rows = _csv_text_to_grid(gf.csv_text or "")
    if not headers:
        headers = [""]
        rows = []

    percent_col_indexes = {
        idx
        for idx, header in enumerate(headers)
        if _normalized_header_key(header) in PERCENT_SCORE_HEADERS
    }
    workbook_bytes = _graded_workbook_bytes(
        headers=headers,
        rows=rows,
        percent_col_indexes=percent_col_indexes,
        sheet_name=gf.form_slug or "Sheet",
    )
    response = HttpResponse(
        workbook_bytes,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="{gf.form_slug}_graded.xlsx"'
    )
    return response

@staff_member_required
@require_POST
def start_grading_job(request, form_slug: str):
    priority_emails = _norm_email_list(request.POST.get("priority_emails", ""))
    job = GradingJob.objects.create(
        form_slug=form_slug,
        status=GradingJob.STATUS_PENDING,
        log_text="Queued...\n",
        priority_emails_text="\n".join(priority_emails),
    )

    t = threading.Thread(target=_run_grade_job, args=(job.id,), daemon=True)
    t.start()

    return redirect("admin_grading_job_status", job_id=job.id)
# ============================
# Emparejamiento (Pairing)
# ============================

PAIR_HEADERS = [
    "emprendedora_name",
    "mentora_name",
    "emprendedora_email",
    "mentora_email",
    "matching_availability",
    "matching_industry",
    "emprendedora_industry",
    "mentora_industry",
    "matching_country",
    "business_age_matching",
    "expertise_growth_matching",
    "motivation_challenge_match",
]

# ---- availability mapping ----
DAY_MAP_ES_TO_EN = {
    "lunes": "mon",
    "martes": "tue",
    "miercoles": "wed",
    "miércoles": "wed",
    "jueves": "thu",
    "viernes": "fri",
    "sabado": "sat",
    "sábado": "sat",
    "domingo": "sun",
}
TIME_MAP_ES_TO_EN = {
    "manana": "morning",
    "mañana": "morning",
    "tarde": "afternoon",
    "noche": "night",
}


def _norm_email_list(s: str) -> list[str]:
    if not s:
        return []
    # Accept one email per line (paste from docs/sheets), commas, or semicolons.
    raw_parts = re.split(r"[\n\r,;]+", s)
    out: list[str] = []
    seen: set[str] = set()
    for part in raw_parts:
        email = (part or "").strip().lower()
        if not email or email in seen:
            continue
        seen.add(email)
        out.append(email)
    return out


def _normalize_csv_data_rows(rows, width: int) -> list[list[str]]:
    normalized: list[list[str]] = []
    if width <= 0:
        return normalized
    if not isinstance(rows, list):
        return normalized
    for raw_row in rows:
        if not isinstance(raw_row, list):
            continue
        row: list[str] = []
        for i in range(width):
            cell = raw_row[i] if i < len(raw_row) else ""
            row.append("" if cell is None else str(cell))
        normalized.append(row)
    return normalized


def _csv_text_to_grid(csv_text: str) -> tuple[list[str], list[list[str]]]:
    if not csv_text:
        return [], []
    parsed = list(csv.reader(io.StringIO(csv_text)))
    if not parsed:
        return [], []
    headers = ["" if cell is None else str(cell) for cell in parsed[0]]
    width = len(headers)
    rows = _normalize_csv_data_rows(parsed[1:], width)
    return headers, rows


def _grid_to_csv_text(headers: list[str], rows: list[list[str]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["" if h is None else str(h) for h in headers])
    for row in _normalize_csv_data_rows(rows, len(headers)):
        writer.writerow(row)
    return buf.getvalue()


def _normalized_header_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _excel_col_name(col_num: int) -> str:
    name = ""
    n = max(1, int(col_num))
    while n:
        n, rem = divmod(n - 1, 26)
        name = chr(65 + rem) + name
    return name


def _parse_percent_decimal(value: str) -> float | None:
    raw = str(value or "").strip()
    if not raw or raw.upper() == "NA":
        return None

    had_percent = raw.endswith("%")
    if had_percent:
        raw = raw[:-1].strip()
    raw = raw.replace(",", "")
    try:
        num = float(raw)
    except (TypeError, ValueError):
        return None

    if had_percent:
        return num / 100.0
    if -1.0 <= num <= 1.0:
        return num
    return num / 100.0


def _xlsx_cell_xml(col: int, row: int, value, style_id: int = 0, number_value: float | None = None) -> str:
    ref = f"{_excel_col_name(col)}{row}"
    if number_value is not None:
        return f'<c r="{ref}" s="{style_id}"><v>{number_value}</v></c>'

    text = "" if value is None else str(value)
    text_escaped = escape(text)
    preserve = ' xml:space="preserve"' if text.startswith(" ") or text.endswith(" ") or ("\n" in text) else ""
    return (
        f'<c r="{ref}" s="{style_id}" t="inlineStr">'
        f"<is><t{preserve}>{text_escaped}</t></is>"
        "</c>"
    )


def _graded_sheet_xml(headers: list[str], rows: list[list[str]], percent_col_indexes: set[int]) -> bytes:
    total_cols = max(1, len(headers))

    header_cells = "".join(
        _xlsx_cell_xml(col=i + 1, row=1, value=headers[i], style_id=1)
        for i in range(total_cols)
    )
    body_rows: list[str] = [f'<row r="1" ht="22.5" customHeight="1">{header_cells}</row>']

    for r_idx, row_values in enumerate(rows, start=2):
        padded = list(row_values) + [""] * max(0, total_cols - len(row_values))
        row_cells: list[str] = []
        for c_idx, cell_value in enumerate(padded[:total_cols]):
            col_index = c_idx
            if col_index in percent_col_indexes:
                pct_val = _parse_percent_decimal(cell_value)
                if pct_val is not None:
                    row_cells.append(
                        _xlsx_cell_xml(
                            col=c_idx + 1,
                            row=r_idx,
                            value=cell_value,
                            style_id=2,
                            number_value=pct_val,
                        )
                    )
                    continue
            row_cells.append(_xlsx_cell_xml(col=c_idx + 1, row=r_idx, value=cell_value, style_id=0))
        body_rows.append(f'<row r="{r_idx}">{"".join(row_cells)}</row>')

    auto_filter_ref = f"A1:{_excel_col_name(total_cols)}1"
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetViews>"
        '<sheetView workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        "</sheetView>"
        "</sheetViews>"
        '<sheetFormatPr defaultRowHeight="15"/>'
        f'<sheetData>{"".join(body_rows)}</sheetData>'
        f'<autoFilter ref="{auto_filter_ref}"/>'
        "</worksheet>"
    )
    return xml.encode("utf-8")


def _graded_workbook_bytes(headers: list[str], rows: list[list[str]], percent_col_indexes: set[int], sheet_name: str) -> bytes:
    safe_sheet_name = (sheet_name or "Sheet").strip() or "Sheet"
    safe_sheet_name = re.sub(r"[:\\/?*\[\]]", "_", safe_sheet_name)[:31]

    sheet_xml = _graded_sheet_xml(headers=headers, rows=rows, percent_col_indexes=percent_col_indexes)

    bio = io.BytesIO()
    with zipfile.ZipFile(bio, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)

        workbook_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets><sheet name="{escape(safe_sheet_name)}" sheetId="1" r:id="rId1"/></sheets>'
            "</workbook>"
        )
        zf.writestr("xl/workbook.xml", workbook_xml)

        workbook_rels_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
            'Target="styles.xml"/>'
            "</Relationships>"
        )
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)

        styles_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<numFmts count="1"><numFmt numFmtId="164" formatCode="0.00%"/></numFmts>'
            '<fonts count="2">'
            '<font><sz val="11"/><color rgb="FF000000"/><name val="Calibri"/><family val="2"/></font>'
            '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/><family val="2"/></font>'
            "</fonts>"
            '<fills count="3">'
            '<fill><patternFill patternType="none"/></fill>'
            '<fill><patternFill patternType="gray125"/></fill>'
            '<fill><patternFill patternType="solid"><fgColor rgb="FF1F2937"/><bgColor indexed="64"/></patternFill></fill>'
            "</fills>"
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="3">'
            '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1">'
            '<alignment horizontal="center" vertical="center"/>'
            "</xf>"
            '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
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
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>"
        )
        zf.writestr("[Content_Types].xml", content_types_xml)

    return bio.getvalue()


def _parse_emp_availability(cell: str) -> set[str]:
    # expected: "mon_morning, tue_afternoon"
    if not cell:
        return set()
    return {x.strip().lower() for x in str(cell).split(",") if x.strip()}


def _parse_mentor_availability(cell: str) -> set[str]:
    # expected: "martes_manana, viernes_noche"
    if not cell:
        return set()
    out = set()
    for raw in str(cell).split(","):
        tok = raw.strip().lower()
        if not tok or "_" not in tok:
            continue
        day_es, time_es = tok.split("_", 1)
        day_es = day_es.strip()
        time_es = time_es.strip()

        day_en = DAY_MAP_ES_TO_EN.get(day_es)
        time_en = TIME_MAP_ES_TO_EN.get(time_es)
        if day_en and time_en:
            out.add(f"{day_en}_{time_en}")
    return out


def _business_age_bucket_to_min_years(emp_val: str) -> int:
    # emprendedora business_age: lt_1y, 1_3y, 4_6y, 7_10y, gt_10y
    v = (emp_val or "").strip().lower()
    return {
        "lt_1y": 0,
        "1_3y": 1,
        "4_6y": 4,
        "7_10y": 7,
        "gt_10y": 10,
    }.get(v, 0)


def _mentor_years_to_max_years(mentor_val: str) -> int:
    # mentora business_years: 0_1, 1_5, 5_10, 10_plus
    v = (mentor_val or "").strip().lower()
    return {
        "0_1": 1,
        "1_5": 5,
        "5_10": 10,
        "10_plus": 99,
    }.get(v, 0)


def _safe_lower(x):
    return (x or "").strip().lower()


def _llm_fit_score(client: OpenAI, mentor_text: str, emp_text: str, label: str) -> tuple[int, str]:
    mentor_text = mentor_text or ""
    emp_text = emp_text or ""

    if not mentor_text.strip() or not emp_text.strip():
        return 0, "none"

    prompt = f"""
You are matching a mentor with an entrepreneur for a program.
Task: Rate how well the mentor’s text can help the entrepreneur’s needs.

Label: {label}

Mentor text:
\"\"\"{mentor_text}\"\"\"

Entrepreneur text:
\"\"\"{emp_text}\"\"\"

Output EXACTLY:
Score: <0-5>
Reasoning: <2-3 sentences, concise, in English>
"""

    # Keep this under your gunicorn timeout pressure
    REQUEST_TIMEOUT = 20
    MAX_TRIES = 2

    last_err = None
    for attempt in range(1, MAX_TRIES + 1):
        try:
            r = client.chat.completions.create(
                model="gpt-5.2",
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                timeout=REQUEST_TIMEOUT,
            )
            content = (r.choices[0].message.content or "").strip()

            score = 0
            reasoning = "none"
            for line in content.splitlines():
                if line.startswith("Score:"):
                    try:
                        score = int(line.split(":", 1)[1].strip())
                    except Exception:
                        score = 0
                elif line.startswith("Reasoning:"):
                    reasoning = line.split(":", 1)[1].strip() or "none"

            score = max(0, min(5, score))
            if score == 0:
                reasoning = "none"
            return score, reasoning

        except Exception as e:
            last_err = e
            # small backoff
            time.sleep(0.5 * attempt)

    logger.exception("LLM fit scoring failed (label=%s). Last error: %s", label, last_err)
    return 0, "none"



def _df_col(df, colname: str):
    """
    Safe column accessor that tolerates duplicate column labels.
    If df[colname] returns a DataFrame (duplicate labels), take the FIRST column.
    Returns a Series-like object.
    """
    obj = df[colname]
    # If duplicate labels exist, pandas returns a DataFrame here.
    if hasattr(obj, "columns"):
        return obj.iloc[:, 0]
    return obj


def _row_get(row, colname: str, default=""):
    """
    Safe row accessor for dict-like pandas row.
    """
    try:
        v = row.get(colname, default)
        # If duplicate column labels existed, pandas Series may be returned; take first value.
        if hasattr(v, "iloc"):
            try:
                v = v.iloc[0]
            except Exception:
                v = v.values[0] if hasattr(v, "values") and len(getattr(v, "values", [])) else default
    except Exception:
        v = default
    return v


def _build_master_df_for_form(fd: FormDefinition):
    """
    Builds a DataFrame structurally similar to your 'Master CSV' download:
    created_at, application_id, name, email, + question slugs.

    IMPORTANT: This can produce duplicate column labels if a question slug == "email" or "name".
    That's OK—pairing code uses _df_col() to safely read identity columns.
    """
    apps = (
        Application.objects.filter(form=fd)
        .prefetch_related("answers__question")
        .order_by("created_at", "id")
    )

    questions = list(fd.questions.filter(active=True).order_by("position", "id"))

    headers = ["created_at", "application_id", "name", "email"] + [q.slug for q in questions]
    rows = []

    for app in apps:
        amap = {a.question.slug: (a.value or "") for a in app.answers.all()}
        rows.append(
            [
                app.created_at.isoformat(),
                app.id,
                app.name or "",
                (app.email or "").strip().lower(),
            ]
            + [amap.get(q.slug, "") for q in questions]
        )

    import pandas as pd
    return pd.DataFrame(rows, columns=headers)


def _pair_one_group(
    group_num: int,
    emp_emails: list[str],
    mentor_emails: list[str],
    log_fn=None,
):
    """
    Returns a DataFrame with PAIR_HEADERS.
    ALWAYS uses OpenAI for unstructured matching.
    Availability overlap is REQUIRED.

    Assumes these helpers already exist in your file (do NOT redefine here):
      - _build_master_df_for_form(fd)
      - _df_col(df, name)
      - _row_get(row, key, default="")
      - _parse_emp_availability(cell)
      - _parse_mentor_availability(cell)
      - _safe_lower(x)
      - _business_age_bucket_to_min_years(emp_val)
      - _mentor_years_to_max_years(mentor_val)
      - _llm_fit_score(client, mentor_text, emp_text, label)
      - PAIR_HEADERS
    """

    emp_slug = f"G{group_num}_E_A2"
    mentor_slug = f"G{group_num}_M_A2"

    emp_fd = get_object_or_404(FormDefinition, slug=emp_slug)
    mentor_fd = get_object_or_404(FormDefinition, slug=mentor_slug)

    if log_fn:
        log_fn(f"📥 Loading DB master data for {emp_slug} and {mentor_slug}")

    emp_df = _build_master_df_for_form(emp_fd)
    mentor_df = _build_master_df_for_form(mentor_fd)

    # determine question columns (keep identity columns out)
    ID_COLS = {"created_at", "application_id", "name", "email"}
    emp_question_cols = [c for c in emp_df.columns if c not in ID_COLS]
    mentor_question_cols = [c for c in mentor_df.columns if c not in ID_COLS]

    emp_suffix_headers = [f"{c}_emprendedora" for c in emp_question_cols]
    mentor_suffix_headers = [f"{c}_mentora" for c in mentor_question_cols]
    base_headers = PAIR_HEADERS
    full_headers = base_headers + emp_suffix_headers + mentor_suffix_headers

    # normalize incoming email lists once
    emp_emails_norm = [str(x).strip().lower() for x in (emp_emails or []) if str(x).strip()]
    mentor_emails_norm = [str(x).strip().lower() for x in (mentor_emails or []) if str(x).strip()]

    # Identity columns (safe even if duplicate column labels exist)
    emp_email_col = _df_col(emp_df, "email").astype(str).str.strip().str.lower()
    mentor_email_col = _df_col(mentor_df, "email").astype(str).str.strip().str.lower()

    # filter by selected email lists
    emp_df = emp_df[emp_email_col.isin(emp_emails_norm)].copy()
    mentor_df = mentor_df[mentor_email_col.isin(mentor_emails_norm)].copy()

    # recompute identity cols after filtering
    emp_email_col = _df_col(emp_df, "email").astype(str).str.strip().str.lower()
    mentor_email_col = _df_col(mentor_df, "email").astype(str).str.strip().str.lower()

    # drop duplicates by normalized email to avoid double-counting rows from master CSV
    emp_dup_count = emp_email_col.duplicated().sum()
    mentor_dup_count = mentor_email_col.duplicated().sum()
    if emp_dup_count and log_fn:
        log_fn(f"ℹ️ Deduping emprendedoras by email: removed {emp_dup_count} duplicate rows.")
    if mentor_dup_count and log_fn:
        log_fn(f"ℹ️ Deduping mentoras by email: removed {mentor_dup_count} duplicate rows.")
    emp_df = emp_df.loc[~emp_email_col.duplicated()].copy()
    mentor_df = mentor_df.loc[~mentor_email_col.duplicated()].copy()

    # recompute identity cols after dedup
    emp_email_col = _df_col(emp_df, "email").astype(str).str.strip().str.lower()
    mentor_email_col = _df_col(mentor_df, "email").astype(str).str.strip().str.lower()

    if emp_df.empty:
        if log_fn:
            log_fn(f"⚠️ No emprendedoras found for the given emails in {emp_slug}. Returning empty pairing file.")
        import pandas as pd
        return pd.DataFrame(columns=full_headers)
    if mentor_df.empty:
        if log_fn:
            log_fn(f"⚠️ No mentoras found for the given emails in {mentor_slug}. Returning empty pairing file.")
        import pandas as pd
        return pd.DataFrame(columns=full_headers)

    # validate all emails found
    found_emp = set(emp_email_col.tolist())
    found_mentor = set(mentor_email_col.tolist())

    missing_emp = sorted(set(emp_emails_norm) - found_emp)
    if missing_emp and log_fn:
        log_fn(f"⚠️ Missing emprendedora emails in {emp_slug}: {missing_emp[:10]} (total {len(missing_emp)})")

    missing_mentor = sorted(set(mentor_emails_norm) - found_mentor)
    if missing_mentor and log_fn:
        log_fn(f"⚠️ Missing mentora emails in {mentor_slug}: {missing_mentor[:10]} (total {len(missing_mentor)})")

    if len(emp_emails_norm) != len(mentor_emails_norm) and log_fn:
        log_fn(
            f"⚠️ Requested counts differ (1-to-1 expected). "
            f"Emprendedoras={len(emp_emails_norm)} Mentoras={len(mentor_emails_norm)}. "
            "Will pair only the rows found."
        )
    if len(emp_df) != len(mentor_df) and log_fn:
        log_fn(
            f"⚠️ After filtering, counts differ: emprendedoras={len(emp_df)}, mentoras={len(mentor_df)}. "
            "Extra entries will remain unmatched."
        )
    if log_fn:
        log_fn(f"🚦 Starting pairing loop for {len(emp_df)} emprendedoras vs {len(mentor_df)} mentoras.")

    # ALWAYS use OpenAI
    api_key = os.getenv("OPENAI_API_KEY") or getattr(settings, "OPENAI_API_KEY", None)
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set (required for pairing).")
    client = OpenAI(api_key=api_key)

    # Precompute availability (keyed by normalized email)
    emp_av = {}
    for _, r in emp_df.iterrows():
        email = str(_row_get(r, "email", "")).strip().lower()
        emp_av[email] = _parse_emp_availability(_row_get(r, "preferred_schedule", ""))

    mentor_av = {}
    for _, r in mentor_df.iterrows():
        email = str(_row_get(r, "email", "")).strip().lower()
        mentor_av[email] = _parse_mentor_availability(_row_get(r, "availability_grid", ""))

    # Build mentor lookup by email (safe even if duplicates exist)
    mentor_rows_by_email = {}
    for _, mr in mentor_df.iterrows():
        m_email = str(_row_get(mr, "email", "")).strip().lower()
        if m_email and m_email not in mentor_rows_by_email:
            mentor_rows_by_email[m_email] = mr

    # Use only mentors with non-empty normalized email
    unassigned_mentors = set(mentor_rows_by_email.keys())

    # Cache LLM results so we never re-call for the same pair
    llm_cache = {}  # key: (mentor_email, emp_email, label) -> (score, reasoning)

    TOP_K_FOR_LLM = 3  # only run LLM on top K candidates per emprendedora

    def score_pair_base(emp_row, mentor_row):
        """
        Fast scoring without OpenAI. Also enforces required availability overlap.
        Returns (score, matches_dict) where score < 0 means invalid.
        """
        emp_email = str(_row_get(emp_row, "email", "")).strip().lower()
        mentor_email = str(_row_get(mentor_row, "email", "")).strip().lower()

        overlap = sorted(emp_av.get(emp_email, set()).intersection(mentor_av.get(mentor_email, set())))
        if not overlap:
            return -10_000, {"availability": []}

        score = 0
        matches = {}

        # availability required: reward more overlaps
        score += 100 + 10 * len(overlap)
        matches["availability"] = overlap

        # industry
        emp_ind = _safe_lower(_row_get(emp_row, "industry", ""))
        mentor_ind = _safe_lower(_row_get(mentor_row, "business_industry", ""))
        matches["emp_industry_val"] = _row_get(emp_row, "industry", "") or ""
        matches["mentor_industry_val"] = _row_get(mentor_row, "business_industry", "") or ""
        if emp_ind and mentor_ind and emp_ind == mentor_ind:
            score += 30
            matches["industry"] = emp_ind
        else:
            matches["industry"] = "none"

        # same country only if emp says yes
        same_country = _safe_lower(_row_get(emp_row, "same_country", ""))
        if same_country == "yes":
            emp_country = _safe_lower(_row_get(emp_row, "country_residence", ""))
            mentor_country = _safe_lower(_row_get(mentor_row, "country_residence", ""))
            if emp_country and mentor_country and emp_country == mentor_country:
                score += 20
                matches["country"] = emp_country
            else:
                matches["country"] = "none"
        else:
            matches["country"] = "none"

        # business years: mentor >= emp (rough bucket compare)
        emp_min = _business_age_bucket_to_min_years(_row_get(emp_row, "business_age", ""))
        mentor_max = _mentor_years_to_max_years(_row_get(mentor_row, "business_years", ""))
        if mentor_max >= emp_min:
            score += 10
            matches["biz_age"] = f"mentor_max={mentor_max} >= emp_min={emp_min}"
        else:
            matches["biz_age"] = "none"

        return score, matches

    def add_llm_score(emp_row, mentor_row, score, matches):
        """
        Always runs OpenAI for unstructured fits on already-good candidates.
        Adds weighted LLM score and explanations.
        """
        emp_email = str(_row_get(emp_row, "email", "")).strip().lower()
        mentor_email = str(_row_get(mentor_row, "email", "")).strip().lower()

        # LLM 1
        key1 = (mentor_email, emp_email, "expertise_vs_growth")
        if key1 in llm_cache:
            s1, expl1 = llm_cache[key1]
        else:
            s1, expl1 = _llm_fit_score(
                client,
                mentor_text=_row_get(mentor_row, "professional_expertise", ""),
                emp_text=_row_get(emp_row, "growth_how", ""),
                label="expertise vs growth plan",
            )
            llm_cache[key1] = (s1, expl1)

        # LLM 2
        key2 = (mentor_email, emp_email, "motivation_vs_challenge")
        if key2 in llm_cache:
            s2, expl2 = llm_cache[key2]
        else:
            s2, expl2 = _llm_fit_score(
                client,
                mentor_text=_row_get(mentor_row, "motivation", ""),
                emp_text=_row_get(emp_row, "biggest_challenge", ""),
                label="motivation vs challenge",
            )
            llm_cache[key2] = (s2, expl2)

        score += 6 * s1 + 6 * s2
        matches["llm1"] = expl1 if s1 > 0 else "none"
        matches["llm2"] = expl2 if s2 > 0 else "none"
        return score, matches

    pairs = []
    unmatched_emps = []

    for i, (_, e) in enumerate(emp_df.iterrows(), start=1):
        e_email = str(_row_get(e, "email", "")).strip().lower()
        if log_fn:
            log_fn(f"🔗 Pairing {i}/{len(emp_df)}: {e_email}")

        if not unassigned_mentors:
            # no mentors left to assign — record unmatched and continue
            unmatched_emps.append(e_email)
            row_vals = [
                _row_get(e, "name", "") or "",
                "NO MENTOR FOUND",
                e_email,
                "none",
                "none",
                "",
                "",
                "none",
                "none",
                "none",
                "none",
                "none",
            ]
            row_vals.extend([_row_get(e, col, "") or "" for col in emp_question_cols])
            row_vals.extend(["" for _ in mentor_question_cols])
            pairs.append(row_vals)
            continue

        # 1) base-score all mentors fast
        scored = []
        for m_email in list(unassigned_mentors):
            m = mentor_rows_by_email.get(m_email)
            if m is None:
                continue
            base_score, base_matches = score_pair_base(e, m)
            if base_score > -10_000:
                scored.append((base_score, m_email, m, base_matches))

        if not scored:
            # ⚠️ No availability match found — DO NOT FAIL
            # Pick any remaining mentor (with a valid row) and mark availability as NO MATCH FOUND
            fallback_email = next((m for m in unassigned_mentors if m in mentor_rows_by_email), None)
            if fallback_email is None:
                unmatched_emps.append(e_email)
                row_vals = [
                    _row_get(e, "name", "") or "",
                    "NO MENTOR FOUND",
                    e_email,
                    "none",  # mentora email placeholder
                    "none",  # availability
                    "none",  # matching industry
                    _row_get(e, "industry", "") or "",  # emprendedora industry
                    "",  # mentora industry
                    "none",  # country
                    "none",  # business age
                    "none",  # llm1
                    "none",  # llm2
                ]
                row_vals.extend([_row_get(e, col, "") or "" for col in emp_question_cols])
                row_vals.extend(["" for _ in mentor_question_cols])
                pairs.append(row_vals)
                continue

            fallback_m = mentor_rows_by_email.get(fallback_email)

            best_email = fallback_email
            best_m = fallback_m
            best_matches = {
                "availability": ["NO MATCH FOUND"],
                "industry": "none",
                "emp_industry_val": _row_get(e, "industry", "") or "",
                "mentor_industry_val": _row_get(fallback_m, "business_industry", "") or "",
                "country": "none",
                "biz_age": "none",
                "llm1": "none",
                "llm2": "none",
            }
            best_score = 0
        scored.sort(key=lambda x: x[0], reverse=True)

        # 2) run LLM only on top K
        top = scored[:TOP_K_FOR_LLM]
        best_score = -10_000
        best_email = None
        best_m = None
        best_matches = None

        for base_score, m_email, m, base_matches in top:
            final_score, final_matches = add_llm_score(e, m, base_score, base_matches)
            if final_score > best_score:
                best_score = final_score
                best_email = m_email
                best_m = m
                best_matches = final_matches

        if best_email is None or best_m is None or best_score < 0:
            # if LLM scoring eliminates all options, fall back to any remaining mentor
            fallback_email = next((m for m in unassigned_mentors if m in mentor_rows_by_email), None)
            if fallback_email is None:
                unmatched_emps.append(e_email)
                row_vals = [
                    _row_get(e, "name", "") or "",
                    "NO MENTOR FOUND",
                    e_email,
                    "none",  # mentora email placeholder
                    "none",  # availability
                    "none",  # matching industry
                    _row_get(e, "industry", "") or "",  # emprendedora industry
                    "",  # mentora industry
                    "none",  # country
                    "none",  # business age
                    "none",  # llm1
                    "none",  # llm2
                ]
                row_vals.extend([_row_get(e, col, "") or "" for col in emp_question_cols])
                row_vals.extend(["" for _ in mentor_question_cols])
                pairs.append(row_vals)
                continue

            fallback_m = mentor_rows_by_email.get(fallback_email)
            best_email = fallback_email
            best_m = fallback_m
            best_matches = {
                "availability": ["NO MATCH FOUND"],
                "industry": "none",
                "emp_industry_val": _row_get(e, "industry", "") or "",
                "mentor_industry_val": _row_get(fallback_m, "business_industry", "") or "",
                "country": "none",
                "biz_age": "none",
                "llm1": "none",
                "llm2": "none",
            }
            best_score = 0

        unassigned_mentors.remove(best_email)

        overlap = best_matches.get("availability", [])
        row_vals = [
            _row_get(e, "name", "") or "",
            _row_get(best_m, "name", "") or "",
            e_email,
            best_email,
            ", ".join(overlap) if overlap else "none",
            best_matches.get("industry", "none") or "none",
            best_matches.get("emp_industry_val", "") or "",
            best_matches.get("mentor_industry_val", "") or "",
            best_matches.get("country", "none") or "none",
            best_matches.get("biz_age", "none") or "none",
            best_matches.get("llm1", "none") or "none",
            best_matches.get("llm2", "none") or "none",
        ]
        row_vals.extend([_row_get(e, col, "") or "" for col in emp_question_cols])
        row_vals.extend([_row_get(best_m, col, "") or "" for col in mentor_question_cols])
        pairs.append(row_vals)

    import pandas as pd

    if unmatched_emps and log_fn:
        log_fn(f"⚠️ No mentors were available for: {unmatched_emps[:10]} (total {len(unmatched_emps)})")
    if unassigned_mentors and log_fn:
        remaining = sorted(list(unassigned_mentors))
        log_fn(f"⚠️ Mentoras left unmatched: {remaining[:10]} (total {len(remaining)})")
    if missing_emp and log_fn:
        log_fn(f"📌 Missing emprendedoras (not found in master CSV): {missing_emp[:10]} (total {len(missing_emp)})")
    if missing_mentor and log_fn:
        log_fn(f"📌 Missing mentoras (not found in master CSV): {missing_mentor[:10]} (total {len(missing_mentor)})")
    if log_fn:
        log_fn(f"✅ Pairing complete. Output rows: {len(pairs)}.")

    return pd.DataFrame(pairs, columns=full_headers)



from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render
from applications.models import FormGroup, GradedFile

@staff_member_required
def emparejamiento_home(request):
    groups = list(FormGroup.objects.order_by("number"))

    group_raw = (request.GET.get("group") or "").strip()
    selected_group = None
    job = None

    if group_raw.isdigit():
        selected_group = FormGroup.objects.filter(number=int(group_raw)).first()

    job_id = (request.GET.get("job") or "").strip()
    if job_id.isdigit():
        try:
            job = PairingJob.objects.filter(id=int(job_id)).first()
        except DatabaseError:
            job = None

    try:
        pairing_files = GradedFile.objects.filter(
            form_slug__startswith="PAIR_G"
        ).order_by("-created_at")[:50]
    except DatabaseError:
        pairing_files = []

    return render(
        request,
        "admin_dash/emparejamiento_home.html",
        {
            "groups": groups,
            "selected_group": selected_group,
            "pairing_files": pairing_files,
            "job": job,
        },
    )



    


@staff_member_required
@require_POST
def run_emparejamiento(request, group_num: int):
    mentoras_emails = request.POST.get("mentoras_emails", "")
    emprendedoras_emails = request.POST.get("emprendedoras_emails", "")

    mentor_list = _norm_email_list(mentoras_emails)
    emp_list = _norm_email_list(emprendedoras_emails)

    job = PairingJob.objects.create(
        group_number=group_num,
        status=PairingJob.STATUS_QUEUED,
    )

    t = threading.Thread(
        target=_run_pair_job,
        args=(job.id, group_num, emp_list, mentor_list),
        daemon=True,
    )
    t.start()

    return redirect(f"{reverse('admin_emparejamiento_home')}?group={group_num}&job={job.id}")


# ----------------------------
# Toggle (accepting submissions)
# ----------------------------
@staff_member_required
@require_POST
def toggle_form_accepting(request, form_slug: str):
    """
    Toggle whether a form accepts new submissions.
    """
    fd = get_object_or_404(FormDefinition, slug=form_slug)
    fd.accepting_responses = not fd.accepting_responses
    fd.save(update_fields=["accepting_responses"])

    state = "OPEN" if fd.accepting_responses else "CLOSED"
    messages.success(request, f"{fd.slug} is now {state} for new submissions.")
    return redirect("admin_apps_list")


# ----------------------------
# Forms
# ----------------------------
MONTH_CHOICES_ES = [
    ("enero", "enero"),
    ("febrero", "febrero"),
    ("marzo", "marzo"),
    ("abril", "abril"),
    ("mayo", "mayo"),
    ("junio", "junio"),
    ("julio", "julio"),
    ("agosto", "agosto"),
    ("septiembre", "septiembre"),
    ("octubre", "octubre"),
    ("noviembre", "noviembre"),
    ("diciembre", "diciembre"),
]

MONTH_NUM_TO_ES = {
    month_number: month_name
    for month_number, (month_name, _) in enumerate(MONTH_CHOICES_ES, start=1)
}

RESPOND_BY_MONTH_CHOICES = [("", "---------"), *MONTH_CHOICES_ES]
RESPOND_BY_MONTH_TO_NUM = {
    month_name: month_number
    for month_number, (month_name, _) in enumerate(MONTH_CHOICES_ES, start=1)
}
DT_LOCAL_INPUT_FORMATS = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"]


class CreateGroupForm(forms.Form):
    group_num = forms.IntegerField(min_value=1, label="Group number")
    start_day = forms.IntegerField(min_value=1, max_value=31, label="Start day")
    start_month = forms.ChoiceField(choices=MONTH_CHOICES_ES, label="mes de inicio")
    end_month = forms.ChoiceField(choices=MONTH_CHOICES_ES, label="mes de fin")
    year = forms.IntegerField(min_value=2020, max_value=2100, label="Year")
    a2_deadline = forms.DateField(label="Fecha límite A2", required=False, help_text="YYYY-MM-DD")
    respond_by_day = forms.CharField(
        required=False,
        label="Respond by day",
        widget=forms.TextInput(attrs={"placeholder": "ej. 15"}),
    )
    respond_by_month = forms.ChoiceField(
        choices=RESPOND_BY_MONTH_CHOICES,
        required=False,
        label="mes para responder",
    )
    open_at = forms.DateTimeField(
        label="Abrir automáticamente en",
        required=False,
        help_text="YYYY-MM-DD HH:MM",
        input_formats=DT_LOCAL_INPUT_FORMATS,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )
    close_at = forms.DateTimeField(
        label="Cerrar automáticamente en",
        required=False,
        help_text="YYYY-MM-DD HH:MM",
        input_formats=DT_LOCAL_INPUT_FORMATS,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )
    reminder_1_at = forms.DateTimeField(
        label="Recordatorio automático #1",
        required=False,
        help_text="YYYY-MM-DD HH:MM",
        input_formats=DT_LOCAL_INPUT_FORMATS,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )
    reminder_2_at = forms.DateTimeField(
        label="Recordatorio automático #2",
        required=False,
        help_text="YYYY-MM-DD HH:MM",
        input_formats=DT_LOCAL_INPUT_FORMATS,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )
    reminder_3_at = forms.DateTimeField(
        label="Recordatorio automático #3",
        required=False,
        help_text="YYYY-MM-DD HH:MM",
        input_formats=DT_LOCAL_INPUT_FORMATS,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )

    def clean(self):
        cleaned = super().clean()
        year = cleaned.get("year")
        a2_deadline = cleaned.get("a2_deadline")
        respond_by_day = (cleaned.get("respond_by_day") or "").strip()
        respond_by_month = (cleaned.get("respond_by_month") or "").strip().lower()

        has_day = bool(respond_by_day)
        has_month = bool(respond_by_month)

        if has_day != has_month:
            raise forms.ValidationError(
                "If you use 'Respond by', provide both day and month."
            )

        if has_day and has_month:
            month_num = RESPOND_BY_MONTH_TO_NUM.get(respond_by_month)
            if not month_num:
                raise forms.ValidationError("Respond by month is invalid.")
            if not respond_by_day.isdigit():
                raise forms.ValidationError(
                    "Respond by day must be a number from 1 to 31."
                )
            respond_by_day_num = int(respond_by_day)
            if respond_by_day_num < 1 or respond_by_day_num > 31:
                raise forms.ValidationError(
                    "Respond by day must be a number from 1 to 31."
                )
            try:
                derived_deadline = datetime(
                    int(year), int(month_num), respond_by_day_num
                ).date()
            except Exception:
                raise forms.ValidationError(
                    "Respond by day/month is not a valid date for the selected year."
                )

            cleaned["a2_deadline"] = derived_deadline

        reminder_1_at = cleaned.get("reminder_1_at")
        reminder_2_at = cleaned.get("reminder_2_at")
        reminder_3_at = cleaned.get("reminder_3_at")
        if reminder_1_at and reminder_2_at and reminder_2_at < reminder_1_at:
            raise forms.ValidationError(
                "Reminder #2 must be after Reminder #1."
            )
        if reminder_2_at and reminder_3_at and reminder_3_at < reminder_2_at:
            raise forms.ValidationError(
                "Reminder #3 must be after Reminder #2."
            )

        return cleaned


class PoolAssignmentForm(forms.Form):
    TRACK_EMPRENDEDORAS = "E"
    TRACK_MENTORAS = "M"
    TRACK_CHOICES = [
        (TRACK_EMPRENDEDORAS, "Emprendedoras"),
        (TRACK_MENTORAS, "Mentoras"),
    ]

    source_pool = forms.ChoiceField(
        choices=[
            (key, cfg["label"])
            for key, cfg in RECRUITMENT_POOL_SOURCES.items()
        ],
        initial="april_recruitment",
        label="Source applications",
    )
    track = forms.ChoiceField(
        choices=TRACK_CHOICES,
        initial=TRACK_EMPRENDEDORAS,
        label="Applicant type",
    )
    target_group_num = forms.IntegerField(
        min_value=1,
        label="New group number",
    )
    emails_text = forms.CharField(
        required=True,
        label="Applicant emails",
        widget=forms.Textarea(
            attrs={
                "rows": 7,
                "placeholder": "paste one email per line, or comma/semicolon separated",
            }
        ),
    )

    def clean(self):
        cleaned = super().clean()
        emails_text = cleaned.get("emails_text") or ""
        normalized_emails = _norm_email_list(emails_text)
        if not normalized_emails:
            raise forms.ValidationError("Paste at least one valid email.")

        cleaned["normalized_emails"] = normalized_emails
        return cleaned


# ----------------------------
# Helpers
# ----------------------------
def _fill_placeholders(
    text: str | None,
    group_num: int,
    start_day: int,
    start_month: str,
    end_month: str,
    year: int,
    respond_day: str = "",
    respond_month: str = "",
) -> str | None:
    if not text:
        return text

    out = text.replace("#(group number)", str(group_num))
    out = out.replace("#(day)", str(start_day))

    if "#(month)" in out:
        out = out.replace("#(month)", start_month, 1)
    if "#(month)" in out:
        out = out.replace("#(month)", end_month, 1)

    out = out.replace("#(year)", str(year))
    out = out.replace("#(respond_day)", str(respond_day or ""))
    out = out.replace("#(respond_month)", str(respond_month or ""))
    return out


def _model_has_field(model: type[Model], field_name: str) -> bool:
    try:
        model._meta.get_field(field_name)
        return True
    except Exception:
        return False


def _apply_group_reminder_schedule(
    group: FormGroup,
    reminder_1_at,
    reminder_2_at,
    reminder_3_at,
) -> list[str]:
    update_fields: list[str] = []
    for idx, new_dt in enumerate((reminder_1_at, reminder_2_at, reminder_3_at), start=1):
        at_field = f"reminder_{idx}_at"
        sent_field = f"reminder_{idx}_sent_at"
        current_dt = getattr(group, at_field, None)

        if current_dt != new_dt:
            setattr(group, at_field, new_dt)
            update_fields.append(at_field)
            if getattr(group, sent_field, None) is not None:
                setattr(group, sent_field, None)
                update_fields.append(sent_field)

    return update_fields


def _sync_group_open_close(group: FormGroup):
    """
    Auto-open/close all forms in a group based on open_at/close_at window.
    If no schedule is set, leave forms as-is.
    """
    if not group:
        return

    forms_qs = FormDefinition.objects.filter(group=group)
    if _model_has_field(FormDefinition, "manual_open_override"):
        forms_qs.filter(manual_open_override=True).update(
            is_public=True,
            accepting_responses=True,
        )
        forms_qs.filter(manual_open_override=False).update(
            is_public=False,
            accepting_responses=False,
        )
        forms_qs = forms_qs.filter(manual_open_override__isnull=True)

    desired_open = scheduled_group_open_state(group)
    if desired_open is None:
        return

    forms_qs.update(
        is_public=desired_open,
        accepting_responses=desired_open,
    )

def _ensure_test_a2_form(role: str) -> FormDefinition:
    """
    role: "E" or "M"
    Creates TEST_E_A2 or TEST_M_A2 if missing by cloning from the master E_A2 / M_A2.
    """
    if role not in ("E", "M"):
        raise ValueError("role must be 'E' or 'M'")

    test_slug = TEST_E_A2_SLUG if role == "E" else TEST_M_A2_SLUG
    master_slug = "E_A2" if role == "E" else "M_A2"

    existing = FormDefinition.objects.filter(slug=test_slug).first()
    if existing:
        return existing

    master_fd = get_object_or_404(FormDefinition, slug=master_slug)

    # Create new FormDefinition
    clone = FormDefinition.objects.create(
        slug=test_slug,
        name=f"TEST — {master_fd.name}",
        description=(master_fd.description or "") + "\n\n(Imported test CSV submissions.)",
        is_master=False,
        group=None,
        is_public=False,             # keep it out of public view
        accepting_responses=False,   # prevent real submissions
    )

    # Clone questions + choices
    for q in master_fd.questions.all().order_by("position", "id"):
        q_clone = Question.objects.create(
            form=clone,
            text=q.text,
            help_text=q.help_text,
            field_type=q.field_type,
            required=q.required,
            position=q.position,
            slug=q.slug,
            active=q.active,
        )
        for c in q.choices.all().order_by("position", "id"):
            Choice.objects.create(
                question=q_clone,
                label=c.label,
                value=c.value,
                position=c.position,
            )

    return clone

def _clone_form(master_fd: FormDefinition, group: FormGroup) -> FormDefinition:
    group_num = group.number
    start_day = group.start_day
    start_month = group.start_month
    end_month = group.end_month
    year = group.year
    respond_day = ""
    respond_month = ""
    if group.a2_deadline:
        respond_day = str(group.a2_deadline.day)
        respond_month = MONTH_NUM_TO_ES.get(group.a2_deadline.month, "")

    new_slug = f"G{group_num}_{master_fd.slug}"
    new_name = f"Grupo {group_num} — {master_fd.name}"

    existing = FormDefinition.objects.filter(slug=new_slug).first()
    if existing:
        return existing

    clone = FormDefinition.objects.create(
        slug=new_slug,
        name=new_name,
        description=_fill_placeholders(
            master_fd.description,
            group_num,
            start_day,
            start_month,
            end_month,
            year,
            respond_day=respond_day,
            respond_month=respond_month,
        )
        or "",
        is_master=False,
        group=group,
        is_public=master_fd.is_public,
    )

    # Clone sections first so questions can be linked
    section_map: dict[int, Section] = {}
    for s in master_fd.sections.all().order_by("position", "id"):
        section_map[s.id] = Section.objects.create(
            form=clone,
            title=_fill_placeholders(
                s.title,
                group_num,
                start_day,
                start_month,
                end_month,
                year,
                respond_day=respond_day,
                respond_month=respond_month,
            ) or s.title,
            description=_fill_placeholders(
                s.description,
                group_num,
                start_day,
                start_month,
                end_month,
                year,
                respond_day=respond_day,
                respond_month=respond_month,
            ) or s.description,
            position=s.position,
        )

    for q in master_fd.questions.select_related("section").all().order_by("position", "id"):
        new_section = section_map.get(q.section_id)
        q_clone = Question.objects.create(
            form=clone,
            text=_fill_placeholders(
                q.text,
                group_num,
                start_day,
                start_month,
                end_month,
                year,
                respond_day=respond_day,
                respond_month=respond_month,
            ) or q.text,
            help_text=_fill_placeholders(
                q.help_text,
                group_num,
                start_day,
                start_month,
                end_month,
                year,
                respond_day=respond_day,
                respond_month=respond_month,
            ) or q.help_text,
            field_type=q.field_type,
            required=q.required,
            position=q.position,
            slug=q.slug,  # IMPORTANT: stable
            active=q.active,
            confirm_value=q.confirm_value,
            section=new_section,
        )
        for c in q.choices.all().order_by("position", "id"):
            Choice.objects.create(
                question=q_clone,
                label=_fill_placeholders(
                    c.label,
                    group_num,
                    start_day,
                    start_month,
                    end_month,
                    year,
                    respond_day=respond_day,
                    respond_month=respond_month,
                ) or c.label,
                value=c.value,
                position=c.position,
            )

    return clone


def _build_csv_for_form(form_def: FormDefinition) -> Tuple[List[str], List[List[str]]]:
    apps = (
        Application.objects.filter(form=form_def)
        .prefetch_related("answers__question")
        .order_by("created_at", "id")
    )

    questions = list(form_def.questions.filter(active=True).order_by("position", "id"))
    headers = ["created_at", "application_id", "name", "email"] + [q.slug for q in questions]

    rows: List[List[str]] = []
    for app in apps:
        amap = {a.question.slug: (a.value or "") for a in app.answers.all()}
        row = [
            app.created_at.isoformat(),
            str(app.id),
            app.name,
            app.email,
        ] + [amap.get(q.slug, "") for q in questions]
        rows.append(row)

    return headers, rows


def _group_number_from_slug(slug: str) -> int | None:
    m = GROUP_SLUG_RE.match((slug or "").strip())
    if not m:
        return None
    try:
        return int(m.group("num"))
    except Exception:
        return None


def _group_forms_for_app_type(
    app_type: str,
    include_combined_groups: bool | None = None,
) -> list[FormDefinition]:
    app_type = (app_type or "").upper().strip()
    if app_type not in MASTER_SLUGS:
        raise ValueError(f"Unsupported app type: {app_type}")

    candidates = list(
        FormDefinition.objects.filter(
            slug__startswith="G",
            slug__endswith=f"_{app_type}",
        ).select_related("group")
    )

    forms: list[FormDefinition] = []
    for fd in candidates:
        m = GROUP_SLUG_RE.match((fd.slug or "").strip())
        if not m:
            continue
        if m.group("master") != app_type:
            continue
        if include_combined_groups is not None:
            use_combined = bool(getattr(getattr(fd, "group", None), "use_combined_application", False))
            if include_combined_groups and not use_combined:
                continue
            if not include_combined_groups and use_combined:
                continue
        forms.append(fd)

    def _sort_key(fd: FormDefinition):
        gnum = getattr(getattr(fd, "group", None), "number", None)
        if gnum is None:
            gnum = _group_number_from_slug(fd.slug or "") or 0
        return (gnum, fd.slug or "")

    forms.sort(key=_sort_key)
    return forms


def _build_csv_for_app_type(
    app_type: str,
    group_number: int | None = None,
    include_combined_groups: bool | None = None,
) -> Tuple[List[str], List[List[str]], list[FormDefinition]]:
    forms = _group_forms_for_app_type(app_type, include_combined_groups=include_combined_groups)
    if group_number is not None:
        filtered_forms: list[FormDefinition] = []
        for fd in forms:
            gnum = getattr(getattr(fd, "group", None), "number", None)
            if gnum is None:
                gnum = _group_number_from_slug(fd.slug or "")
            if gnum == group_number:
                filtered_forms.append(fd)
        forms = filtered_forms

    if not forms:
        headers = ["created_at", "application_id", "group_number", "form_slug", "name", "email"]
        return headers, [], forms

    question_slugs: list[str] = []
    seen_slugs: set[str] = set()
    for fd in forms:
        for q in fd.questions.filter(active=True).order_by("position", "id"):
            if q.slug in seen_slugs:
                continue
            seen_slugs.add(q.slug)
            question_slugs.append(q.slug)

    headers = [
        "created_at",
        "application_id",
        "group_number",
        "form_slug",
        "name",
        "email",
    ] + question_slugs

    apps = (
        Application.objects.filter(form__in=forms)
        .select_related("form", "form__group")
        .prefetch_related("answers__question")
        .order_by("created_at", "id")
    )

    rows: List[List[str]] = []
    for app in apps:
        amap = {a.question.slug: (a.value or "") for a in app.answers.all() if getattr(a, "question_id", None)}
        gnum = getattr(getattr(app.form, "group", None), "number", None)
        if gnum is None:
            gnum = _group_number_from_slug(getattr(app.form, "slug", "") or "")

        row = [
            app.created_at.isoformat(),
            str(app.id),
            str(gnum or ""),
            app.form.slug,
            app.name,
            app.email,
        ] + [amap.get(slug, "") for slug in question_slugs]
        rows.append(row)

    return headers, rows, forms


def _group_forms_for_track(track: str) -> list[FormDefinition]:
    track = (track or "").upper().strip()
    if track not in {"E", "M"}:
        raise ValueError(f"Unsupported track: {track}")

    app_types = [f"{track}_A1", f"{track}_A2"]
    forms: list[FormDefinition] = []
    seen_ids: set[int] = set()
    for app_type in app_types:
        for fd in _group_forms_for_app_type(app_type, include_combined_groups=True):
            if fd.id in seen_ids:
                continue
            seen_ids.add(fd.id)
            forms.append(fd)

    def _sort_key(fd: FormDefinition):
        gnum = getattr(getattr(fd, "group", None), "number", None)
        if gnum is None:
            gnum = _group_number_from_slug(fd.slug or "") or 0
        return (gnum, fd.slug or "")

    forms.sort(key=_sort_key)
    return forms


def _build_csv_for_track(
    track: str,
    group_number: int | None = None,
) -> Tuple[List[str], List[List[str]], list[FormDefinition]]:
    track = (track or "").upper().strip()
    forms = _group_forms_for_track(track)
    if group_number is not None:
        filtered_forms: list[FormDefinition] = []
        for fd in forms:
            gnum = getattr(getattr(fd, "group", None), "number", None)
            if gnum is None:
                gnum = _group_number_from_slug(fd.slug or "")
            if gnum == group_number:
                filtered_forms.append(fd)
        forms = filtered_forms

    if not forms:
        headers = ["created_at", "application_id", "group_number", "name", "email"]
        return headers, [], forms

    question_slugs: list[str] = []
    seen_slugs: set[str] = set()
    for fd in forms:
        for q in fd.questions.filter(active=True).order_by("position", "id"):
            if q.slug in seen_slugs:
                continue
            seen_slugs.add(q.slug)
            question_slugs.append(q.slug)

    headers = [
        "created_at",
        "application_id",
        "group_number",
        "name",
        "email",
    ] + question_slugs

    apps = (
        Application.objects.filter(form__in=forms)
        .select_related("form", "form__group")
        .prefetch_related("answers__question")
        .order_by("created_at", "id")
    )

    rows: List[List[str]] = []
    for app in apps:
        amap = {a.question.slug: (a.value or "") for a in app.answers.all() if getattr(a, "question_id", None)}
        gnum = getattr(getattr(app.form, "group", None), "number", None)
        if gnum is None:
            gnum = _group_number_from_slug(getattr(app.form, "slug", "") or "")

        row = [
            app.created_at.isoformat(),
            str(app.id),
            str(gnum or ""),
            app.name,
            app.email,
        ] + [amap.get(slug, "") for slug in question_slugs]
        rows.append(row)

    return headers, rows, forms


def _normalize_identity_email(value: str | None) -> str:
    return str(value or "").strip().lower()


def _application_identity_tokens(app: Application) -> set[str]:
    tokens: set[str] = set()

    app_email = _normalize_identity_email(getattr(app, "email", ""))
    if app_email:
        tokens.add(f"email:{app_email}")

    for ans in app.answers.all():
        slug = (getattr(getattr(ans, "question", None), "slug", "") or "").strip().lower()
        raw_value = ans.value or ""

        if slug in IDENTITY_EMAIL_SLUGS:
            answer_email = _normalize_identity_email(raw_value)
            if answer_email:
                tokens.add(f"email:{answer_email}")
            continue

        if slug in IDENTITY_DOCUMENT_SLUGS:
            doc_key = _normalize_document_id(raw_value)
            if doc_key:
                tokens.add(f"doc:{doc_key}")

    return tokens


def _row_identity_tokens(headers: List[str], row: List[str]) -> set[str]:
    tokens: set[str] = set()
    width = min(len(headers), len(row))
    for i in range(width):
        slug = (headers[i] or "").strip().lower()
        value = row[i]
        if slug in IDENTITY_EMAIL_SLUGS:
            email_key = _normalize_identity_email(value)
            if email_key:
                tokens.add(f"email:{email_key}")
            continue
        if slug in IDENTITY_DOCUMENT_SLUGS:
            doc_key = _normalize_document_id(value)
            if doc_key:
                tokens.add(f"doc:{doc_key}")
    return tokens


def _track_completion_filter_data(
    track: str,
    forms: list[FormDefinition],
    completion_filter: str,
) -> tuple[set[int], set[str], dict[str, str], list[Application]]:
    a1_suffix = f"{track}_A1"
    a2_suffix = f"{track}_A2"

    apps = list(
        Application.objects.filter(form__in=forms)
        .select_related("form", "form__group")
        .prefetch_related("answers__question")
        .order_by("-created_at", "-id")
    )

    parent: dict[str, str] = {}

    def find(x: str) -> str:
        root = parent.get(x, x)
        if root != x:
            root = find(root)
            parent[x] = root
        return root

    def union(a: str, b: str) -> None:
        ra = find(a)
        rb = find(b)
        if ra == rb:
            return
        parent[rb] = ra

    app_tokens: dict[int, set[str]] = {}
    app_stage: dict[int, str] = {}

    for app in apps:
        slug = (getattr(getattr(app, "form", None), "slug", "") or "").strip().upper()
        stage = ""
        if slug.endswith(a1_suffix):
            stage = "a1"
        elif slug.endswith(a2_suffix):
            stage = "a2"
        if not stage:
            continue

        tokens = _application_identity_tokens(app)
        if not tokens:
            continue

        token_list = sorted(tokens)
        for tok in token_list:
            parent.setdefault(tok, tok)
        head = token_list[0]
        for tok in token_list[1:]:
            union(head, tok)

        app_tokens[app.id] = tokens
        app_stage[app.id] = stage

    has_a1_roots: set[str] = set()
    has_a2_roots: set[str] = set()
    for app in apps:
        tokens = app_tokens.get(app.id)
        stage = app_stage.get(app.id)
        if not tokens or not stage:
            continue
        root = find(next(iter(tokens)))
        if stage == "a1":
            has_a1_roots.add(root)
        elif stage == "a2":
            has_a2_roots.add(root)

    if completion_filter == TRACK_COMPLETION_FILTER_A1_ONLY:
        matched_roots = has_a1_roots - has_a2_roots
    elif completion_filter == TRACK_COMPLETION_FILTER_A1_A2:
        matched_roots = has_a1_roots & has_a2_roots
    else:
        matched_roots = has_a1_roots | has_a2_roots

    allowed_app_ids: set[int] = set()
    for app in apps:
        tokens = app_tokens.get(app.id)
        if not tokens:
            continue
        token_roots = {find(tok) for tok in tokens}
        if token_roots & matched_roots:
            allowed_app_ids.add(app.id)

    token_to_root = {tok: find(tok) for tok in parent}
    return allowed_app_ids, matched_roots, token_to_root, apps


def _csv_http_response(filename: str, headers: List[str], rows: List[List[str]]) -> HttpResponse:
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    w = csv.writer(resp)
    w.writerow(headers)
    w.writerows(rows)
    return resp


def _csv_preview_html(headers: List[str], rows: List[List[str]], max_rows: int | None = None) -> str:
    def esc(s: str) -> str:
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def esc_attr(s: str) -> str:
        return (
            (s or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;")
        )

    preview = rows if max_rows is None else rows[:max_rows]
    table_id = f"csv-preview-{abs(hash(tuple(headers))) % 1000000}-{len(rows)}"

    ths = "".join(
        f"<th style='text-align:left;padding:6px;border-bottom:1px solid #ddd;white-space:nowrap;width:1%;'>"
        f"{esc(h)}</th>"
        for h in headers
    )
    unique_values_by_col: list[list[str]] = []
    for i in range(len(headers)):
        seen: set[str] = set()
        values: list[str] = []
        for r in preview:
            v = str(r[i]) if i < len(r) and r[i] is not None else ""
            if v in seen:
                continue
            seen.add(v)
            values.append(v)
        values.sort(key=lambda x: x.lower())
        unique_values_by_col.append(values)

    filter_ths = "".join(
        (
            "<th style='padding:4px 6px;border-bottom:1px solid #ddd;background:#fafafa;'>"
            "<div style='display:flex;flex-direction:column;gap:4px;'>"
            f"<input type='text' data-csv-filter-search='{i}' placeholder='Search options...' "
            "style='width:100%;box-sizing:border-box;font-size:12px;padding:4px 6px;"
            "border:1px solid #ccc;border-radius:4px;'>"
            f"<select data-csv-filter='{i}' "
            "style='width:100%;box-sizing:border-box;font-size:12px;padding:4px 6px;"
            "border:1px solid #ccc;border-radius:4px;'>"
            "<option value=''>All</option>"
            + "".join(
                (
                    "<option value='__BLANK__'>(blank)</option>"
                    if v == ""
                    else f"<option value='{esc_attr(v)}'>{esc(v)}</option>"
                )
                for v in unique_values_by_col[i]
            )
            + "</select>"
            "</div>"
            "</th>"
        )
        for i, _h in enumerate(headers)
    )

    body = []
    for r in preview:
        tds = "".join(
            (
                "<td style='border-bottom:1px solid #eee;vertical-align:middle;height:34px;'>"
                f"<div title='{esc_attr(str(v))}' "
                "style='padding:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;"
                "line-height:1.2;'>"
                f"{esc(str(v))}</div>"
                "</td>"
            )
            for v in r
        )
        body.append(f"<tr data-csv-row='1'>{tds}</tr>")

    if max_rows is None:
        info_text = f"Showing all {len(preview)} rows."
    else:
        info_text = f"Showing {len(preview)} of {len(rows)} rows."

    return (
        f"<div id='{table_id}' style='overflow:auto;border:1px solid #ddd;border-radius:8px;'>"
        "<table style='border-collapse:collapse;width:100%;font-size:13px;table-layout:auto;'>"
        f"<thead><tr>{ths}</tr><tr>{filter_ths}</tr></thead>"
        f"<tbody>{''.join(body) if body else f'<tr><td colspan=\"{max(1, len(headers))}\" style=\"padding:8px;\">No submissions yet.</td></tr>'}</tbody>"
        "</table>"
        "</div>"
        f"<p data-csv-count style='margin-top:8px;color:#666;font-size:12px;'>{info_text}</p>"
        "<script>"
        "(function(){"
        f"const root=document.getElementById('{table_id}');"
        "if(!root||root.dataset.filterInit==='1') return;"
        "root.dataset.filterInit='1';"
        "const inputs=Array.from(root.querySelectorAll('select[data-csv-filter]'));"
        "const searchInputs=Array.from(root.querySelectorAll('input[data-csv-filter-search]'));"
        "const rows=Array.from(root.querySelectorAll('tbody tr[data-csv-row]'));"
        "const countEl=(root.nextElementSibling&&root.nextElementSibling.matches('[data-csv-count]'))?root.nextElementSibling:null;"
        "function filterSelectOptions(searchEl){"
        "const idx=(searchEl&&searchEl.getAttribute('data-csv-filter-search'))||'';"
        "const sel=root.querySelector(`select[data-csv-filter='${idx}']`);"
        "if(!sel) return;"
        "const q=(searchEl.value||'').trim().toLowerCase();"
        "Array.from(sel.options).forEach((opt,optIdx)=>{"
        "if(optIdx===0){opt.hidden=false;return;}"
        "const txt=(opt.textContent||'').trim().toLowerCase();"
        "const val=(opt.value||'').trim().toLowerCase();"
        "opt.hidden=!!q&&txt.indexOf(q)===-1&&val.indexOf(q)===-1;"
        "});"
        "}"
        "function applyFilters(){"
        "const filters=inputs.map(i=>(i.value||'').trim().toLowerCase());"
        "let visible=0;"
        "rows.forEach((tr)=>{"
        "const cells=Array.from(tr.querySelectorAll('td'));"
        "const show=filters.every((f,idx)=>{"
        "if(!f) return true;"
        "const txt=(cells[idx]&&cells[idx].innerText)?cells[idx].innerText:'';"
        "const normalized=txt.trim().toLowerCase();"
        "if(f==='__blank__') return normalized==='';"
        "return normalized===f;"
        "});"
        "tr.style.display=show?'':'none';"
        "if(show) visible+=1;"
        "});"
        "if(countEl){countEl.textContent=`Showing ${visible} of ${rows.length} rows.`;}"
        "}"
        "inputs.forEach((inp)=>inp.addEventListener('change',applyFilters));"
        "searchInputs.forEach((inp)=>inp.addEventListener('input',()=>filterSelectOptions(inp)));"
        "searchInputs.forEach((inp)=>filterSelectOptions(inp));"
        "applyFilters();"
        "})();"
        "</script>"
    )

def _pair_log(job: PairingJob, msg: str):
    print(msg, flush=True)          # shows in Render logs
    job.append_log(msg)


def _run_pair_job(job_id: int, group_num: int, emp_list: list[str], mentor_list: list[str]):
    job = PairingJob.objects.get(id=job_id)
    try:
        job.status = PairingJob.STATUS_RUNNING
        job.save(update_fields=["status"])

        _pair_log(job, "✅ Starting emparejamiento job")
        _pair_log(job, f"Group: {group_num}")
        _pair_log(job, f"Mentoras: {len(mentor_list)}")
        _pair_log(job, f"Emprendedoras: {len(emp_list)}")

        df = _pair_one_group(
            group_num=group_num,
            emp_emails=emp_list,
            mentor_emails=mentor_list,
            log_fn=lambda m: _pair_log(job, m),
        )

        csv_text = df.to_csv(index=False)

        form_slug = f"PAIR_G{group_num}"
        GradedFile.objects.filter(form_slug=form_slug).delete()
        gf = GradedFile.objects.create(form_slug=form_slug, csv_text=csv_text)

        drive_sync = sync_generated_csv_artifact(form_slug, csv_text)
        _pair_log(job, f"☁️ Drive sync: {drive_sync.status} - {drive_sync.detail}")

        _pair_log(job, f"📄 CSV saved (GradedFile id={gf.id})")

        job.status = PairingJob.STATUS_DONE
        job.save(update_fields=["status"])
        _pair_log(job, "✅ Emparejamiento completed successfully")

    except Exception:
        import traceback
        _pair_log(job, "❌ Emparejamiento failed")
        _pair_log(job, traceback.format_exc())
        job.status = PairingJob.STATUS_FAILED
        job.save(update_fields=["status"])

# ----------------------------
# Toggle (open/closed) for display — your "toggle-form" URL
# ----------------------------
@staff_member_required
@require_POST
def toggle_form_open(request, form_slug: str):
    """
    Toggle whether a form accepts new submissions.
    Uses FormDefinition.is_public as the "open" flag.
    - True  => open
    - False => closed (no new applications)
    """
    fd = get_object_or_404(FormDefinition, slug=form_slug)

    target_open = not bool(fd.is_public)
    fd.is_public = target_open
    fd.accepting_responses = target_open

    update_fields = ["is_public", "accepting_responses"]
    if _model_has_field(FormDefinition, "manual_open_override"):
        scheduled_open = scheduled_group_open_state(getattr(fd, "group", None))
        if scheduled_open is None:
            fd.manual_open_override = None
        elif scheduled_open == target_open:
            fd.manual_open_override = None
        else:
            fd.manual_open_override = target_open
        update_fields.append("manual_open_override")

    fd.save(update_fields=update_fields)

    if fd.is_public:
        messages.success(request, f"{fd.slug} is now OPEN (accepting new submissions).")
    else:
        messages.warning(request, f"{fd.slug} is now CLOSED (no new submissions will be accepted).")

    return redirect("admin_apps_list")


def _soft_archive_group(group: FormGroup) -> None:
    FormDefinition.objects.filter(group=group).update(is_public=False)
    if _model_has_field(FormGroup, "is_active"):
        FormGroup.objects.filter(id=group.id).update(is_active=False)


def _extract_storage_path_from_value(value: str) -> str | None:
    if not value:
        return None

    v = value.strip()

    if v.startswith("http://") or v.startswith("https://"):
        parsed = urlparse(v)
        v = parsed.path

    if v.startswith("/media/"):
        v = v[len("/media/"):]

    v = v.lstrip("/")
    return v or None


def _looks_like_file_value(value: str) -> bool:
    if not value:
        return False

    s = value.strip().lower()
    if not s:
        return False

    if s.startswith("http://") or s.startswith("https://"):
        return True
    if s.startswith("/media/") or s.startswith("media/"):
        return True
    if "uploads/" in s:
        return True

    exts = (
        ".pdf", ".png", ".jpg", ".jpeg", ".webp",
        ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".txt", ".csv",
    )
    return s.endswith(exts)


# ----------------------------
# Admin "Apps" dashboard
# ----------------------------
@staff_member_required
def apps_list(request):
    _maybe_run_due_group_reminders()
    try:
        from applications.views import _maybe_run_due_a1_to_a2_reminders
        _maybe_run_due_a1_to_a2_reminders()
    except Exception:
        logger.exception("A1->A2 auto reminder scheduler check failed.")

    masters = list(FormDefinition.objects.filter(slug__in=MASTER_SLUGS).order_by("slug"))

    groups = list(FormGroup.objects.order_by("-number"))
    has_combined_groups = FormGroup.objects.filter(use_combined_application=True).exists()
    group_list = []
    for g in groups:
        _sync_group_open_close(g)
        forms_for_group = list(FormDefinition.objects.filter(group=g).order_by("slug"))
        group_list.append((g, forms_for_group))

    return render(
        request,
        "admin_dash/apps_list.html",
        {
            "masters": masters,
            "create_group_form": CreateGroupForm(),
            "group_list": group_list,
            "has_combined_groups": has_combined_groups,
        },
    )


@staff_member_required
@require_POST
def create_group(request):
    form = CreateGroupForm(request.POST)
    if not form.is_valid():
        masters = list(FormDefinition.objects.filter(slug__in=MASTER_SLUGS).order_by("slug"))
        groups = list(FormGroup.objects.order_by("-number"))
        has_combined_groups = FormGroup.objects.filter(use_combined_application=True).exists()
        group_list = []
        for g in groups:
            forms_for_group = list(FormDefinition.objects.filter(group=g).order_by("slug"))
            group_list.append((g, forms_for_group))

        return render(
            request,
            "admin_dash/apps_list.html",
            {
                "masters": masters,
                "create_group_form": form,
                "group_list": group_list,
                "has_combined_groups": has_combined_groups,
            },
        )

    group_num = form.cleaned_data["group_num"]
    start_month = form.cleaned_data["start_month"]
    end_month = form.cleaned_data["end_month"]
    year = form.cleaned_data["year"]
    start_day = form.cleaned_data["start_day"]
    a2_deadline = form.cleaned_data.get("a2_deadline")
    open_at = form.cleaned_data.get("open_at")
    close_at = form.cleaned_data.get("close_at")
    reminder_1_at = form.cleaned_data.get("reminder_1_at")
    reminder_2_at = form.cleaned_data.get("reminder_2_at")
    reminder_3_at = form.cleaned_data.get("reminder_3_at")

    with transaction.atomic():
        group, _created = FormGroup.objects.get_or_create(
            number=group_num,
            defaults={
                "start_month": start_month,
                "end_month": end_month,
                "year": year,
                "start_day": start_day,
                "a2_deadline": a2_deadline,
                "open_at": open_at,
                "close_at": close_at,
                "reminder_1_at": reminder_1_at,
                "reminder_2_at": reminder_2_at,
                "reminder_3_at": reminder_3_at,
                "use_combined_application": True,
            },
        )
        group.start_month = start_month
        group.end_month = end_month
        group.year = year
        group.start_day = start_day
        group.a2_deadline = a2_deadline
        group.open_at = open_at
        group.close_at = close_at
        update_fields = [
            "start_month",
            "end_month",
            "year",
            "start_day",
            "a2_deadline",
            "open_at",
            "close_at",
        ]
        update_fields.extend(
            _apply_group_reminder_schedule(
                group,
                reminder_1_at,
                reminder_2_at,
                reminder_3_at,
            )
        )
        group.save(update_fields=list(dict.fromkeys(update_fields)))

        masters = FormDefinition.objects.filter(slug__in=MASTER_SLUGS).order_by("slug")
        for master_fd in masters:
            _clone_form(master_fd, group)

    drive_result = None
    try:
        drive_result = ensure_group_drive_tree(
            group_num=group.number,
            start_month=group.start_month,
            end_month=group.end_month,
            year=group.year,
        )
    except Exception as exc:
        logger.exception("Drive folder sync failed for group %s", group.number)
        detail = str(exc).strip()
        if len(detail) > 220:
            detail = detail[:220] + "..."
        messages.warning(
            request,
            (
                f"Grupo {group_num} creado/actualizado, pero falló la creación de carpetas en Drive."
                + (f" Error: {detail}" if detail else "")
            ),
        )

    messages.success(request, f"Grupo {group_num} creado/actualizado y formularios clonados.")
    if drive_result:
        if drive_result.status == "created":
            messages.success(
                request,
                f"Drive: estructura creada para G{group_num} ({drive_result.folder_name}).",
            )
        elif drive_result.status == "exists":
            messages.info(
                request,
                f"Drive: G{group_num} ya existe ({drive_result.folder_name}). No se hicieron cambios.",
                )
        elif drive_result.status == "skipped":
            messages.warning(request, f"Drive: {drive_result.detail}")

        # Seed/update application response CSV files right after group creation, even with 0 submissions.
        if drive_result.status in {"created", "exists"}:
            seed_results = []
            for track in ("E", "M"):
                try:
                    res = sync_group_track_responses_csv(group.number, track)
                    seed_results.append(f"{track}: {res.status} ({res.detail})")
                except Exception as exc:
                    logger.exception("Drive seed CSV sync failed for G%s track %s", group.number, track)
                    seed_results.append(f"{track}: error ({exc})")
            if seed_results:
                messages.info(request, "Drive CSV seed -> " + " | ".join(seed_results))
    _sync_group_open_close(group)
    return redirect("admin_apps_list")


@staff_member_required
@require_POST
def delete_group(request, group_num: int):
    group = get_object_or_404(FormGroup, number=group_num)

    qs_apps = Application.objects.filter(form__group=group)
    has_apps = qs_apps.exists()
    force = request.POST.get("force") == "1"

    if has_apps and not force:
        _soft_archive_group(group)
        messages.warning(
            request,
            "Este grupo tiene postulaciones guardadas, así que no se puede eliminar. "
            "Lo archivamos (formularios ocultos) para proteger el historial. "
            "Si realmente quieres borrarlo todo, usa 'force delete'."
        )
        return redirect("admin_apps_list")

    with transaction.atomic():
        if has_apps and force:
            Answer.objects.filter(application__in=qs_apps).delete()
            qs_apps.delete()

        FormDefinition.objects.filter(group=group).delete()
        group.delete()

    if has_apps and force:
        messages.success(request, "Grupo eliminado PERMANENTEMENTE junto con todas las postulaciones.")
    else:
        messages.success(request, "Grupo eliminado correctamente.")

    return redirect("admin_apps_list")


@staff_member_required
@require_POST
def update_group_dates(request, group_num: int):
    """
    Update start_day, A2 deadline, open/close schedule, and reminder schedule for
    a group without recloning forms.
    """
    group = get_object_or_404(FormGroup, number=group_num)

    try:
        start_day = int(request.POST.get("start_day") or group.start_day or 1)
    except ValueError:
        start_day = group.start_day or 1

    raw_deadline = (request.POST.get("a2_deadline") or "").strip()
    deadline = None
    if raw_deadline:
        try:
            from datetime import datetime
            deadline = datetime.strptime(raw_deadline, "%Y-%m-%d").date()
        except Exception:
            deadline = group.a2_deadline

    raw_open = (request.POST.get("open_at") or "").strip()
    raw_close = (request.POST.get("close_at") or "").strip()
    raw_reminder_1 = (request.POST.get("reminder_1_at") or "").strip()
    raw_reminder_2 = (request.POST.get("reminder_2_at") or "").strip()
    raw_reminder_3 = (request.POST.get("reminder_3_at") or "").strip()
    open_at = group.open_at
    close_at = group.close_at
    reminder_1_at = getattr(group, "reminder_1_at", None)
    reminder_2_at = getattr(group, "reminder_2_at", None)
    reminder_3_at = getattr(group, "reminder_3_at", None)
    try:
        from django.utils import timezone
        if raw_open:
            open_at = timezone.make_aware(datetime.strptime(raw_open, "%Y-%m-%dT%H:%M"))
        if raw_close:
            close_at = timezone.make_aware(datetime.strptime(raw_close, "%Y-%m-%dT%H:%M"))
        if "reminder_1_at" in request.POST:
            reminder_1_at = (
                timezone.make_aware(datetime.strptime(raw_reminder_1, "%Y-%m-%dT%H:%M"))
                if raw_reminder_1
                else None
            )
        if "reminder_2_at" in request.POST:
            reminder_2_at = (
                timezone.make_aware(datetime.strptime(raw_reminder_2, "%Y-%m-%dT%H:%M"))
                if raw_reminder_2
                else None
            )
        if "reminder_3_at" in request.POST:
            reminder_3_at = (
                timezone.make_aware(datetime.strptime(raw_reminder_3, "%Y-%m-%dT%H:%M"))
                if raw_reminder_3
                else None
            )
    except Exception:
        pass

    update_fields: list[str] = []
    if group.start_day != start_day:
        group.start_day = start_day
        update_fields.append("start_day")
    if group.a2_deadline != deadline:
        group.a2_deadline = deadline
        update_fields.append("a2_deadline")
    if group.open_at != open_at:
        group.open_at = open_at
        update_fields.append("open_at")
    if group.close_at != close_at:
        group.close_at = close_at
        update_fields.append("close_at")

    update_fields.extend(
        _apply_group_reminder_schedule(
            group,
            reminder_1_at,
            reminder_2_at,
            reminder_3_at,
        )
    )
    if update_fields:
        group.save(update_fields=list(dict.fromkeys(update_fields)))

    _sync_group_open_close(group)

    messages.success(
        request,
        f"Actualizado Grupo {group.number}: día {start_day}, fecha límite A2 "
        f"{deadline.strftime('%d/%m/%Y') if deadline else 'no definida'}, "
        f"apertura {open_at} / cierre {close_at}."
    )
    return redirect("admin_apps_list")


# ----------------------------
# Database
# ----------------------------
@staff_member_required
def database_home(request):
    masters = list(FormDefinition.objects.filter(slug__in=MASTER_SLUGS).order_by("slug"))

    counts = {
        row["form__slug"]: row["c"]
        for row in Application.objects.values("form__slug").annotate(c=Count("id"))
    }

    groups = list(FormGroup.objects.order_by("number"))
    group_blocks: list[dict] = []
    combined_track_counts = {"E": 0, "M": 0}
    legacy_type_counts = {k: 0 for k in MASTER_SLUGS}

    for g in groups:
        forms_for_group = list(FormDefinition.objects.filter(group=g).order_by("slug"))
        use_combined = bool(getattr(g, "use_combined_application", False))

        if use_combined:
            track_counts = {"E": 0, "M": 0}
            for fd in forms_for_group:
                slug = (fd.slug or "").strip()
                c = counts.get(slug, 0)
                if slug.endswith("E_A1") or slug.endswith("E_A2"):
                    track_counts["E"] += c
                elif slug.endswith("M_A1") or slug.endswith("M_A2"):
                    track_counts["M"] += c
            combined_track_counts["E"] += track_counts["E"]
            combined_track_counts["M"] += track_counts["M"]
            group_blocks.append({
                "group": g,
                "mode": "combined",
                "track_counts": track_counts,
            })
            continue

        for fd in forms_for_group:
            fd.submission_count = counts.get(fd.slug, 0)
            fd.admin_edit_url = reverse("admin:applications_formdefinition_change", args=[fd.id])
            m = GROUP_SLUG_RE.match((fd.slug or "").strip())
            if not m:
                continue
            master = m.group("master")
            if master in legacy_type_counts:
                legacy_type_counts[master] += fd.submission_count

        group_blocks.append({
            "group": g,
            "mode": "legacy",
            "forms": forms_for_group,
        })

    SURVEY_SLUGS = ["PRIMER_E", "FINAL_E", "PRIMER_M", "FINAL_M"]
    surveys = list(FormDefinition.objects.filter(slug__in=SURVEY_SLUGS).order_by("slug"))
    surveys_e = [s for s in surveys if s.slug.endswith("_E")]
    surveys_m = [s for s in surveys if s.slug.endswith("_M")]

    for fd in masters:
        fd.submission_count = counts.get(fd.slug, 0)
        fd.admin_edit_url = reverse("admin:applications_formdefinition_change", args=[fd.id])

    for s in surveys:
        s.submission_count = counts.get(s.slug, 0)
        s.admin_edit_url = reverse("admin:applications_formdefinition_change", args=[s.id])

    latest_graded_by_form: dict[str, GradedFile] = {}
    graded_candidates = (
        GradedFile.objects.exclude(form_slug__startswith="PAIR_G")
        .order_by("-created_at", "-id")
    )
    for gf in graded_candidates:
        slug = (gf.form_slug or "").strip()
        if not slug or slug in latest_graded_by_form:
            continue
        latest_graded_by_form[slug] = gf

    graded_files = sorted(
        latest_graded_by_form.values(),
        key=lambda item: (item.created_at, item.id),
        reverse=True,
    )[:200]
    graded_files_by_group_map = {}
    graded_files_other = []
    for gf in graded_files:
        m = GRADED_GROUP_RE.match((gf.form_slug or "").strip())
        if not m:
            graded_files_other.append(gf)
            continue
        group_num = int(m.group("num"))
        graded_files_by_group_map.setdefault(group_num, []).append(gf)

    graded_files_by_group = [
        {"group_num": group_num, "files": graded_files_by_group_map[group_num]}
        for group_num in sorted(graded_files_by_group_map.keys(), reverse=True)
    ]

    pairing_files = GradedFile.objects.filter(
        form_slug__startswith="PAIR_G"
    ).order_by("-created_at")[:100]

    transfer_form_options: list[dict] = []
    transfer_forms = list(
        FormDefinition.objects.filter(is_master=False, group__isnull=False).select_related("group")
    )
    for fd in transfer_forms:
        slug = (fd.slug or "").strip()
        if not GROUP_SLUG_RE.match(slug):
            continue
        gnum = getattr(getattr(fd, "group", None), "number", None) or _group_number_from_slug(slug) or 0
        transfer_form_options.append(
            {
                "slug": slug,
                "group_num": int(gnum),
                "label": f"G{gnum} — {slug} — {fd.name}",
            }
        )
    transfer_form_options.sort(key=lambda x: (-x["group_num"], x["slug"]))

    source_pool_default_key = "april_recruitment"
    next_group_num = max([g.number for g in groups], default=8) + 1
    pool_assignment_defaults = {
        "source_pool": source_pool_default_key,
        "track": PoolAssignmentForm.TRACK_EMPRENDEDORAS,
        "target_group_num": next_group_num,
    }

    return render(
        request,
        "admin_dash/database_home.html",
        {
            "masters": masters,
            "master_forms": masters,  # template compatibility
            "group_blocks": group_blocks,
            "surveys": surveys,
            "surveys_e": surveys_e,
            "surveys_m": surveys_m,
            "legacy_type_counts": legacy_type_counts,
            "combined_track_counts": combined_track_counts,
            "graded_files": graded_files,
            "graded_files_by_group": graded_files_by_group,
            "graded_files_other": graded_files_other,
            "pairing_files": pairing_files,
            "transfer_form_options": transfer_form_options,
            "pool_source_choices": [
                {"value": key, "label": cfg["label"]}
                for key, cfg in RECRUITMENT_POOL_SOURCES.items()
            ],
            "pool_assignment_defaults": pool_assignment_defaults,
            "database_next": request.get_full_path(),
        },
    )


def _sync_drive_for_group_number(group_num: int) -> list[tuple[str, str, str]]:
    """
    Returns list of tuples:
      (status, label, detail)
    """
    out: list[tuple[str, str, str]] = []

    for track in ("E", "M"):
        label = f"G{group_num}_{track} Respuestas.csv"
        try:
            res = sync_group_track_responses_csv(group_num, track)
            out.append((res.status, label, res.detail))
        except Exception as exc:
            logger.exception("Drive manual sync failed for %s", label)
            out.append(("error", label, str(exc)))

    for slug in (f"G{group_num}_E_A2", f"G{group_num}_M_A2", f"PAIR_G{group_num}"):
        gf = GradedFile.objects.filter(form_slug=slug).order_by("-created_at").first()
        if not gf:
            out.append(("skipped", slug, "No CSV found in database yet."))
            continue
        try:
            res = sync_generated_csv_artifact(slug, gf.csv_text)
            out.append((res.status, slug, res.detail))
        except Exception as exc:
            logger.exception("Drive manual sync failed for artifact %s", slug)
            out.append(("error", slug, str(exc)))

    return out


def _group_forms_by_master_slug(group: FormGroup) -> dict[str, FormDefinition]:
    out: dict[str, FormDefinition] = {}
    forms_for_group = FormDefinition.objects.filter(group=group, is_master=False).order_by("slug", "id")
    for fd in forms_for_group:
        m = GROUP_SLUG_RE.match((fd.slug or "").strip())
        if not m:
            continue
        master_slug = (m.group("master") or "").strip().upper()
        if master_slug and master_slug not in out:
            out[master_slug] = fd
    return out


def _latest_apps_by_normalized_email_for_form(
    form_def: FormDefinition,
    target_emails: set[str],
) -> dict[str, Application]:
    if not target_emails:
        return {}

    qs = (
        Application.objects.filter(form=form_def)
        .exclude(email__isnull=True)
        .exclude(email__exact="")
        .annotate(_email_norm=Lower("email"))
        .filter(_email_norm__in=target_emails)
        .prefetch_related("answers__question")
        .order_by("_email_norm", "-created_at", "-id")
    )

    out: dict[str, Application] = {}
    for app in qs:
        email_norm = (getattr(app, "_email_norm", "") or "").strip().lower()
        if not email_norm or email_norm in out:
            continue
        out[email_norm] = app
    return out


def _copy_application_to_form(source_app: Application, target_form: FormDefinition) -> tuple[Application, int, int]:
    source_answers = list(source_app.answers.select_related("question"))
    target_questions = {
        q.slug: q
        for q in target_form.questions.order_by("position", "id")
    }

    prepared_answers: list[tuple[Question, str]] = []
    skipped_answers = 0
    for ans in source_answers:
        src_slug = getattr(getattr(ans, "question", None), "slug", "")
        if not src_slug:
            continue
        tgt_q = target_questions.get(src_slug)
        if not tgt_q:
            skipped_answers += 1
            continue
        prepared_answers.append((tgt_q, ans.value or ""))

    if not prepared_answers:
        raise ValueError("No matching question slugs were found between source and target forms.")

    new_app = Application.objects.create(
        form=target_form,
        name=source_app.name,
        email=source_app.email,
        tablestakes_score=source_app.tablestakes_score,
        commitment_score=source_app.commitment_score,
        nice_to_have_score=source_app.nice_to_have_score,
        overall_score=source_app.overall_score,
        recommendation=source_app.recommendation,
        invite_token=None,
        invited_to_second_stage=source_app.invited_to_second_stage,
        second_stage_reminder_due_at=source_app.second_stage_reminder_due_at,
        second_stage_reminder_sent_at=source_app.second_stage_reminder_sent_at,
    )
    Answer.objects.bulk_create(
        [
            Answer(
                application=new_app,
                question=question,
                value=value,
            )
            for question, value in prepared_answers
        ]
    )
    return new_app, len(prepared_answers), skipped_answers


@staff_member_required
@require_POST
def database_copy_application(request):
    email = (request.POST.get("email") or "").strip().lower()
    source_form_slug = (request.POST.get("source_form_slug") or "").strip()
    target_form_slug = (request.POST.get("target_form_slug") or "").strip()
    raw_source_app_id = (request.POST.get("source_app_id") or "").strip()

    if not email:
        messages.error(request, "Email is required.")
        return _database_next_redirect(request, fallback_name="admin_database")
    if not source_form_slug or not target_form_slug:
        messages.error(request, "Select both source and target applications.")
        return _database_next_redirect(request, fallback_name="admin_database")
    if source_form_slug == target_form_slug:
        messages.error(request, "Source and target applications must be different.")
        return _database_next_redirect(request, fallback_name="admin_database")

    source_form = FormDefinition.objects.filter(slug=source_form_slug, is_master=False).first()
    target_form = FormDefinition.objects.filter(slug=target_form_slug, is_master=False).first()
    if not source_form or not target_form:
        messages.error(request, "Source or target application form was not found.")
        return _database_next_redirect(request, fallback_name="admin_database")

    source_candidates = (
        Application.objects.filter(form=source_form, email__iexact=email)
        .prefetch_related("answers__question")
        .order_by("-created_at", "-id")
    )
    if not source_candidates.exists():
        messages.error(
            request,
            f"No submissions found for {email} in {source_form.slug}.",
        )
        return _database_next_redirect(request, fallback_name="admin_database")

    source_app = None
    if raw_source_app_id:
        try:
            source_app_id = int(raw_source_app_id)
        except ValueError:
            messages.error(request, "Source submission ID must be a number.")
            return _database_next_redirect(request, fallback_name="admin_database")
        source_app = source_candidates.filter(id=source_app_id).first()
        if not source_app:
            messages.error(
                request,
                f"Submission #{source_app_id} was not found for {email} in {source_form.slug}.",
            )
            return _database_next_redirect(request, fallback_name="admin_database")
    else:
        source_app = source_candidates.first()
        if source_candidates.count() > 1:
            messages.info(
                request,
                (
                    f"Multiple source submissions found for {email} in {source_form.slug}. "
                    f"Using the latest one (#{source_app.id})."
                ),
            )

    target_questions = {
        q.slug: q
        for q in target_form.questions.filter(active=True).order_by("position", "id")
    }
    source_answers = list(source_app.answers.select_related("question"))

    copied_answers = 0
    skipped_answers = 0
    try:
        with transaction.atomic():
            new_app = Application.objects.create(
                form=target_form,
                name=source_app.name,
                email=source_app.email,
                tablestakes_score=source_app.tablestakes_score,
                commitment_score=source_app.commitment_score,
                nice_to_have_score=source_app.nice_to_have_score,
                overall_score=source_app.overall_score,
                recommendation=source_app.recommendation,
                invite_token=None,
                invited_to_second_stage=source_app.invited_to_second_stage,
                second_stage_reminder_due_at=source_app.second_stage_reminder_due_at,
                second_stage_reminder_sent_at=source_app.second_stage_reminder_sent_at,
            )

            for ans in source_answers:
                src_slug = getattr(getattr(ans, "question", None), "slug", "")
                if not src_slug:
                    continue
                tgt_q = target_questions.get(src_slug)
                if not tgt_q:
                    skipped_answers += 1
                    continue
                Answer.objects.create(
                    application=new_app,
                    question=tgt_q,
                    value=ans.value or "",
                )
                copied_answers += 1

            if copied_answers == 0:
                raise ValueError("No matching questions were found between source and target forms.")
    except Exception as exc:
        messages.error(request, f"Could not copy application: {exc}")
        return _database_next_redirect(request, fallback_name="admin_database")

    messages.success(
        request,
        (
            f"Copied submission #{source_app.id} ({source_form.slug}) to #{new_app.id} ({target_form.slug}) "
            f"for {email}. Copied {copied_answers} answers."
        ),
    )
    if skipped_answers:
        messages.info(
            request,
            f"Skipped {skipped_answers} answers because those question slugs do not exist in {target_form.slug}.",
        )
    return _database_next_redirect(request, fallback_name="admin_database")


@staff_member_required
@require_POST
def database_create_assigned_group(request):
    form = PoolAssignmentForm(request.POST)
    if not form.is_valid():
        for _field, errs in form.errors.items():
            for err in errs:
                messages.error(request, str(err))
        return _database_next_redirect(request, fallback_name="admin_database")

    source_pool = str(form.cleaned_data["source_pool"]).strip()
    source_cfg = RECRUITMENT_POOL_SOURCES.get(source_pool)
    if not source_cfg:
        messages.error(request, "Invalid source application pool selected.")
        return _database_next_redirect(request, fallback_name="admin_database")
    source_group_num = int(source_cfg["group_num"])
    source_label = str(source_cfg["label"])
    selected_track = str(form.cleaned_data["track"]).strip().upper()
    track_label = "Emprendedoras" if selected_track == "E" else "Mentoras"
    target_group_num = int(form.cleaned_data["target_group_num"])
    wanted_emails = {
        e.strip().lower()
        for e in (form.cleaned_data.get("normalized_emails") or [])
        if str(e or "").strip()
    }

    source_group = FormGroup.objects.filter(number=source_group_num).first()
    if not source_group:
        messages.error(
            request,
            f"Source application pool '{source_label}' is not configured correctly.",
        )
        return _database_next_redirect(request, fallback_name="admin_database")

    try:
        start_day = int(getattr(source_group, "start_day", 0) or 0)
        year = int(getattr(source_group, "year", 0) or 0)
    except (TypeError, ValueError):
        start_day = 0
        year = 0
    start_month = str(getattr(source_group, "start_month", "") or "").strip().lower()
    end_month = str(getattr(source_group, "end_month", "") or "").strip().lower()
    if not (start_day and year and start_month and end_month):
        messages.error(
            request,
            (
                f"Source application pool '{source_label}' is missing date configuration. "
                "Update that group first, then assign applicants."
            ),
        )
        return _database_next_redirect(request, fallback_name="admin_database")

    source_forms_by_master = _group_forms_by_master_slug(source_group)
    source_forms_by_master = {
        master_slug: fd
        for master_slug, fd in source_forms_by_master.items()
        if master_slug.startswith(f"{selected_track}_")
    }

    copied_apps = 0
    copied_answers = 0
    skipped_existing = 0
    skipped_no_match = 0
    unmatched_email_count = 0
    unmatched_emails: list[str] = []
    unmatched_inserted = 0
    unmatched_already_present = 0
    matched_emails: set[str] = set()
    assigned_track_emails: dict[str, set[str]] = {"E": set(), "M": set()}

    try:
        with transaction.atomic():
            target_group, created_group = FormGroup.objects.get_or_create(
                number=target_group_num,
                defaults={
                    "start_day": start_day,
                    "start_month": start_month,
                    "end_month": end_month,
                    "year": year,
                    "use_combined_application": True,
                },
            )

            update_fields: list[str] = []
            if target_group.start_day != start_day:
                target_group.start_day = start_day
                update_fields.append("start_day")
            if target_group.start_month != start_month:
                target_group.start_month = start_month
                update_fields.append("start_month")
            if target_group.end_month != end_month:
                target_group.end_month = end_month
                update_fields.append("end_month")
            if target_group.year != year:
                target_group.year = year
                update_fields.append("year")
            if not target_group.use_combined_application:
                target_group.use_combined_application = True
                update_fields.append("use_combined_application")
            if update_fields:
                target_group.save(update_fields=update_fields)

            required_master_slugs = [f"{selected_track}_A1", f"{selected_track}_A2"]
            for master_slug in required_master_slugs:
                if master_slug not in MASTER_SLUGS:
                    continue
                target_slug = f"G{target_group_num}_{master_slug}"
                if FormDefinition.objects.filter(slug=target_slug).exists():
                    continue
                master_form = FormDefinition.objects.filter(slug=master_slug).first()
                if master_form:
                    _clone_form(master_form, target_group)

            if target_group.id != source_group.id:
                FormDefinition.objects.filter(group=target_group).update(
                    is_public=False,
                    accepting_responses=False,
                )

            target_forms_by_master = _group_forms_by_master_slug(target_group)
            existing_target_emails_by_master: dict[str, set[str]] = {}
            for master_slug, target_form in target_forms_by_master.items():
                if not master_slug.startswith(f"{selected_track}_"):
                    continue
                existing_target_emails = set(
                    Application.objects.filter(form=target_form)
                    .exclude(email__isnull=True)
                    .exclude(email__exact="")
                    .annotate(_email_norm=Lower("email"))
                    .values_list("_email_norm", flat=True)
                )
                existing_target_emails_by_master[master_slug] = {
                    (e or "").strip().lower() for e in existing_target_emails if (e or "").strip()
                }

            if not existing_target_emails_by_master:
                messages.error(
                    request,
                    (
                        f"Could not prepare target dataset for Group {target_group_num} "
                        f"({track_label})."
                    ),
                )
                return _database_next_redirect(request, fallback_name="admin_database")

            for master_slug, source_form in source_forms_by_master.items():
                if not master_slug.startswith(f"{selected_track}_"):
                    continue
                target_form = target_forms_by_master.get(master_slug)
                if not target_form:
                    continue

                source_apps_by_email = _latest_apps_by_normalized_email_for_form(
                    source_form,
                    wanted_emails,
                )
                track = "E" if master_slug.startswith("E_") else ("M" if master_slug.startswith("M_") else "")
                existing_target_emails = existing_target_emails_by_master.setdefault(master_slug, set())

                for email_norm, source_app in source_apps_by_email.items():
                    matched_emails.add(email_norm)
                    if email_norm in existing_target_emails:
                        skipped_existing += 1
                        if track:
                            assigned_track_emails[track].add(email_norm)
                        continue
                    try:
                        _new_app, copied_count, skipped_count = _copy_application_to_form(source_app, target_form)
                    except ValueError:
                        skipped_no_match += 1
                        continue
                    copied_apps += 1
                    copied_answers += copied_count
                    skipped_no_match += skipped_count
                    existing_target_emails.add(email_norm)
                    if track:
                        assigned_track_emails[track].add(email_norm)

            unmatched_emails = sorted(wanted_emails - matched_emails)
            unmatched_email_count = len(unmatched_emails)

            placeholder_master_slug = (
                f"{selected_track}_A1"
                if f"{selected_track}_A1" in existing_target_emails_by_master
                else f"{selected_track}_A2"
            )
            placeholder_form = target_forms_by_master.get(placeholder_master_slug)
            existing_for_placeholder = existing_target_emails_by_master.setdefault(
                placeholder_master_slug,
                set(),
            )
            existing_any_target_track = set()
            for vals in existing_target_emails_by_master.values():
                existing_any_target_track.update(vals)

            if placeholder_form:
                for email in unmatched_emails:
                    assigned_track_emails[selected_track].add(email)
                    if email in existing_any_target_track:
                        unmatched_already_present += 1
                        continue
                    Application.objects.create(
                        form=placeholder_form,
                        name=(email or "")[:200],
                        email=email,
                    )
                    unmatched_inserted += 1
                    existing_for_placeholder.add(email)
                    existing_any_target_track.add(email)

            participant_list, _ = GroupParticipantList.objects.get_or_create(group=target_group)
            current_mentoras = set(_norm_email_list(participant_list.mentoras_emails_text or ""))
            current_emprendedoras = set(_norm_email_list(participant_list.emprendedoras_emails_text or ""))

            merged_mentoras = sorted(
                current_mentoras | (assigned_track_emails["M"] if selected_track == "M" else set())
            )
            merged_emprendedoras = sorted(
                current_emprendedoras | (assigned_track_emails["E"] if selected_track == "E" else set())
            )

            participant_updates: list[str] = []
            new_mentoras_text = "\n".join(merged_mentoras)
            new_emprendedoras_text = "\n".join(merged_emprendedoras)
            if participant_list.mentoras_emails_text != new_mentoras_text:
                participant_list.mentoras_emails_text = new_mentoras_text
                participant_updates.append("mentoras_emails_text")
            if participant_list.emprendedoras_emails_text != new_emprendedoras_text:
                participant_list.emprendedoras_emails_text = new_emprendedoras_text
                participant_updates.append("emprendedoras_emails_text")
            if participant_updates:
                participant_list.save(update_fields=participant_updates + ["updated_at"])

    except Exception as exc:
        messages.error(request, f"Could not create assigned group: {exc}")
        return _database_next_redirect(request, fallback_name="admin_database")

    if created_group:
        messages.success(
            request,
            f"Created Group {target_group_num} from {source_label} ({track_label}).",
        )
    else:
        messages.success(
            request,
            f"Updated Group {target_group_num} from {source_label} ({track_label}).",
        )
    seeded_count = len(assigned_track_emails["E"] if selected_track == "E" else assigned_track_emails["M"])
    messages.success(
        request,
        (
            f"Copied {copied_apps} application(s), {copied_answers} answer(s). "
            f"{track_label} seeded: {seeded_count}. "
            f"Unmatched emails added as email-only rows: {unmatched_inserted}."
        ),
    )
    if skipped_existing:
        messages.info(
            request,
            f"Skipped {skipped_existing} already-existing application(s) in the target group.",
        )
    if skipped_no_match:
        messages.warning(
            request,
            (
                f"Skipped {skipped_no_match} item(s) because source and target forms "
                "did not share matching question slugs."
            ),
        )
    if unmatched_email_count:
        messages.warning(
            request,
            f"{unmatched_email_count} pasted email(s) did not match any application in {source_label}.",
        )
        preview_limit = 40
        preview = unmatched_emails[:preview_limit]
        remainder = max(0, len(unmatched_emails) - preview_limit)
        preview_text = ", ".join(preview)
        if remainder:
            preview_text = f"{preview_text}, ... (+{remainder} more)"
        messages.warning(
            request,
            f"Unmatched emails: {preview_text}",
        )
        if unmatched_already_present:
            messages.info(
                request,
                (
                    f"{unmatched_already_present} unmatched email(s) were already present in the "
                    f"Group {target_group_num} {track_label} dataset."
                ),
            )
    return _database_next_redirect(request, fallback_name="admin_database")


def _run_database_drive_sync_background(group_numbers: list[int]) -> None:
    try:
        for gnum in group_numbers:
            results = _sync_drive_for_group_number(gnum)
            for status, label, detail in results:
                if status == "updated":
                    logger.info("Drive background sync updated: %s (%s)", label, detail)
                elif status == "error":
                    logger.error("Drive background sync error: %s (%s)", label, detail)
                else:
                    logger.info("Drive background sync skipped: %s (%s)", label, detail)
    except Exception:
        logger.exception("Drive background sync crashed")


@staff_member_required
@require_POST
def database_sync_drive(request):
    raw_group = (request.POST.get("group_num") or "").strip()
    group_numbers: list[int]

    if raw_group:
        try:
            group_numbers = [int(raw_group)]
        except ValueError:
            messages.error(request, f"Invalid group number: {raw_group}")
            return _database_next_redirect(request, fallback_name="admin_database")
    else:
        group_numbers = list(
            FormGroup.objects.order_by("number").values_list("number", flat=True)
        )

    if not group_numbers:
        messages.info(request, "No groups found to sync.")
        return _database_next_redirect(request, fallback_name="admin_database")

    # Full sync can exceed HTTP timeout on production; run asynchronously.
    if not raw_group:
        threading.Thread(
            target=_run_database_drive_sync_background,
            args=(group_numbers,),
            daemon=True,
        ).start()
        messages.success(
            request,
            f"Started background Drive sync for {len(group_numbers)} groups. Refresh in a minute to verify files in Drive.",
        )
        return _database_next_redirect(request, fallback_name="admin_database")

    updated = 0
    skipped = 0
    errors = 0
    details: list[str] = []

    for gnum in group_numbers:
        for status, label, detail in _sync_drive_for_group_number(gnum):
            if status == "updated":
                updated += 1
            elif status == "error":
                errors += 1
                if len(details) < 8:
                    details.append(f"{label}: {detail}")
            else:
                skipped += 1
                if len(details) < 8:
                    details.append(f"{label}: {detail}")

    scope = f"group {group_numbers[0]}" if len(group_numbers) == 1 else "all groups"
    messages.success(
        request,
        f"Drive manual sync completed for {scope}: {updated} updated, {skipped} skipped, {errors} errors.",
    )
    if details:
        messages.info(request, " | ".join(details))

    return _database_next_redirect(request, fallback_name="admin_database")


@staff_member_required
def database_form_detail(request, form_slug: str):
    form_def = get_object_or_404(FormDefinition, slug=form_slug)

    apps = (
        Application.objects.filter(form=form_def)
        .prefetch_related("answers__question", "form")
        .order_by("-created_at", "-id")
    )
    submission_count = apps.count()
    is_first_application = (form_def.slug or "").endswith("E_A1") or (form_def.slug or "").endswith("M_A1")
    second_stage_sent_count = (
        apps.filter(invited_to_second_stage=True).count()
        if is_first_application
        else None
    )

    headers, rows = _build_csv_for_form(form_def)
    preview_html = _csv_preview_html(headers, rows, max_rows=None)

    return render(
        request,
        "admin_dash/database_form_detail.html",
        {
            "form_def": form_def,
            "apps": apps,
            "submission_count": submission_count,
            "is_first_application": is_first_application,
            "second_stage_sent_count": second_stage_sent_count,
            "preview_html": preview_html,
            "sheet_headers": headers,
            "sheet_rows": rows,
        },
    )


@staff_member_required
def database_form_master_csv(request, form_slug: str):
    form_def = get_object_or_404(FormDefinition, slug=form_slug)
    headers, rows = _build_csv_for_form(form_def)
    return _csv_http_response(f"{form_slug}_MASTER.csv", headers, rows)


@staff_member_required
def database_type_detail(request, app_type: str):
    app_type = (app_type or "").upper().strip()
    if app_type not in MASTER_SLUGS:
        raise Http404("Unsupported application type")

    group_raw = (request.GET.get("group") or "").strip()
    selected_group: int | None = int(group_raw) if group_raw.isdigit() else None

    headers, rows, forms = _build_csv_for_app_type(
        app_type,
        selected_group,
        include_combined_groups=False,
    )
    preview_html = _csv_preview_html(headers, rows, max_rows=None)

    all_forms = _group_forms_for_app_type(app_type, include_combined_groups=False)
    group_options = sorted(
        g
        for g in {
            getattr(getattr(fd, "group", None), "number", None)
            or _group_number_from_slug(fd.slug or "")
            for fd in all_forms
        }
        if g is not None
    )

    apps = (
        Application.objects.filter(form__in=forms)
        .select_related("form", "form__group")
        .order_by("-created_at", "-id")
    ) if forms else []

    return render(
        request,
        "admin_dash/database_type_detail.html",
        {
            "app_type": app_type,
            "selected_group": selected_group,
            "group_options": group_options,
            "apps": apps,
            "preview_html": preview_html,
            "rows_count": len(rows),
            "sheet_headers": headers,
            "sheet_rows": rows,
        },
    )


@staff_member_required
def database_type_master_csv(request, app_type: str):
    app_type = (app_type or "").upper().strip()
    if app_type not in MASTER_SLUGS:
        raise Http404("Unsupported application type")

    group_raw = (request.GET.get("group") or "").strip()
    selected_group: int | None = int(group_raw) if group_raw.isdigit() else None

    headers, rows, _forms = _build_csv_for_app_type(
        app_type,
        selected_group,
        include_combined_groups=False,
    )
    filename = (
        f"{app_type}_ALL_GROUPS.csv"
        if selected_group is None
        else f"{app_type}_G{selected_group}.csv"
    )
    return _csv_http_response(filename, headers, rows)


@staff_member_required
def database_track_detail(request, track: str):
    track = (track or "").upper().strip()
    if track not in {"E", "M"}:
        raise Http404("Unsupported combined track")

    group_raw = (request.GET.get("group") or "").strip()
    selected_group: int | None = int(group_raw) if group_raw.isdigit() else None
    completion_filter = (request.GET.get("completion") or "").strip().lower()
    if completion_filter == TRACK_COMPLETION_FILTER_EXCLUDE_A2_ONLY:
        completion_filter = TRACK_COMPLETION_FILTER_A1_ONLY
    if completion_filter not in {
        TRACK_COMPLETION_FILTER_ALL,
        TRACK_COMPLETION_FILTER_A1_ONLY,
        TRACK_COMPLETION_FILTER_A1_A2,
    }:
        completion_filter = TRACK_COMPLETION_FILTER_ALL

    headers, rows, forms = _build_csv_for_track(track, selected_group)

    second_part_forms = [
        fd for fd in forms
        if (fd.slug or "").strip().upper().endswith(f"{track}_A2")
    ]
    second_part_completed_count = (
        Application.objects.filter(form__in=second_part_forms)
        .exclude(email__isnull=True)
        .exclude(email__exact="")
        .annotate(_email_norm=Lower("email"))
        .values("_email_norm")
        .distinct()
        .count()
        if second_part_forms
        else 0
    )

    all_forms = _group_forms_for_track(track)
    group_options = sorted(
        g
        for g in {
            getattr(getattr(fd, "group", None), "number", None)
            or _group_number_from_slug(fd.slug or "")
            for fd in all_forms
        }
        if g is not None
    )

    apps_qs = (
        Application.objects.filter(form__in=forms)
        .select_related("form", "form__group")
        .order_by("-created_at", "-id")
    ) if forms else Application.objects.none()

    if completion_filter == TRACK_COMPLETION_FILTER_A1_ONLY:
        apps_qs = apps_qs.filter(form__slug__iendswith=f"{track}_A1")
    elif completion_filter == TRACK_COMPLETION_FILTER_A1_A2:
        apps_qs = apps_qs.filter(form__slug__iendswith=f"{track}_A2")

    apps = list(apps_qs)

    if completion_filter in {TRACK_COMPLETION_FILTER_A1_ONLY, TRACK_COMPLETION_FILTER_A1_A2} and rows:
        allowed_app_ids = {app.id for app in apps}
        app_id_idx = headers.index("application_id") if "application_id" in headers else -1
        if app_id_idx >= 0:
            filtered_rows: list[list[str]] = []
            for row in rows:
                if app_id_idx >= len(row):
                    continue
                raw_id = str(row[app_id_idx] or "").strip()
                if raw_id.isdigit() and int(raw_id) in allowed_app_ids:
                    filtered_rows.append(row)
            rows = filtered_rows

    preview_html = _csv_preview_html(headers, rows, max_rows=None)

    track_label = "Emprendedoras" if track == "E" else "Mentoras"
    return render(
        request,
        "admin_dash/database_track_detail.html",
        {
            "track": track,
            "track_label": track_label,
            "selected_group": selected_group,
            "completion_filter": completion_filter,
            "group_options": group_options,
            "apps": apps,
            "second_part_completed_count": second_part_completed_count,
            "preview_html": preview_html,
            "rows_count": len(rows),
            "sheet_headers": headers,
            "sheet_rows": rows,
        },
    )


@staff_member_required
def database_track_master_csv(request, track: str):
    track = (track or "").upper().strip()
    if track not in {"E", "M"}:
        raise Http404("Unsupported combined track")

    group_raw = (request.GET.get("group") or "").strip()
    selected_group: int | None = int(group_raw) if group_raw.isdigit() else None

    headers, rows, _forms = _build_csv_for_track(track, selected_group)
    filename = (
        f"{track}_COMBINED_ALL_GROUPS.csv"
        if selected_group is None
        else f"{track}_COMBINED_G{selected_group}.csv"
    )
    return _csv_http_response(filename, headers, rows)


@staff_member_required
def database_submission_detail(request, app_id: int):
    app = get_object_or_404(
        Application.objects.select_related("form").prefetch_related("answers__question"),
        id=app_id,
    )

    questions = list(app.form.questions.filter(active=True).order_by("position", "id"))
    amap = {a.question.slug: a.value for a in app.answers.all()}
    ordered_answers = [(q, amap.get(q.slug, "")) for q in questions]

    return render(
        request,
        "admin_dash/database_submission_detail.html",
        {"app": app, "ordered_answers": ordered_answers},
    )


@staff_member_required
def export_form_csv(request, form_slug: str):
    form_def = get_object_or_404(FormDefinition, slug=form_slug)
    headers, rows = _build_csv_for_form(form_def)
    return _csv_http_response(f"{form_slug}.csv", headers, rows)


# ----------------------------
# Delete submission (REAL delete)
# ----------------------------
def _database_next_redirect(request, fallback_name: str = "admin_database", **kwargs):
    next_url = (request.POST.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)
    return redirect(fallback_name, **kwargs) if kwargs else redirect(fallback_name)


def _delete_application_record(app: Application) -> int:
    answers = Answer.objects.filter(application=app)
    deleted_storage = 0

    for ans in answers:
        v = (ans.value or "").strip()
        if not v or not _looks_like_file_value(v):
            continue

        storage_path = _extract_storage_path_from_value(v)
        if not storage_path:
            continue
        try:
            if default_storage.exists(storage_path):
                default_storage.delete(storage_path)
                deleted_storage += 1
        except Exception:
            pass

    app.delete()
    return deleted_storage


@staff_member_required
@require_POST
def delete_submission(request, app_id: int):
    app = get_object_or_404(Application.objects.select_related("form"), id=app_id)
    form_slug = app.form.slug
    deleted_storage = _delete_application_record(app)

    msg = "Submission eliminada de la base de datos."
    if deleted_storage:
        msg += f" Archivos eliminados del storage: {deleted_storage}."
    messages.success(request, msg)

    return _database_next_redirect(
        request,
        fallback_name="admin_database_form_detail",
        form_slug=form_slug,
    )


@staff_member_required
@require_POST
def bulk_delete_submissions(request):
    raw_ids = request.POST.getlist("app_ids")
    app_ids: list[int] = []
    seen_ids: set[int] = set()
    for raw in raw_ids:
        try:
            app_id = int(raw)
        except (TypeError, ValueError):
            continue
        if app_id in seen_ids:
            continue
        seen_ids.add(app_id)
        app_ids.append(app_id)

    if not app_ids:
        messages.info(request, "No se seleccionaron submissions para eliminar.")
        return _database_next_redirect(request)

    apps = list(
        Application.objects.select_related("form")
        .filter(id__in=app_ids)
        .order_by("id")
    )
    if not apps:
        messages.info(request, "Las submissions seleccionadas ya no existen.")
        return _database_next_redirect(request)

    deleted_count = 0
    deleted_storage = 0
    fallback_form_slug = apps[0].form.slug if len({app.form.slug for app in apps}) == 1 else None

    for app in apps:
        deleted_storage += _delete_application_record(app)
        deleted_count += 1

    msg = f"Submissions eliminadas: {deleted_count}."
    if deleted_storage:
        msg += f" Archivos eliminados del storage: {deleted_storage}."
    messages.success(request, msg)

    if fallback_form_slug:
        return _database_next_redirect(
            request,
            fallback_name="admin_database_form_detail",
            form_slug=fallback_form_slug,
        )
    return _database_next_redirect(request)


# ----------------------------
# Delete graded/file actions (admin database)
# ----------------------------
@staff_member_required
@require_POST
def delete_graded_file(request, graded_file_id: int):
    gf = get_object_or_404(GradedFile, id=graded_file_id)
    form_slug = gf.form_slug
    gf.delete()
    messages.success(request, f"Archivo calificado eliminado: {form_slug}.")
    return _database_next_redirect(request)


@staff_member_required
@require_POST
def delete_answer_file_value(request, answer_id: int):
    ans = get_object_or_404(
        Answer.objects.select_related("application", "question"),
        id=answer_id,
    )

    if not _looks_like_file_value(ans.value or ""):
        messages.info(request, "Esta respuesta no parece ser un archivo. No se hizo ningún cambio.")
        return redirect("admin_database_submission_detail", app_id=ans.application_id)

    storage_path = _extract_storage_path_from_value(ans.value or "")

    deleted_from_storage = False
    storage_error = None

    if storage_path:
        try:
            if default_storage.exists(storage_path):
                default_storage.delete(storage_path)
                deleted_from_storage = True
        except Exception as e:
            storage_error = str(e)

    ans.value = ""
    ans.save(update_fields=["value"])

    if deleted_from_storage:
        messages.success(request, "Archivo eliminado del storage y referencia borrada.")
    else:
        msg = "Referencia borrada."
        if storage_path:
            msg += f" No se pudo borrar del storage (path: {storage_path})."
        else:
            msg += " No se pudo mapear el valor a un archivo del storage."
        if storage_error:
            msg += f" Error: {storage_error}"
        messages.warning(request, msg)

    return redirect("admin_database_submission_detail", app_id=ans.application_id)


@staff_member_required
@require_POST
def delete_application_files(request, app_id: int):
    app = get_object_or_404(Application, id=app_id)

    answers = Answer.objects.filter(application=app)

    cleared_count = 0
    deleted_storage_count = 0
    skipped_count = 0

    for ans in answers:
        v = (ans.value or "").strip()
        if not v:
            continue

        if not _looks_like_file_value(v):
            skipped_count += 1
            continue

        storage_path = _extract_storage_path_from_value(v)
        deleted_from_storage = False

        if storage_path:
            try:
                if default_storage.exists(storage_path):
                    default_storage.delete(storage_path)
                    deleted_from_storage = True
            except Exception:
                deleted_from_storage = False

        ans.value = ""
        ans.save(update_fields=["value"])
        cleared_count += 1
        if deleted_from_storage:
            deleted_storage_count += 1

    if cleared_count == 0:
        messages.info(
            request,
            f"No se encontraron archivos para borrar. (Se omitieron {skipped_count} respuestas que no eran archivos.)"
        )
    else:
        messages.success(
            request,
            f"Referencias de archivo borradas: {cleared_count} "
            f"(archivos eliminados del storage: {deleted_storage_count}). "
            f"Omitidas (no-archivo): {skipped_count}."
        )

    return redirect("admin_database_submission_detail", app_id=app.id)


# ============================================================
# Grading (Admin) — BATCH PER FORM + CSV UPLOAD + MASTER CSV
# ============================================================

A2_FORM_RE = re.compile(r"^(G\d+_)?(E_A2|M_A2)$")


def _redirect_back_to_grading(request):
    group = (request.GET.get("group") or "").strip()
    url = reverse("admin_grading_home")
    if group:
        url = f"{url}?group={group}"
    return redirect(url)


def grading_home(request):
    group = (request.GET.get("group") or "").strip()

    fds = FormDefinition.objects.filter(slug__regex=r"^(G\d+_)?(E_A2|M_A2)$").order_by("slug")
    if group:
        fds = fds.filter(slug__startswith=f"G{group}_")

    form_slugs = [fd.slug for fd in fds]
    latest_graded_by_slug: dict[str, GradedFile] = {}
    if form_slugs:
        latest_candidates = (
            GradedFile.objects.filter(form_slug__in=form_slugs)
            .order_by("-created_at", "-id")
        )
        for gf in latest_candidates:
            slug = (gf.form_slug or "").strip()
            if slug and slug not in latest_graded_by_slug:
                latest_graded_by_slug[slug] = gf

    totals = {
        row["form__slug"]: row["c"]
        for row in Application.objects.filter(form__in=fds)
        .values("form__slug")
        .annotate(c=Count("id"))
    }

    pending = {
        row["form__slug"]: row["c"]
        for row in (
            Application.objects.filter(form__in=fds)
            .filter(Q(recommendation__isnull=True) | Q(recommendation=""))
            .values("form__slug")
            .annotate(c=Count("id"))
        )
    }

    rows = []
    for fd in fds:
        latest_file = latest_graded_by_slug.get(fd.slug)
        rows.append({
            "slug": fd.slug,
            "name": fd.name,
            "total": totals.get(fd.slug, 0),
            "pending": pending.get(fd.slug, 0),
            "latest_graded_file_id": latest_file.id if latest_file else None,
            "latest_graded_at": latest_file.created_at if latest_file else None,
        })

    return render(
        request,
        "admin_dash/grading_home.html",
        {
            "group": group,
            "forms": rows,
        },
    )


@staff_member_required
def grading_live_sheet(request, form_slug: str):
    latest_file = (
        GradedFile.objects.filter(form_slug=form_slug)
        .order_by("-created_at", "-id")
        .first()
    )
    if not latest_file:
        messages.error(request, f"No graded file found for {form_slug}.")
        return redirect("admin_grading_home")

    headers, rows = _csv_text_to_grid(latest_file.csv_text or "")

    if request.method == "POST":
        if not headers:
            messages.error(request, "This graded file has no header row and cannot be edited in-sheet.")
            return redirect("admin_grading_live_sheet", form_slug=form_slug)

        headers_raw = (request.POST.get("headers_json") or "").strip()
        edited_headers = list(headers)
        if headers_raw:
            try:
                headers_payload = json.loads(headers_raw)
            except json.JSONDecodeError:
                messages.error(request, "Could not read sheet columns. Please try again.")
                return redirect("admin_grading_live_sheet", form_slug=form_slug)

            if not isinstance(headers_payload, list):
                messages.error(request, "Sheet columns were out of sync. Please reload and try again.")
                return redirect("admin_grading_live_sheet", form_slug=form_slug)

            edited_headers = ["" if h is None else str(h) for h in headers_payload]

        if not edited_headers:
            messages.error(request, "The sheet must have at least one column.")
            return redirect("admin_grading_live_sheet", form_slug=form_slug)

        rows_raw = (request.POST.get("rows_json") or "").strip()
        try:
            rows_payload = json.loads(rows_raw) if rows_raw else []
        except json.JSONDecodeError:
            messages.error(request, "Could not read sheet edits. Please try again.")
            return redirect("admin_grading_live_sheet", form_slug=form_slug)

        rows = _normalize_csv_data_rows(rows_payload, len(edited_headers))
        csv_text = _grid_to_csv_text(edited_headers, rows)

        latest_file.csv_text = csv_text
        latest_file.save(update_fields=["csv_text"])

        try:
            drive_sync = sync_generated_csv_artifact(form_slug, csv_text)
            messages.success(
                request,
                f"Saved sheet edits. Drive sync: {drive_sync.status}.",
            )
        except Exception:
            logger.exception("Drive sync failed after sheet edit for %s", form_slug)
            messages.warning(
                request,
                "Saved sheet edits, but Drive sync failed. You can retry from Database > Sync.",
            )

        return redirect("admin_grading_live_sheet", form_slug=form_slug)

    return render(
        request,
        "admin_dash/grading_live_sheet.html",
        {
            "form_slug": form_slug,
            "graded_file": latest_file,
            "headers": headers,
            "rows": rows,
        },
    )


@staff_member_required
@require_POST
def grade_application(request, app_id: int):
    """
    Compatibility endpoint: grade one submission.
    """
    app = get_object_or_404(
        Application.objects.select_related("form").prefetch_related("answers__question"),
        id=app_id,
    )

    if not A2_FORM_RE.match(app.form.slug):
        messages.error(request, "This application is not eligible for grading.")
        return _redirect_back_to_grading(request)

    try:
        scores = grade_from_answers(app)
        app.tablestakes_score = scores.get("tablestakes_score")
        app.commitment_score = scores.get("commitment_score")
        app.nice_to_have_score = scores.get("nice_to_have_score")
        app.overall_score = scores.get("overall_score")
        app.recommendation = scores.get("recommendation")
        app.save(
            update_fields=[
                "tablestakes_score",
                "commitment_score",
                "nice_to_have_score",
                "overall_score",
                "recommendation",
            ]
        )
        messages.success(request, f"Graded submission #{app.id} ({app.form.slug}).")
    except Exception as e:
        messages.error(request, f"Grading failed for #{app.id}: {e}")

    return _redirect_back_to_grading(request)


@staff_member_required
@require_POST
def grade_form_batch(request, form_slug: str):
    """
    Batch grades ALL pending submissions for a given A2 form slug.
    """
    if not A2_FORM_RE.match(form_slug):
        messages.error(request, f"{form_slug} is not an A2 form slug.")
        return _redirect_back_to_grading(request)

    fd = get_object_or_404(FormDefinition, slug=form_slug)

    qs = (
        Application.objects.filter(form=fd)
        .select_related("form")
        .prefetch_related("answers__question")
        .order_by("created_at", "id")
    )

    qs_pending = qs.filter(Q(recommendation__isnull=True) | Q(recommendation=""))

    total = qs.count()
    pending = qs_pending.count()

    if pending == 0:
        messages.info(request, f"{form_slug}: No pending submissions to grade. Total submissions: {total}.")
        return _redirect_back_to_grading(request)

    updated = 0
    failed = 0

    with transaction.atomic():
        for app in qs_pending:
            try:
                scores = grade_from_answers(app)

                app.tablestakes_score = scores.get("tablestakes_score")
                app.commitment_score = scores.get("commitment_score")
                app.nice_to_have_score = scores.get("nice_to_have_score")
                app.overall_score = scores.get("overall_score")
                app.recommendation = scores.get("recommendation")

                app.save(update_fields=[
                    "tablestakes_score",
                    "commitment_score",
                    "nice_to_have_score",
                    "overall_score",
                    "recommendation",
                ])
                updated += 1
            except Exception:
                failed += 1

    if failed:
        messages.warning(request, f"{form_slug}: Graded {updated} (failed {failed}). Total submissions: {total}.")
    else:
        messages.success(request, f"{form_slug}: Graded {updated}. Total submissions: {total}.")

    return _redirect_back_to_grading(request)

@staff_member_required
@require_POST
def grading_upload_test_csv(request):
    """
    Upload CSV for testing only.
    Creates/imports into TEST_E_A2 or TEST_M_A2 based on POST 'role' = 'E' or 'M'.
    """
    role = (request.POST.get("role") or "").strip().upper()
    if role not in ("E", "M"):
        messages.error(request, "Select role (E or M) for the test upload.")
        return _redirect_back_to_grading(request)

    fd = _ensure_test_a2_form(role)

    f = request.FILES.get("csv_file")
    if not f:
        messages.error(request, "No CSV file uploaded.")
        return _redirect_back_to_grading(request)

    try:
        raw = f.read().decode("utf-8-sig")
    except Exception:
        raw = f.read().decode("latin-1")

    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames:
        messages.error(request, "CSV appears to have no header row.")
        return _redirect_back_to_grading(request)

    qmap = {q.slug: q for q in fd.questions.all()}

    created = 0
    with transaction.atomic():
        for row in reader:
            name = (row.get("name") or row.get("full_name") or row.get("Nombre") or "").strip()
            email = (row.get("email") or row.get("Correo") or "").strip()

            app = Application.objects.create(
                form=fd,
                name=name,
                email=email,
            )

            for col, val in row.items():
                if col not in qmap:
                    continue
                v = (val or "").strip()
                Answer.objects.create(application=app, question=qmap[col], value=v)

            created += 1

    messages.success(request, f"Imported {created} submissions into {fd.slug} (sandbox).")
    return _redirect_back_to_grading(request)

@staff_member_required
def grading_master_csv(request, form_slug: str):
    """
    Download MASTER CSV for a form slug, ALWAYS including grade columns.
    """
    fd = get_object_or_404(FormDefinition, slug=form_slug)

    questions = list(fd.questions.filter(active=True).order_by("position", "id"))

    headers = [
        "created_at",
        "application_id",
        "name",
        "email",
        "tablestakes_score",
        "commitment_score",
        "nice_to_have_score",
        "overall_score",
        "recommendation",
    ] + [q.slug for q in questions]

    apps = (
        Application.objects.filter(form=fd)
        .prefetch_related("answers__question")
        .order_by("created_at", "id")
    )

    rows = []
    for app in apps:
        amap = {a.question.slug: (a.value or "") for a in app.answers.all()}

        rows.append([
            app.created_at.isoformat(),
            str(app.id),
            app.name or "",
            app.email or "",
            app.tablestakes_score or "",
            app.commitment_score or "",
            app.nice_to_have_score or "",
            app.overall_score or "",
            app.recommendation or "",
        ] + [amap.get(q.slug, "") for q in questions])

    return _csv_http_response(f"{form_slug}_MASTER.csv", headers, rows)


def _norm(s: str) -> str:
    return (s or "").strip().lower()


@staff_member_required
@require_POST
def grading_upload_csv(request, form_slug: str):
    """
    Upload a CSV and import rows as Applications + Answers for this form_slug.

    Robust mapping:
    - If a CSV column matches Question.slug -> use it
    - Else if it matches Question.text (case-insensitive) -> use it
    - name/email columns recognized in common Spanish/English variants
    Unknown columns are ignored.
    """
    fd = get_object_or_404(FormDefinition, slug=form_slug)

    f = request.FILES.get("csv_file")
    if not f:
        messages.error(request, "No CSV file uploaded.")
        return _redirect_back_to_grading(request)

    # decode
    try:
        raw = f.read().decode("utf-8-sig")
    except Exception:
        raw = f.read().decode("latin-1")

    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames:
        messages.error(request, "CSV appears to have no header row.")
        return _redirect_back_to_grading(request)

    questions = list(fd.questions.all())
    q_by_slug = {_norm(q.slug): q for q in questions}
    q_by_text = {_norm(q.text): q for q in questions}

    # common header variants
    NAME_KEYS = {"name", "nombre", "full_name", "nombre completo", "nombre_completo"}
    EMAIL_KEYS = {"email", "correo", "correo electrónico", "correo electronico", "dirección de correo electrónico", "direccion de correo electronico"}

    created = 0
    with transaction.atomic():
        for row in reader:
            # pull name/email if present
            name = ""
            email = ""

            for k, v in row.items():
                nk = _norm(k)
                if nk in NAME_KEYS and not name:
                    name = (v or "").strip()
                if nk in EMAIL_KEYS and not email:
                    email = (v or "").strip()

            app = Application.objects.create(
                form=fd,
                name=name.strip(),
                email=email.strip(),
            )

            # create answers
            for col, val in row.items():
                col_norm = _norm(col)
                q = q_by_slug.get(col_norm) or q_by_text.get(col_norm)
                if not q:
                    continue
                Answer.objects.create(
                    application=app,
                    question=q,
                    value=(val or "").strip(),
                )

            created += 1

    messages.success(request, f"Imported {created} submissions into {form_slug}.")
    return _redirect_back_to_grading(request)


# ----------------------------
# Email helpers + reminders
# ----------------------------
def _send_html_email(to_email: str, subject: str, html_body: str):
    msg = EmailMultiAlternatives(
        subject=subject,
        body="",
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        to=[to_email],
    )
    msg.attach_alternative(html_body, "text/html")
    msg.send(fail_silently=False)


def _send_reminders_worker(
    form_slug: str,
    targets: list[str],
    subject: str,
    html_body: str,
    lock_key: str | None = None,
):
    """
    Background sender for reminder emails.
    Sends one-by-one with retry + periodic reconnect to reduce timeout/socket issues.
    """
    total = len(targets)
    sent = 0
    failed = 0

    MAX_RETRIES = 3
    RECONNECT_EVERY = 25
    connection = None

    def _reconnect():
        nonlocal connection
        try:
            if connection:
                connection.close()
        except Exception:
            pass
        connection = get_connection(fail_silently=False)
        connection.open()

    try:
        _reconnect()

        for idx, email in enumerate(targets, start=1):
            if idx > 1 and (idx - 1) % RECONNECT_EVERY == 0:
                _reconnect()

            delivered = False
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    msg = EmailMultiAlternatives(
                        subject=subject,
                        body="",
                        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                        to=[email],
                        connection=connection,
                    )
                    msg.attach_alternative(html_body, "text/html")
                    sent_count = msg.send()
                    if sent_count:
                        sent += 1
                        delivered = True
                    break
                except Exception:
                    if attempt < MAX_RETRIES:
                        time.sleep(0.6 * attempt)
                        _reconnect()
                    else:
                        logger.exception(
                            "Reminder send failed after retries (form=%s, email=%s)",
                            form_slug,
                            email,
                        )
                        failed += 1

            if not delivered and failed < (idx - sent):
                failed += 1

    except Exception:
        logger.exception("Reminder worker fatal error (form=%s)", form_slug)
        failed = max(failed, total - sent)
    finally:
        try:
            if connection:
                connection.close()
        except Exception:
            pass
        if lock_key:
            try:
                cache.delete(lock_key)
            except Exception:
                logger.exception("Failed to clear reminder lock key: %s", lock_key)

    logger.info(
        "Reminder worker completed for %s: sent=%s failed=%s total=%s",
        form_slug,
        sent,
        failed,
        total,
    )


def _a1_slug_for_a2(form_slug: str) -> str | None:
    """
    Map:
      M_A2 -> M_A1
      E_A2 -> E_A1
      G5_M_A2 -> G5_M_A1
      G5_E_A2 -> G5_E_A1
    Returns None if not an A2 slug.
    """
    if not form_slug:
        return None

    if form_slug.endswith("M_A2"):
        if form_slug.startswith("G") and "_M_A2" in form_slug:
            return form_slug.replace("_M_A2", "_M_A1")
        return "M_A1"

    if form_slug.endswith("E_A2"):
        if form_slug.startswith("G") and "_E_A2" in form_slug:
            return form_slug.replace("_E_A2", "_E_A1")
        return "E_A1"

    return None


@staff_member_required
@require_POST
def send_second_stage_reminders(request, form_slug: str):
    status, sent_count, detail = _start_second_stage_reminders(form_slug)

    if status == "started":
        messages.success(
            request,
            f"Reminders iniciados para {form_slug}: {sent_count} destinatarios. Se enviarán en segundo plano.",
        )
    elif status == "no_targets":
        messages.info(request, detail)
    elif status == "already_running":
        messages.warning(request, detail)
    else:
        messages.error(request, detail)

    return redirect("admin_apps_list")

def _build_second_stage_reminder_payload(form_slug: str) -> tuple[dict | None, str | None]:
    """
    Build reminder recipient list + rendered email payload for one A2 form.
    Returns (payload, error). payload includes targets/subject/html_body.
    """
    if not (form_slug.endswith("E_A2") or form_slug.endswith("M_A2")):
        return None, "Este botón solo funciona para formularios A2 (E_A2 o M_A2)."

    a2_form = FormDefinition.objects.filter(slug=form_slug).select_related("group").first()
    if not a2_form:
        return None, f"No se encontró el formulario {form_slug}."

    is_emprendedora = form_slug.endswith("E_A2")
    track_word = "emprendedora" if is_emprendedora else "mentora"

    group = getattr(a2_form, "group", None)
    if not group:
        return None, (
            f"No se pudo enviar reminders para {form_slug}: "
            "el formulario A2 no está vinculado a un grupo."
        )

    a1_suffix = "_E_A1" if is_emprendedora else "_M_A1"
    a1_forms = list(
        FormDefinition.objects.filter(group=group, slug__endswith=a1_suffix).only("id", "slug")
    )
    if not a1_forms:
        return None, (
            f"No se pudo enviar reminders para {form_slug}: "
            "no se encontró el formulario A1 correspondiente en este grupo."
        )

    a1_apps_qs = (
        Application.objects.filter(form__in=a1_forms)
        .filter(invited_to_second_stage=True)
        .exclude(email__isnull=True)
        .exclude(email__exact="")
        .only("id", "email", "created_at")
        .order_by("-created_at", "-id")
    )

    completed_emails = {
        (email or "").strip().lower()
        for email in Application.objects.filter(form=a2_form)
        .exclude(email__isnull=True)
        .exclude(email__exact="")
        .values_list("email", flat=True)
        if (email or "").strip()
    }
    completed_emails.discard("")

    targets: list[str] = []
    seen: set[str] = set()
    for app in a1_apps_qs:
        email = (app.email or "").strip().lower()
        if not email or email in seen or email in completed_emails:
            continue
        seen.add(email)
        targets.append(email)

    deadline = getattr(group, "a2_deadline", None)
    if not deadline:
        return None, (
            f"No se pudo enviar reminders para {form_slug}: "
            "este formulario no tiene fecha límite A2 configurada en su grupo."
        )

    deadline_month = MONTH_NUM_TO_ES.get(deadline.month, "")
    deadline_str = (
        f"{deadline.day} de {deadline_month} de {deadline.year}"
        if deadline_month
        else deadline.strftime("%d/%m/%Y")
    )
    a2_link = f"https://apply.clubemprendo.org/apply/{form_slug}/"

    subject = "Últimos días para completar la segunda aplicación"
    html_body = f"""
    <div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;margin:0 auto;">
      <p>Hola,</p>
      <p>Esperamos que te encuentres muy bien.</p>
      <p>
        Queremos recordarte que, según la primera aplicación que completaste, cumples con el perfil para ser
        <strong>{track_word}</strong>, y nos encantaría que continúes con el proceso.
      </p>
      <p>
        Te recordamos que es necesario completar la segunda aplicación, ya que la fecha límite es el
        <strong>{deadline_str}</strong>. Estamos en los últimos días para aplicar.
      </p>
      <p>A continuación, te dejamos nuevamente el enlace y las instrucciones:</p>
      <ol>
        <li>Haz clic en el enlace: 👉 <a href="{a2_link}">{a2_link}</a></li>
        <li>Responde las preguntas (no toma más de 10 minutos).</li>
        <li>Haz clic en <strong>Enviar</strong> para completar tu aplicación.</li>
      </ol>
      <p>
        Tu participación es muy valiosa para nosotras, y esperamos contar contigo en esta nueva etapa del programa.
        Si tienes alguna pregunta o inconveniente, no dudes en escribirnos.
      </p>
      <p>Con cariño,<br><strong>Melanie Guzmán</strong></p>
    </div>
    """

    return {
        "form_slug": form_slug,
        "targets": targets,
        "subject": subject,
        "html_body": html_body,
    }, None


def _start_second_stage_reminders(form_slug: str) -> tuple[str, int, str]:
    payload, error = _build_second_stage_reminder_payload(form_slug)
    if error:
        return "error", 0, error
    if not payload:
        return "error", 0, f"No se pudo construir reminders para {form_slug}."

    targets = payload["targets"]
    if not targets:
        return "no_targets", 0, f"No hay personas pendientes para {form_slug}."

    lock_key = _reminder_lock_key(form_slug)
    if not cache.add(lock_key, "1", timeout=REMINDER_LOCK_TTL_SECONDS):
        return (
            "already_running",
            0,
            f"Ya hay un envío de reminders en progreso para {form_slug}. Espera a que termine antes de volver a enviarlo.",
        )

    try:
        threading.Thread(
            target=_send_reminders_worker,
            args=(form_slug, targets, payload["subject"], payload["html_body"], lock_key),
            daemon=True,
        ).start()
    except Exception:
        cache.delete(lock_key)
        logger.exception("Failed to start reminder worker for %s", form_slug)
        return "error", 0, f"No se pudo iniciar el envío de reminders para {form_slug}."

    return "started", len(targets), f"Reminders started for {form_slug}"


def _run_due_group_reminders():
    now = timezone.now()
    due_groups = (
        FormGroup.objects.filter(
            Q(reminder_1_at__isnull=False, reminder_1_at__lte=now, reminder_1_sent_at__isnull=True)
            | Q(reminder_2_at__isnull=False, reminder_2_at__lte=now, reminder_2_sent_at__isnull=True)
            | Q(reminder_3_at__isnull=False, reminder_3_at__lte=now, reminder_3_sent_at__isnull=True)
        )
        .order_by("number")
    )

    for group in due_groups:
        a2_slugs = list(
            FormDefinition.objects.filter(group=group)
            .filter(Q(slug__endswith="_E_A2") | Q(slug__endswith="_M_A2"))
            .values_list("slug", flat=True)
        )
        if not a2_slugs:
            continue

        for idx in (1, 2, 3):
            at_field = f"reminder_{idx}_at"
            sent_field = f"reminder_{idx}_sent_at"
            due_at = getattr(group, at_field, None)
            sent_at = getattr(group, sent_field, None)
            if not due_at or sent_at or due_at > now:
                continue

            claimed = FormGroup.objects.filter(
                id=group.id,
                **{
                    f"{sent_field}__isnull": True,
                    f"{at_field}__isnull": False,
                    f"{at_field}__lte": now,
                },
            ).update(**{sent_field: now})
            if not claimed:
                continue

            had_non_error = False
            for form_slug in a2_slugs:
                status, sent_count, detail = _start_second_stage_reminders(form_slug)
                if status in {"started", "no_targets", "already_running"}:
                    had_non_error = True
                    logger.info(
                        "Auto reminder slot #%s for Group %s on %s -> %s (%s recipients)",
                        idx,
                        group.number,
                        form_slug,
                        status,
                        sent_count,
                    )
                else:
                    logger.warning(
                        "Auto reminder slot #%s for Group %s on %s failed: %s",
                        idx,
                        group.number,
                        form_slug,
                        detail,
                    )

            if not had_non_error:
                FormGroup.objects.filter(id=group.id).update(**{sent_field: None})


def _maybe_run_due_group_reminders():
    gate_key = "admin:reminders:auto:check"
    if not cache.add(gate_key, "1", timeout=AUTO_REMINDER_CHECK_THROTTLE_SECONDS):
        return
    try:
        _run_due_group_reminders()
    except Exception:
        logger.exception("Auto reminder scheduler check failed.")


@staff_member_required
@require_POST
def grade_one_emprendedora(request, app_id: int):
    app = get_object_or_404(
        Application.objects.select_related("form").prefetch_related("answers__question"),
        id=app_id,
    )

    # only allow E_A2 forms
    if not app.form.slug.endswith("E_A2"):
        messages.error(request, "This grading button is only for Emprendedoras (E_A2).")
        return redirect("admin_grading_home")

    # convert answers -> dict matching your CSV column names
    row = {a.question.slug: (a.value or "") for a in app.answers.all()}

    # attach identity fields your CSV expects
    row["full_name"] = app.name or ""
    row["email"] = app.email or ""

    api_key = getattr(settings, "OPENAI_API_KEY", None) or os.getenv("OPENAI_API_KEY")
    if not api_key:
        messages.error(request, "OPENAI_API_KEY is not configured on the server.")
        return redirect("admin_grading_home")

    client = OpenAI(api_key=api_key)

    try:
        graded_df = grade_single_row(row, client)

        csv_text = graded_df.to_csv(index=False)
        gf = GradedFile.objects.create(
            form_slug=app.form.slug,
            application=app,
            csv_text=csv_text,
        )
        drive_sync = sync_generated_csv_artifact(app.form.slug, csv_text)

        messages.success(
            request,
            (
                f"✅ Graded app #{app.id}. CSV stored in database (id={gf.id}). "
                f"Drive sync: {drive_sync.status}."
            ),
        )

    except Exception as e:
        messages.error(request, f"Grading failed: {e}")

    return redirect("admin_grading_home")
