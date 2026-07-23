# applications/admin_views.py
import openai
import calendar
import csv
import io
import json
import re
import zipfile
import unicodedata
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
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.core.cache import cache
from django.db import transaction, DatabaseError
from django.db.models import Model, Count, Q, Prefetch, prefetch_related_objects
from django.db.models.functions import Lower
from django.http import HttpResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
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
    fetch_drive_csv_file_text,
    sync_generated_csv_artifact,
    sync_group_track_responses_csv,
)


def _parse_bulk_email_recipients(raw_value: str) -> tuple[list[str], list[str]]:
    candidates = re.split(r"[\s,;]+", raw_value or "")
    valid: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        email = candidate.strip().lower()
        if not email or email in seen:
            continue
        seen.add(email)
        try:
            validate_email(email)
        except ValidationError:
            invalid.append(candidate.strip())
        else:
            valid.append(email)
    return valid, invalid


@staff_member_required
def bulk_email_compose(request):
    context = {
        "recipients": "",
        "subject": "",
        "message_body": "",
        "reply_to": "",
    }
    if request.method == "POST":
        context.update({
            "recipients": request.POST.get("recipients") or "",
            "subject": (request.POST.get("subject") or "").strip(),
            "message_body": request.POST.get("message_body") or "",
            "reply_to": (request.POST.get("reply_to") or "").strip(),
        })
        recipients, invalid = _parse_bulk_email_recipients(context["recipients"])
        errors = []
        if invalid:
            errors.append("Invalid email addresses: " + ", ".join(invalid[:20]))
        if not recipients:
            errors.append("Enter at least one valid recipient.")
        if len(recipients) > 500:
            errors.append("A single send is limited to 500 recipients.")
        if not context["subject"]:
            errors.append("Enter a subject.")
        if not context["message_body"].strip():
            errors.append("Enter a message.")
        if context["reply_to"]:
            try:
                validate_email(context["reply_to"])
            except ValidationError:
                errors.append("Enter a valid reply-to address.")
        if request.POST.get("confirm_send") != "yes":
            errors.append("Confirm that you are ready to send this email.")

        if errors:
            context["errors"] = errors
            context["recipient_count"] = len(recipients)
            return render(request, "admin_dash/bulk_email_compose.html", context, status=400)

        connection = get_connection(fail_silently=False)
        queued = 0
        try:
            connection.open()
            for start in range(0, len(recipients), 40):
                batch = recipients[start:start + 40]
                email = EmailMultiAlternatives(
                    subject=context["subject"],
                    body=context["message_body"],
                    from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                    to=[],
                    bcc=batch,
                    reply_to=[context["reply_to"]] if context["reply_to"] else None,
                    connection=connection,
                )
                if email.send(fail_silently=False):
                    queued += len(batch)
        except Exception as exc:
            logger.exception("Bulk email send failed after %s recipient(s)", queued)
            context["errors"] = [
                f"Sending stopped after {queued} recipient(s): {exc}"
            ]
            context["recipient_count"] = len(recipients)
            return render(request, "admin_dash/bulk_email_compose.html", context, status=502)
        finally:
            connection.close()

        messages.success(
            request,
            f"Email queued for {queued} recipient(s). Addresses were hidden from other recipients.",
        )
        return redirect("admin_bulk_email_compose")

    return render(request, "admin_dash/bulk_email_compose.html", context)


def _application_email_recipients_for_form(form_def) -> list[str]:
    recipients: list[str] = []
    seen: set[str] = set()
    for raw_email in (
        Application.objects
        .filter(form=form_def)
        .exclude(email__isnull=True)
        .exclude(email__exact="")
        .order_by("created_at", "id")
        .values_list("email", flat=True)
    ):
        email = (raw_email or "").strip().lower()
        if not email or email in seen:
            continue
        try:
            validate_email(email)
        except ValidationError:
            continue
        seen.add(email)
        recipients.append(email)
    return recipients


def _send_application_update_email(
    *,
    form_slug: str,
    recipients: list[str],
    message_body: str,
    subject: str = "Actualización Club Emprendo",
) -> int:
    if not recipients:
        return 0

    connection = get_connection(fail_silently=False)
    sent = 0
    try:
        connection.open()
        for start in range(0, len(recipients), 40):
            batch = recipients[start:start + 40]
            msg = EmailMultiAlternatives(
                subject=subject,
                body=message_body,
                from_email="contacto@clubemprendo.org",
                to=[],
                bcc=batch,
                connection=connection,
            )
            msg.extra_headers = {"X-Club-Emprendo-Form": form_slug}
            if msg.send(fail_silently=False):
                sent += len(batch)
    finally:
        try:
            connection.close()
        except Exception:
            pass
    return sent
from applications.emprendedora_a1_autograde import emprendedora_a1_passes
from applications.email_templates import build_form_email_context, resolve_form_email_template
from applications.grading_config import (
    ensure_grading_config_for_form,
    ensure_pairing_config_for_group,
    runtime_grading_config_for_form_slug,
    runtime_pairing_config_for_group,
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
    StoredEmailTemplate,
    Question,
    GradedFile,
    GradingJob,
    PairingJob,
    scheduled_group_open_state,
)
import logging

logger = logging.getLogger(__name__)
MASTER_SLUGS = ["E_A1", "E_A2", "M_A1", "M_A2"]
ACTIVE_GROUP_MASTER_SLUGS = ["E_A1", "M_A1"]
GROUP_SLUG_RE = re.compile(r"^(?:G(?P<num>\d+)|[A-Za-z0-9_]+)_(?P<master>E_A1|E_A2|M_A1|M_A2)$")
GRADED_GROUP_RE = re.compile(r"^G(?P<num>\d+)_")
EMAIL_EXTRACT_RE = re.compile(r"[A-Z0-9._%+\-']+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.IGNORECASE)
EMAIL_TOKEN_SPLIT_RE = re.compile(r"[\s,;,]+")
TEST_E_A1_SLUG = "TEST_E_A1"
TEST_M_A1_SLUG = "TEST_M_A1"
CURRENT_GRADING_FORM_RE = re.compile(r"^(?:[A-Za-z0-9_]+_)?(?:E_A1|M_A1)$")
A2_FORM_RE = re.compile(r"^(?:[A-Za-z0-9_]+_)?(?:E_A2|M_A2)$")
TEST_A2_FORM_RE = re.compile(r"^TEST_(E_A2|M_A2)$")
REMINDER_LOCK_TTL_SECONDS = 60 * 60
AUTO_REMINDER_CHECK_THROTTLE_SECONDS = 45
TRACK_COMPLETION_FILTER_ALL = ""
TRACK_COMPLETION_FILTER_A1_ONLY = "a1_only"
TRACK_COMPLETION_FILTER_A1_A2 = "a1_a2"
TRACK_COMPLETION_FILTER_A1_NOT_PASSED = "a1_not_passed"
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
        "group_num": 800,
        "start_day": 1,
        "start_month": "abril",
        "end_month": "abril",
    },
}
DATABASE_ENCUESTAS_LABEL_DEFAULT = "Encuesta inicial - Emprendedoras"
DATABASE_ENCUESTAS_MENTORAS_LABEL_DEFAULT = "Encuesta inicial - Mentoras"
DATABASE_ENCUESTAS_FINAL_LABEL_DEFAULT = "Encuesta final - Emprendedoras"
DATABASE_ENCUESTAS_MENTORAS_FINAL_LABEL_DEFAULT = "Encuesta final - Mentoras"
DATABASE_ENCUESTAS_MENTORAS_DRIVE_FILE_DEFAULT = (
    "https://docs.google.com/spreadsheets/d/1oPndaqPrrD6vgstAd9KNfVc96fci_8h_x7oRRaUGEls/edit?gid=2016744608#gid=2016744608"
)
DATABASE_ENCUESTAS_FINAL_DRIVE_FILE_DEFAULT = (
    "https://docs.google.com/spreadsheets/d/179TvOCaIiWUivSSsADhbkRR5Eb_ErXiaW_JWxzaZ5aA/edit?gid=998065993#gid=998065993"
)
DATABASE_ENCUESTAS_MENTORAS_FINAL_DRIVE_FILE_DEFAULT = (
    "https://docs.google.com/spreadsheets/d/1OdQ0exguYQkOz8zGmKex8txi-8KsJxVLfBnuUTJtSMg/edit?resourcekey=&gid=1172577522#gid=1172577522"
)


def _reminder_lock_key(form_slug: str) -> str:
    return f"admin:reminders:lock:{(form_slug or '').strip().lower()}"


def _setting_or_env(name: str, default: str = "") -> str:
    raw = getattr(settings, name, None)
    if raw is not None:
        text = str(raw).strip()
        if text:
            return text
    return str(os.environ.get(name, default) or default).strip()


def _database_encuestas_label(kind: str) -> str:
    key = (kind or "").strip().lower()
    if key == "mentoras_final":
        return _setting_or_env(
            "DATABASE_ENCUESTAS_MENTORAS_FINAL_LABEL",
            DATABASE_ENCUESTAS_MENTORAS_FINAL_LABEL_DEFAULT,
        )
    if key == "mentoras":
        return _setting_or_env(
            "DATABASE_ENCUESTAS_MENTORAS_LABEL",
            DATABASE_ENCUESTAS_MENTORAS_LABEL_DEFAULT,
        )
    if key == "emprendedoras_final":
        return _setting_or_env(
            "DATABASE_ENCUESTAS_FINAL_LABEL",
            DATABASE_ENCUESTAS_FINAL_LABEL_DEFAULT,
        )
    return _setting_or_env("DATABASE_ENCUESTAS_LABEL", DATABASE_ENCUESTAS_LABEL_DEFAULT)


