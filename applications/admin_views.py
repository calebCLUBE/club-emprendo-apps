# applications/admin_views.py
import openai
import csv
import io
import re
from typing import List, Tuple
from urllib.parse import urlparse
from django.core.mail import get_connection
import time
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
from django.db import transaction, DatabaseError
from django.db.models import Model, Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
import os
from applications.grader_e import grade_single_row, grade_from_dataframe
from django.db import connection
from applications.grader_e import grade_from_dataframe as grade_e_df
from applications.grader_m import grade_from_dataframe as grade_m_df
import math
from django.db import connection
from applications.models import (
    Application,
    Answer,
    Choice,
    FormDefinition,
    FormGroup,
    Section,
    Question,
    GradedFile,
    GradingJob,
    PairingJob
)
import logging

logger = logging.getLogger(__name__)
MASTER_SLUGS = ["E_A1", "E_A2", "M_A1", "M_A2"]
GROUP_SLUG_RE = re.compile(r"^G(?P<num>\d+)_(?P<master>E_A1|E_A2|M_A1|M_A2)$")
TEST_E_A2_SLUG = "TEST_E_A2"
TEST_M_A2_SLUG = "TEST_M_A2"
A2_FORM_RE = re.compile(r"^(G\d+_)?(E_A2|M_A2)$")
TEST_A2_FORM_RE = re.compile(r"^TEST_(E_A2|M_A2)$")

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

        # ----------------------------------
        # Validate form type
        # ----------------------------------
        if not (job.form_slug.endswith("E_A2") or job.form_slug.endswith("M_A2")):
            raise RuntimeError(f"Unsupported form type: {job.form_slug}")

        fd = FormDefinition.objects.get(slug=job.form_slug)

        apps = (
            Application.objects
            .filter(form=fd)
            .prefetch_related("answers__question")
            .order_by("created_at", "id")
        )

        if not apps.exists():
            raise RuntimeError("No applications to grade.")

        _job_log(job, f"📦 Building master dataset ({apps.count()} applications)")

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
        for app in apps:
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
                log_fn=lambda msg: _job_log(job, msg)
            )

        else:  # M_A2
            from applications.grader_m import grade_from_dataframe
            graded_df = grade_from_dataframe(
                master_df,
                client,
                log_fn=lambda msg: _job_log(job, msg)
            )

        if graded_df is None or graded_df.empty:
            raise RuntimeError("Grader returned empty output")

        # ----------------------------------
        # STORE ONE GRADED FILE
        # ----------------------------------
        csv_text = graded_df.to_csv(index=False)

        GradedFile.objects.filter(form_slug=job.form_slug).delete()

        gf = GradedFile.objects.create(
            form_slug=job.form_slug,
            csv_text=csv_text,
        )

        _job_log(job, f"📄 Saved graded file (id={gf.id}, bytes={len(csv_text)})")
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
@require_POST
def start_grading_job(request, form_slug: str):
    job = GradingJob.objects.create(
        form_slug=form_slug,
        status=GradingJob.STATUS_PENDING,
        log_text="Queued...\n",
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
    parts = [p.strip().lower() for p in s.split(",")]
    return [p for p in parts if p]


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
class CreateGroupForm(forms.Form):
    group_num = forms.IntegerField(min_value=1, label="Group number")
    start_month = forms.CharField(max_length=30, label="Start month")
    end_month = forms.CharField(max_length=30, label="End month")
    year = forms.IntegerField(min_value=2020, max_value=2100, label="Year")


# ----------------------------
# Helpers
# ----------------------------
def _fill_placeholders(
    text: str | None, group_num: int, start_month: str, end_month: str, year: int
) -> str | None:
    if not text:
        return text

    out = text.replace("#(group number)", str(group_num))

    if "#(month)" in out:
        out = out.replace("#(month)", start_month, 1)
    if "#(month)" in out:
        out = out.replace("#(month)", end_month, 1)

    out = out.replace("#(year)", str(year))
    return out


def _model_has_field(model: type[Model], field_name: str) -> bool:
    try:
        model._meta.get_field(field_name)
        return True
    except Exception:
        return False

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
    start_month = group.start_month
    end_month = group.end_month
    year = group.year

    new_slug = f"G{group_num}_{master_fd.slug}"
    new_name = f"Grupo {group_num} — {master_fd.name}"

    existing = FormDefinition.objects.filter(slug=new_slug).first()
    if existing:
        return existing

    clone = FormDefinition.objects.create(
        slug=new_slug,
        name=new_name,
        description=_fill_placeholders(
            master_fd.description, group_num, start_month, end_month, year
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
            title=_fill_placeholders(s.title, group_num, start_month, end_month, year) or s.title,
            description=_fill_placeholders(s.description, group_num, start_month, end_month, year) or s.description,
            position=s.position,
        )

    for q in master_fd.questions.select_related("section").all().order_by("position", "id"):
        new_section = section_map.get(q.section_id)
        q_clone = Question.objects.create(
            form=clone,
            text=_fill_placeholders(q.text, group_num, start_month, end_month, year) or q.text,
            help_text=_fill_placeholders(q.help_text, group_num, start_month, end_month, year) or q.help_text,
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
                label=_fill_placeholders(c.label, group_num, start_month, end_month, year) or c.label,
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


def _csv_http_response(filename: str, headers: List[str], rows: List[List[str]]) -> HttpResponse:
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    w = csv.writer(resp)
    w.writerow(headers)
    w.writerows(rows)
    return resp


def _csv_preview_html(headers: List[str], rows: List[List[str]], max_rows: int = 25) -> str:
    def esc(s: str) -> str:
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    preview = rows[:max_rows]

    ths = "".join(
        f"<th style='text-align:left;padding:6px;border-bottom:1px solid #ddd;'>{esc(h)}</th>"
        for h in headers
    )

    body = []
    for r in preview:
        tds = "".join(
            f"<td style='padding:6px;border-bottom:1px solid #eee;vertical-align:top;'>{esc(str(v))}</td>"
            for v in r
        )
        body.append(f"<tr>{tds}</tr>")

    return (
        "<div style='overflow:auto;border:1px solid #ddd;border-radius:8px;'>"
        "<table style='border-collapse:collapse;width:100%;font-size:13px;'>"
        f"<thead><tr>{ths}</tr></thead>"
        f"<tbody>{''.join(body) if body else '<tr><td style=\"padding:8px;\">No submissions yet.</td></tr>'}</tbody>"
        "</table></div>"
        f"<p style='margin-top:8px;color:#666;font-size:12px;'>Showing up to {max_rows} rows.</p>"
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

    fd.is_public = not bool(fd.is_public)
    fd.save(update_fields=["is_public"])

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
    masters = list(FormDefinition.objects.filter(slug__in=MASTER_SLUGS).order_by("slug"))

    groups = list(FormGroup.objects.order_by("number"))
    group_list = []
    for g in groups:
        forms_for_group = list(FormDefinition.objects.filter(group=g).order_by("slug"))
        group_list.append((g, forms_for_group))

    return render(
        request,
        "admin_dash/apps_list.html",
        {
            "masters": masters,
            "create_group_form": CreateGroupForm(),
            "group_list": group_list,
        },
    )


@staff_member_required
@require_POST
def create_group(request):
    form = CreateGroupForm(request.POST)
    if not form.is_valid():
        masters = list(FormDefinition.objects.filter(slug__in=MASTER_SLUGS).order_by("slug"))
        groups = list(FormGroup.objects.order_by("number"))
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
            },
        )

    group_num = form.cleaned_data["group_num"]
    start_month = form.cleaned_data["start_month"]
    end_month = form.cleaned_data["end_month"]
    year = form.cleaned_data["year"]

    with transaction.atomic():
        group, _created = FormGroup.objects.get_or_create(
            number=group_num,
            defaults={"start_month": start_month, "end_month": end_month, "year": year},
        )
        group.start_month = start_month
        group.end_month = end_month
        group.year = year
        group.save()

        masters = FormDefinition.objects.filter(slug__in=MASTER_SLUGS).order_by("slug")
        for master_fd in masters:
            _clone_form(master_fd, group)

    messages.success(request, f"Grupo {group_num} creado/actualizado y formularios clonados.")
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


# ----------------------------
# Database
# ----------------------------
@staff_member_required
def database_home(request):
    masters = list(FormDefinition.objects.filter(slug__in=MASTER_SLUGS).order_by("slug"))

    groups = list(FormGroup.objects.order_by("number"))
    group_blocks = []
    for g in groups:
        forms_for_group = list(FormDefinition.objects.filter(group=g).order_by("slug"))
        group_blocks.append((g, forms_for_group))

    SURVEY_SLUGS = ["PRIMER_E", "FINAL_E", "PRIMER_M", "FINAL_M"]
    surveys = list(FormDefinition.objects.filter(slug__in=SURVEY_SLUGS).order_by("slug"))
    surveys_e = [s for s in surveys if s.slug.endswith("_E")]
    surveys_m = [s for s in surveys if s.slug.endswith("_M")]

    counts = {
        row["form__slug"]: row["c"]
        for row in Application.objects.values("form__slug").annotate(c=Count("id"))
    }

    for fd in masters:
        fd.submission_count = counts.get(fd.slug, 0)
        fd.admin_edit_url = reverse("admin:applications_formdefinition_change", args=[fd.id])

    for _g, forms_for_group in group_blocks:
        for fd in forms_for_group:
            fd.submission_count = counts.get(fd.slug, 0)
            fd.admin_edit_url = reverse("admin:applications_formdefinition_change", args=[fd.id])

    for s in surveys:
        s.submission_count = counts.get(s.slug, 0)
        s.admin_edit_url = reverse("admin:applications_formdefinition_change", args=[s.id])

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
        },
    )


