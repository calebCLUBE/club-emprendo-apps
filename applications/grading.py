# applications/grading.py
from __future__ import annotations
import re
from .models import Application


# Group copies look like "G5_E_A2"; we want to grade them using the same
# logic as the master slug ("E_A2").
GROUP_SLUG_RE = re.compile(r"^G(?P<num>\d+)_(?P<master>E_A1|E_A2|M_A1|M_A2)$")


def _safe_int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def grade_from_answers(application: Application) -> dict:
    """
    Basic rule-based grading.

    Uses different heuristics for:
      - E_A2: Emprendedora second application
      - M_A2: Mentora second application

    Feel free to change weights/thresholds as you learn from real data.
    """
    answers = {a.question.slug: a.value for a in application.answers.all()}

    form_slug = application.form.slug
    m = GROUP_SLUG_RE.match(form_slug)
    master_slug = m.group("master") if m else form_slug

    tablestakes = 0.0
    commitment = 0.0
    nice_to_have = 0.0

    # ------------- EMPRENDEDORA – Application 2 (E_A2) -----------------
    if master_slug == "E_A2":
        # Business age: more established -> higher tablestakes
        age = answers.get("e2_business_age", "")
        age_map = {
            "idea": 0,
            "less_1": 1,
            "1_3": 2,
            "4_6": 3,
            "7_10": 3.5,
            "more_10": 4,
        }
        tablestakes += age_map.get(age, 0)

        # Employees: having employees suggests some traction
        emp = answers.get("e2_has_employees", "")
        if emp == "yes":
            tablestakes += 2

        # Commitment: program commitment
        commitment_choice = answers.get("e2_commitment_program", "")
        if commitment_choice == "yes":
            commitment += 3
        elif commitment_choice == "not_sure":
            commitment += 1

        # Hours/week
        hours_choice = answers.get("e2_hours_per_week", "")
        hours_map = {
            "lt2": 1,
            "2_4": 2,
            "gt4": 3,
        }
        commitment += hours_map.get(hours_choice, 0)

        # Internet access (tablestakes)
        internet = answers.get("e2_internet_access", "")
        internet_map = {
            "good": 2,
            "some_difficulties": 1,
            "no_access": 0,
        }
        tablestakes += internet_map.get(internet, 0)

        # Mentor experience (nice-to-have)
        mentor_exp = answers.get("e2_mentor_experience", "")
        if mentor_exp == "yes":
            nice_to_have += 1

        # Text length proxies for commitment/clarity
        growth = answers.get("e2_growth_how_help", "").strip()
        challenge = answers.get("e2_main_challenge", "").strip()

        if len(growth) > 300:
            commitment += 1
        if len(challenge) > 300:
            commitment += 1

    # ------------- MENTORA – Application 2 (M_A2) -----------------
    elif master_slug == "M_A2":
        # Basic requirement flags from the big checklist could be added later.
        # For now we read key slugs.

        has_run_business = answers.get("m2_has_run_business", "")
        if has_run_business == "yes":
            tablestakes += 3

        # Business age
        age = answers.get("m2_business_age", "")
        age_map = {
            "0_1": 1,
            "1_5": 2,
            "5_10": 3,
            "10_plus": 3.5,
        }
        tablestakes += age_map.get(age, 0)

        # Employees
        emp = answers.get("m2_has_employees", "")
        if emp == "yes":
            tablestakes += 1.5

        # Area of expertise + motivation + why_good_mentor
        motivation = answers.get("m2_motivation", "").strip()
        why_good = answers.get("m2_why_good_mentor", "").strip()

        if len(motivation) > 300:
            commitment += 1.5
        if len(why_good) > 300:
            commitment += 1.5

        # Mentoring/coaching experience
        coach_exp = answers.get("m2_coach_experience", "")
        student_exp = answers.get("m2_student_experience", "")

        if coach_exp == "yes":
            nice_to_have += 2
        if student_exp == "yes":
            nice_to_have += 1

        # Hours per week available
        hours = answers.get("m2_hours_per_week", "")
        hours_map = {
            "lt2": 0.5,
            "2_3": 1.5,
            "3_4": 2.5,
            "gt4": 3.0,
        }
        commitment += hours_map.get(hours, 0)

    # Fallback: if form is something else, everything stays at 0.

    overall = 0.5 * tablestakes + 0.3 * commitment + 0.2 * nice_to_have

    if overall >= 8:
        recommendation = "strong_yes"
    elif overall >= 6:
        recommendation = "yes"
    elif overall >= 4:
        recommendation = "maybe"
    else:
        recommendation = "no"

    return {
        "tablestakes_score": round(tablestakes, 2),
        "commitment_score": round(commitment, 2),
        "nice_to_have_score": round(nice_to_have, 2),
        "overall_score": round(overall, 2),
        "recommendation": recommendation,
    }