def _database_encuestas_drive_file_ref(kind: str) -> str:
    key = (kind or "").strip().lower()
    if key == "mentoras_final":
        for env_key in (
            "DATABASE_ENCUESTAS_MENTORAS_FINAL_DRIVE_FILE",
            "MENTORAS_ENCUESTAS_FINAL_DRIVE_FILE",
        ):
            value = _setting_or_env(env_key, "")
            if value:
                return value
        return DATABASE_ENCUESTAS_MENTORAS_FINAL_DRIVE_FILE_DEFAULT

    if key == "mentoras":
        for env_key in (
            "DATABASE_ENCUESTAS_MENTORAS_DRIVE_FILE",
            "MENTORAS_ENCUESTAS_DRIVE_FILE",
        ):
            value = _setting_or_env(env_key, "")
            if value:
                return value
        return DATABASE_ENCUESTAS_MENTORAS_DRIVE_FILE_DEFAULT

    if key == "emprendedoras_final":
        for env_key in (
            "DATABASE_ENCUESTAS_FINAL_DRIVE_FILE",
            "ENCUESTAS_FINAL_DRIVE_FILE",
        ):
            value = _setting_or_env(env_key, "")
            if value:
                return value
        return DATABASE_ENCUESTAS_FINAL_DRIVE_FILE_DEFAULT

    for env_key in (
        "DATABASE_ENCUESTAS_DRIVE_FILE",
        "DATABASE_ENCUESTAS_FILE_ID",
        "ENCUESTAS_DRIVE_FILE",
    ):
        value = _setting_or_env(env_key, "")
        if value:
            return value
    return ""

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
    Upload CSV into current sandbox forms only:
      - role=E -> TEST_E_A1
      - role=M -> TEST_M_A1

    This is independent from real group applications.
    """
    role = (request.POST.get("role") or "E").strip().upper()
    sandbox_slug = "TEST_E_A1" if role == "E" else "TEST_M_A1"

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
    if slug.endswith("E_A1"):
        return "E"
    if slug.endswith("M_A1"):
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
    if match:
        try:
            return int(match.group("num"))
        except (TypeError, ValueError):
            return None

    # Custom group-name slug fallback.
    linked = (
        FormDefinition.objects.select_related("group")
        .only("id", "group__number")
        .filter(slug=(form_slug or "").strip())
        .first()
    )
    if linked and getattr(linked, "group_id", None):
        try:
            return int(linked.group.number)
        except Exception:
            return None
    return None


def _mentor_dual_applicant_identifiers(
    mentor_form_slug: str,
    mentor_form: FormDefinition | None = None,
) -> tuple[set[str], set[str]]:
    if not (mentor_form_slug or "").strip().upper().endswith("M_A1"):
        return set(), set()

    group_obj = getattr(mentor_form, "group", None)
    if not group_obj:
        group_num = _group_number_from_form(mentor_form_slug, mentor_form)
        if not group_num:
            return set(), set()
        group_obj = FormGroup.objects.filter(number=group_num).first()
        if not group_obj:
            return set(), set()

    empr_form = _group_form_for_master(group_obj, "E_A1")
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


def _normalized_a1_pass_filter(raw_value: str | None) -> str:
    value = (raw_value or "").strip().lower()
    if value in {"passed", "not_passed"}:
        return value
    return ""


def _a1_application_is_passed(app: Application) -> bool:
    slug = (getattr(getattr(app, "form", None), "slug", "") or "").strip().upper()
    if not (slug.endswith("E_A1") or slug.endswith("M_A1")):
        return False
    if bool(getattr(app, "invited_to_second_stage", False)):
        return True

    answer_map = _application_answer_map(app)
    if slug.endswith("E_A1"):
        return bool(emprendedora_a1_passes(answer_map))
    return bool(_mentor_a1_passes(answer_map))


def _filter_a1_apps_by_pass_status(apps: list[Application], pass_filter: str) -> list[Application]:
    normalized = _normalized_a1_pass_filter(pass_filter)
    if not normalized:
        return apps

    filtered: list[Application] = []
    for app in apps:
        is_passed = _a1_application_is_passed(app)
        if normalized == "passed" and is_passed:
            filtered.append(app)
        elif normalized == "not_passed" and not is_passed:
            filtered.append(app)
    return filtered


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
        if not CURRENT_GRADING_FORM_RE.match(job.form_slug):
            raise RuntimeError(f"Unsupported form type: {job.form_slug}")

        fd = FormDefinition.objects.get(slug=job.form_slug)
        dual_applicant_emails: set[str] = set()
        dual_applicant_doc_ids: set[str] = set()
        if job.form_slug.endswith("M_A1"):
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
            .filter(form=fd, approved_for_grading=True)
            .prefetch_related("answers__question")
            .order_by("created_at", "id")
        )

        if not apps.exists():
            raise RuntimeError("No applications to grade.")

        app_list = list(apps)
        previous_application_ids = _prior_application_ids_for_track(
            "E" if job.form_slug.endswith("E_A1") else "M",
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
        ]
        source_questions = []
        seen_headers = {header.lower() for header in headers}
        skipped_duplicate_headers = []
        for question in questions:
            slug = (question.slug or "").strip()
            header_key = slug.lower()
            if not slug:
                continue
            if header_key in seen_headers:
                skipped_duplicate_headers.append(slug)
                continue
            seen_headers.add(header_key)
            source_questions.append(question)
        headers += [q.slug for q in source_questions]
        if skipped_duplicate_headers:
            _job_log(
                job,
                (
                    "↔️ Skipped duplicate source column(s): "
                    + ", ".join(skipped_duplicate_headers)
                    + ". Identity values are already exported once."
                ),
            )

        rows = []
        for app in app_list:
            answer_map = {
                a.question.slug: (a.value or "")
                for a in app.answers.all()
            }
            full_name = (
                app.name
                or answer_map.get("full_name")
                or answer_map.get("certificate_name")
                or answer_map.get("preferred_name")
                or answer_map.get("nombre")
                or ""
            )
            email = (
                app.email
                or answer_map.get("email")
                or answer_map.get("correo")
                or answer_map.get("correo_electronico")
                or ""
            )

            rows.append([
                app.created_at.isoformat(),
                app.id,
                full_name,
                email,
            ] + [answer_map.get(q.slug, "") for q in source_questions])

        import pandas as pd
        master_df = pd.DataFrame(rows, columns=headers)

        _job_log(job, "🤖 Running grader on full dataset")
        grading_config = runtime_grading_config_for_form_slug(job.form_slug)
        configured_criteria = len(getattr(grading_config, "weights", {}) or {})
        if configured_criteria:
            _job_log(job, f"⚙️ Grading config loaded with {configured_criteria} weighted criteria.")

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not set")

        client = OpenAI(api_key=api_key)

        # ----------------------------------
        # Run correct grader
        # ----------------------------------
        if job.form_slug.endswith("E_A1"):
            from applications.grader_e import grade_from_dataframe
            graded_df = grade_from_dataframe(
                master_df,
                client,
                log_fn=lambda msg: _job_log(job, msg),
                priority_emails=priority_emails,
                active_participant_emails=active_participant_emails,
                previous_application_ids=previous_application_ids,
                grading_config=grading_config,
            )

        else:  # M_A1
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
                grading_config=grading_config,
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


def _latest_graded_file_for_slug_or_404(form_slug: str) -> GradedFile:
    form_slug = (form_slug or "").strip()
    if not form_slug:
        raise Http404("No graded file found.")
    graded_file = (
        GradedFile.objects.filter(form_slug=form_slug)
        .order_by("-created_at", "-id")
        .first()
    )
    if not graded_file:
        raise Http404(f"No graded file found for {form_slug}.")
    return graded_file


@staff_member_required
def download_latest_graded_csv(request, form_slug: str):
    graded_file = _latest_graded_file_for_slug_or_404(form_slug)
    return download_graded_csv(request, graded_file.id)


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
def download_latest_graded_excel(request, form_slug: str):
    graded_file = _latest_graded_file_for_slug_or_404(form_slug)
    return download_graded_excel(request, graded_file.id)

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
    # Extract only valid-looking email addresses from pasted text.
    matches = EMAIL_EXTRACT_RE.findall(s)
    out: list[str] = []
    seen: set[str] = set()
    for part in matches:
        email = (part or "").strip().lower()
        if not email or email in seen:
            continue
        seen.add(email)
        out.append(email)
    return out


def _norm_assignment_email_list(s: str) -> list[str]:
    """
    Lenient parser for admin "assign applicants" paste box.
    Keeps entries that contain '@' even if they don't pass strict EMAIL_EXTRACT_RE,
    so pasted rows are not silently dropped.
    """
    emails, _mentions, _dupes, _duplicate_values = _parse_assignment_email_list_with_stats(s)
    return emails


def _parse_assignment_email_list_with_stats(s: str) -> tuple[list[str], int, int, list[str]]:
    if not s:
        return [], 0, 0, []

    out: list[str] = []
    seen: set[str] = set()
    mentions = 0
    duplicates = 0
    duplicate_values: list[str] = []
    duplicate_seen: set[str] = set()

    # Regex pass captures normal email text embedded in richer lines.
    for raw in EMAIL_EXTRACT_RE.findall(s):
        email = (raw or "").strip().lower().strip("<>[](){}\"'").rstrip(".,;:")
        if not email or "@" not in email:
            continue
        mentions += 1
        if email in seen:
            duplicates += 1
            if email not in duplicate_seen:
                duplicate_values.append(email)
                duplicate_seen.add(email)
            continue
        seen.add(email)
        out.append(email)

    # Fallback pass catches malformed-but-useful tokens that still contain "@",
    # and separators not handled in the primary pass.
    for raw_part in re.split(r"[\s,;|/]+", s):
        token = (raw_part or "").strip()
        if not token:
            continue
        token = token.strip("<>[](){}\"'")
        if token.lower().startswith("mailto:"):
            token = token[7:]
        # If regex can parse this token, it was already handled in the regex pass.
        if EMAIL_EXTRACT_RE.search(token):
            continue
        token = token.strip().lower().rstrip(".,;:")
        if not token or "@" not in token:
            continue
        mentions += 1
        if token in seen:
            duplicates += 1
            if token not in duplicate_seen:
                duplicate_values.append(token)
                duplicate_seen.add(token)
            continue
        seen.add(token)
        out.append(token)

    return out, mentions, duplicates, duplicate_values


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


def _render_pairing_prompt(template: str, *, label: str, mentor_text: str, entrepreneur_text: str) -> str:
    return (
        (template or "")
        .replace("{{ label }}", label or "")
        .replace("{{ mentor_text }}", mentor_text or "")
        .replace("{{ entrepreneur_text }}", entrepreneur_text or "")
    )


def _llm_fit_score(
    client: OpenAI,
    mentor_text: str,
    emp_text: str,
    label: str,
    prompt_template: str = "",
    model_name: str = "",
) -> tuple[int, str]:
    mentor_text = mentor_text or ""
    emp_text = emp_text or ""

    if not mentor_text.strip() or not emp_text.strip():
        return 0, "none"

    prompt = _render_pairing_prompt(
        prompt_template,
        label=label,
        mentor_text=mentor_text,
        entrepreneur_text=emp_text,
    )
    if not prompt.strip():
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
                model=(model_name or "gpt-5.2"),
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

    # The current application flow has one application per track. Historical A2
    # forms may still exist, but pairing must use the current A1 responses.
    emp_fd = _group_form_for_number_master(group_num, "E_A1")
    mentor_fd = _group_form_for_number_master(group_num, "M_A1")
    if not emp_fd or not mentor_fd:
        raise Http404(f"Could not resolve current A1 forms for group {group_num}.")
    emp_slug = emp_fd.slug
    mentor_slug = mentor_fd.slug
    pairing_config = runtime_pairing_config_for_group(group_num)

    if log_fn:
        log_fn(f"📥 Loading DB master data for {emp_slug} and {mentor_slug}")
        log_fn(
            "⚙️ Pairing config loaded: "
            f"{len(pairing_config.priority_rules)} priority rule(s), "
            f"{len(pairing_config.ai_comparisons)} AI comparison(s)."
        )

    emp_df = _build_master_df_for_form(emp_fd)
    mentor_df = _build_master_df_for_form(mentor_fd)

    # determine question columns (keep identity columns out)
    ID_COLS = {"created_at", "application_id", "name", "email"}
    emp_question_cols = [c for c in emp_df.columns if c not in ID_COLS]
    mentor_question_cols = [c for c in mentor_df.columns if c not in ID_COLS]

    emp_suffix_headers = [f"{c}_emprendedora" for c in emp_question_cols]
    mentor_suffix_headers = [f"{c}_mentora" for c in mentor_question_cols]
    extra_ai_headers = [
        f"ai_{item.get('output_key') or item.get('label')}"
        for item in pairing_config.ai_comparisons[2:]
    ]
    base_headers = PAIR_HEADERS + extra_ai_headers
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

    TOP_K_FOR_LLM = max(1, int(pairing_config.top_k_for_ai or 3))  # only run LLM on top K candidates per emprendedora

    def score_pair_base(emp_row, mentor_row):
        """
        Fast scoring without OpenAI. Also enforces required availability overlap.
        Returns (score, matches_dict) where score < 0 means invalid.
        """
        emp_email = str(_row_get(emp_row, "email", "")).strip().lower()
        mentor_email = str(_row_get(mentor_row, "email", "")).strip().lower()

        overlap = sorted(emp_av.get(emp_email, set()).intersection(mentor_av.get(mentor_email, set())))
        if pairing_config.availability_required and not overlap:
            return -10_000, {"availability": []}

        score = 0
        matches = {
            "availability": overlap,
            "emp_industry_val": _row_get(emp_row, "industry", "") or "",
            "mentor_industry_val": _row_get(mentor_row, "business_industry", "") or "",
            "industry": "none",
            "country": "none",
            "biz_age": "none",
        }

        for rule in pairing_config.priority_rules:
            comparison_type = rule.get("comparison_type")
            emp_slug = rule.get("emprendedora_question_slug") or ""
            mentor_slug = rule.get("mentora_question_slug") or ""
            output_key = rule.get("output_key") or rule.get("label") or comparison_type
            weight = float(rule.get("weight") or 0)
            required = bool(rule.get("required"))
            matched = False
            matched_value = "none"

            if comparison_type == "availability_overlap":
                matched = bool(overlap)
                matched_value = overlap if matched else "none"
                if matched:
                    score += 100 + weight * len(overlap)
            elif comparison_type == "business_age":
                emp_min = _business_age_bucket_to_min_years(_row_get(emp_row, emp_slug, ""))
                mentor_max = _mentor_years_to_max_years(_row_get(mentor_row, mentor_slug, ""))
                matched = mentor_max >= emp_min
                if matched:
                    score += weight
                    matched_value = f"mentor_max={mentor_max} >= emp_min={emp_min}"
            else:
                emp_value = _safe_lower(_row_get(emp_row, emp_slug, ""))
                mentor_value = _safe_lower(_row_get(mentor_row, mentor_slug, ""))

                # Preserve the previous country preference behavior for the default country rule.
                if output_key == "country" and _safe_lower(_row_get(emp_row, "same_country", "")) != "yes":
                    matched = False
                else:
                    matched = bool(emp_value and mentor_value and emp_value == mentor_value)

                if matched:
                    score += weight
                    matched_value = emp_value

            if required and not matched:
                return -10_000, matches
            matches[output_key] = matched_value

        return score, matches

    def add_llm_score(emp_row, mentor_row, score, matches):
        """
        Always runs OpenAI for unstructured fits on already-good candidates.
        Adds weighted LLM score and explanations.
        """
        emp_email = str(_row_get(emp_row, "email", "")).strip().lower()
        mentor_email = str(_row_get(mentor_row, "email", "")).strip().lower()

        for index, item in enumerate(pairing_config.ai_comparisons, start=1):
            output_key = item.get("output_key") or f"llm{index}"
            label = item.get("label") or output_key
            cache_key = (mentor_email, emp_email, output_key)
            if cache_key in llm_cache:
                ai_score, explanation = llm_cache[cache_key]
            else:
                ai_score, explanation = _llm_fit_score(
                    client,
                    mentor_text=_row_get(mentor_row, item.get("mentora_question_slug") or "", ""),
                    emp_text=_row_get(emp_row, item.get("emprendedora_question_slug") or "", ""),
                    label=label,
                    prompt_template=item.get("prompt") or "",
                    model_name=pairing_config.model_name,
                )
                llm_cache[cache_key] = (ai_score, explanation)

            score += float(item.get("weight") or 0) * ai_score
            matches[output_key] = explanation if ai_score > 0 else "none"

            if index == 1:
                matches["llm1"] = matches[output_key]
            elif index == 2:
                matches["llm2"] = matches[output_key]

        matches.setdefault("llm1", "none")
        matches.setdefault("llm2", "none")
        return score, matches

    def _extra_ai_values(matches: dict | None) -> list[str]:
        matches = matches or {}
        return [
            matches.get(item.get("output_key") or item.get("label"), "none") or "none"
            for item in pairing_config.ai_comparisons[2:]
        ]

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
            row_vals.extend(_extra_ai_values(None))
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
                row_vals.extend(_extra_ai_values(None))
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
                row_vals.extend(_extra_ai_values(None))
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
        row_vals.extend(_extra_ai_values(best_matches))
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
    mentoras_emails = []
    emprendedoras_emails = []

    if group_raw.isdigit():
        selected_group = FormGroup.objects.filter(number=int(group_raw)).first()
        if selected_group:
            participant_list = GroupParticipantList.objects.filter(
                group=selected_group
            ).first()
            if participant_list:
                def emails_from_participant_rows(rows, fallback_text):
                    row_emails = "\n".join(
                        str(row[5])
                        for row in (rows or [])
                        if isinstance(row, (list, tuple)) and len(row) > 5 and row[5]
                    )
                    return _norm_email_list(row_emails) or _norm_email_list(fallback_text)

                mentoras_emails = emails_from_participant_rows(
                    participant_list.mentoras_sheet_rows,
                    participant_list.mentoras_emails_text,
                )
                emprendedoras_emails = emails_from_participant_rows(
                    participant_list.emprendedoras_sheet_rows,
                    participant_list.emprendedoras_emails_text,
                )

    job_id = (request.GET.get("job") or "").strip()
    if job_id.isdigit():
        try:
            job = PairingJob.objects.filter(id=int(job_id)).first()
        except DatabaseError:
            job = None

    try:
        pairing_files = GradedFile.objects.filter(form_slug__startswith="PAIR_G")
        if selected_group:
            pairing_files = pairing_files.filter(
                form_slug=f"PAIR_G{selected_group.number}"
            )
        pairing_files = pairing_files.order_by("-created_at")[:50]
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
            "mentoras_emails": "\n".join(mentoras_emails),
            "emprendedoras_emails": "\n".join(emprendedoras_emails),
            "mentoras_count": len(mentoras_emails),
            "emprendedoras_count": len(emprendedoras_emails),
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
MONTH_VALUES_ES = {month_name for month_name, _ in MONTH_CHOICES_ES}

RESPOND_BY_MONTH_CHOICES = [("", "---------"), *MONTH_CHOICES_ES]
RESPOND_BY_MONTH_TO_NUM = {
    month_name: month_number
    for month_number, (month_name, _) in enumerate(MONTH_CHOICES_ES, start=1)
}
DT_LOCAL_INPUT_FORMATS = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"]


def _normalize_month_choice(value: str, fallback: str = "") -> str:
    raw = (value or "").strip().lower()
    if raw in MONTH_VALUES_ES:
        return raw
    month_num = _month_name_to_number(raw)
    if month_num and month_num in MONTH_NUM_TO_ES:
        return MONTH_NUM_TO_ES[month_num]

    fb = (fallback or "").strip().lower()
    if fb in MONTH_VALUES_ES:
        return fb
    month_num = _month_name_to_number(fb)
    if month_num and month_num in MONTH_NUM_TO_ES:
        return MONTH_NUM_TO_ES[month_num]
    return ""


class CreateGroupForm(forms.Form):
    group_name = forms.CharField(
        required=True,
        max_length=120,
        label="Group name",
        widget=forms.TextInput(attrs={"placeholder": "e.g. Abril 2026 Cohorte"}),
    )
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
        normalized_emails, parsed_mentions, duplicate_mentions, duplicate_values = _parse_assignment_email_list_with_stats(
            emails_text
        )
        if not normalized_emails:
            raise forms.ValidationError("Paste at least one email.")

        cleaned["normalized_emails"] = normalized_emails
        cleaned["parsed_mentions"] = parsed_mentions
        cleaned["duplicate_mentions"] = duplicate_mentions
        cleaned["duplicate_values"] = duplicate_values
        return cleaned


ASSIGNMENT_SOURCE_GROUP_PREFIX = "group:"


def _build_assignment_source_choices(
    groups: list[FormGroup] | None = None,
) -> list[dict]:
    if groups is None:
        groups = list(FormGroup.objects.order_by("number"))
    group_map = {int(g.number): g for g in groups}

    candidate_group_numbers: set[int] = set()
    source_forms = FormDefinition.objects.filter(
        is_master=False,
        group__isnull=False,
    ).select_related("group")
    for fd in source_forms:
        slug = str(getattr(fd, "slug", "") or "").strip()
        if not _master_slug_from_group_form_slug(slug):
            continue
        group_obj = getattr(fd, "group", None)
        group_num = getattr(group_obj, "number", None) or _group_number_from_slug(slug) or 0
        try:
            group_num = int(group_num)
        except (TypeError, ValueError):
            group_num = 0
        if group_num > 0:
            candidate_group_numbers.add(group_num)

    choices: list[dict] = []
    used_group_numbers: set[int] = set()

    for key, cfg in RECRUITMENT_POOL_SOURCES.items():
        try:
            group_num = int(cfg.get("group_num", 0) or 0)
        except (TypeError, ValueError):
            group_num = 0
        if group_num <= 0:
            continue
        choices.append(
            {
                "value": key,
                "label": _group_label_for_number(group_num, group_map),
                "group_num": group_num,
                "kind": "pool",
            }
        )
        used_group_numbers.add(group_num)

    for group_num in sorted(candidate_group_numbers, reverse=True):
        if group_num in used_group_numbers:
            continue
        choices.append(
            {
                "value": f"{ASSIGNMENT_SOURCE_GROUP_PREFIX}{group_num}",
                "label": _group_label_for_number(group_num, group_map),
                "group_num": group_num,
                "kind": "group",
            }
        )

    return choices


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
    while "#(month)" in out:
        out = out.replace("#(month)", start_month, 1)

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


def _is_missing_group_custom_name_column_error(exc: Exception) -> bool:
    msg = (str(exc) or "").lower()
    if "custom_name" not in msg:
        return False
    return ("no such column" in msg) or ("does not exist" in msg)


def _is_generic_group_name(raw_name: str | None, group_number: int) -> bool:
    name = str(raw_name or "").strip()
    if not name:
        return True
    norm = re.sub(r"\s+", "", name.lower())
    return norm in {f"group{group_number}", f"g{group_number}"}


def _group_form_slug_from_custom_name(group: FormGroup, master_slug: str) -> str:
    """
    Build group form slugs from custom group names when available.
    Falls back to legacy G#_MASTER slug when custom_name is generic/empty.
    """
    legacy_slug = f"G{group.number}_{(master_slug or '').strip().upper()}"
    custom_name = str(getattr(group, "custom_name", "") or "").strip()
    if not custom_name or _is_generic_group_name(custom_name, int(group.number)):
        return legacy_slug

    token = slugify(custom_name).replace("-", "_")
    token = re.sub(r"_+", "_", token).strip("_")
    if not token:
        return legacy_slug
    return f"{token}_{(master_slug or '').strip().upper()}"


def _group_form_name_from_custom_name(group: FormGroup, master_slug: str, master_name: str) -> str:
    """
    Build group form display names from custom group names when available.
    Names stay human-readable while slugs are resolved separately.
    """
    fallback = f"Grupo {group.number} — {master_name}"
    custom_name = str(getattr(group, "custom_name", "") or "").strip()
    if not custom_name or _is_generic_group_name(custom_name, int(group.number)):
        return fallback

    token = slugify(custom_name).replace("-", "_")
    token = re.sub(r"_+", "_", token).strip("_")
    if not token:
        return fallback

    suffix_map = {
        "E_A1": "e_1",
        "E_A2": "e_2",
        "M_A1": "m_1",
        "M_A2": "m_2",
    }
    suffix = suffix_map.get((master_slug or "").strip().upper())
    if not suffix:
        return fallback
    return f"{token}_{suffix}"


def _master_slug_from_group_form_slug(raw_slug: str) -> str | None:
    slug = (raw_slug or "").strip()
    if not slug:
        return None
    m = GROUP_SLUG_RE.match(slug)
    if not m:
        return None
    master = (m.group("master") or "").strip().upper()
    return master if master in MASTER_SLUGS else None


def _group_form_for_master(group: FormGroup | None, master_slug: str) -> FormDefinition | None:
    if not group:
        return None
    normalized_master = (master_slug or "").strip().upper()
    if normalized_master not in MASTER_SLUGS:
        return None

    expected_slug = _group_form_slug_from_custom_name(group, normalized_master)
    exact = (
        FormDefinition.objects.filter(group=group, is_master=False, slug=expected_slug)
        .order_by("id")
        .first()
    )
    if exact:
        return exact

    legacy_slug = f"G{group.number}_{normalized_master}"
    if legacy_slug != expected_slug:
        legacy = (
            FormDefinition.objects.filter(group=group, is_master=False, slug=legacy_slug)
            .order_by("id")
            .first()
        )
        if legacy:
            return legacy

    return (
        FormDefinition.objects.filter(group=group, is_master=False, slug__endswith=normalized_master)
        .order_by("id")
        .first()
    )


def _group_form_for_number_master(group_num: int, master_slug: str) -> FormDefinition | None:
    group = FormGroup.objects.filter(number=group_num).first()
    if group:
        return _group_form_for_master(group, master_slug)

    normalized_master = (master_slug or "").strip().upper()
    if normalized_master not in MASTER_SLUGS:
        return None
    return (
        FormDefinition.objects.filter(
            is_master=False,
            group__number=group_num,
            slug__endswith=normalized_master,
        )
        .order_by("id")
        .first()
    )


def _sync_group_form_names(group: FormGroup):
    """
    Ensure all A1/A2 group forms use the naming convention based on group custom_name.
    """
    master_name_map = {
        fd.slug: fd.name
        for fd in FormDefinition.objects.filter(slug__in=MASTER_SLUGS).only("slug", "name")
    }
    group_forms = FormDefinition.objects.filter(group=group).only("id", "slug", "name")
    for fd in group_forms:
        master_slug = _master_slug_from_group_form_slug(fd.slug or "")
        if master_slug not in MASTER_SLUGS:
            continue
        master_name = master_name_map.get(master_slug) or fd.name
        expected_name = _group_form_name_from_custom_name(group, master_slug, master_name)
        if (fd.name or "") != expected_name:
            FormDefinition.objects.filter(id=fd.id).update(name=expected_name)


def _ensure_recruitment_pool_group(pool_key: str) -> dict:
    """
    Ensure the configured source pool group exists and has group forms attached.
    Returns a summary dict with what was restored.
    """
    cfg = RECRUITMENT_POOL_SOURCES.get(pool_key)
    if not cfg:
        raise ValueError(f"Unknown recruitment pool key: {pool_key}")

    label = str(cfg.get("label") or pool_key).strip() or pool_key
    group_num = int(cfg.get("group_num") or 0)
    if group_num <= 0:
        raise ValueError(f"Invalid group_num for pool '{pool_key}'")

    start_day = int(cfg.get("start_day") or 1)
    start_month_cfg = str(cfg.get("start_month") or "abril")
    end_month_cfg = str(cfg.get("end_month") or start_month_cfg)
    start_month = _normalize_month_choice(start_month_cfg, fallback=start_month_cfg)
    end_month = _normalize_month_choice(end_month_cfg, fallback=end_month_cfg)
    year = int(cfg.get("year") or timezone.localdate().year)

    group, group_created = FormGroup.objects.get_or_create(
        number=group_num,
        defaults={
            "start_day": start_day,
            "start_month": start_month,
            "end_month": end_month,
            "year": year,
            "use_combined_application": True,
            "custom_name": label,
        },
    )

    group_updates: list[str] = []
    if _is_generic_group_name(getattr(group, "custom_name", ""), group_num):
        group.custom_name = label
        group_updates.append("custom_name")
    if not group.use_combined_application:
        group.use_combined_application = True
        group_updates.append("use_combined_application")
    if group_created:
        # Already covered via defaults; keep update_fields clean.
        group_updates = [f for f in group_updates if f not in {"start_day", "start_month", "end_month", "year"}]
    if group_updates:
        group.save(update_fields=list(dict.fromkeys(group_updates)))

    forms_created = 0
    forms_relinked = 0
    for master_slug in MASTER_SLUGS:
        master = FormDefinition.objects.filter(slug=master_slug).first()
        if not master:
            continue
        before = _group_form_for_master(group, master_slug)
        before_group_id = getattr(before, "group_id", None) if before else None
        _clone_form(master, group)
        after = _group_form_for_master(group, master_slug)
        if before is None and after is not None:
            forms_created += 1
        elif after is not None and before_group_id != group.id:
            forms_relinked += 1

    # Keep all A1/A2 form names aligned with the group naming convention.
    _sync_group_form_names(group)

    return {
        "pool_key": pool_key,
        "label": label,
        "group_num": group_num,
        "group_created": group_created,
        "forms_created": forms_created,
        "forms_relinked": forms_relinked,
    }


def _ensure_recruitment_pool_groups() -> list[dict]:
    results: list[dict] = []
    for pool_key in RECRUITMENT_POOL_SOURCES:
        try:
            results.append(_ensure_recruitment_pool_group(pool_key))
        except Exception:
            logger.exception("Failed to ensure recruitment pool '%s'.", pool_key)
    return results


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


def _sync_groups_open_close(groups: list[FormGroup]):
    """Apply schedule state for a page of groups with a fixed number of queries."""
    group_ids = [group.id for group in groups if group and group.id]
    if not group_ids:
        return

    forms_qs = FormDefinition.objects.filter(group_id__in=group_ids)
    if _model_has_field(FormDefinition, "manual_open_override"):
        forms_qs.filter(manual_open_override=True).exclude(
            is_public=True, accepting_responses=True
        ).update(is_public=True, accepting_responses=True)
        forms_qs.filter(manual_open_override=False).exclude(
            is_public=False, accepting_responses=False
        ).update(is_public=False, accepting_responses=False)
        forms_qs = forms_qs.filter(manual_open_override__isnull=True)

    open_group_ids: list[int] = []
    closed_group_ids: list[int] = []
    for group in groups:
        desired_open = scheduled_group_open_state(group)
        if desired_open is True:
            open_group_ids.append(group.id)
        elif desired_open is False:
            closed_group_ids.append(group.id)

    if open_group_ids:
        forms_qs.filter(group_id__in=open_group_ids).exclude(
            is_public=True, accepting_responses=True
        ).update(is_public=True, accepting_responses=True)
    if closed_group_ids:
        forms_qs.filter(group_id__in=closed_group_ids).exclude(
            is_public=False, accepting_responses=False
        ).update(is_public=False, accepting_responses=False)

def _ensure_test_grading_form(role: str) -> FormDefinition:
    """
    role: "E" or "M"
    Creates TEST_E_A1 or TEST_M_A1 if missing by cloning from the current master E_A1 / M_A1.
    """
    if role not in ("E", "M"):
        raise ValueError("role must be 'E' or 'M'")

    test_slug = TEST_E_A1_SLUG if role == "E" else TEST_M_A1_SLUG
    master_slug = "E_A1" if role == "E" else "M_A1"

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
        approval_email_name=getattr(master_fd, "approval_email_name", "") or "",
    )

    for template in master_fd.stored_emails.all().order_by("position", "id"):
        StoredEmailTemplate.objects.create(
            form=clone,
            name=template.name,
            subject=template.subject,
            body=template.body,
            position=template.position,
        )

    # Clone questions + choices
    for q in master_fd.questions.all().order_by("position", "id"):
        q_clone = Question.objects.create(
            form=clone,
            text=q.text,
            help_text=q.help_text,
            field_type=q.field_type,
            grid_rows=q.grid_rows,
            required=q.required,
            position=q.position,
            slug=q.slug,
            active=q.active,
            end_form_rules=q.end_form_rules,
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

    preferred_slug = _group_form_slug_from_custom_name(group, master_fd.slug)
    legacy_slug = f"G{group_num}_{master_fd.slug}"
    new_slug = preferred_slug
    new_name = _group_form_name_from_custom_name(group, master_fd.slug, master_fd.name)

    existing = FormDefinition.objects.filter(slug=new_slug).first()
    if not existing and preferred_slug != legacy_slug:
        # Backward compatibility: keep using the legacy slug if this group already
        # has one from previous runs.
        existing = FormDefinition.objects.filter(slug=legacy_slug).first()
        if existing:
            new_slug = legacy_slug
    elif existing and existing.group_id not in {None, group.id}:
        # Avoid hijacking another group's form when custom names collide.
        base_slug = preferred_slug
        master_suffix = str(master_fd.slug or "").strip().upper()
        base_prefix = base_slug
        if master_suffix and base_slug.endswith(master_suffix):
            base_prefix = (base_slug[: -len(master_suffix)]).rstrip("_")
        if base_prefix:
            candidate_slug = f"{base_prefix}_g{group_num}_{master_suffix}"
        else:
            candidate_slug = f"g{group_num}_{master_suffix}"
        attempt = 2
        while True:
            conflict = (
                FormDefinition.objects.filter(slug=candidate_slug)
                .exclude(group=group)
                .exists()
            )
            if not conflict:
                break
            if base_prefix:
                candidate_slug = f"{base_prefix}_g{group_num}_{attempt}_{master_suffix}"
            else:
                candidate_slug = f"g{group_num}_{attempt}_{master_suffix}"
            attempt += 1
        new_slug = candidate_slug
        existing = FormDefinition.objects.filter(slug=new_slug).first()
    if existing:
        # If a group was deleted, group-specific forms may survive with group=None
        # (FormDefinition.group uses SET_NULL). Reattach by slug so assignment
        # flows can recover datasets without manual DB surgery.
        update_fields: list[str] = []
        if existing.group_id != group.id:
            existing.group = group
            update_fields.append("group")
        if existing.is_master:
            existing.is_master = False
            update_fields.append("is_master")
        if (existing.name or "") != new_name:
            existing.name = new_name
            update_fields.append("name")
        if update_fields:
            existing.save(update_fields=update_fields)
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
        thanks_approved_title=_fill_placeholders(
            getattr(master_fd, "thanks_approved_title", ""),
            group_num,
            start_day,
            start_month,
            end_month,
            year,
            respond_day=respond_day,
            respond_month=respond_month,
        )
        or "",
        thanks_approved_message=_fill_placeholders(
            getattr(master_fd, "thanks_approved_message", ""),
            group_num,
            start_day,
            start_month,
            end_month,
            year,
            respond_day=respond_day,
            respond_month=respond_month,
        )
        or "",
        approval_email_name=getattr(master_fd, "approval_email_name", "") or "",
        thanks_rejected_title=_fill_placeholders(
            getattr(master_fd, "thanks_rejected_title", ""),
            group_num,
            start_day,
            start_month,
            end_month,
            year,
            respond_day=respond_day,
            respond_month=respond_month,
        )
        or "",
        thanks_rejected_message=_fill_placeholders(
            getattr(master_fd, "thanks_rejected_message", ""),
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

    for template in master_fd.stored_emails.all().order_by("position", "id"):
        StoredEmailTemplate.objects.create(
            form=clone,
            name=template.name,
            subject=_fill_placeholders(
                template.subject, group_num, start_day, start_month, end_month, year,
                respond_day=respond_day, respond_month=respond_month,
            ) or template.subject,
            body=_fill_placeholders(
                template.body, group_num, start_day, start_month, end_month, year,
                respond_day=respond_day, respond_month=respond_month,
            ) or template.body,
            position=template.position,
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
            grid_rows=q.grid_rows,
            required=q.required,
            position=q.position,
            slug=q.slug,  # IMPORTANT: stable
            active=q.active,
            confirm_value=q.confirm_value,
            end_form_rules=q.end_form_rules,
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
    if m:
        raw_num = (m.groupdict() or {}).get("num")
        if raw_num:
            try:
                return int(raw_num)
            except Exception:
                return None

    linked = (
        FormDefinition.objects.select_related("group")
        .only("id", "group__number")
        .filter(slug=(slug or "").strip())
        .first()
    )
    if linked and getattr(linked, "group_id", None):
        try:
            return int(linked.group.number)
        except Exception:
            return None
    return None


def _group_label_for_number(
    group_number: int | None,
    group_map: dict[int, FormGroup] | None = None,
) -> str:
    if group_number is None:
        return "Group ?"
    group_obj = None
    if group_map is not None:
        group_obj = group_map.get(int(group_number))
    if group_obj is None:
        group_obj = FormGroup.objects.filter(number=int(group_number)).first()
    custom_name = str(getattr(group_obj, "custom_name", "") or "").strip() if group_obj else ""
    # Treat generic labels like "Group 800" as default/internal text so pool labels
    # (for example "April Recruitment") can still show consistently.
    custom_name_norm = re.sub(r"\s+", "", custom_name.lower()) if custom_name else ""
    default_label_norm = f"group{int(group_number)}"
    if custom_name and custom_name_norm != default_label_norm:
        return custom_name

    # Fallback to configured recruitment-pool labels when custom_name is empty.
    for cfg in RECRUITMENT_POOL_SOURCES.values():
        try:
            if int(cfg.get("group_num", 0) or 0) == int(group_number):
                label = str(cfg.get("label") or "").strip()
                if label:
                    return label
        except Exception:
            continue
    return f"Group {int(group_number)}"


def _reserved_pool_group_numbers() -> set[int]:
    out: set[int] = set()
    for cfg in RECRUITMENT_POOL_SOURCES.values():
        try:
            raw = int(cfg.get("group_num", 0) or 0)
        except Exception:
            raw = 0
        if raw > 0:
            out.add(raw)
    return out


def _next_cohort_group_number(existing_numbers: list[int] | None = None) -> int:
    numbers = [int(n) for n in (existing_numbers or []) if str(n).isdigit()]
    if not numbers:
        numbers = [int(v) for v in FormGroup.objects.values_list("number", flat=True)]

    reserved = _reserved_pool_group_numbers()
    cohort_numbers = [n for n in numbers if n not in reserved]
    next_num = max(cohort_numbers, default=8) + 1
    taken = set(numbers)
    while next_num in taken:
        next_num += 1
    return next_num


def _group_forms_for_app_type(
    app_type: str,
    include_combined_groups: bool | None = None,
) -> list[FormDefinition]:
    app_type = (app_type or "").upper().strip()
    if app_type not in MASTER_SLUGS:
        raise ValueError(f"Unsupported app type: {app_type}")

    candidates = list(
        FormDefinition.objects.filter(
            is_master=False,
            group__isnull=False,
            slug__endswith=app_type,
        ).select_related("group")
    )

    forms: list[FormDefinition] = []
    for fd in candidates:
        master_slug = _master_slug_from_group_form_slug(fd.slug or "")
        if master_slug != app_type:
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
    empty_row_html = (
        f"<tr><td colspan=\"{max(1, len(headers))}\" style=\"padding:8px;\">"
        "No submissions yet.</td></tr>"
    )
    tbody_html = "".join(body) if body else empty_row_html

    return (
        f"<div id='{table_id}' style='overflow:auto;border:1px solid #ddd;border-radius:8px;'>"
        "<table style='border-collapse:collapse;width:100%;font-size:13px;table-layout:auto;'>"
        f"<thead><tr>{ths}</tr><tr>{filter_ths}</tr></thead>"
        f"<tbody>{tbody_html}</tbody>"
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
def _combined_application_entries(forms_for_group):
    """Expose one current application per track.

    A2 forms may still exist for older submissions/history, but they are retired
    from the group application flow and must not be appended to A1.
    """
    forms = list(forms_for_group)
    by_suffix = {}
    for form_def in forms:
        slug = (form_def.slug or "").upper()
        for suffix in MASTER_SLUGS:
            if slug.endswith(suffix):
                by_suffix[suffix] = form_def
                break

    entries = []
    for track, label in (("E", "Aplicación para emprendedoras"), ("M", "Aplicación para mentoras")):
        entry = by_suffix.get(f"{track}_A1")
        if not entry:
            continue
        entry.combined_display_name = label
        entry.companion_form = None
        entries.append(entry)
    return entries


def _combined_master_entries(masters):
    return _combined_application_entries(masters)


@staff_member_required
def apps_list(request):
    try:
        _maybe_run_due_group_reminders()
        try:
            from applications.views import _maybe_run_due_a1_to_a2_reminders
            _maybe_run_due_a1_to_a2_reminders()
        except Exception:
            logger.exception("A1->A2 auto reminder scheduler check failed.")

        pool_restore_results = _ensure_recruitment_pool_groups()
        for restore in pool_restore_results:
            if restore.get("group_created") or restore.get("forms_created") or restore.get("forms_relinked"):
                messages.info(
                    request,
                    (
                        f"Restored {restore['label']} "
                        f"(group {restore['group_num']}): "
                        f"created {restore.get('forms_created', 0)} forms, "
                        f"relinked {restore.get('forms_relinked', 0)}."
                    ),
                )

        masters = _combined_master_entries(
            FormDefinition.objects.filter(slug__in=MASTER_SLUGS).order_by("slug")
        )

        groups = list(FormGroup.objects.order_by("-created_at", "-id"))
        _sync_groups_open_close(groups)
        prefetch_related_objects(
            groups,
            Prefetch("forms", queryset=FormDefinition.objects.order_by("-id")),
        )
        groups_by_number = {int(g.number): g for g in groups}
        has_combined_groups = any(g.use_combined_application for g in groups)
        group_list = []
        for g in groups:
            g.display_label = _group_label_for_number(int(g.number), groups_by_number)
            all_group_forms = list(g.forms.all())
            forms_for_group = (
                _combined_application_entries(all_group_forms)
                if g.use_combined_application
                else all_group_forms
            )
            group_list.append((g, forms_for_group))

        return render(
            request,
            "admin_dash/apps_list.html",
            {
                "masters": masters,
                "create_group_form": CreateGroupForm(),
                "group_list": group_list,
                "has_combined_groups": has_combined_groups,
                "month_choices": MONTH_CHOICES_ES,
            },
        )
    except DatabaseError as exc:
        if not _is_missing_group_custom_name_column_error(exc):
            raise
        logger.exception("Apps page loaded before FormGroup.custom_name migration was applied.")
        messages.error(
            request,
            "Database migration pending: run `python manage.py migrate` to enable group rename and load groups.",
        )
        masters = _combined_master_entries(
            FormDefinition.objects.filter(slug__in=MASTER_SLUGS).order_by("slug")
        )
        return render(
            request,
            "admin_dash/apps_list.html",
            {
                "masters": masters,
                "create_group_form": CreateGroupForm(),
                "group_list": [],
                "has_combined_groups": False,
                "month_choices": MONTH_CHOICES_ES,
            },
        )


@staff_member_required
@require_POST
def create_group(request):
    form = CreateGroupForm(request.POST)
    if not form.is_valid():
        masters = _combined_master_entries(
            FormDefinition.objects.filter(slug__in=MASTER_SLUGS).order_by("slug")
        )
        groups = list(FormGroup.objects.order_by("-created_at", "-id"))
        groups_by_number = {int(g.number): g for g in groups}
        has_combined_groups = FormGroup.objects.filter(use_combined_application=True).exists()
        group_list = []
        for g in groups:
            g.display_label = _group_label_for_number(int(g.number), groups_by_number)
            all_group_forms = list(FormDefinition.objects.filter(group=g).order_by("-id"))
            forms_for_group = (
                _combined_application_entries(all_group_forms)
                if g.use_combined_application
                else all_group_forms
            )
            group_list.append((g, forms_for_group))

        return render(
            request,
            "admin_dash/apps_list.html",
            {
                "masters": masters,
                "create_group_form": form,
                "group_list": group_list,
                "has_combined_groups": has_combined_groups,
                "month_choices": MONTH_CHOICES_ES,
            },
        )

    group_name = (form.cleaned_data.get("group_name") or "").strip()
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
        existing_numbers = list(
            FormGroup.objects.select_for_update().values_list("number", flat=True)
        )
        group_num = _next_cohort_group_number(existing_numbers)
        group = FormGroup.objects.create(
            number=group_num,
            start_month=start_month,
            end_month=end_month,
            year=year,
            start_day=start_day,
            a2_deadline=a2_deadline,
            open_at=open_at,
            close_at=close_at,
            reminder_1_at=reminder_1_at,
            reminder_2_at=reminder_2_at,
            reminder_3_at=reminder_3_at,
            use_combined_application=True,
            custom_name=group_name,
        )
        update_fields: list[str] = []
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

        masters = FormDefinition.objects.filter(slug__in=ACTIVE_GROUP_MASTER_SLUGS).order_by("slug")
        for master_fd in masters:
            _clone_form(master_fd, group)

    drive_result = None
    if re.search(r"\bgroup\b", group_name, flags=re.IGNORECASE):
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
                    f"Grupo {group_num} creado, pero falló la creación de carpetas en Drive."
                    + (f" Error: {detail}" if detail else "")
                ),
            )

    if group_name:
        messages.success(request, f"Grupo {group_num} ({group_name}) creado y formularios clonados.")
    else:
        messages.success(request, f"Grupo {group_num} creado y formularios clonados.")
    if drive_result:
        if drive_result.status == "created":
            messages.success(
                request,
                f"Drive: estructura creada para G{group_num} ({drive_result.folder_name}).",
            )
        elif drive_result.status == "exists":
            messages.info(
                request,
                f"Drive: estructura verificada para G{group_num} ({drive_result.folder_name}).",
                )
        elif drive_result.status == "skipped":
            messages.warning(request, f"Drive: {drive_result.detail}")

        # Seed/update native application response Sheets immediately, even with 0 submissions.
        if drive_result.status in {"created", "exists"}:
            seed_results = []
            for track in ("E", "M"):
                try:
                    res = sync_group_track_responses_csv(group.number, track)
                    seed_results.append(f"{track}: {res.status} ({res.detail})")
                except Exception as exc:
                    logger.exception("Drive seed Sheet sync failed for G%s track %s", group.number, track)
                    seed_results.append(f"{track}: error ({exc})")
            if seed_results:
                messages.info(request, "Drive Sheet seed -> " + " | ".join(seed_results))
    _sync_group_open_close(group)
    return redirect("admin_apps_list")


@staff_member_required
@require_POST
def rename_group(request, group_num: int):
    group = get_object_or_404(FormGroup, number=group_num)
    custom_name = (request.POST.get("custom_name") or "").strip()
    group.custom_name = custom_name
    group.save(update_fields=["custom_name"])
    _sync_group_form_names(group)

    if custom_name:
        messages.success(request, f"Group {group.number} renamed to '{custom_name}'.")
    else:
        messages.success(request, f"Group {group.number} name reset to default.")
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
    Update start day/month/year, A2 deadline, open/close schedule, and reminder schedule for
    a group without recloning forms.
    """
    group = get_object_or_404(FormGroup, number=group_num)

    try:
        start_day = int(request.POST.get("start_day") or group.start_day or 1)
    except ValueError:
        start_day = group.start_day or 1
    if start_day < 1:
        start_day = 1
    if start_day > 31:
        start_day = 31

    start_month = _normalize_month_choice(
        request.POST.get("start_month") or "",
        fallback=(group.start_month or ""),
    )
    end_month = _normalize_month_choice(
        request.POST.get("end_month") or "",
        fallback=(group.end_month or ""),
    )

    try:
        year = int(request.POST.get("year") or group.year or timezone.now().year)
    except Exception:
        year = int(group.year or timezone.now().year)
    if year < 2000:
        year = 2000
    if year > 2100:
        year = 2100

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
    if (group.start_month or "").strip().lower() != start_month:
        group.start_month = start_month
        update_fields.append("start_month")
    if (group.end_month or "").strip().lower() != end_month:
        group.end_month = end_month
        update_fields.append("end_month")
    if int(group.year or 0) != int(year):
        group.year = year
        update_fields.append("year")
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
        f"Actualizado Grupo {group.number}: {start_day} {start_month}–{end_month} {year}, fecha límite A2 "
        f"{deadline.strftime('%d/%m/%Y') if deadline else 'no definida'}, "
        f"apertura {open_at} / cierre {close_at}."
    )
    return redirect("admin_apps_list")


