import pandas as pd
from openai import OpenAI
import logging

MODEL = "gpt-5.2"
TIMEOUT = 60
logger = logging.getLogger(__name__)
PRIORITY_STATUS = "Priority"
ACTIVE_PARTICIPANT_STATUS = "participante activa"
DUAL_APPLICANT_STATUS = "aplicante a emprendediora"
MAX_TOTAL_SCORE = 81

W = {
    "owned_business": 3,
    "business_years": 4,
    "has_employees": 2,
    "professional_expertise_struct": 2,
    "mentoring_exp_as_mentor": 3,
    "mentoring_exp_as_student": 2,
    "business_description": 3,
    "mentoring_exp_detail": 2,
    "motivation": 4,
    "professional_expertise": 4,
}

# -----------------------
# Helpers
# -----------------------

def yes(v):
    return str(v).strip().lower() == "yes"

def business_years_pts(v, owned):
    mapping = {"0_1": 1, "1_5": 2, "5_10": 3, "10_plus": 4}
    if pd.isna(v) or v == "":
        return -1 if yes(owned) else 0
    return mapping.get(v, 0)


def has_prior_participation(v) -> bool:
    text = str(v or "").strip().lower()
    selected_tokens = (
        "as_entrepreneur",
        "as_mentor",
        "as_mentora",
        "yes_entrepreneur",
        "yes_mentor",
    )
    return any(tok in text for tok in selected_tokens)


def _row_application_id(row: dict) -> int | None:
    raw = row.get("application_id")
    if raw is None:
        return None
    text = str(raw).strip()
    if text.isdigit():
        try:
            return int(text)
        except Exception:
            return None
    return None


def status_from_participation(v, *, disqualified: bool, has_previous_application: bool) -> str:
    if disqualified:
        return "N/A"

    if has_prior_participation(v):
        return "Seleccionada"

    if has_previous_application:
        return "Aplicante anterios"

    return ""


def red_flag_color(red_flag_text: str, prior_participation_value) -> str:
    if str(red_flag_text or "").strip():
        return "red"
    if has_prior_participation(prior_participation_value):
        return "green"
    return ""


def _normalized_document_identifier(value) -> str:
    text = str(value or "").strip().lower()
    return "".join(ch for ch in text if ch.isalnum())


def _normalized_identifier(row: dict | pd.Series, keys: list[str]) -> tuple[str, str] | None:
    for key in keys:
        if key == "cedula":
            raw_value = row.get("cedula", "") or row.get("id_number", "")
            value = _normalized_document_identifier(raw_value)
        elif key == "id_number":
            raw_value = row.get("id_number", "") or row.get("cedula", "")
            value = _normalized_document_identifier(raw_value)
        else:
            value = str(row.get(key, "") or "").strip().lower()
        if value:
            return key, value
    return None


def _score_rank(value) -> float:
    if value in (None, "", "NA"):
        return float("-inf")
    if isinstance(value, str):
        raw = value.strip()
        if not raw or raw.upper() == "NA":
            return float("-inf")
        if raw.endswith("%"):
            value = raw[:-1].strip()
        else:
            value = raw
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("-inf")


def _format_total_percentage(total_score: float) -> str:
    pct = (float(total_score) / MAX_TOTAL_SCORE) * 100 if MAX_TOTAL_SCORE else 0.0
    return f"{pct:.2f}%"


def _dedupe_scored_rows(df: pd.DataFrame, score_col: str, id_keys: list[str]) -> tuple[pd.DataFrame, int]:
    if df.empty:
        return df, 0

    winners: dict[tuple[str, str] | tuple[str, int], tuple[float, int]] = {}
    for idx, row in df.iterrows():
        identifier = _normalized_identifier(row, id_keys)
        if identifier is None:
            identifier = ("row", idx)
        candidate_rank = (_score_rank(row.get(score_col)), idx)
        current_rank = winners.get(identifier)
        if current_rank is None or candidate_rank > current_rank:
            winners[identifier] = candidate_rank

    keep_indexes = sorted(rank[1] for rank in winners.values())
    removed = len(df) - len(keep_indexes)
    return df.loc[keep_indexes].reset_index(drop=True), removed


REQ_FIELDS = [
    "req_basic_woman",
    "req_basic_latam",
    "req_basic_business_exp",
    "req_basic_punctual",
    "req_basic_internet_device",
    "req_basic_training",
    "req_basic_surveys",
    "req_avail_period",
    "req_avail_2hrs_week",
    "req_avail_kickoff",
]


def _disqualification_reasons(row: dict) -> list[str]:
    return [field for field in REQ_FIELDS if str(row.get(field, "")).lower() != "yes"]