@staff_member_required
def database_form_detail(request, form_slug: str):
    form_def = get_object_or_404(FormDefinition, slug=form_slug)

    apps = (
        Application.objects.filter(form=form_def)
        .prefetch_related("answers__question", "form")
        .order_by("-created_at", "-id")
    )

    headers, rows = _build_csv_for_form(form_def)
    preview_html = _csv_preview_html(headers, rows, max_rows=25)

    return render(
        request,
        "admin_dash/database_form_detail.html",
        {
            "form_def": form_def,
            "apps": apps,
            "preview_html": preview_html,
        },
    )


@staff_member_required
def database_form_master_csv(request, form_slug: str):
    form_def = get_object_or_404(FormDefinition, slug=form_slug)
    headers, rows = _build_csv_for_form(form_def)
    return _csv_http_response(f"{form_slug}_MASTER.csv", headers, rows)


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
@staff_member_required
@require_POST
def delete_submission(request, app_id: int):
    app = get_object_or_404(Application.objects.select_related("form"), id=app_id)
    form_slug = app.form.slug

    answers = Answer.objects.filter(application=app)
    deleted_storage = 0

    for ans in answers:
        v = (ans.value or "").strip()
        if not v:
            continue
        if not _looks_like_file_value(v):
            continue

        storage_path = _extract_storage_path_from_value(v)
        if storage_path:
            try:
                if default_storage.exists(storage_path):
                    default_storage.delete(storage_path)
                    deleted_storage += 1
            except Exception:
                pass

    app.delete()

    msg = "Submission eliminada de la base de datos."
    if deleted_storage:
        msg += f" Archivos eliminados del storage: {deleted_storage}."
    messages.success(request, msg)

    return redirect("admin_database_form_detail", form_slug=form_slug)