# ----------------------------
# Database
# ----------------------------
@staff_member_required
def database_home(request):
    _ensure_recruitment_pool_groups()

    masters = list(FormDefinition.objects.filter(slug__in=MASTER_SLUGS).order_by("slug"))

    counts = {
        row["form__slug"]: row["c"]
        for row in Application.objects.values("form__slug").annotate(c=Count("id"))
    }

    groups = list(
        FormGroup.objects.prefetch_related(
            Prefetch("forms", queryset=FormDefinition.objects.order_by("slug"))
        ).order_by("number")
    )
    groups_by_number = {int(g.number): g for g in groups}
    group_blocks: list[dict] = []
    combined_track_counts = {"E": 0, "M": 0}
    legacy_type_counts = {k: 0 for k in MASTER_SLUGS}

    for g in groups:
        forms_for_group = list(g.forms.all())
        use_combined = bool(getattr(g, "use_combined_application", False))
        group_label = _group_label_for_number(int(g.number), groups_by_number)

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
                "group_label": group_label,
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
            "group_label": group_label,
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
        .defer("csv_text")
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
    graded_slugs = {
        (gf.form_slug or "").strip()
        for gf in graded_files
        if str(getattr(gf, "form_slug", "") or "").strip()
    }
    graded_slug_group_map = {
        row["slug"]: row["group__number"]
        for row in FormDefinition.objects.filter(slug__in=graded_slugs).values("slug", "group__number")
    }
    graded_files_by_group_map = {}
    graded_files_other = []
    for gf in graded_files:
        graded_slug = (gf.form_slug or "").strip()
        group_num = graded_slug_group_map.get(graded_slug)
        if group_num is None:
            m = GRADED_GROUP_RE.match(graded_slug)
            if m:
                try:
                    group_num = int(m.group("num"))
                except (TypeError, ValueError):
                    group_num = None
        if group_num is None:
            graded_files_other.append(gf)
            continue
        graded_files_by_group_map.setdefault(int(group_num), []).append(gf)

    graded_files_by_group = [
        {
            "group_num": group_num,
            "group_label": _group_label_for_number(group_num, groups_by_number),
            "files": graded_files_by_group_map[group_num],
        }
        for group_num in sorted(graded_files_by_group_map.keys(), reverse=True)
    ]

    pairing_files = GradedFile.objects.filter(
        form_slug__startswith="PAIR_G"
    ).defer("csv_text").order_by("-created_at")[:100]

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
    next_group_num = _next_cohort_group_number([int(g.number) for g in groups])
    pool_source_choices = _build_assignment_source_choices(groups)
    default_source_value = source_pool_default_key
    source_values = [str(c.get("value") or "") for c in pool_source_choices]
    if default_source_value not in source_values and source_values:
        default_source_value = source_values[0]
    pool_assignment_defaults = {
        "source_pool": default_source_value,
        "track": PoolAssignmentForm.TRACK_EMPRENDEDORAS,
        "target_group_num": next_group_num,
    }
    encuestas_label = _database_encuestas_label("emprendedoras")
    encuestas_drive_file_ref = _database_encuestas_drive_file_ref("emprendedoras")
    encuestas_mentoras_label = _database_encuestas_label("mentoras")
    encuestas_mentoras_drive_file_ref = _database_encuestas_drive_file_ref("mentoras")
    encuestas_final_label = _database_encuestas_label("emprendedoras_final")
    encuestas_final_drive_file_ref = _database_encuestas_drive_file_ref("emprendedoras_final")
    encuestas_mentoras_final_label = _database_encuestas_label("mentoras_final")
    encuestas_mentoras_final_drive_file_ref = _database_encuestas_drive_file_ref("mentoras_final")

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
            "pool_source_choices": pool_source_choices,
            "pool_assignment_defaults": pool_assignment_defaults,
            "encuestas_label": encuestas_label,
            "encuestas_drive_file_ref": encuestas_drive_file_ref,
            "encuestas_mentoras_label": encuestas_mentoras_label,
            "encuestas_mentoras_drive_file_ref": encuestas_mentoras_drive_file_ref,
            "encuestas_final_label": encuestas_final_label,
            "encuestas_final_drive_file_ref": encuestas_final_drive_file_ref,
            "encuestas_mentoras_final_label": encuestas_mentoras_final_label,
            "encuestas_mentoras_final_drive_file_ref": encuestas_mentoras_final_drive_file_ref,
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
        track_label = "Mentoras" if track == "M" else "Emprendedoras"
        label = f"G{group_num} Aplicaciones {track_label}"
        try:
            res = sync_group_track_responses_csv(group_num, track)
            out.append((res.status, label, res.detail))
        except Exception as exc:
            logger.exception("Drive manual sync failed for %s", label)
            out.append(("error", label, str(exc)))

    artifact_slugs: list[str] = []
    for master_slug in ("E_A2", "M_A2"):
        fd = _group_form_for_number_master(group_num, master_slug)
        if fd:
            artifact_slugs.append(fd.slug)
        else:
            artifact_slugs.append(f"G{group_num}_{master_slug}")
    artifact_slugs.append(f"PAIR_G{group_num}")

    for slug in artifact_slugs:
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
        master_slug = _master_slug_from_group_form_slug(fd.slug or "")
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
    dynamic_source_choices = _build_assignment_source_choices()
    form.fields["source_pool"].choices = [
        (str(c.get("value") or ""), str(c.get("label") or ""))
        for c in dynamic_source_choices
    ]
    if not form.is_valid():
        for _field, errs in form.errors.items():
            for err in errs:
                messages.error(request, str(err))
        return _database_next_redirect(request, fallback_name="admin_database")

    source_pool = str(form.cleaned_data["source_pool"]).strip()
    source_cfg = RECRUITMENT_POOL_SOURCES.get(source_pool)
    source_group_num = 0
    source_label = ""
    if source_cfg:
        source_group_num = int(source_cfg["group_num"])
        source_label = str(source_cfg["label"])
        _ensure_recruitment_pool_group(source_pool)
    else:
        raw_group_value = source_pool
        if raw_group_value.startswith(ASSIGNMENT_SOURCE_GROUP_PREFIX):
            raw_group_value = raw_group_value[len(ASSIGNMENT_SOURCE_GROUP_PREFIX):]
        try:
            source_group_num = int(raw_group_value)
        except (TypeError, ValueError):
            source_group_num = 0
        if source_group_num <= 0:
            messages.error(request, "Invalid source application selected.")
            return _database_next_redirect(request, fallback_name="admin_database")
        source_group = FormGroup.objects.filter(number=source_group_num).first()
        if not source_group:
            messages.error(request, "Selected source applications group was not found.")
            return _database_next_redirect(request, fallback_name="admin_database")
        source_label = _group_label_for_number(source_group_num)

    selected_track = str(form.cleaned_data["track"]).strip().upper()
    track_label = "Emprendedoras" if selected_track == "E" else "Mentoras"
    target_group_num = int(form.cleaned_data["target_group_num"])
    wanted_emails = {
        e.strip().lower()
        for e in (form.cleaned_data.get("normalized_emails") or [])
        if str(e or "").strip()
    }
    parsed_mentions = int(form.cleaned_data.get("parsed_mentions") or len(wanted_emails))
    duplicate_mentions = int(form.cleaned_data.get("duplicate_mentions") or 0)
    duplicate_values = [
        str(v or "").strip().lower()
        for v in (form.cleaned_data.get("duplicate_values") or [])
        if str(v or "").strip()
    ]

    source_group = FormGroup.objects.filter(number=source_group_num).first()
    if not source_group:
        messages.error(
            request,
            f"Source applications '{source_label}' are not configured correctly.",
        )
        return _database_next_redirect(request, fallback_name="admin_database")
    source_label = str(getattr(source_group, "custom_name", "") or "").strip() or source_label

    try:
        start_day = int(getattr(source_group, "start_day", 0) or 0)
        year = int(getattr(source_group, "year", 0) or 0)
    except (TypeError, ValueError):
        start_day = 0
        year = 0
    source_start_month_raw = str(getattr(source_group, "start_month", "") or "")
    source_end_month_raw = str(getattr(source_group, "end_month", "") or "")
    start_month = _normalize_month_choice(source_start_month_raw, fallback=source_start_month_raw)
    end_month = _normalize_month_choice(source_end_month_raw, fallback=source_end_month_raw)
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
    skipped_no_match_answers = 0
    mapping_mismatch_fallback_inserted = 0
    mapping_mismatch_still_missing = 0
    unmatched_email_count = 0
    unmatched_inserted = 0
    unmatched_already_present = 0
    removed_not_requested = 0
    removed_duplicates = 0
    matched_emails: set[str] = set()
    mapping_mismatch_emails: set[str] = set()
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
            if target_group.id == source_group.id:
                messages.error(
                    request,
                    "Target group must be different from the source applications pool.",
                )
                return _database_next_redirect(request, fallback_name="admin_database")

            update_fields: list[str] = []
            # Always enforce start day/month from the selected source pool.
            # This intentionally overrides prior manual edits on the target group.
            target_group.start_day = start_day
            target_group.start_month = start_month
            update_fields.extend(["start_day", "start_month"])
            if not target_group.use_combined_application:
                target_group.use_combined_application = True
                update_fields.append("use_combined_application")
            target_group.save(update_fields=list(dict.fromkeys(update_fields)))

            required_master_slugs = [f"{selected_track}_A1"]
            for master_slug in required_master_slugs:
                if master_slug not in MASTER_SLUGS:
                    continue
                master_form = FormDefinition.objects.filter(slug=master_slug).first()
                if not master_form:
                    # Fallback: use the source pool form as the cloning template.
                    master_form = source_forms_by_master.get(master_slug)
                if master_form:
                    _clone_form(master_form, target_group)

            if target_group.id != source_group.id:
                FormDefinition.objects.filter(group=target_group).update(
                    is_public=False,
                    accepting_responses=False,
                )

            # Extra recovery: if group-specific slugs exist but are detached from
            # this group (e.g. prior group deletion), relink them explicitly.
            for master_slug in required_master_slugs:
                expected_slug = _group_form_slug_from_custom_name(target_group, master_slug)
                existing_target = FormDefinition.objects.filter(slug=expected_slug).first()
                if not existing_target:
                    legacy_slug = f"G{target_group_num}_{master_slug}"
                    existing_target = FormDefinition.objects.filter(slug=legacy_slug).first()
                if not existing_target:
                    continue
                target_updates: list[str] = []
                if existing_target.group_id != target_group.id:
                    existing_target.group = target_group
                    target_updates.append("group")
                if existing_target.is_master:
                    existing_target.is_master = False
                    target_updates.append("is_master")
                if target_updates:
                    existing_target.save(update_fields=target_updates)

            target_forms_by_master = _group_forms_by_master_slug(target_group)
            selected_target_forms = [
                fd
                for master_slug, fd in target_forms_by_master.items()
                if master_slug.startswith(f"{selected_track}_")
            ]
            selected_target_form_ids = [fd.id for fd in selected_target_forms]
            if not selected_target_form_ids:
                expected_slugs = [
                    _group_form_slug_from_custom_name(target_group, m)
                    for m in required_master_slugs
                ]
                found_slugs = list(
                    FormDefinition.objects.filter(group=target_group)
                    .order_by("slug")
                    .values_list("slug", flat=True)
                )
                messages.error(
                    request,
                    (
                        f"Could not prepare target dataset for Group {target_group_num} "
                        f"({track_label}). Expected forms: {', '.join(expected_slugs)}. "
                        f"Found: {', '.join(found_slugs) if found_slugs else 'none'}."
                    ),
                )
                return _database_next_redirect(request, fallback_name="admin_database")

            # Keep target track synchronized to the pasted email list.
            # Anything outside the requested set is removed from this target track.
            empty_email_qs = Application.objects.filter(form_id__in=selected_target_form_ids).filter(
                Q(email__isnull=True) | Q(email__exact="")
            )
            removed_empty_email = empty_email_qs.count()
            if removed_empty_email:
                empty_email_qs.delete()
            removed_not_requested += removed_empty_email

            not_wanted_qs = (
                Application.objects.filter(form_id__in=selected_target_form_ids)
                .annotate(_email_norm=Lower("email"))
                .exclude(_email_norm__in=wanted_emails)
            )
            deleted_not_wanted = not_wanted_qs.count()
            if deleted_not_wanted:
                not_wanted_qs.delete()
            removed_not_requested += deleted_not_wanted

            dedupe_candidates = (
                Application.objects.filter(form_id__in=selected_target_form_ids)
                .exclude(email__isnull=True)
                .exclude(email__exact="")
                .annotate(_email_norm=Lower("email"))
                .order_by("form_id", "_email_norm", "-created_at", "-id")
            )
            seen_dedupe_keys: set[tuple[int, str]] = set()
            duplicate_ids: list[int] = []
            for app in dedupe_candidates:
                email_norm = (getattr(app, "_email_norm", "") or "").strip().lower()
                if not email_norm:
                    duplicate_ids.append(app.id)
                    continue
                dedupe_key = (int(getattr(app, "form_id", 0) or 0), email_norm)
                if dedupe_key in seen_dedupe_keys:
                    duplicate_ids.append(app.id)
                    continue
                seen_dedupe_keys.add(dedupe_key)
            if duplicate_ids:
                duplicate_qs = Application.objects.filter(id__in=duplicate_ids)
                removed_duplicates = duplicate_qs.count()
                if removed_duplicates:
                    duplicate_qs.delete()

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

            track_target_masters = [
                master_slug
                for master_slug in (f"{selected_track}_A1", f"{selected_track}_A2")
                if master_slug in existing_target_emails_by_master
            ]
            if not track_target_masters:
                messages.error(
                    request,
                    (
                        f"Could not prepare both target applications for Group {target_group_num} "
                        f"({track_label})."
                    ),
                )
                return _database_next_redirect(request, fallback_name="admin_database")

            source_best_app_by_email: dict[str, Application] = {}
            for master_slug, source_form in source_forms_by_master.items():
                if not master_slug.startswith(f"{selected_track}_"):
                    continue
                source_apps_by_email = _latest_apps_by_normalized_email_for_form(
                    source_form,
                    wanted_emails,
                )
                for email_norm, source_app in source_apps_by_email.items():
                    existing_app = source_best_app_by_email.get(email_norm)
                    if existing_app is None:
                        source_best_app_by_email[email_norm] = source_app
                        continue
                    existing_key = (getattr(existing_app, "created_at", None), int(getattr(existing_app, "id", 0) or 0))
                    candidate_key = (getattr(source_app, "created_at", None), int(getattr(source_app, "id", 0) or 0))
                    if candidate_key > existing_key:
                        source_best_app_by_email[email_norm] = source_app

            existing_any_target_track: set[str] = set()
            for vals in existing_target_emails_by_master.values():
                existing_any_target_track.update(vals)
            existing_any_target_track_before_fill = set(existing_any_target_track)

            for email_norm, source_app in source_best_app_by_email.items():
                matched_emails.add(email_norm)
                copied_or_present = False
                for target_master in track_target_masters:
                    target_form = target_forms_by_master.get(target_master)
                    if target_form is None:
                        mapping_mismatch_emails.add(email_norm)
                        continue
                    existing_for_master = existing_target_emails_by_master.setdefault(target_master, set())
                    if email_norm in existing_for_master:
                        skipped_existing += 1
                        copied_or_present = True
                        continue
                    try:
                        _new_app, copied_count, skipped_count = _copy_application_to_form(source_app, target_form)
                    except ValueError:
                        mapping_mismatch_emails.add(email_norm)
                        continue
                    copied_apps += 1
                    copied_answers += copied_count
                    skipped_no_match_answers += skipped_count
                    copied_or_present = True
                    existing_any_target_track.add(email_norm)
                    existing_for_master.add(email_norm)

                if copied_or_present:
                    assigned_track_emails[selected_track].add(email_norm)

            source_unmatched_emails = sorted(wanted_emails - matched_emails)
            unmatched_email_count = len(source_unmatched_emails)
            unmatched_already_present = len(
                [email for email in source_unmatched_emails if email in existing_any_target_track_before_fill]
            )

            for target_master in track_target_masters:
                target_form = target_forms_by_master.get(target_master)
                if target_form is None:
                    continue
                existing_for_master = existing_target_emails_by_master.setdefault(target_master, set())
                missing_for_master = sorted(wanted_emails - existing_for_master)
                for email in missing_for_master:
                    Application.objects.create(
                        form=target_form,
                        name=(email or "")[:200],
                        email=email,
                    )
                    unmatched_inserted += 1
                    existing_for_master.add(email)
                    existing_any_target_track.add(email)
                    assigned_track_emails[selected_track].add(email)

            mapping_mismatch_fallback_inserted = len(
                [
                    email
                    for email in mapping_mismatch_emails
                    if all(
                        email in existing_target_emails_by_master.get(master_slug, set())
                        for master_slug in track_target_masters
                    )
                ]
            )
            mapping_mismatch_still_missing = len(
                mapping_mismatch_emails
            ) - mapping_mismatch_fallback_inserted

            # Participant lists are not auto-created/synced from assignment anymore.
            # They are managed explicitly from the Participants admin page.

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
    messages.info(
        request,
        (
            f"Group {target_group_num} schedule defaulted from {source_label}: "
            f"start day {start_day}, start month {start_month}."
        ),
    )
    if parsed_mentions != len(wanted_emails) or duplicate_mentions:
        messages.info(
            request,
            (
                f"Pasted entries detected: {parsed_mentions}. Unique emails processed: {len(wanted_emails)}. "
                f"Duplicate entries merged: {duplicate_mentions}."
            ),
        )
        if duplicate_values:
            preview_limit = 20
            preview_dupes = duplicate_values[:preview_limit]
            remainder_dupes = max(0, len(duplicate_values) - preview_limit)
            dupes_text = ", ".join(preview_dupes)
            if remainder_dupes:
                dupes_text = f"{dupes_text}, ... (+{remainder_dupes} more)"
            messages.info(request, f"Duplicate pasted email(s) merged into one row each: {dupes_text}")
    if skipped_existing:
        messages.info(
            request,
            f"Skipped {skipped_existing} already-existing application(s) in the target group.",
        )
    if removed_not_requested:
        messages.info(
            request,
            (
                f"Removed {removed_not_requested} existing {track_label.lower()} row(s) from "
                f"Group {target_group_num} because they were not in the pasted list."
            ),
        )
    if removed_duplicates:
        messages.info(
            request,
            (
                f"Removed {removed_duplicates} duplicate {track_label.lower()} row(s) in "
                f"Group {target_group_num}."
            ),
        )
    if mapping_mismatch_fallback_inserted:
        messages.info(
            request,
            (
                f"{mapping_mismatch_fallback_inserted} matched applicant(s) had no compatible question mapping "
                "and were added as email-only rows."
            ),
        )
    if mapping_mismatch_still_missing:
        messages.warning(
            request,
            (
                f"{mapping_mismatch_still_missing} matched applicant(s) could not be copied or inserted as "
                "email-only rows."
            ),
        )
    if skipped_no_match_answers:
        messages.info(
            request,
            (
                f"Skipped {skipped_no_match_answers} answer value(s) because those specific question slugs "
                "do not exist in the target form."
            ),
        )
    if unmatched_email_count:
        messages.warning(
            request,
            f"{unmatched_email_count} pasted email(s) did not match any application in {source_label}.",
        )
        preview_limit = 40
        preview = source_unmatched_emails[:preview_limit]
        remainder = max(0, len(source_unmatched_emails) - preview_limit)
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

    apps_qs = (
        Application.objects.filter(form=form_def)
        .select_related("form")
        .order_by("-created_at", "-id")
    )
    is_first_application = (form_def.slug or "").endswith("E_A1") or (form_def.slug or "").endswith("M_A1")
    a1_pass_filter = _normalized_a1_pass_filter(request.GET.get("a1_pass"))
    approval_filter = (request.GET.get("approval") or "").strip().lower()
    if approval_filter not in {"approved", "not_approved"}:
        approval_filter = ""
    if is_first_application:
        apps_qs = apps_qs.prefetch_related("answers__question")

    submission_total_count = apps_qs.count()
    second_stage_sent_count = (
        apps_qs.filter(invited_to_second_stage=True).count()
        if is_first_application
        else None
    )
    if approval_filter == "approved":
        apps_qs = apps_qs.filter(approved_for_grading=True)
    elif approval_filter == "not_approved":
        apps_qs = apps_qs.filter(approved_for_grading=False)
    apps = list(apps_qs)
    if is_first_application and a1_pass_filter:
        apps = _filter_a1_apps_by_pass_status(apps, a1_pass_filter)
    submission_count = len(apps)

    return render(
        request,
        "admin_dash/database_form_detail.html",
        {
            "form_def": form_def,
            "apps": apps,
            "submission_count": submission_count,
            "submission_total_count": submission_total_count,
            "is_first_application": is_first_application,
            "second_stage_sent_count": second_stage_sent_count,
            "a1_pass_filter": a1_pass_filter,
            "approval_filter": approval_filter,
        },
    )


