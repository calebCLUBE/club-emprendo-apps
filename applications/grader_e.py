import pandas as pd
from openai import OpenAI
import logging

# ==================================================
# Config
# ==================================================

MODEL = "gpt-5.2"
TIMEOUT = 60
logger = logging.getLogger(__name__)

W = {
    "prior_mentoring": 2,
    "business_age": 3,
    "has_employees": 2,
    "business_description": 3,
    "growth_how": 4,
    "biggest_challenge": 4,
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


def status_from_participation(v, *, disqualified: bool) -> str:
    if disqualified:
        return "N/A"

    if has_prior_participation(v):
        return "Seleccionada"

    return "Aplicante anterios"


def red_flag_color(red_flag_text: str, participated_before_value) -> str:
    if str(red_flag_text or "").strip():
        return "red"
    if has_prior_participation(participated_before_value):
        return "green"
    return ""


def _normalized_identifier(row: dict | pd.Series, keys: list[str]) -> tuple[str, str] | None:
    for key in keys:
        value = str(row.get(key, "") or "").strip().lower()
        if value:
            return key, value
    return None


def _score_rank(value) -> float:
    if value in (None, "", "NA"):
        return float("-inf")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("-inf")


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
    if row.get("hours_per_week") == "lt_2":
        reasons.append("hours_per_week")
    if not yes(row.get("commit_3_months")):
        reasons.append("commit_3_months")
    if str(row.get("business_age", "")).strip().lower() == "idea":
        reasons.append("business_age=idea")
    return reasons


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
# Output layout (EXACT)
# ==================================================

CATEGORY_ROW = [
    "TOTAL",
    "",
    "", "", "", "", "",
    "FLAGS",
    "TABLESTAKES",
    "NICE TO HAVE", "",
    "NICE TO HAVE", "",
    "NICE TO HAVE", "",
    "NICE TO HAVE", "",
    "INSIGHT", "",
    "INSIGHT", "",
    "INSIGHT", "",
    "", "EXPLANATION", "RUBRIC"
]

COLUMNS = [
    "total_score",
    "status",
    "full_name",
    "whatsapp",
    "email",
    "cedula",
    "country_residence",
    "flag_color",
    "meets_all_req",
    "prior_mentoring",
    "prior_mentoring_pt",
    "business_age",
    "business_age_pt",
    "has_employees",
    "has_employees_pt",
    "participated_before",
    "participated_before_pt",
    "business_description",
    "business_description_pt",
    "growth_how",
    "growth_how_pt",
    "biggest_challenge",
    "biggest_challenge_pt",
    "additional_comments",
    "score_exp",
    "grading_rubric",
]

# ==================================================
# Grade ONE row (NO category row here)
# ==================================================

def grade_single_row(row: dict, client: OpenAI) -> list:
    disqual_reasons = _disqualification_reasons(row)
    status = status_from_participation(
        row.get("participated_before"),
        disqualified=bool(disqual_reasons),
    )

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

    if disqual_reasons:
        reason_text = "Disqualified: " + ", ".join(disqual_reasons)
        return [
            "NA",
            status,
            row.get("full_name"),
            row.get("whatsapp"),
            row.get("email"),
            row.get("cedula"),
            row.get("country_residence"),
            flag_color,
            "no",
            row.get("prior_mentoring"), "",
            row.get("business_age"), "",
            row.get("has_employees"), "",
            row.get("participated_before"), "",
            row.get("business_description"), "",
            row.get("growth_how"), "",
            row.get("biggest_challenge"), "",
            row.get("additional_comments"),
            reason_text,
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

    return [
        total_score,
        status,
        row.get("full_name"),
        row.get("whatsapp"),
        row.get("email"),
        row.get("cedula"),
        row.get("country_residence"),
        flag_color,
        "yes",
        row.get("prior_mentoring"), prior_pt,
        row.get("business_age"), business_age_pt,
        row.get("has_employees"), employees_pt,
        row.get("participated_before"), "",
        row.get("business_description"), bd_pt,
        row.get("growth_how"), gh_pt,
        row.get("biggest_challenge"), bc_pt,
        row.get("additional_comments"),
        "\n".join(score_exp_lines),
        "Applicants must meet all tablestakes; structured fields are deterministic; unstructured responses scored 1–5 and weighted.",
    ]


# ==================================================
# Grade FULL dataframe (MASTER CSV)
# ==================================================

def grade_from_dataframe(df: pd.DataFrame, client: OpenAI, log_fn=None) -> pd.DataFrame:
    rows = []
    total = len(df)

    for i, (_, r) in enumerate(df.iterrows(), start=1):
        if log_fn:
            log_fn(f"→ Grading row {i}/{total}")

        out = grade_single_row(r.to_dict(), client)
        rows.append(out)

    out_df = pd.DataFrame(rows, columns=COLUMNS)
    out_df, removed = _dedupe_scored_rows(out_df, "total_score", ["email", "cedula"])
    if removed and log_fn:
        log_fn(f"→ Removed {removed} duplicate emprendedora rows, keeping the highest score per person")

    # CATEGORY ROW GOES ONCE, AT TOP
    return pd.concat(
        [pd.DataFrame([CATEGORY_ROW], columns=COLUMNS), out_df],
        ignore_index=True,
    )