# ----------------------------
# Delete file(s) actions (admin database)
# ----------------------------
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
        rows.append({
            "slug": fd.slug,
            "name": fd.name,
            "total": totals.get(fd.slug, 0),
            "pending": pending.get(fd.slug, 0),
        })

    # ✅ ADD THIS
    graded_files = GradedFile.objects.order_by("-created_at")[:50]

    return render(
        request,
        "admin_dash/grading_home.html",
        {
            "group": group,
            "forms": rows,
            # ✅ ADD THIS
            "graded_files": graded_files,
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

    """
    Sends reminder emails to A1-approved users who have NOT completed A2 yet.
    Works for:
      - G6_E_A2 / G6_M_A2
      - E_A2 / M_A2

    Rules:
    - Only sends to users who were invited_to_second_stage=True
    - Only sends 1 email per unique email address
    - Skips anyone who already submitted this A2 form
    - No max cap
    - Uses one SMTP connection to avoid timeouts
    """

    # Validate form_slug is A2
    if not (form_slug.endswith("E_A2") or form_slug.endswith("M_A2")):
        messages.error(request, "Este botón solo funciona para formularios A2 (E_A2 o M_A2).")
        return redirect("admin_apps_list")

    # Ensure A2 form exists
    _a2_form = get_object_or_404(FormDefinition, slug=form_slug)

    is_emprendedora = form_slug.endswith("E_A2")
    track_word = "emprendedora" if is_emprendedora else "mentora"

    # -------- derive matching A1 slug --------
    m = GROUP_SLUG_RE.match(form_slug)
    if m:
        gnum = m.group("num")
        a1_slug = f"G{gnum}_{'E_A1' if is_emprendedora else 'M_A1'}"
    else:
        a1_slug = "E_A1" if is_emprendedora else "M_A1"

    # ✅ Find approved A1 submissions only (they MUST have been invited)
    a1_apps_qs = (
        Application.objects.filter(
            form__slug=a1_slug,
            invited_to_second_stage=True,
        )
        .exclude(email__isnull=True)
        .exclude(email__exact="")
        .only("id", "email", "created_at")
        .order_by("-created_at", "-id")
    )

    # ✅ Find who already completed THIS A2
    completed_emails = set(
        Application.objects.filter(form__slug=form_slug)
        .exclude(email__isnull=True)
        .exclude(email__exact="")
        .values_list("email", flat=True)
    )

    # ✅ Build unique target list
    targets = []
    seen = set()

    for app in a1_apps_qs:
        email = (app.email or "").strip().lower()
        if not email:
            continue
        if email in seen:
            continue
        if email in completed_emails:
            continue
        seen.add(email)
        targets.append(email)

    if not targets:
        messages.info(request, f"No hay personas pendientes para {form_slug}.")
        return redirect("admin_apps_list")

    # ✅ Link for this group’s A2 form (public slug link)
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
        <strong>18 de enero de 2026</strong>. Estamos en los últimos días para aplicar.
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

    sent = 0
    failed = 0

    # ✅ One shared SMTP connection (much faster + safer on Render)
    connection = get_connection(fail_silently=False)
    try:
        connection.open()

        for email in targets:
            try:
                msg = EmailMultiAlternatives(
                    subject=subject,
                    body="",
                    from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                    to=[email],
                    connection=connection,
                )
                msg.attach_alternative(html_body, "text/html")
                msg.send()
                sent += 1
            except Exception:
                failed += 1

    finally:
        try:
            connection.close()
        except Exception:
            pass

    if failed > 0:
        messages.warning(
            request,
            f"Reminders enviados para {form_slug}: {sent} enviados, {failed} fallidos."
        )
    else:
        messages.success(
            request,
            f"Reminders enviados para {form_slug}: {sent} enviados."
        )

    return redirect("admin_apps_list")
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

        # write CSV in-memory
        buf = io.StringIO()
        graded_df.to_csv(buf, index=False)
        csv_bytes = buf.getvalue().encode("utf-8")

        filename = f"{app.form.slug}_app_{app.id}_graded.csv"

        gf = GradedFile.objects.create(
            form_slug=app.form.slug,
            application=app,
        )
        gf.file.save(filename, ContentFile(csv_bytes), save=True)

        messages.success(request, f"✅ Graded app #{app.id}. File saved: {filename}")

    except Exception as e:
        messages.error(request, f"Grading failed: {e}")

    return redirect("admin_grading_home")