@staff_member_required
def database_form_sheet(request, form_slug: str):
    form_def = get_object_or_404(FormDefinition, slug=form_slug)
    headers, rows = _build_csv_for_form(form_def)
    return render(
        request,
        "admin_dash/database_form_sheet.html",
        {
            "form_def": form_def,
            "sheet_headers": headers,
            "sheet_rows": rows,
        },
    )


@staff_member_required
def database_form_master_csv(request, form_slug: str):
    form_def = get_object_or_404(FormDefinition, slug=form_slug)
    headers, rows = _build_csv_for_form(form_def)
    return _csv_http_response(f"{form_slug}_MASTER.csv", headers, rows)


def _load_database_encuestas_grid(kind: str) -> tuple[str, list[str], list[list[str]], str, str]:
    key = (kind or "").strip().lower()
    label = _database_encuestas_label(key)
    file_ref = _database_encuestas_drive_file_ref(key)
    if not file_ref:
        if key == "mentoras":
            hint = "DATABASE_ENCUESTAS_MENTORAS_DRIVE_FILE"
        elif key == "mentoras_final":
            hint = "DATABASE_ENCUESTAS_MENTORAS_FINAL_DRIVE_FILE"
        elif key == "emprendedoras_final":
            hint = "DATABASE_ENCUESTAS_FINAL_DRIVE_FILE"
        else:
            hint = "DATABASE_ENCUESTAS_DRIVE_FILE"
        raise RuntimeError(
            f"{label} Drive file is not configured. Set {hint} "
            "(file ID or full Google Drive/Sheets URL)."
        )

    csv_text, file_id, file_name = fetch_drive_csv_file_text(file_ref)
    headers, rows = _csv_text_to_grid(csv_text)
    if not headers:
        raise RuntimeError(
            f"The configured {label} file returned no header row. "
            "Confirm the file is a non-empty Google Sheet or CSV."
        )
    return label, headers, rows, file_name, file_id


