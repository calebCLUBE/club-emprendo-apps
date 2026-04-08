import csv
import io
import re
from collections import defaultdict

from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render
from django.utils.dateparse import parse_datetime

from .models import Answer, Application, GradedFile

GROUP_SLUG_RE = re.compile(r"^G(?P<num>\d+)_")

IDENTITY_SLUGS = ("cedula", "id_number")
CSV_IDENTITY_KEYS = (
    "cedula",
    "idnumber",
    "documento",
    "documentnumber",
    "numeroidentidad",
    "numerodedocumento",
)
CSV_RECOMMENDATION_KEYS = ("recommendation", "recomendacion", "calificacion", "status", "estado")
CSV_OVERALL_SCORE_KEYS = ("overallscore", "totalscore", "score")
CSV_TABLESTAKES_KEYS = ("tablestakesscore",)
CSV_COMMITMENT_KEYS = ("commitmentscore",)
CSV_NICE_TO_HAVE_KEYS = ("nicetohavescore",)

PROFILE_OVERVIEW_FIELDS = [
    ("full_name", "Full name"),
    ("preferred_name", "Preferred name"),
    ("email", "Email"),
    ("whatsapp", "WhatsApp"),
    ("city_residence", "City"),
    ("country_residence", "Country"),
    ("country_birth", "Country of birth"),
    ("age_range", "Age range"),
    ("business_name", "Business"),
    ("industry", "Industry"),
    ("professional_expertise", "Expertise"),
]


