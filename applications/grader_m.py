import pandas as pd
from openai import OpenAI
import logging

MODEL = "gpt-5.2"
TIMEOUT = 60
MODERATION_MODEL = "omni-moderation-latest"
MODERATION_INPUT_LIMIT = 15000
MODERATION_FIELDS = (
    "business_description",
    "mentoring_exp_detail",
    "motivation",
    "professional_expertise",
)
DEFAULT_AI_FIELDS = (
    "business_description",
    "mentoring_exp_detail",
    "motivation",
    "professional_expertise",
)
DEFAULT_GRADING_INSTRUCTIONS = (
    "Score from 1 to 5 based on clarity, relevance, and depth.\n"
    "Give 0 to a bad or nonsensical answer.\n"
    "Use a negative score only when it is justified."
)
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


def _format_total_percentage(total_score: float, max_total_score: float = MAX_TOTAL_SCORE) -> str:
    pct = (float(total_score) / float(max_total_score)) * 100 if max_total_score else 0.0
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
    # Current A1 forms use two aggregate confirmations. Older/full mentor forms
    # use individual req_* confirmations. Missing legacy columns must not count
    # as explicit "no" answers on the current schema.
    aggregate_requirements = next(
        (field for field in ("meets_requirements", "meets_all_req") if field in row),
        None,
    )
    aggregate_availability = next(
        (field for field in ("available_period", "availability_ok") if field in row),
        None,
    )
    if aggregate_requirements or aggregate_availability:
        return [
            field
            for field in (aggregate_requirements, aggregate_availability)
            if field and not yes(row.get(field))
        ]

    present_fields = [field for field in REQ_FIELDS if field in row]
    if not present_fields:
        return []

    reasons = [field for field in present_fields if not yes(row.get(field))]
    if "req_avail_period" not in row:
        period_alias = next(
            (
                field
                for field in row
                if str(field).startswith("req_avail_")
                and field not in {"req_avail_2hrs_week", "req_avail_kickoff"}
            ),
            None,
        )
        if period_alias and not yes(row.get(period_alias)):
            reasons.append(period_alias)
    return reasons


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


def _pick_row_value(row: dict, *keys: str):
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return value
    return ""


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
            model=MODERATION_MODEL,
            input=combined[:MODERATION_INPUT_LIMIT],
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

def _render_prompt_template(template: str, *, criterion: str, response: str) -> str:
    return (
        (template or "")
        .replace("{{ criterion }}", criterion or "")
        .replace("{{ response }}", response or "")
    )


def build_grading_prompt(text: str, criterion: str, prompt_template: str = "") -> str:
    template = prompt_template or ""
    if "{{ response }}" in template or "{{ criterion }}" in template:
        return _render_prompt_template(template, criterion=criterion, response=text)
    instructions = template.strip() or DEFAULT_GRADING_INSTRUCTIONS
    return f"""
Criterion: {criterion}

Instructions:
{instructions}

Response:
\"\"\"{text}\"\"\"

Output EXACTLY:
Score: <int>
Explanation: <2–3 sentences justifying the score>
"""