@staff_member_required
def database_encuestas_sheet(request):
    try:
        label, headers, rows, file_name, file_id = _load_database_encuestas_grid("emprendedoras")
    except Exception as exc:
        messages.error(request, f"Could not load Encuesta inicial from Drive: {exc}")
        return redirect("admin_database")

    refresh_requested = str(request.GET.get("refresh") or "").strip().lower() in {"1", "true", "yes"}
    if refresh_requested:
        messages.success(
            request,
            f"{label} refreshed from Drive: {len(rows)} row(s), {len(headers)} column(s).",
        )

    return render(
        request,
        "admin_dash/database_encuestas_sheet.html",
        {
            "encuestas_label": label,
            "encuestas_source_name": file_name,
            "encuestas_source_file_id": file_id,
            "encuestas_refresh_url_name": "admin_database_encuestas_sheet",
            "sheet_headers": headers,
            "sheet_rows": rows,
        },
    )


@staff_member_required
def database_encuestas_csv(request):
    try:
        label, headers, rows, _file_name, _file_id = _load_database_encuestas_grid("emprendedoras")
    except Exception as exc:
        messages.error(request, f"Could not load Encuesta inicial from Drive: {exc}")
        return redirect("admin_database")

    safe_label = re.sub(r"[^A-Za-z0-9_-]+", "_", label or DATABASE_ENCUESTAS_LABEL_DEFAULT).strip("_")
    if not safe_label:
        safe_label = "encuestas"
    return _csv_http_response(f"{safe_label}.csv", headers, rows)