def _is_priority_email(row: dict, priority_emails: set[str] | None) -> bool:
    if not priority_emails:
        return False
    email = str(row.get("email", "") or "").strip().lower()
    return bool(email and email in priority_emails)


def _is_active_participant_email(row: dict, active_participant_emails: set[str] | None) -> bool:
    if not active_participant_emails:
        return False
    email = str(row.get("email", "") or "").strip().lower()
    return bool(email and email in active_participant_emails)


def _normalize_document_id(raw_value: str) -> str:
    value = str(raw_value or "").strip().lower()
    if not value:
        return ""
    return "".join(ch for ch in value if ch.isalnum())


def _is_dual_applicant(
    row: dict,
    dual_applicant_emails: set[str] | None,
    dual_applicant_doc_ids: set[str] | None,
) -> bool:
    email = str(row.get("email", "") or "").strip().lower()
    if email and dual_applicant_emails and email in dual_applicant_emails:
        return True

    doc_raw = row.get("id_number") or row.get("cedula") or ""
    doc_key = _normalize_document_id(doc_raw)
    if doc_key and dual_applicant_doc_ids and doc_key in dual_applicant_doc_ids:
        return True
    return False


def _categories_to_dict(categories) -> dict:
    if categories is None:
        return {}
    if isinstance(categories, dict):
        return {str(k): bool(v) for k, v in categories.items()}
    if hasattr(categories, "model_dump"):
        dumped = categories.model_dump()
        if isinstance(dumped, dict):
            return {str(k): bool(v) for k, v in dumped.items()}

    out = {}
    for attr in dir(categories):
        if attr.startswith("_"):
            continue
        try:
            val = getattr(categories, attr)
        except Exception:
            continue
        if isinstance(val, bool):
            out[attr] = val
    return out


def detect_red_flags(client: OpenAI, *texts):
    combined = "\n".join(
        t.strip() for t in texts if isinstance(t, str) and t.strip()
    )
    if not combined:
        return ""

    try:
        moderation = client.moderations.create(
            model="omni-moderation-latest",
            input=combined[:15000],
        )
        result = moderation.results[0] if moderation.results else None
        data = _categories_to_dict(getattr(result, "categories", None))
    except Exception:
        logger.exception("OpenAI moderation failed in grader_m red flag detection.")
        return ""

    sexual = bool(data.get("sexual") or data.get("sexual_minors") or data.get("sexual/minors"))
    illicit = bool(data.get("illicit") or data.get("illicit_violent") or data.get("illicit/violent"))

    flags = []
    if sexual:
        flags.append("contenido sexual")
    if illicit:
        flags.append("contenido ilicito")
    return ", ".join(flags)

# -----------------------
# OpenAI grading
# -----------------------

def grade_unstructured(client: OpenAI, text, criterion, negative_allowed=False):
    if not isinstance(text, str) or not text.strip():
        score = -1 if negative_allowed else 0
        return score, "Blank or insufficient response."

    prompt = f"""
Criterion: {criterion}

Rules:
- Score 1–5 based on clarity, relevance, and depth
- Bad or nonsensical → 0
- Negative allowed only if justified

Response:
\"\"\"{text}\"\"\"

Output EXACTLY:
Score: <int>
Explanation: <2–3 sentences justifying the score>
"""

    r = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        timeout=TIMEOUT,
    )

    content = r.choices[0].message.content.strip()
    score = 0
    explanation = "No explanation returned."

    for line in content.splitlines():
        if line.startswith("Score:"):
            score = int(line.split(":")[1].strip())
        elif line.startswith("Explanation:"):
            explanation = line.replace("Explanation:", "").strip()

    return score, explanation

# -----------------------
# Columns (EXACT)
# -----------------------

COLUMNS = [
    "total_pts",
    "status",
    "certificate_name",
    "preferred_name",
    "id_number",
    "email",
    "whatsapp",
    "country_residence",
    "age_range",
    "flag_color",
    "meets_all_req",
    "req_explain",
    "owned_business",
    "owned_business_pt",
    "prior_participation",
    "prior_participation_pt",
    "business_years",
    "business_years_pt",
    "has_employees",
    "has_employees_pt",
    "professional_expertise",
    "professional_expertise_pt",
    "mentoring_exp_as_mentor",
    "mentoring_exp_as_mentor_pt",
    "mentoring_exp_as_student",
    "mentoring_exp_as_student_pt",
    "business_description",
    "business_description_pt",
    "mentoring_exp_detail",
    "mentoring_exp_detail_pt",
    "motivation",
    "motivation_pt",
    "additional_comments",
    "professional_expertise",
    "professional_expertise_pt",
    "score_exp",
    "grading_rubric",
]

