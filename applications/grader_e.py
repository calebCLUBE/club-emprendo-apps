import pandas as pd
from openai import OpenAI
import logging

# ==================================================
# Config
# ==================================================

MODEL = "gpt-5.2"
TIMEOUT = 60
MODERATION_MODEL = "omni-moderation-latest"
MODERATION_INPUT_LIMIT = 15000
MODERATION_FIELDS = (
    "business_description",
    "growth_how",
    "biggest_challenge",
)
DEFAULT_AI_FIELDS = ("business_description", "growth_how", "biggest_challenge")
DEFAULT_GRADING_INSTRUCTIONS = (
    "Score from 1 to 5 based on clarity, relevance, and insight.\n"
    "Give 0 to a bad, nonsensical, or irrelevant answer.\n"
    "Be strict but fair."
)
logger = logging.getLogger(__name__)
PRIORITY_STATUS = "Priority"
ACTIVE_PARTICIPANT_STATUS = "participante activa"
MAX_TOTAL_SCORE = 64

W = {
    "prior_mentoring": 2,
    "business_age": 3,
    "has_employees": 2,
    "business_description": 3,
    "growth_how": 4,
    "biggest_challenge": 4,
}

AVAILABILITY_LABELS = {
    # English-style keys
    "mon_morning": "Lunes - Manana",
    "mon_afternoon": "Lunes - Tarde",
    "mon_night": "Lunes - Noche",
    "tue_morning": "Martes - Manana",
    "tue_afternoon": "Martes - Tarde",
    "tue_night": "Martes - Noche",
    "wed_morning": "Miercoles - Manana",
    "wed_afternoon": "Miercoles - Tarde",
    "wed_night": "Miercoles - Noche",
    "thu_morning": "Jueves - Manana",
    "thu_afternoon": "Jueves - Tarde",
    "thu_night": "Jueves - Noche",
    "fri_morning": "Viernes - Manana",
    "fri_afternoon": "Viernes - Tarde",
    "fri_night": "Viernes - Noche",
    "sat_morning": "Sabado - Manana",
    "sat_afternoon": "Sabado - Tarde",
    "sat_night": "Sabado - Noche",
    "sun_morning": "Domingo - Manana",
    "sun_afternoon": "Domingo - Tarde",
    "sun_night": "Domingo - Noche",
    # Spanish-style keys
    "lunes_manana": "Lunes - Manana",
    "lunes_tarde": "Lunes - Tarde",
    "lunes_noche": "Lunes - Noche",
    "martes_manana": "Martes - Manana",
    "martes_tarde": "Martes - Tarde",
    "martes_noche": "Martes - Noche",
    "miercoles_manana": "Miercoles - Manana",
    "miercoles_tarde": "Miercoles - Tarde",
    "miercoles_noche": "Miercoles - Noche",
    "jueves_manana": "Jueves - Manana",
    "jueves_tarde": "Jueves - Tarde",
    "jueves_noche": "Jueves - Noche",
    "viernes_manana": "Viernes - Manana",
    "viernes_tarde": "Viernes - Tarde",
    "viernes_noche": "Viernes - Noche",
    "sabado_manana": "Sabado - Manana",
    "sabado_tarde": "Sabado - Tarde",
    "sabado_noche": "Sabado - Noche",
    "domingo_manana": "Domingo - Manana",
    "domingo_tarde": "Domingo - Tarde",
    "domingo_noche": "Domingo - Noche",
}

# ==================================================
# Helpers (structured)
# ==================================================

def yes(v):
    return str(v).strip().lower() == "yes"


def affirmative(v) -> bool:
    text = str(v or "").strip().lower()
    if not text:
        return False
    return text in {
        "yes",
        "yes_ok",
        "si",
        "sí",
        "true",
        "1",
        "y",
        "s",
        "ok",
    }


def prior_mentoring_pts(v):
    return 2 if yes(v) else 0


def business_age_pts(v):
    mapping = {
        "idea": 0,
        "lt_1": 1,
        "1_3y": 2,
        "4_6y": 3,
        "7_10y": 4,
        "gt_10y": 5,
    }
    return mapping.get(str(v), 0)


def has_employees_pts(v):
    return 2 if yes(v) else 0