@staff_member_required
def database_encuestas_final_sheet(request):
    try:
        label, headers, rows, file_name, file_id = _load_database_encuestas_grid("emprendedoras_final")
    except Exception as exc:
        messages.error(request, f"Could not load Encuesta final from Drive: {exc}")
        return redirect("admin_database")

    refresh_requested = str(request.GET.get("refresh") or "").strip().lower() in {"1", "true", "yes"}
    if refresh_requested:
        messages.success(
            request,
            f"{label} refreshed from Drive: {len(rows)} row(s), {len(headers)} column(s).",
        )

    return render(
        request,
        "admin_dash/database_encuestas_sheet.html",
        {
            "encuestas_label": label,
            "encuestas_source_name": file_name,
            "encuestas_source_file_id": file_id,
            "encuestas_refresh_url_name": "admin_database_encuestas_final_sheet",
            "sheet_headers": headers,
            "sheet_rows": rows,
        },
    )


@staff_member_required
def database_encuestas_final_csv(request):
    try:
        label, headers, rows, _file_name, _file_id = _load_database_encuestas_grid("emprendedoras_final")
    except Exception as exc:
        messages.error(request, f"Could not load Encuesta final from Drive: {exc}")
        return redirect("admin_database")

    safe_label = re.sub(
        r"[^A-Za-z0-9_-]+",
        "_",
        label or DATABASE_ENCUESTAS_FINAL_LABEL_DEFAULT,
    ).strip("_")
    if not safe_label:
        safe_label = "encuestas_final"
    return _csv_http_response(f"{safe_label}.csv", headers, rows)


@staff_member_required
def database_encuestas_mentoras_sheet(request):
    try:
        label, headers, rows, file_name, file_id = _load_database_encuestas_grid("mentoras")
    except Exception as exc:
        messages.error(request, f"Could not load Encuesta inicial from Drive: {exc}")
        return redirect("admin_database")

    refresh_requested = str(request.GET.get("refresh") or "").strip().lower() in {"1", "true", "yes"}
    if refresh_requested:
        messages.success(
            request,
            f"{label} refreshed from Drive: {len(rows)} row(s), {len(headers)} column(s).",
        )

    return render(
        request,
        "admin_dash/database_encuestas_sheet.html",
        {
            "encuestas_label": label,
            "encuestas_source_name": file_name,
            "encuestas_source_file_id": file_id,
            "encuestas_refresh_url_name": "admin_database_encuestas_mentoras_sheet",
            "sheet_headers": headers,
            "sheet_rows": rows,
        },
    )


@staff_member_required
def database_encuestas_mentoras_csv(request):
    try:
        label, headers, rows, _file_name, _file_id = _load_database_encuestas_grid("mentoras")
    except Exception as exc:
        messages.error(request, f"Could not load Encuesta inicial from Drive: {exc}")
        return redirect("admin_database")

    safe_label = re.sub(
        r"[^A-Za-z0-9_-]+",
        "_",
        label or DATABASE_ENCUESTAS_MENTORAS_LABEL_DEFAULT,
    ).strip("_")
    if not safe_label:
        safe_label = "encuestas_mentoras"
    return _csv_http_response(f"{safe_label}.csv", headers, rows)


