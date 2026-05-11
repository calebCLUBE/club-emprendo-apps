import re
from datetime import date


GROUP_SLUG_RE = re.compile(r"^G(?P<num>\d+)_")
EMAIL_PLACEHOLDER_RE = re.compile(r"{{\s*([a-zA-Z0-9_]+)\s*}}")
MONTH_NUM_TO_ES = {
    1: "enero",
    2: "febrero",
    3: "marzo",
    4: "abril",
    5: "mayo",
    6: "junio",
    7: "julio",
    8: "agosto",
    9: "septiembre",
    10: "octubre",
    11: "noviembre",
    12: "diciembre",
}


def _group_num_from_slug(slug: str) -> str:
    m = GROUP_SLUG_RE.match(slug or "")
    return m.group("num") if m else ""


def _track_from_slug(slug: str) -> str:
    s = (slug or "").strip()
    if s.endswith("E_A1") or s.endswith("E_A2"):
        return "emprendedoras"
    if s.endswith("M_A1") or s.endswith("M_A2"):
        return "mentoras"
    return ""


def _format_deadline_text(deadline: date | None) -> str:
    if not deadline:
        return ""
    month = MONTH_NUM_TO_ES.get(deadline.month, "")
    if month:
        return f"{deadline.day} de {month} de {deadline.year}"
    return deadline.strftime("%d/%m/%Y")


def build_form_email_context(
    *,
    form_def=None,
    track: str = "",
    role_word: str = "",
    a2_link: str = "",
    deadline: date | None = None,
    form_name: str = "",
) -> dict[str, str]:
    slug = (getattr(form_def, "slug", "") or "").strip()
    group = getattr(form_def, "group", None)
    group_num = str(getattr(group, "number", "") or _group_num_from_slug(slug) or "").strip()

    track_value = (track or _track_from_slug(slug) or "").strip().lower()
    if track_value.startswith("e"):
        track_label = "emprendedoras"
        default_role_word = "emprendedora"
    elif track_value.startswith("m"):
        track_label = "mentoras"
        default_role_word = "mentora"
    elif track_value:
        track_label = track_value
        default_role_word = track_value
    else:
        track_label = "Club Emprendo"
        default_role_word = "aplicante"

    deadline_value = deadline or getattr(group, "a2_deadline", None)
    deadline_text = _format_deadline_text(deadline_value)

    respond_by_day = str(getattr(deadline_value, "day", "") or "")
    respond_by_month = MONTH_NUM_TO_ES.get(getattr(deadline_value, "month", 0), "")
    respond_by_year = str(getattr(deadline_value, "year", "") or "")

    return {
        "group_num": group_num,
        "group_label": f"Grupo {group_num}" if group_num else "Grupo #",
        "track": track_label,
        "track_label": track_label,
        "role_word": (role_word or default_role_word).strip(),
        "form_name": str(form_name or getattr(form_def, "name", "") or "").strip(),
        "a2_link": str(a2_link or "").strip(),
        "deadline_text": deadline_text,
        "respond_by_text": deadline_text,
        "respond_by_day": respond_by_day,
        "respond_by_month": respond_by_month,
        "respond_by_year": respond_by_year,
    }


def render_email_template(raw_text: str, replacements: dict[str, str]) -> str:
    text = str(raw_text or "")

    def _replace(match: re.Match) -> str:
        key = (match.group(1) or "").strip()
        return str(replacements.get(key, match.group(0)))

    return EMAIL_PLACEHOLDER_RE.sub(_replace, text)


def resolve_form_email_template(
    *,
    form_def,
    field_name: str,
    default_text: str,
    replacements: dict[str, str],
    is_subject: bool = False,
) -> str:
    custom_value = (getattr(form_def, field_name, "") or "") if form_def else ""
    template = custom_value if str(custom_value).strip() else default_text
    rendered = render_email_template(template, replacements)
    if is_subject:
        return " ".join(rendered.splitlines()).strip()
    return rendered
