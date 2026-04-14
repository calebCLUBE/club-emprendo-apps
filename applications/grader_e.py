import pandas as pd
from openai import OpenAI
import logging

# ==================================================
# Config
# ==================================================

MODEL = "gpt-5.2"
TIMEOUT = 60
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


def _disqualification_reasons(row: dict) -> list[str]:
    reasons = []
    if row.get("internet_access") != "yes_ok":
        reasons.append("internet_access")
    if not yes(row.get("commit_3_months")):
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
            model="omni-moderation-latest",
            input=combined[:15000],
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

def grade_unstructured(client: OpenAI, text, criterion):
    if not isinstance(text, str) or not text.strip():
        return 0, "Blank or insufficient response."

    prompt = f"""
Criterion: {criterion}

Rules:
- Score from 1–5 based on clarity, relevance, and insight
- Bad, nonsensical, or irrelevant → 0
- Be strict but fair

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
    "business_age",
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
) -> list:
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

    prior_pt = prior_mentoring_pts(row.get("prior_mentoring"))
    business_age_pt = business_age_pts(row.get("business_age"))
    employees_pt = has_employees_pts(row.get("has_employees"))

    red_flags = detect_red_flags(
        client,
        row.get("business_description"),
        row.get("growth_how"),
        row.get("biggest_challenge"),
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
    req_interner = _pick_row_value(row, "internet_access", "req_basic_internet_device")
    weekly_time = _pick_row_value(row, "hours_per_week", "weekly_time", "req_avail_2hrs_week")
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
            business_age_value,
            business_description,
            growth_how,
            biggest_challenge,
            additional_comments,
            availability_grid,
            "Disqualified before scoring. Total score set to NA.",
        ]

    score_exp_lines = []

    bd_raw, bd_exp = grade_unstructured(client, row.get("business_description"), "business_description")
    bd_pt = bd_raw * W["business_description"]
    score_exp_lines.append(f"business_description - {bd_exp}")

    gh_raw, gh_exp = grade_unstructured(client, row.get("growth_how"), "growth_how")
    gh_pt = gh_raw * W["growth_how"]
    score_exp_lines.append(f"growth_how - {gh_exp}")

    bc_raw, bc_exp = grade_unstructured(client, row.get("biggest_challenge"), "biggest_challenge")
    bc_pt = bc_raw * W["biggest_challenge"]
    score_exp_lines.append(f"biggest_challenge - {bc_exp}")

    total_score = sum([
        prior_pt,
        business_age_pt,
        employees_pt,
        bd_pt,
        gh_pt,
        bc_pt,
    ])
    total_score_pct = _format_total_percentage(total_score)
    score_exp_lines.append(f"total_score - {total_score}/{MAX_TOTAL_SCORE} ({total_score_pct})")

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
        business_age_value,
        business_description,
        growth_how,
        biggest_challenge,
        additional_comments,
        availability_grid,
        "Applicants must meet all tablestakes; structured fields are deterministic; unstructured responses scored 1–5 and weighted. Total score shown as percentage.",
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
        )
        rows.append(out)

    out_df = pd.DataFrame(rows, columns=COLUMNS)
    out_df, removed = _dedupe_scored_rows(out_df, "score", ["email", "cedula", "id_number"])
    if removed and log_fn:
        log_fn(f"→ Removed {removed} duplicate emprendedora rows, keeping the highest score per person")
    return out_df