@staff_member_required
def database_encuestas_mentoras_final_sheet(request):
    try:
        label, headers, rows, file_name, file_id = _load_database_encuestas_grid("mentoras_final")
    except Exception as exc:
        messages.error(request, f"Could not load Encuesta final from Drive: {exc}")
        return redirect("admin_database")

    refresh_requested = str(request.GET.get("refresh") or "").strip().lower() in {"1", "true", "yes"}
    if refresh_requested:
        messages.success(
            request,
            f"{label} refreshed from Drive: {len(rows)} row(s), {len(headers)} column(s).",
        )

    return render(
        request,
        "admin_dash/database_encuestas_sheet.html",
        {
            "encuestas_label": label,
            "encuestas_source_name": file_name,
            "encuestas_source_file_id": file_id,
            "encuestas_refresh_url_name": "admin_database_encuestas_mentoras_final_sheet",
            "sheet_headers": headers,
            "sheet_rows": rows,
        },
    )


@staff_member_required
def database_encuestas_mentoras_final_csv(request):
    try:
        label, headers, rows, _file_name, _file_id = _load_database_encuestas_grid("mentoras_final")
    except Exception as exc:
        messages.error(request, f"Could not load Encuesta final from Drive: {exc}")
        return redirect("admin_database")

    safe_label = re.sub(
        r"[^A-Za-z0-9_-]+",
        "_",
        label or DATABASE_ENCUESTAS_MENTORAS_FINAL_LABEL_DEFAULT,
    ).strip("_")
    if not safe_label:
        safe_label = "encuestas_mentoras_final"
    return _csv_http_response(f"{safe_label}.csv", headers, rows)


@staff_member_required
def database_type_detail(request, app_type: str):
    app_type = (app_type or "").upper().strip()
    if app_type not in MASTER_SLUGS:
        raise Http404("Unsupported application type")

    group_raw = (request.GET.get("group") or "").strip()
    selected_group: int | None = int(group_raw) if group_raw.isdigit() else None
    is_first_application_type = app_type.endswith("_A1")
    a1_pass_filter = _normalized_a1_pass_filter(request.GET.get("a1_pass")) if is_first_application_type else ""
    approval_filter = (request.GET.get("approval") or "").strip().lower()
    if approval_filter not in {"approved", "not_approved"}:
        approval_filter = ""

    all_forms = _group_forms_for_app_type(app_type, include_combined_groups=False)
    if selected_group is None:
        forms = list(all_forms)
    else:
        filtered_forms: list[FormDefinition] = []
        for fd in all_forms:
            gnum = getattr(getattr(fd, "group", None), "number", None)
            if gnum is None:
                gnum = _group_number_from_slug(fd.slug or "")
            if gnum == selected_group:
                filtered_forms.append(fd)
        forms = filtered_forms

    group_options = sorted(
        g
        for g in {
            getattr(getattr(fd, "group", None), "number", None)
            or _group_number_from_slug(fd.slug or "")
            for fd in all_forms
        }
        if g is not None
    )
    group_map = {
        int(g.number): g
        for g in FormGroup.objects.filter(number__in=group_options)
    }
    group_option_items = [
        {"number": gnum, "label": _group_label_for_number(gnum, group_map)}
        for gnum in group_options
    ]
    selected_group_label = (
        _group_label_for_number(selected_group, group_map)
        if selected_group is not None
        else ""
    )

    apps_qs = (
        Application.objects.filter(form__in=forms)
        .select_related("form", "form__group")
        .order_by("-created_at", "-id")
    ) if forms else Application.objects.none()

    if is_first_application_type:
        apps_qs = apps_qs.prefetch_related("answers__question")

    submission_total_count = apps_qs.count()
    if approval_filter == "approved":
        apps_qs = apps_qs.filter(approved_for_grading=True)
    elif approval_filter == "not_approved":
        apps_qs = apps_qs.filter(approved_for_grading=False)
    apps = list(apps_qs)
    if is_first_application_type and a1_pass_filter:
        apps = _filter_a1_apps_by_pass_status(apps, a1_pass_filter)
    submission_count = len(apps)

    return render(
        request,
        "admin_dash/database_type_detail.html",
        {
            "app_type": app_type,
            "selected_group": selected_group,
            "group_options": group_options,
            "group_option_items": group_option_items,
            "selected_group_label": selected_group_label,
            "apps": apps,
            "submission_count": submission_count,
            "submission_total_count": submission_total_count,
            "is_first_application_type": is_first_application_type,
            "a1_pass_filter": a1_pass_filter,
            "approval_filter": approval_filter,
        },
    )


