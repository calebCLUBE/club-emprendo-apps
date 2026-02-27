import pandas as pd
from openai import OpenAI
import logging

MODEL = "gpt-5.2"
TIMEOUT = 60
logger = logging.getLogger(__name__)

W = {
    "owned_business": 3,
    "prior_participation": 4,
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

def prior_participation_pts(v):
    if not isinstance(v, str):
        return 0
    if "first_time" in v:
        return 0
    if "as_entrepreneur" in v and "as_mentora" in v:
        return 5
    if "as_entrepreneur" in v or "as_mentora" in v:
        return 4
    return 0

def business_years_pts(v, owned):
    mapping = {"0_1": 1, "1_5": 2, "5_10": 3, "10_plus": 4}
    if pd.isna(v) or v == "":
        return -1 if yes(owned) else 0
    return mapping.get(v, 0)


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
    "certificate_name",
    "preferred_name",
    "id_number",
    "email",
    "whatsapp",
    "country_residence",
    "age_range",
    "red_flags",
    "meets_all_req",
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
    "professional_expertise",
    "professional_expertise_pt",
    "score_exp",
    "grading_rubric",
]

# -----------------------
# Grade ONE row
# -----------------------

def grade_single_row(row: dict, client: OpenAI) -> dict:
    meets_all = all(str(row.get(c, "")).lower() == "yes" for c in [
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
    ])

    if not meets_all:
        return {c: "" for c in COLUMNS}

    score_exp = []

    owned_pt = yes(row.get("owned_business")) * W["owned_business"]
    prior_pt = prior_participation_pts(row.get("prior_participation"))
    years_pt = business_years_pts(row.get("business_years"), row.get("owned_business"))
    emp_pt = yes(row.get("has_employees")) * W["has_employees"]
    mentor_pt = yes(row.get("mentoring_exp_as_mentor")) * W["mentoring_exp_as_mentor"]
    student_pt = yes(row.get("mentoring_exp_as_student")) * W["mentoring_exp_as_student"]

    prof_struct_pt = (
        1 if isinstance(row.get("professional_expertise"), str)
        and row.get("professional_expertise").strip()
        else 0
    ) * W["professional_expertise_struct"]

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
        owned_pt, prior_pt, years_pt, emp_pt,
        prof_struct_pt, mentor_pt, student_pt,
        bd_raw * W["business_description"],
        med_raw * W["mentoring_exp_detail"],
        mot_raw * W["motivation"],
        prof_raw * W["professional_expertise"],
    ])

    red_flags = detect_red_flags(
        client,
        row.get("business_description"),
        row.get("mentoring_exp_detail"),
        row.get("motivation"),
        row.get("professional_expertise"),
    )

    return {
        "total_pts": total_pts,
        "certificate_name": row.get("certificate_name"),
        "preferred_name": row.get("preferred_name"),
        "id_number": row.get("id_number"),
        "email": row.get("email"),
        "whatsapp": row.get("whatsapp"),
        "country_residence": row.get("country_residence"),
        "age_range": row.get("age_range"),
        "red_flags": red_flags,
        "meets_all_req": "yes",
        "owned_business": row.get("owned_business"),
        "owned_business_pt": owned_pt,
        "prior_participation": row.get("prior_participation"),
        "prior_participation_pt": prior_pt,
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
        "professional_expertise": row.get("professional_expertise"),
        "professional_expertise_pt": prof_raw * W["professional_expertise"],
        "score_exp": "\n".join(score_exp),
        "grading_rubric": "Weighted rubric applied; unstructured responses scored 1–5.",
    }

# -----------------------
# Grade FULL dataframe
# -----------------------

def grade_from_dataframe(df: pd.DataFrame, client: OpenAI, log_fn=None) -> pd.DataFrame:
    out = []
    total = len(df)

    for i, (_, row) in enumerate(df.iterrows(), start=1):
        if log_fn:
            log_fn(f"→ Grading mentor row {i}/{total}")

        out.append(grade_single_row(row.to_dict(), client))

    return pd.DataFrame(out, columns=COLUMNS)