def has_prior_participation(v) -> bool:
    text = str(v or "").strip().lower()
    selected_tokens = (
        "yes_entrepreneur",
        "yes_mentor",
        "as_entrepreneur",
        "as_mentor",
        "as_mentora",
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


def red_flag_color(red_flag_text: str, participated_before_value) -> str:
    if str(red_flag_text or "").strip():
        return "red"
    if has_prior_participation(participated_before_value):
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


def _disqualification_reasons(row: dict) -> list[str]:
    reasons = []

    requirements_field = next(
        (field for field in ("meets_requirements", "meets_all_req") if field in row),
        None,
    )
    availability_field = next(
        (field for field in ("available_period", "availability_ok") if field in row),
        None,
    )
    if requirements_field and not affirmative(row.get(requirements_field)):
        reasons.append(requirements_field)
    if availability_field and not affirmative(row.get(availability_field)):
        reasons.append(availability_field)

    # Legacy/full-form aliases. Missing legacy columns must not count as failed
    # requirements on current A1 forms, because eligibility is now already
    # captured by aggregate approval-page fields above.
    if not requirements_field and "internet_access" in row and not affirmative(row.get("internet_access")):
        reasons.append("internet_access")
    if not availability_field and "commit_3_months" in row and not affirmative(row.get("commit_3_months")):
        reasons.append("commit_3_months")

    if str(row.get("business_age", "")).strip().lower() == "idea":
        reasons.append("business_age=idea")
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


def _format_availability_grid(raw_value) -> str:
    text = str(raw_value or "").strip()
    if not text:
        return ""

    cleaned = (
        text.replace("[", "")
        .replace("]", "")
        .replace('"', "")
        .replace("'", "")
        .replace("\n", ",")
        .replace(";", ",")
    )
    parts = [p.strip() for p in cleaned.split(",") if p.strip()]
    if not parts:
        return text

    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        key = part.strip().lower()
        label = AVAILABILITY_LABELS.get(key, part.strip())
        if label in seen:
            continue
        seen.add(label)
        out.append(label)
    return "; ".join(out)


# ==================================================
# Red flag detection
# ==================================================

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
        logger.exception("OpenAI moderation failed in grader_e red flag detection.")
        return ""

    sexual = bool(data.get("sexual") or data.get("sexual_minors") or data.get("sexual/minors"))
    illicit = bool(data.get("illicit") or data.get("illicit_violent") or data.get("illicit/violent"))

    flags = []
    if sexual:
        flags.append("contenido sexual")
    if illicit:
        flags.append("contenido ilicito")
    return ", ".join(flags)


# ==================================================
# OpenAI grading (unstructured)
# ==================================================

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


def grade_unstructured(client: OpenAI, text, criterion, prompt_template: str = "", model_name: str = ""):
    if not isinstance(text, str) or not text.strip():
        return 0, "Blank or insufficient response."

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


# ==================================================
# Output layout
# ==================================================

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
    "business_age",
    "Req_Interner",
    "weekly_time",
    "participated_before",
    "business_industry",
    "business_description",
    "growth_how",
    "biggest_challenge",
    "additional_comments",
    "availability_grid",
    "grading_rubric",
]

# ==================================================
# Grade ONE row (NO category row here)
# ==================================================