@staff_member_required
def database_type_sheet(request, app_type: str):
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
    group_map = {
        int(g.number): g
        for g in FormGroup.objects.filter(number__in=group_options)
    }
    group_option_items = [
        {"number": gnum, "label": _group_label_for_number(gnum, group_map)}
        for gnum in group_options
    ]
    selected_group_label = (
        _group_label_for_number(selected_group, group_map)
        if selected_group is not None
        else ""
    )

    return render(
        request,
        "admin_dash/database_type_sheet.html",
        {
            "app_type": app_type,
            "selected_group": selected_group,
            "group_options": group_options,
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
    approval_filter = (request.GET.get("approval") or "").strip().lower()
    if approval_filter not in {"approved", "not_approved"}:
        approval_filter = ""
    completion_filter = (request.GET.get("completion") or "").strip().lower()
    if completion_filter == TRACK_COMPLETION_FILTER_EXCLUDE_A2_ONLY:
        completion_filter = TRACK_COMPLETION_FILTER_A1_ONLY
    if completion_filter not in {
        TRACK_COMPLETION_FILTER_ALL,
        TRACK_COMPLETION_FILTER_A1_ONLY,
        TRACK_COMPLETION_FILTER_A1_A2,
        TRACK_COMPLETION_FILTER_A1_NOT_PASSED,
    }:
        completion_filter = TRACK_COMPLETION_FILTER_ALL

    all_forms = _group_forms_for_track(track)
    if selected_group is None:
        forms = list(all_forms)
    else:
        filtered_forms: list[FormDefinition] = []
        for fd in all_forms:
            gnum = getattr(getattr(fd, "group", None), "number", None)
            if gnum is None:
                gnum = _group_number_from_slug(fd.slug or "")
            if gnum == selected_group:
                filtered_forms.append(fd)
        forms = filtered_forms

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

    group_options = sorted(
        g
        for g in {
            getattr(getattr(fd, "group", None), "number", None)
            or _group_number_from_slug(fd.slug or "")
            for fd in all_forms
        }
        if g is not None
    )
    group_map = {
        int(g.number): g
        for g in FormGroup.objects.filter(number__in=group_options)
    }
    group_option_items = [
        {"number": gnum, "label": _group_label_for_number(gnum, group_map)}
        for gnum in group_options
    ]
    selected_group_label = (
        _group_label_for_number(selected_group, group_map)
        if selected_group is not None
        else ""
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
    elif completion_filter == TRACK_COMPLETION_FILTER_A1_NOT_PASSED:
        apps_qs = (
            apps_qs.filter(form__slug__iendswith=f"{track}_A1")
            .prefetch_related("answers__question")
        )

    if approval_filter == "approved":
        apps_qs = apps_qs.filter(approved_for_grading=True)
    elif approval_filter == "not_approved":
        apps_qs = apps_qs.filter(approved_for_grading=False)

    apps = list(apps_qs)
    if completion_filter == TRACK_COMPLETION_FILTER_A1_NOT_PASSED:
        apps = _filter_a1_apps_by_pass_status(apps, "not_passed")

    track_label = "Emprendedoras" if track == "E" else "Mentoras"
    return render(
        request,
        "admin_dash/database_track_detail.html",
        {
            "track": track,
            "track_label": track_label,
            "selected_group": selected_group,
            "selected_group_label": selected_group_label,
            "completion_filter": completion_filter,
            "approval_filter": approval_filter,
            "group_options": group_options,
            "group_option_items": group_option_items,
            "apps": apps,
            "second_part_completed_count": second_part_completed_count,
        },
    )


@staff_member_required
def database_track_sheet(request, track: str):
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
        TRACK_COMPLETION_FILTER_A1_NOT_PASSED,
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
    elif completion_filter == TRACK_COMPLETION_FILTER_A1_NOT_PASSED:
        apps_qs = (
            apps_qs.filter(form__slug__iendswith=f"{track}_A1")
            .prefetch_related("answers__question")
        )

    apps = list(apps_qs)
    if completion_filter == TRACK_COMPLETION_FILTER_A1_NOT_PASSED:
        apps = _filter_a1_apps_by_pass_status(apps, "not_passed")

    if completion_filter in {
        TRACK_COMPLETION_FILTER_A1_ONLY,
        TRACK_COMPLETION_FILTER_A1_A2,
        TRACK_COMPLETION_FILTER_A1_NOT_PASSED,
    } and rows:
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

    track_label = "Emprendedoras" if track == "E" else "Mentoras"
    return render(
        request,
        "admin_dash/database_track_sheet.html",
        {
            "track": track,
            "track_label": track_label,
            "selected_group": selected_group,
            "completion_filter": completion_filter,
            "group_options": group_options,
            "second_part_completed_count": second_part_completed_count,
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

    scope_group_raw = (request.POST.get("scope_group") or "").strip()
    scope_group: int | None = int(scope_group_raw) if scope_group_raw.isdigit() else None
    scope_form_slug = (request.POST.get("scope_form_slug") or "").strip()
    scope_filtered_out = 0

    if scope_group is not None:
        scoped_apps: list[Application] = []
        slug_prefix = f"G{scope_group}_".upper()
        for app in apps:
            form = getattr(app, "form", None)
            slug = str(getattr(form, "slug", "") or "")
            form_group_num = getattr(getattr(form, "group", None), "number", None)
            in_scope = (form_group_num == scope_group) or slug.upper().startswith(slug_prefix)
            if in_scope:
                scoped_apps.append(app)
            else:
                scope_filtered_out += 1
        apps = scoped_apps

    if scope_form_slug:
        scoped_apps = [app for app in apps if str(getattr(getattr(app, "form", None), "slug", "") or "") == scope_form_slug]
        scope_filtered_out += max(0, len(apps) - len(scoped_apps))
        apps = scoped_apps

    if not apps:
        messages.warning(
            request,
            "No selected submissions matched the current group/form scope, so nothing was deleted.",
        )
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
    if scope_filtered_out:
        messages.info(
            request,
            (
                f"{scope_filtered_out} selected submission(s) were outside the current "
                "group/form scope and were not deleted."
            ),
        )

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

A2_FORM_RE = re.compile(r"^(?:[A-Za-z0-9_]+_)?(?:E_A2|M_A2)$")


def _redirect_back_to_grading(request):
    group = (request.GET.get("group") or "").strip()
    url = reverse("admin_grading_home")
    if group:
        url = f"{url}?group={group}"
    return redirect(url)


def grading_home(request):
    group = (request.GET.get("group") or "").strip()

    fds = FormDefinition.objects.filter(slug__regex=CURRENT_GRADING_FORM_RE.pattern).order_by("slug")
    if group:
        fds = fds.filter(group__number=group)

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
        for row in Application.objects.filter(form__in=fds, approved_for_grading=True)
        .values("form__slug")
        .annotate(c=Count("id"))
    }

    pending = {
        row["form__slug"]: row["c"]
        for row in (
            Application.objects.filter(form__in=fds, approved_for_grading=True)
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
def grading_config_editor(request, form_slug: str):
    form = get_object_or_404(FormDefinition, slug=form_slug)
    config = ensure_grading_config_for_form(form)

    if request.method == "POST":
        config.model_name = (request.POST.get("model_name") or "").strip()
        config.rubric_note = (request.POST.get("rubric_note") or "").strip()
        max_total_raw = (request.POST.get("max_total_score") or "").strip()
        if max_total_raw:
            try:
                config.max_total_score = max_total_raw
            except Exception:
                messages.error(request, "Max total score must be a valid number.")
                return redirect(request.path)
        else:
            config.max_total_score = None
        config.save(update_fields=["model_name", "rubric_note", "max_total_score", "updated_at"])

        for criterion in config.criteria.all():
            prefix = f"criterion_{criterion.id}"
            criterion.active = request.POST.get(f"{prefix}_active") == "on"
            weight_key = f"{prefix}_weight"
            if weight_key in request.POST:
                criterion.weight = (request.POST.get(weight_key) or "0").strip() or "0"
            criterion.negative_allowed = request.POST.get(f"{prefix}_negative_allowed") == "on"
            if criterion.criterion_type == criterion.TYPE_AI_TEXT:
                instructions_key = f"{prefix}_instructions"
                before_key = f"{prefix}_prompt_before"
                after_key = f"{prefix}_prompt_after"
                if instructions_key in request.POST:
                    criterion.prompt = request.POST.get(instructions_key) or ""
                elif before_key in request.POST or after_key in request.POST:
                    prompt_before = request.POST.get(before_key) or ""
                    prompt_after = request.POST.get(after_key) or ""
                    criterion.prompt = f"{prompt_before}{{{{ response }}}}{prompt_after}"
                else:
                    # Backwards-compatible with older clients and saved forms.
                    criterion.prompt = request.POST.get(f"{prefix}_prompt") or ""
            else:
                criterion.prompt = ""
            criterion.save(update_fields=["active", "weight", "negative_allowed", "prompt", "updated_at"])

        for response_weight in config.response_weights.all():
            prefix = f"response_weight_{response_weight.id}"
            response_weight.active = request.POST.get(f"{prefix}_active") == "on"
            response_weight.weight = (request.POST.get(f"{prefix}_weight") or "0").strip() or "0"
            response_weight.save(update_fields=["active", "weight", "updated_at"])

        messages.success(request, f"Saved grading rules for {form.slug}.")
        return redirect(request.path)

    questions_by_slug = {
        q.slug: q
        for q in form.questions.filter(active=True).order_by("position", "id")
    }
    criteria = list(config.criteria.all().order_by("position", "id"))
    for criterion in criteria:
        criterion.question_obj = questions_by_slug.get(criterion.question_slug)
    paragraph_criteria = [c for c in criteria if c.criterion_type == c.TYPE_AI_TEXT]
    structured_criteria = [c for c in criteria if c.criterion_type == c.TYPE_STRUCTURED]

    criteria_by_slug = {criterion.question_slug: criterion for criterion in paragraph_criteria}
    is_mentor = form_slug.upper().endswith(("M_A1", "M_A2"))
    if is_mentor:
        from applications import grader_m as grader

        disqualification_rules = [
            "Every required field below must equal exactly 'yes':",
            *grader.REQ_FIELDS,
        ]
    else:
        from applications import grader_e as grader

        disqualification_rules = [
            "Current forms pass requirements when meets_requirements/meets_all_req is affirmative.",
            "Current forms pass availability when available_period/availability_ok is affirmative.",
            "Legacy internet_access and commit_3_months are checked only when those columns exist.",
            "business_age = 'idea' disqualifies the application only when that column exists.",
        ]

    ai_field_slugs = [criterion.question_slug for criterion in paragraph_criteria if criterion.active]
    ai_request_previews = []
    for slug in ai_field_slugs:
        criterion = criteria_by_slug.get(slug)
        prompt_template = criterion.prompt if criterion and criterion.active else ""
        response_placeholder = f"<applicant response for {slug}>"
        ai_request_previews.append({
            "slug": slug,
            "question": questions_by_slug.get(slug),
            "active": bool(criterion and criterion.active),
            "uses_custom_prompt": bool(prompt_template.strip()),
            "prompt": grader.build_grading_prompt(
                response_placeholder,
                slug,
                prompt_template,
            ),
        })

    for criterion in paragraph_criteria:
        raw_prompt = (criterion.prompt or "").strip()
        if not raw_prompt:
            criterion.editor_instructions = grader.DEFAULT_GRADING_INSTRUCTIONS
            continue
        if "{{ response }}" not in raw_prompt and "{{ criterion }}" not in raw_prompt:
            criterion.editor_instructions = raw_prompt
            continue
        before = raw_prompt.split("{{ response }}", 1)[0]
        question_label = getattr(criterion.question_obj, "text", "") or criterion.label or criterion.question_slug
        before = before.replace("{{ criterion }}", question_label)
        before = re.sub(r"^\s*Criterion:.*?\n\s*(?:Rules|Instructions):\s*", "", before, flags=re.DOTALL)
        before = re.sub(r"\s*(?:Response|Answer):\s*(?:\"\"\")?\s*$", "", before, flags=re.IGNORECASE)
        criterion.editor_instructions = before.strip() or grader.DEFAULT_GRADING_INSTRUCTIONS

    effective_model = (config.model_name or "").strip() or grader.MODEL
    openai_flow = {
        "model": effective_model,
        "using_override": bool((config.model_name or "").strip()),
        "temperature": 0,
        "timeout": grader.TIMEOUT,
        "moderation_model": grader.MODERATION_MODEL,
        "moderation_limit": grader.MODERATION_INPUT_LIMIT,
        "moderation_fields": list(grader.MODERATION_FIELDS),
        "ai_requests": ai_request_previews,
        "disqualification_rules": disqualification_rules,
    }

    response_groups_by_question: dict[int, dict] = {}
    for item in (
        config.response_weights
        .select_related("question", "choice")
        .order_by("question__position", "choice__position", "id")
    ):
        question = item.question
        group = response_groups_by_question.setdefault(
            question.id,
            {
                "question": question,
                "items": [],
            },
        )
        group["items"].append(item)

    structured_by_slug = {criterion.question_slug: criterion for criterion in structured_criteria}
    structured_groups = list(response_groups_by_question.values())
    grouped_slugs = set()
    for group in structured_groups:
        slug = group["question"].slug
        group["criterion"] = structured_by_slug.get(slug)
        grouped_slugs.add(slug)
    for criterion in structured_criteria:
        if criterion.question_slug in grouped_slugs:
            continue
        structured_groups.append({
            "question": criterion.question_obj,
            "criterion": criterion,
            "items": [],
        })

    return render(
        request,
        "admin_dash/grading_config_editor.html",
        {
            "form_def": form,
            "config": config,
            "paragraph_criteria": paragraph_criteria,
            "structured_groups": structured_groups,
            "openai_flow": openai_flow,
            "back_url": reverse("admin_grading_home"),
        },
    )


@staff_member_required
def pairing_config_editor(request, group_num: int):
    group = get_object_or_404(FormGroup, number=group_num)
    config = ensure_pairing_config_for_group(group)
    return redirect(f"/admin/applications/pairingconfig/{config.id}/change/")


@staff_member_required
def _render_grading_live_sheet(
    request,
    graded_file: GradedFile,
    form_slug: str,
    *,
    back_url_name: str,
    back_label: str,
):
    headers, rows = _csv_text_to_grid(graded_file.csv_text or "")
    if request.method == "POST":
        if not headers:
            messages.error(request, "This graded file has no header row and cannot be edited in-sheet.")
            return redirect(request.path)

        headers_raw = (request.POST.get("headers_json") or "").strip()
        edited_headers = list(headers)
        if headers_raw:
            try:
                headers_payload = json.loads(headers_raw)
            except json.JSONDecodeError:
                messages.error(request, "Could not read sheet columns. Please try again.")
                return redirect(request.path)

            if not isinstance(headers_payload, list):
                messages.error(request, "Sheet columns were out of sync. Please reload and try again.")
                return redirect(request.path)

            edited_headers = ["" if h is None else str(h) for h in headers_payload]

        if not edited_headers:
            messages.error(request, "The sheet must have at least one column.")
            return redirect(request.path)

        rows_raw = (request.POST.get("rows_json") or "").strip()
        try:
            rows_payload = json.loads(rows_raw) if rows_raw else []
        except json.JSONDecodeError:
            messages.error(request, "Could not read sheet edits. Please try again.")
            return redirect(request.path)

        rows = _normalize_csv_data_rows(rows_payload, len(edited_headers))
        csv_text = _grid_to_csv_text(edited_headers, rows)

        graded_file.csv_text = csv_text
        graded_file.save(update_fields=["csv_text"])

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

        return redirect(request.path)

    return render(
        request,
        "admin_dash/grading_live_sheet.html",
        {
            "form_slug": form_slug,
            "graded_file": graded_file,
            "headers": headers,
            "rows": rows,
            "needs_regeneration": _graded_sheet_needs_regeneration(form_slug, headers),
            "back_url": reverse(back_url_name),
            "back_label": back_label,
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
    return _render_grading_live_sheet(
        request,
        latest_file,
        form_slug,
        back_url_name="admin_grading_home",
        back_label="Back to Grading",
    )


@staff_member_required
def grading_live_sheet_file(request, graded_file_id: int):
    graded_file = get_object_or_404(GradedFile, id=graded_file_id)
    form_slug = (graded_file.form_slug or "").strip()
    if not form_slug:
        messages.error(request, "This graded file has no form slug and cannot be opened in sheet mode.")
        return redirect("admin_database")
    return _render_grading_live_sheet(
        request,
        graded_file,
        form_slug,
        back_url_name="admin_database",
        back_label="Back to Database",
    )


def _graded_sheet_needs_regeneration(form_slug: str, headers: list[str]) -> bool:
    normalized_headers = {_normalized_header_key(header) for header in headers or []}
    slug = (form_slug or "").strip().upper()
    if not (slug.endswith("E_A1") or slug.endswith("M_A1")):
        return False
    # Current grading exports include application_id from the source dataset.
    # The older legacy scorer-only exports did not, and can still contain stale
    # all-disqualified rows until grading is rerun.
    return bool(headers) and "applicationid" not in normalized_headers


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
    Creates/imports into TEST_E_A1 or TEST_M_A1 based on POST 'role' = 'E' or 'M'.
    """
    role = (request.POST.get("role") or "").strip().upper()
    if role not in ("E", "M"):
        messages.error(request, "Select role (E or M) for the test upload.")
        return _redirect_back_to_grading(request)

    fd = _ensure_test_grading_form(role)

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
        return f"{form_slug[:-1]}1"

    if form_slug.endswith("E_A2"):
        return f"{form_slug[:-1]}1"

    return None


def _mentor_a1_passes(answers: dict[str, str]) -> bool:
    answers = {k: (v or "") for k, v in (answers or {}).items()}

    def yesish(v: str) -> bool:
        t = (v or "").strip().lower()
        return ("si" in t) or ("sí" in t) or ("yes" in t) or (t == "true") or (t == "1")

    requisitos = (
        answers.get("meets_requirements")
        or answers.get("m1_meet_requirements")
        or answers.get("m1_meets_requirements")
        or answers.get("m1_requirements_ok")
        or ""
    )
    disponibilidad = (
        answers.get("available_period")
        or answers.get("availability_ok")
        or answers.get("m1_availability_ok")
        or answers.get("m1_available_period")
        or answers.get("m1_available")
        or ""
    )
    return yesish(requisitos) and yesish(disponibilidad)


def _normalize_person_name(raw_value: str | None) -> str:
    text = str(raw_value or "").strip()
    if not text:
        return ""
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


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


@staff_member_required
@require_POST
def send_application_update_email(request, form_slug: str):
    form_def = get_object_or_404(FormDefinition, slug=form_slug)
    message_body = (request.POST.get("message_body") or "").strip()
    next_url = request.POST.get("next") or reverse("admin_apps_list")
    if not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = reverse("admin_apps_list")

    if not message_body:
        messages.error(request, f"Escribe un mensaje antes de enviar correos para {form_slug}.")
        return redirect(next_url)

    recipients = _application_email_recipients_for_form(form_def)
    if not recipients:
        messages.warning(request, f"No hay correos válidos para enviar en {form_slug}.")
        return redirect(next_url)

    try:
        sent = _send_application_update_email(
            form_slug=form_slug,
            recipients=recipients,
            message_body=message_body,
        )
    except Exception as exc:
        logger.exception("Application update email failed for %s", form_slug)
        messages.error(request, f"No se pudo enviar el correo para {form_slug}: {exc}")
        return redirect(next_url)

    messages.success(
        request,
        (
            f"Correo enviado para {form_slug}: {sent} destinataria(s). "
            "Los correos se enviaron ocultos en BCC desde contacto@clubemprendo.org."
        ),
    )
    return redirect(next_url)

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
    use_combined_flow = bool(getattr(group, "use_combined_application", False))

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
        .only("id", "email", "name", "created_at", "invited_to_second_stage")
        .prefetch_related("answers__question")
        .order_by("-created_at", "-id")
    )

    completed_emails: set[str] = set()
    completed_names: set[str] = set()
    if not use_combined_flow:
        # Non-combined cohorts may keep A1 and A2 as separate rows, so we must
        # detect completed A2 identities to avoid reminding those users again.
        completed_a2_qs = (
            Application.objects.filter(form=a2_form)
            .filter(
                Q(second_stage_reminder_sent_at__isnull=False)
                | (Q(recommendation__isnull=False) & ~Q(recommendation__exact=""))
                | Q(overall_score__gt=0)
                | Q(tablestakes_score__gt=0)
                | Q(commitment_score__gt=0)
                | Q(nice_to_have_score__gt=0)
            )
            .only("id", "email", "name")
        )
        for a2_app in completed_a2_qs:
            email_norm = (a2_app.email or "").strip().lower()
            name_norm = _normalize_person_name(getattr(a2_app, "name", ""))
            if email_norm:
                completed_emails.add(email_norm)
            if name_norm:
                completed_names.add(name_norm)

    targets: list[str] = []
    sent_emails: set[str] = set()
    counted_completed_people: set[str] = set()
    scanned_a1_apps = 0
    eligible_count = 0
    ineligible_count = 0
    already_completed_count = 0
    missing_email_count = 0
    for app in a1_apps_qs:
        scanned_a1_apps += 1
        email = (app.email or "").strip().lower()
        name_norm = _normalize_person_name(getattr(app, "name", ""))
        person_key = name_norm or email or f"app:{app.id}"

        has_completed_a2 = False
        if name_norm:
            has_completed_a2 = name_norm in completed_names
        elif email:
            has_completed_a2 = email in completed_emails
        if has_completed_a2:
            if person_key not in counted_completed_people:
                counted_completed_people.add(person_key)
                already_completed_count += 1
            continue

        is_eligible = bool(getattr(app, "invited_to_second_stage", False))
        if not is_eligible:
            answers = {
                a.question.slug: (a.value or "")
                for a in app.answers.all()
            }
            if is_emprendedora:
                is_eligible = emprendedora_a1_passes(answers)
            else:
                is_eligible = _mentor_a1_passes(answers)

        if is_eligible:
            eligible_count += 1
            if email and email not in sent_emails:
                sent_emails.add(email)
                targets.append(email)
            elif not email:
                missing_email_count += 1
        else:
            ineligible_count += 1

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

    default_subject = "Últimos días para completar la segunda aplicación"
    default_html_body = f"""
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
    replacements = build_form_email_context(
        form_def=a2_form,
        role_word=track_word,
        a2_link=a2_link,
        deadline=deadline,
    )
    subject = resolve_form_email_template(
        form_def=a2_form,
        field_name="email_a2_final_reminder_subject",
        default_text=default_subject,
        replacements=replacements,
        is_subject=True,
    )
    html_body = resolve_form_email_template(
        form_def=a2_form,
        field_name="email_a2_final_reminder_body",
        default_text=default_html_body,
        replacements=replacements,
    )

    return {
        "form_slug": form_slug,
        "targets": targets,
        "subject": subject,
        "html_body": html_body,
        "debug_counts": {
            "a1_scanned": scanned_a1_apps,
            "eligible": eligible_count,
            "ineligible": ineligible_count,
            "already_completed_a2": already_completed_count,
            "eligible_missing_email": missing_email_count,
        },
    }, None


def _start_second_stage_reminders(form_slug: str) -> tuple[str, int, str]:
    payload, error = _build_second_stage_reminder_payload(form_slug)
    if error:
        return "error", 0, error
    if not payload:
        return "error", 0, f"No se pudo construir reminders para {form_slug}."

    targets = payload["targets"]
    if not targets:
        counts = payload.get("debug_counts") or {}
        a1_scanned = int(counts.get("a1_scanned") or 0)
        eligible = int(counts.get("eligible") or 0)
        ineligible = int(counts.get("ineligible") or 0)
        completed = int(counts.get("already_completed_a2") or 0)
        missing_email = int(counts.get("eligible_missing_email") or 0)
        return (
            "no_targets",
            0,
            (
                f"No hay personas pendientes para {form_slug}. "
                f"A1 revisadas: {a1_scanned}; elegibles: {eligible}; "
                f"ya con A2: {completed}; no elegibles: {ineligible}; "
                f"elegibles sin email para enviar: {missing_email}."
            ),
        )

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

    # only allow the current Emprendedora application
    if not app.form.slug.endswith("E_A1"):
        messages.error(request, "This grading button is only for Emprendedoras (E_A1).")
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