def grade_unstructured(
    client: OpenAI,
    text,
    criterion,
    negative_allowed=False,
    prompt_template: str = "",
    model_name: str = "",
):
    if not isinstance(text, str) or not text.strip():
        score = -1 if negative_allowed else 0
        return score, "Blank or insufficient response."

    prompt = build_grading_prompt(text, criterion, prompt_template)

    r = client.chat.completions.create(
        model=(model_name or MODEL),
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
    "Status",
    "score",
    "score_exp",
    "full_name",
    "whatsapp",
    "email",
    "ID",
    "age_range",
    "country_residence",
    "country_birth",
    "flag_color",
    "meets_all_req",
    "Req_Interner",
    "weekly_time",
    "participated_before",
    "owned_business",
    "business_industry",
    "business_age",
    "business_description",
    "professional_expertise",
    "motivation",
    "why_good_mentor",
    "additional_comments",
    "availability_grid",
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
    grading_config=None,
) -> dict:
    weights = getattr(grading_config, "weights", None) or W
    max_total_score = float(getattr(grading_config, "max_total_score", MAX_TOTAL_SCORE) or MAX_TOTAL_SCORE)
    model_name = getattr(grading_config, "model_name", "") or ""
    prompt_for = getattr(grading_config, "prompt", lambda _key: "")
    allows_negative = getattr(grading_config, "allows_negative", lambda _key, fallback=False: fallback)
    rubric_note = getattr(grading_config, "rubric_note", "") or ""
    custom_criteria = bool(getattr(grading_config, "uses_configured_criteria", False))
    active_structured = set(getattr(grading_config, "structured_criteria", ()) or ())
    ai_fields = getattr(grading_config, "ai_criteria", None)
    if ai_fields is None:
        ai_fields = DEFAULT_AI_FIELDS

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

    response_score = getattr(grading_config, "response_score", lambda _slug, _value, default=None: default)
    owned_pt = response_score(
        "owned_business",
        row.get("owned_business"),
        yes(row.get("owned_business")) * float(weights.get("owned_business", W["owned_business"])),
    )
    if custom_criteria and "owned_business" not in active_structured:
        owned_pt = 0.0
    years_pt = response_score(
        "business_years",
        row.get("business_years"),
        (business_years_pts(row.get("business_years"), row.get("owned_business")) / 4) * float(weights.get("business_years", W["business_years"])),
    )
    if custom_criteria and "business_years" not in active_structured:
        years_pt = 0.0
    emp_pt = response_score(
        "has_employees",
        row.get("has_employees"),
        yes(row.get("has_employees")) * float(weights.get("has_employees", W["has_employees"])),
    )
    if custom_criteria and "has_employees" not in active_structured:
        emp_pt = 0.0
    mentor_pt = response_score(
        "mentoring_exp_as_mentor",
        row.get("mentoring_exp_as_mentor"),
        yes(row.get("mentoring_exp_as_mentor")) * float(weights.get("mentoring_exp_as_mentor", W["mentoring_exp_as_mentor"])),
    )
    if custom_criteria and "mentoring_exp_as_mentor" not in active_structured:
        mentor_pt = 0.0
    student_pt = response_score(
        "mentoring_exp_as_student",
        row.get("mentoring_exp_as_student"),
        yes(row.get("mentoring_exp_as_student")) * float(weights.get("mentoring_exp_as_student", W["mentoring_exp_as_student"])),
    )
    if custom_criteria and "mentoring_exp_as_student" not in active_structured:
        student_pt = 0.0
    extra_structured_total = sum(
        response_score(slug, row.get(slug), 0.0) or 0.0
        for slug in (getattr(grading_config, "structured_criteria", ()) or ())
        if slug not in {
            "owned_business",
            "business_years",
            "has_employees",
            "professional_expertise_struct",
            "mentoring_exp_as_mentor",
            "mentoring_exp_as_student",
        }
    )

    prof_struct_pt = (
        1 if isinstance(row.get("professional_expertise"), str)
        and row.get("professional_expertise").strip()
        else 0
    ) * float(weights.get("professional_expertise_struct", W["professional_expertise_struct"]))
    if custom_criteria and "professional_expertise_struct" not in active_structured:
        prof_struct_pt = 0.0

    red_flags = detect_red_flags(
        client,
        *(row.get(slug) for slug in ai_fields),
    )
    flag_color = red_flag_color(red_flags, row.get("prior_participation"))

    full_name = _pick_row_value(row, "certificate_name", "preferred_name", "full_name", "name")
    whatsapp = _pick_row_value(row, "whatsapp")
    email = _pick_row_value(row, "email")
    id_value = _pick_row_value(row, "id_number", "cedula")
    age_range = _pick_row_value(row, "age_range")
    country_residence = _pick_row_value(row, "country_residence")
    country_birth = _pick_row_value(row, "country_birth", "birth_country", "country_of_birth")
    req_interner = _pick_row_value(row, "req_basic_internet_device", "internet_access")
    weekly_time = _pick_row_value(row, "weekly_time", "req_avail_2hrs_week", "hours_per_week")
    participated_before = _pick_row_value(row, "prior_participation", "participated_before")
    owned_business = _pick_row_value(row, "owned_business")
    business_industry = _pick_row_value(row, "business_industry", "industry", "business_sector")
    business_age = _pick_row_value(row, "business_age", "business_years")
    business_description = _pick_row_value(row, "business_description")
    professional_expertise = _pick_row_value(row, "professional_expertise")
    motivation = _pick_row_value(row, "motivation")
    why_good_mentor = _pick_row_value(row, "why_good_mentor", "mentoring_exp_detail")
    additional_comments = _pick_row_value(row, "additional_comments")
    availability_grid = _pick_row_value(
        row,
        "availability_grid",
        "availability",
        "availability_options",
        "weekly_availability",
    )

    if not meets_all:
        return [
            status,
            "NA",
            "Disqualified: " + ", ".join(disqual_reasons),
            full_name,
            whatsapp,
            email,
            id_value,
            age_range,
            country_residence,
            country_birth,
            flag_color,
            "no",
            req_interner,
            weekly_time,
            participated_before,
            owned_business,
            business_industry,
            business_age,
            business_description,
            professional_expertise,
            motivation,
            why_good_mentor,
            additional_comments,
            availability_grid,
            "Disqualified before scoring. Total score set to NA.",
        ]

    score_exp = []
    ai_total = 0.0
    for slug in ai_fields:
        raw_score, explanation = grade_unstructured(
            client,
            row.get(slug),
            slug,
            negative_allowed=allows_negative(slug, fallback=(slug == "motivation")),
            prompt_template=prompt_for(slug),
            model_name=model_name,
        )
        ai_total += raw_score * float(weights.get(slug, 1))
        score_exp.append(f"{slug} - {explanation}")

    total_pts = sum([
        owned_pt, years_pt, emp_pt,
        prof_struct_pt, mentor_pt, student_pt,
        extra_structured_total,
        ai_total,
    ])
    total_pts_pct = _format_total_percentage(total_pts, max_total_score)
    score_exp.append(f"total_score - {total_pts}/{max_total_score} ({total_pts_pct})")

    return [
        status,
        total_pts_pct,
        "\n".join(score_exp),
        full_name,
        whatsapp,
        email,
        id_value,
        age_range,
        country_residence,
        country_birth,
        flag_color,
        "yes",
        req_interner,
        weekly_time,
        participated_before,
        owned_business,
        business_industry,
        business_age,
        business_description,
        professional_expertise,
        motivation,
        why_good_mentor,
        additional_comments,
        availability_grid,
        rubric_note or "Weighted rubric applied; unstructured responses scored 1–5. Total score shown as percentage.",
    ]

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
    grading_config=None,
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
                grading_config=grading_config,
            )
        )

    out_df = pd.DataFrame(out, columns=COLUMNS)
    # Keep every source answer in the generated grading sheet. Current forms can
    # use administrator-generated slugs that do not map to the legacy fixed columns.
    source_df = df.reset_index(drop=True)
    if bool(getattr(grading_config, "uses_configured_criteria", False)):
        # Configured/current forms must export the exact application dataset,
        # including questions that are not active grading criteria. Do not emit
        # obsolete legacy placeholders that appear blank despite data existing
        # under the form's real question slug.
        result_parts = [out_df[["Status", "score", "score_exp"]]]
        source_columns = [
            column for column in source_df.columns
            if column not in {"Status", "score", "score_exp", "grading_rubric"}
        ]
        result_parts.append(source_df[source_columns])
        result_parts.append(out_df[["flag_color", "meets_all_req", "grading_rubric"]])
        out_df = pd.concat(result_parts, axis=1)
    else:
        for column in source_df.columns:
            if column not in out_df.columns:
                out_df[column] = source_df[column]
    out_df, removed = _dedupe_scored_rows(out_df, "score", ["email", "cedula", "id_number"])
    if removed and log_fn:
        log_fn(f"→ Removed {removed} duplicate mentora rows, keeping the highest score per person")
    return out_df