# -----------------------
# Grade ONE row
# -----------------------

def grade_single_row(
    row: dict,
    client: OpenAI,
    priority_emails: set[str] | None = None,
    active_participant_emails: set[str] | None = None,
    previous_application_ids: set[int] | None = None,
    dual_applicant_emails: set[str] | None = None,
    dual_applicant_doc_ids: set[str] | None = None,
) -> dict:
    disqual_reasons = _disqualification_reasons(row)
    meets_all = not disqual_reasons
    application_id = _row_application_id(row)
    has_previous_application = bool(
        application_id is not None
        and previous_application_ids
        and application_id in previous_application_ids
    )
    status = status_from_participation(
        row.get("prior_participation"),
        disqualified=not meets_all,
        has_previous_application=has_previous_application,
    )
    if _is_dual_applicant(row, dual_applicant_emails, dual_applicant_doc_ids):
        status = DUAL_APPLICANT_STATUS
    elif _is_active_participant_email(row, active_participant_emails):
        status = ACTIVE_PARTICIPANT_STATUS
    elif _is_priority_email(row, priority_emails):
        status = PRIORITY_STATUS

    owned_pt = yes(row.get("owned_business")) * W["owned_business"]
    years_pt = business_years_pts(row.get("business_years"), row.get("owned_business"))
    emp_pt = yes(row.get("has_employees")) * W["has_employees"]
    mentor_pt = yes(row.get("mentoring_exp_as_mentor")) * W["mentoring_exp_as_mentor"]
    student_pt = yes(row.get("mentoring_exp_as_student")) * W["mentoring_exp_as_student"]

    prof_struct_pt = (
        1 if isinstance(row.get("professional_expertise"), str)
        and row.get("professional_expertise").strip()
        else 0
    ) * W["professional_expertise_struct"]

    red_flags = detect_red_flags(
        client,
        row.get("business_description"),
        row.get("mentoring_exp_detail"),
        row.get("motivation"),
        row.get("professional_expertise"),
    )
    flag_color = red_flag_color(red_flags, row.get("prior_participation"))

    if not meets_all:
        return {
            "total_pts": "NA",
            "status": status,
            "certificate_name": row.get("certificate_name"),
            "preferred_name": row.get("preferred_name"),
            "id_number": row.get("id_number"),
            "email": row.get("email"),
            "whatsapp": row.get("whatsapp"),
            "country_residence": row.get("country_residence"),
            "age_range": row.get("age_range"),
            "flag_color": flag_color,
            "meets_all_req": "no",
            "req_explain": row.get("req_explain", ""),
            "owned_business": row.get("owned_business"),
            "owned_business_pt": "",
            "prior_participation": row.get("prior_participation"),
            "prior_participation_pt": "",
            "business_years": row.get("business_years"),
            "business_years_pt": "",
            "has_employees": row.get("has_employees"),
            "has_employees_pt": "",
            "professional_expertise": row.get("professional_expertise"),
            "professional_expertise_pt": "",
            "mentoring_exp_as_mentor": row.get("mentoring_exp_as_mentor"),
            "mentoring_exp_as_mentor_pt": "",
            "mentoring_exp_as_student": row.get("mentoring_exp_as_student"),
            "mentoring_exp_as_student_pt": "",
            "business_description": row.get("business_description"),
            "business_description_pt": "",
            "mentoring_exp_detail": row.get("mentoring_exp_detail"),
            "mentoring_exp_detail_pt": "",
            "motivation": row.get("motivation"),
            "motivation_pt": "",
            "additional_comments": row.get("additional_comments"),
            "score_exp": "Disqualified: " + ", ".join(disqual_reasons),
            "grading_rubric": "Disqualified before scoring. Total score set to NA.",
        }

    score_exp = []

    bd_raw, bd_exp = grade_unstructured(client, row.get("business_description"), "business_description")
    med_raw, med_exp = grade_unstructured(client, row.get("mentoring_exp_detail"), "mentoring_exp_detail")
    mot_raw, mot_exp = grade_unstructured(client, row.get("motivation"), "motivation", negative_allowed=True)
    prof_raw, prof_exp = grade_unstructured(client, row.get("professional_expertise"), "professional_expertise")

    score_exp.extend([
        f"business_description - {bd_exp}",
        f"mentoring_exp_detail - {med_exp}",
        f"motivation - {mot_exp}",
        f"professional_expertise - {prof_exp}",
    ])

    total_pts = sum([
        owned_pt, years_pt, emp_pt,
        prof_struct_pt, mentor_pt, student_pt,
        bd_raw * W["business_description"],
        med_raw * W["mentoring_exp_detail"],
        mot_raw * W["motivation"],
        prof_raw * W["professional_expertise"],
    ])
    total_pts_pct = _format_total_percentage(total_pts)
    score_exp.append(f"total_score - {total_pts}/{MAX_TOTAL_SCORE} ({total_pts_pct})")

    return {
        "total_pts": total_pts_pct,
        "status": status,
        "certificate_name": row.get("certificate_name"),
        "preferred_name": row.get("preferred_name"),
        "id_number": row.get("id_number"),
        "email": row.get("email"),
        "whatsapp": row.get("whatsapp"),
        "country_residence": row.get("country_residence"),
        "age_range": row.get("age_range"),
        "flag_color": flag_color,
        "meets_all_req": "yes",
        "req_explain": row.get("req_explain", ""),
        "owned_business": row.get("owned_business"),
        "owned_business_pt": owned_pt,
        "prior_participation": row.get("prior_participation"),
        "prior_participation_pt": "",
        "business_years": row.get("business_years"),
        "business_years_pt": years_pt,
        "has_employees": row.get("has_employees"),
        "has_employees_pt": emp_pt,
        "professional_expertise": row.get("professional_expertise"),
        "professional_expertise_pt": prof_struct_pt,
        "mentoring_exp_as_mentor": row.get("mentoring_exp_as_mentor"),
        "mentoring_exp_as_mentor_pt": mentor_pt,
        "mentoring_exp_as_student": row.get("mentoring_exp_as_student"),
        "mentoring_exp_as_student_pt": student_pt,
        "business_description": row.get("business_description"),
        "business_description_pt": bd_raw * W["business_description"],
        "mentoring_exp_detail": row.get("mentoring_exp_detail"),
        "mentoring_exp_detail_pt": med_raw * W["mentoring_exp_detail"],
        "motivation": row.get("motivation"),
        "motivation_pt": mot_raw * W["motivation"],
        "additional_comments": row.get("additional_comments"),
        "professional_expertise": row.get("professional_expertise"),
        "professional_expertise_pt": prof_raw * W["professional_expertise"],
        "score_exp": "\n".join(score_exp),
        "grading_rubric": "Weighted rubric applied; unstructured responses scored 1–5. Total score shown as percentage.",
    }