def _normalize_identity(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    return re.sub(r"[^0-9A-Za-z]", "", raw).upper()


def _normalize_header(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    return re.sub(r"[^a-z0-9]+", "", raw)


def _pick_value(row: dict[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = (row.get(key) or "").strip()
        if value:
            return value
    return ""


def _group_number_from_slug(slug: str | None) -> int | None:
    match = GROUP_SLUG_RE.match((slug or "").strip().upper())
    if not match:
        return None
    try:
        return int(match.group("num"))
    except (TypeError, ValueError):
        return None


def _track_from_slug(slug: str | None) -> str | None:
    s = (slug or "").upper()
    if "E_A" in s:
        return "E"
    if "M_A" in s:
        return "M"
    return None


def _parse_created_at(value: str) -> tuple:
    raw = (value or "").strip()
    parsed = parse_datetime(raw)
    if parsed:
        # Keep comparisons deterministic across naive/aware mixes by using the raw string.
        return (1, raw)
    return (0, raw)


def _parse_graded_file_rows(gf: GradedFile) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not gf.csv_text:
        return out

    reader = csv.DictReader(io.StringIO(gf.csv_text))
    for row in reader:
        normalized = {
            _normalize_header(col): (val or "").strip()
            for col, val in row.items()
            if col is not None
        }
        identity_raw = _pick_value(normalized, CSV_IDENTITY_KEYS)
        identity_key = _normalize_identity(identity_raw)
        if not identity_key:
            continue

        created_marker = _parse_created_at(normalized.get("createdat", ""))
        current = out.get(identity_key)
        if current and current["created_marker"] > created_marker:
            continue

        out[identity_key] = {
            "identity_raw": identity_raw,
            "created_marker": created_marker,
            "recommendation": _pick_value(normalized, CSV_RECOMMENDATION_KEYS),
            "overall_score": _pick_value(normalized, CSV_OVERALL_SCORE_KEYS),
            "tablestakes_score": _pick_value(normalized, CSV_TABLESTAKES_KEYS),
            "commitment_score": _pick_value(normalized, CSV_COMMITMENT_KEYS),
            "nice_to_have_score": _pick_value(normalized, CSV_NICE_TO_HAVE_KEYS),
        }

    return out


def _build_grading_lookup(target_groups: set[int]) -> tuple[dict[tuple[int, str], GradedFile], dict[int, GradedFile], dict[int, dict[str, dict]]]:
    if not target_groups:
        return {}, {}, {}

    latest_by_group_track: dict[tuple[int, str], GradedFile] = {}
    latest_by_group: dict[int, GradedFile] = {}

    graded_files = GradedFile.objects.exclude(form_slug__startswith="PAIR_G").order_by("-created_at", "-id")
    for gf in graded_files:
        group_num = _group_number_from_slug(gf.form_slug)
        if group_num is None or (target_groups and group_num not in target_groups):
            continue

        track = _track_from_slug(gf.form_slug)
        if track and (group_num, track) not in latest_by_group_track:
            latest_by_group_track[(group_num, track)] = gf
        if group_num not in latest_by_group:
            latest_by_group[group_num] = gf

    selected_files = {}
    for gf in latest_by_group_track.values():
        selected_files[gf.id] = gf
    for gf in latest_by_group.values():
        selected_files[gf.id] = gf

    rows_by_file_id = {gf_id: _parse_graded_file_rows(gf) for gf_id, gf in selected_files.items()}
    return latest_by_group_track, latest_by_group, rows_by_file_id


def _build_profiles():
    identity_answers = (
        Answer.objects.filter(question__slug__in=IDENTITY_SLUGS)
        .select_related("application", "application__form", "application__form__group", "question")
        .order_by("-application__created_at", "-application_id", "question__slug", "-id")
    )

    app_ids_by_identity: dict[str, set[int]] = defaultdict(set)
    latest_app_id_by_identity: dict[str, int] = {}
    display_id_by_identity: dict[str, str] = {}

    for ans in identity_answers:
        identity_key = _normalize_identity(ans.value)
        if not identity_key:
            continue
        app_ids_by_identity[identity_key].add(ans.application_id)
        if identity_key not in latest_app_id_by_identity:
            latest_app_id_by_identity[identity_key] = ans.application_id
            display_id_by_identity[identity_key] = (ans.value or "").strip()

    if not latest_app_id_by_identity:
        return []

    apps = (
        Application.objects.filter(id__in=list(latest_app_id_by_identity.values()))
        .select_related("form", "form__group")
        .prefetch_related("answers__question")
    )
    app_by_id = {app.id: app for app in apps}

    target_groups = set()
    app_meta_by_identity = {}
    for identity_key, app_id in latest_app_id_by_identity.items():
        app = app_by_id.get(app_id)
        if not app:
            continue
        group_num = getattr(getattr(app, "form", None), "group_id", None)
        if group_num:
            group_num = getattr(app.form.group, "number", None)
        else:
            group_num = _group_number_from_slug(getattr(app.form, "slug", ""))
        if group_num:
            target_groups.add(group_num)

        app_meta_by_identity[identity_key] = {
            "application": app,
            "group_num": group_num,
            "track": _track_from_slug(getattr(app.form, "slug", "")),
        }

    latest_by_group_track, latest_by_group, rows_by_file_id = _build_grading_lookup(target_groups)

    profiles = []
    for identity_key, meta in app_meta_by_identity.items():
        app = meta["application"]
        group_num = meta["group_num"]
        track = meta["track"]

        answer_map = {}
        for ans in app.answers.all():
            slug = getattr(ans.question, "slug", "")
            if slug:
                answer_map[slug] = (ans.value or "").strip()

        grade_file = None
        if group_num and track:
            grade_file = latest_by_group_track.get((group_num, track))
        if not grade_file and group_num:
            grade_file = latest_by_group.get(group_num)

        grade_row = {}
        if grade_file:
            grade_row = rows_by_file_id.get(grade_file.id, {}).get(identity_key, {})
            if not grade_row and group_num:
                fallback_file = latest_by_group.get(group_num)
                if fallback_file:
                    grade_row = rows_by_file_id.get(fallback_file.id, {}).get(identity_key, {})
                    if grade_row:
                        grade_file = fallback_file

        recommendation = (
            (grade_row.get("recommendation") or "").strip()
            or (app.recommendation or "").strip()
        )
        overall_score = (
            (grade_row.get("overall_score") or "").strip()
            or (f"{app.overall_score:g}" if app.overall_score else "")
        )
        tablestakes_score = (
            (grade_row.get("tablestakes_score") or "").strip()
            or (f"{app.tablestakes_score:g}" if app.tablestakes_score else "")
        )
        commitment_score = (
            (grade_row.get("commitment_score") or "").strip()
            or (f"{app.commitment_score:g}" if app.commitment_score else "")
        )
        nice_to_have_score = (
            (grade_row.get("nice_to_have_score") or "").strip()
            or (f"{app.nice_to_have_score:g}" if app.nice_to_have_score else "")
        )

        calificacion_status = recommendation or ("Scored" if overall_score else "Not graded")
        display_identity = (
            display_id_by_identity.get(identity_key)
            or answer_map.get("cedula")
            or answer_map.get("id_number")
            or grade_row.get("identity_raw")
            or identity_key
        )

        overview_rows = []
        for slug, label in PROFILE_OVERVIEW_FIELDS:
            value = answer_map.get(slug, "")
            if value:
                overview_rows.append({"label": label, "value": value})

        profile = {
            "identity_key": identity_key,
            "identity_display": display_identity,
            "applicant_name": answer_map.get("full_name") or app.name or "—",
            "email": answer_map.get("email") or app.email or "—",
            "group_num": group_num,
            "track": track or "—",
            "form_slug": getattr(app.form, "slug", "—"),
            "form_name": getattr(app.form, "name", "—"),
            "applied_at": app.created_at,
            "application_id": app.id,
            "application_count": len(app_ids_by_identity.get(identity_key, [])),
            "calificacion_status": calificacion_status,
            "recommendation": recommendation,
            "overall_score": overall_score,
            "tablestakes_score": tablestakes_score,
            "commitment_score": commitment_score,
            "nice_to_have_score": nice_to_have_score,
            "is_graded": bool(recommendation or overall_score),
            "graded_file_slug": getattr(grade_file, "form_slug", ""),
            "graded_file_created_at": getattr(grade_file, "created_at", None),
            "overview_rows": overview_rows,
        }
        profile["search_text"] = " ".join(
            [
                str(profile["identity_display"]),
                str(profile["applicant_name"]),
                str(profile["email"]),
                str(profile["form_slug"]),
                str(profile["group_num"] or ""),
                str(profile["calificacion_status"]),
            ]
        ).lower()
        profiles.append(profile)

    profiles.sort(key=lambda p: (p["applied_at"], p["application_id"]), reverse=True)
    return profiles


@staff_member_required
def profiles_list(request):
    profiles = _build_profiles()

    query = (request.GET.get("q") or "").strip()
    query_lower = query.lower()
    group_filter = (request.GET.get("group") or "").strip()
    status_filter = (request.GET.get("grading") or "").strip()
    if status_filter not in {"all", "graded", "not_graded"}:
        status_filter = "all"

    filtered = profiles
    if query_lower:
        filtered = [p for p in filtered if query_lower in p["search_text"]]

    if group_filter.isdigit():
        filtered = [p for p in filtered if p["group_num"] == int(group_filter)]

    if status_filter == "graded":
        filtered = [p for p in filtered if p["is_graded"]]
    elif status_filter == "not_graded":
        filtered = [p for p in filtered if not p["is_graded"]]

    group_options = sorted(
        {p["group_num"] for p in profiles if p["group_num"] is not None},
        reverse=True,
    )

    context = {
        "profiles": filtered,
        "query": query,
        "group_filter": group_filter,
        "status_filter": status_filter,
        "group_options": group_options,
        "total_profiles": len(profiles),
        "visible_profiles": len(filtered),
        "graded_profiles": sum(1 for p in profiles if p["is_graded"]),
    }
    return render(request, "admin_dash/profiles_list.html", context)


@staff_member_required
def profile_detail(request, identity_key: str):
    requested_key = _normalize_identity(identity_key)
    if not requested_key:
        return render(request, "admin_dash/profile_detail.html", {"profile": None})

    profiles = _build_profiles()
    profile = next((p for p in profiles if p["identity_key"] == requested_key), None)
    return render(request, "admin_dash/profile_detail.html", {"profile": profile})