def grade_single_row(
    row: dict,
    client: OpenAI,
    priority_emails: set[str] | None = None,
    active_participant_emails: set[str] | None = None,
    previous_application_ids: set[int] | None = None,
    grading_config=None,
) -> list:
    weights = getattr(grading_config, "weights", None) or W
    max_total_score = float(getattr(grading_config, "max_total_score", MAX_TOTAL_SCORE) or MAX_TOTAL_SCORE)
    model_name = getattr(grading_config, "model_name", "") or ""
    prompt_for = getattr(grading_config, "prompt", lambda _key: "")
    rubric_note = getattr(grading_config, "rubric_note", "") or ""
    custom_criteria = bool(getattr(grading_config, "uses_configured_criteria", False))
    active_structured = set(getattr(grading_config, "structured_criteria", ()) or ())

    disqual_reasons = _disqualification_reasons(row)
    application_id = _row_application_id(row)
    has_previous_application = bool(
        application_id is not None
        and previous_application_ids
        and application_id in previous_application_ids
    )
    status = status_from_participation(
        row.get("participated_before"),
        disqualified=bool(disqual_reasons),
        has_previous_application=has_previous_application,
    )
    if _is_active_participant_email(row, active_participant_emails):
        status = ACTIVE_PARTICIPANT_STATUS
    elif _is_priority_email(row, priority_emails):
        status = PRIORITY_STATUS

    response_score = getattr(grading_config, "response_score", lambda _slug, _value, default=None: default)
    prior_pt = response_score(
        "prior_mentoring",
        row.get("prior_mentoring"),
        (1 if yes(row.get("prior_mentoring")) else 0) * float(weights.get("prior_mentoring", 2)),
    )
    if custom_criteria and "prior_mentoring" not in active_structured:
        prior_pt = 0.0
    business_age_pt = response_score(
        "business_age",
        row.get("business_age"),
        (business_age_pts(row.get("business_age")) / 5) * float(weights.get("business_age", 5)),
    )
    if custom_criteria and "business_age" not in active_structured:
        business_age_pt = 0.0
    employees_pt = response_score(
        "has_employees",
        row.get("has_employees"),
        (1 if yes(row.get("has_employees")) else 0) * float(weights.get("has_employees", 2)),
    )
    if custom_criteria and "has_employees" not in active_structured:
        employees_pt = 0.0
    extra_structured_total = sum(
        response_score(slug, row.get(slug), 0.0) or 0.0
        for slug in (getattr(grading_config, "structured_criteria", ()) or ())
        if slug not in {"prior_mentoring", "business_age", "has_employees"}
    )

    ai_fields = getattr(grading_config, "ai_criteria", None)
    if ai_fields is None:
        ai_fields = DEFAULT_AI_FIELDS
    red_flags = detect_red_flags(
        client,
        *(row.get(slug) for slug in ai_fields),
    )
    flag_color = red_flag_color(red_flags, row.get("participated_before"))

    full_name = _pick_row_value(row, "full_name", "name")
    whatsapp = _pick_row_value(row, "whatsapp")
    email = _pick_row_value(row, "email")
    id_value = _pick_row_value(row, "cedula", "id_number")
    age_range = _pick_row_value(row, "age_range")
    country_residence = _pick_row_value(row, "country_residence")
    country_birth = _pick_row_value(row, "country_birth", "birth_country", "country_of_birth")
    business_age_value = _pick_row_value(row, "business_age")
    req_interner = _pick_row_value(row, "internet_access", "req_basic_internet_device", "meets_requirements")
    weekly_time = _pick_row_value(row, "hours_per_week", "weekly_time", "req_avail_2hrs_week", "available_period")
    participated_before = _pick_row_value(row, "participated_before", "prior_participation")
    business_industry = _pick_row_value(row, "business_industry", "industry", "business_sector")
    business_description = _pick_row_value(row, "business_description")
    growth_how = _pick_row_value(row, "growth_how")
    biggest_challenge = _pick_row_value(row, "biggest_challenge")
    additional_comments = _pick_row_value(row, "additional_comments")
    availability_raw = _pick_row_value(
        row,
        "availability_grid",
        "preferred_schedule",
        "availability",
        "availability_options",
        "weekly_availability",
    )
    availability_grid = _format_availability_grid(availability_raw)

    if disqual_reasons:
        reason_text = "Disqualified: " + ", ".join(disqual_reasons)
        return [
            status,
            "NA",
            reason_text,
            full_name,
            whatsapp,
            email,
            id_value,
            age_range,
            country_residence,
            country_birth,
            flag_color,
            "no",
            business_age_value,
            req_interner,
            weekly_time,
            participated_before,
            business_industry,
            business_description,
            growth_how,
            biggest_challenge,
            additional_comments,
            availability_grid,
            "Disqualified before scoring. Total score set to NA.",
        ]

    score_exp_lines = []
    ai_total = 0.0
    for slug in ai_fields:
        raw_score, explanation = grade_unstructured(
            client,
            row.get(slug),
            slug,
            prompt_template=prompt_for(slug),
            model_name=model_name,
        )
        ai_total += raw_score * float(weights.get(slug, 1))
        score_exp_lines.append(f"{slug} - {explanation}")

    total_score = sum([
        prior_pt,
        business_age_pt,
        employees_pt,
        extra_structured_total,
        ai_total,
    ])
    total_score_pct = _format_total_percentage(total_score, max_total_score)
    score_exp_lines.append(f"total_score - {total_score}/{max_total_score} ({total_score_pct})")

    return [
        status,
        total_score_pct,
        "\n".join(score_exp_lines),
        full_name,
        whatsapp,
        email,
        id_value,
        age_range,
        country_residence,
        country_birth,
        flag_color,
        "yes",
        business_age_value,
        req_interner,
        weekly_time,
        participated_before,
        business_industry,
        business_description,
        growth_how,
        biggest_challenge,
        additional_comments,
        availability_grid,
        rubric_note or "Applicants must meet all tablestakes; structured fields are deterministic; unstructured responses scored 1–5 and weighted. Total score shown as percentage.",
    ]


# ==================================================
# Grade FULL dataframe (MASTER CSV)
# ==================================================

def grade_from_dataframe(
    df: pd.DataFrame,
    client: OpenAI,
    log_fn=None,
    priority_emails: set[str] | list[str] | tuple[str, ...] | None = None,
    active_participant_emails: set[str] | list[str] | tuple[str, ...] | None = None,
    previous_application_ids: set[int] | list[int] | tuple[int, ...] | None = None,
    grading_config=None,
) -> pd.DataFrame:
    rows = []
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

    for i, (_, r) in enumerate(df.iterrows(), start=1):
        if log_fn:
            log_fn(f"→ Grading row {i}/{total}")

        out = grade_single_row(
            r.to_dict(),
            client,
            priority_emails=normalized_priority_emails,
            active_participant_emails=normalized_active_participant_emails,
            previous_application_ids=normalized_previous_application_ids,
            grading_config=grading_config,
        )
        rows.append(out)

    out_df = pd.DataFrame(rows, columns=COLUMNS)
    source_df = df.reset_index(drop=True)
    if bool(getattr(grading_config, "uses_configured_criteria", False)):
        # Match the mentora export behavior: scoring columns first, then the
        # exact source application data in form order, then grading metadata.
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
    out_df = out_df.loc[:, ~out_df.columns.duplicated()]
    # Do not remove applicant rows here. The selection page count and the
    # downloaded graded Excel must represent the same approved submission set.
    return out_df