# -----------------------
# Grade FULL dataframe
# -----------------------

def grade_from_dataframe(
    df: pd.DataFrame,
    client: OpenAI,
    log_fn=None,
    priority_emails: set[str] | list[str] | tuple[str, ...] | None = None,
    active_participant_emails: set[str] | list[str] | tuple[str, ...] | None = None,
    previous_application_ids: set[int] | list[int] | tuple[int, ...] | None = None,
    dual_applicant_emails: set[str] | list[str] | tuple[str, ...] | None = None,
    dual_applicant_doc_ids: set[str] | list[str] | tuple[str, ...] | None = None,
) -> pd.DataFrame:
    out = []
    total = len(df)
    normalized_priority_emails = {str(e).strip().lower() for e in (priority_emails or []) if str(e).strip()}
    normalized_active_participant_emails = {
        str(e).strip().lower()
        for e in (active_participant_emails or [])
        if str(e).strip()
    }
    normalized_previous_application_ids = {
        int(v)
        for v in (previous_application_ids or [])
        if str(v).strip().isdigit()
    }
    normalized_dual_applicant_emails = {
        str(e).strip().lower()
        for e in (dual_applicant_emails or [])
        if str(e).strip()
    }
    normalized_dual_applicant_doc_ids = {
        _normalize_document_id(str(v))
        for v in (dual_applicant_doc_ids or [])
        if _normalize_document_id(str(v))
    }

    for i, (_, row) in enumerate(df.iterrows(), start=1):
        if log_fn:
            log_fn(f"→ Grading mentor row {i}/{total}")

        out.append(
            grade_single_row(
                row.to_dict(),
                client,
                priority_emails=normalized_priority_emails,
                active_participant_emails=normalized_active_participant_emails,
                previous_application_ids=normalized_previous_application_ids,
                dual_applicant_emails=normalized_dual_applicant_emails,
                dual_applicant_doc_ids=normalized_dual_applicant_doc_ids,
            )
        )

    out_df = pd.DataFrame(out, columns=COLUMNS)
    out_df, removed = _dedupe_scored_rows(out_df, "total_pts", ["email", "cedula", "id_number"])
    if removed and log_fn:
        log_fn(f"→ Removed {removed} duplicate mentora rows, keeping the highest score per person")
    return out_df
