import pandas as pd
from openai import OpenAI

MODEL = "gpt-5.2"
TIMEOUT = 60

W = {
    "prior_mentoring": 2,
    "business_age": 3,
    "has_employees": 2,
    "participated_before": 4,
    "business_description": 3,
    "growth_how": 4,
    "biggest_challenge": 4,
}

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

def participated_before_pts(v):
    if not isinstance(v, str):
        return 0
    if "yes_mentor" in v and "yes_entrepreneur" in v:
        return 5
    if "yes_mentor" in v or "yes_entrepreneur" in v:
        return 4
    return 0

def has_employees_pts(v):
    return 2 if yes(v) else 0

def detect_red_flags(*texts):
    flags = []
    keywords = ["sex", "sexual", "drug", "alcohol", "weed", "cocaine", "illegal", "illicit"]
    combined = " ".join([t.lower() for t in texts if isinstance(t, str)])
    for k in keywords:
        if k in combined:
            flags.append(k)
    return ", ".join(sorted(set(flags)))

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


CATEGORY_ROW = [
    "TOTAL",
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
    "full_name",
    "whatsapp",
    "email",
    "cedula",
    "country_residence",
    "red_flags",
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

def grade_single_row(row: dict, client: OpenAI) -> pd.DataFrame:
    qualifies = (
        row.get("internet_access") == "yes_ok"
        and row.get("hours_per_week") != "lt_2"
        and yes(row.get("commit_3_months"))
    )

    if not qualifies:
        return pd.DataFrame([["FAILED_TABLESTAKES"] + [""] * (len(COLUMNS) - 1)], columns=COLUMNS)

    prior_pt = prior_mentoring_pts(row.get("prior_mentoring"))
    business_age_pt = business_age_pts(row.get("business_age"))
    employees_pt = has_employees_pts(row.get("has_employees"))
    participated_pt = participated_before_pts(row.get("participated_before"))

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
        participated_pt,
        bd_pt,
        gh_pt,
        bc_pt,
    ])

    red_flags = detect_red_flags(
        row.get("business_description"),
        row.get("growth_how"),
        row.get("biggest_challenge"),
    )

    out_row = [
        total_score,
        row.get("full_name"),
        row.get("whatsapp"),
        row.get("email"),
        row.get("cedula"),
        row.get("country_residence"),
        red_flags,
        "yes",
        row.get("prior_mentoring"), prior_pt,
        row.get("business_age"), business_age_pt,
        row.get("has_employees"), employees_pt,
        row.get("participated_before"), participated_pt,
        row.get("business_description"), bd_pt,
        row.get("growth_how"), gh_pt,
        row.get("biggest_challenge"), bc_pt,
        row.get("additional_comments"),
        "\n".join(score_exp_lines),
        "Applicants must meet all tablestakes; structured fields are deterministic; unstructured responses scored 1–5 and weighted."
    ]

    df = pd.DataFrame([CATEGORY_ROW, out_row], columns=COLUMNS)
    return df
def grade_from_dataframe(df: "pd.DataFrame", client) -> "pd.DataFrame":
    """
    Grade an entire master dataframe by reusing grade_single_row.
    Returns ONE dataframe with one graded row per input row.
    """
    graded_rows = []

    for _, row in df.iterrows():
        row_dict = row.to_dict()
        one_df = grade_single_row(row_dict, client)  # <- your existing function
        # grade_single_row returns a 1-row df; collect that row
        graded_rows.append(one_df.iloc[0].to_dict())

    return pd.DataFrame(graded_rows)
