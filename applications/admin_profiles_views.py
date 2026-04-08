import csv
import io
import re
from collections import defaultdict

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_datetime

from .models import Answer, Application, GradedFile, ParticipantEmailStatus

GROUP_SLUG_RE = re.compile(r"^G(?P<num>\d+)_")

IDENTITY_SLUGS = ("cedula", "id_number")
EMAIL_SLUGS = ("email",)
CSV_IDENTITY_KEYS = (
    "cedula",
    "idnumber",
    "documento",
    "documentnumber",
    "numeroidentidad",
    "numerodedocumento",
)
CSV_EMAIL_KEYS = ("email", "correo", "correoelectronico", "correoelectrnico")
CSV_RECOMMENDATION_KEYS = ("recommendation", "recomendacion", "calificacion", "status", "estado")
CSV_OVERALL_SCORE_KEYS = ("overallscore", "totalscore", "score")
CSV_TABLESTAKES_KEYS = ("tablestakesscore",)
CSV_COMMITMENT_KEYS = ("commitmentscore",)
CSV_NICE_TO_HAVE_KEYS = ("nicetohavescore",)
EMAIL_SPLIT_RE = re.compile(r"[,\s;]+")

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


def _normalize_email(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    return raw


def _email_status_key(value: str | None) -> str:
    normalized = _normalize_email(value)
    if not normalized or "@" not in normalized:
        return ""
    return normalized


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


def _id_token(value: str | None) -> str:
    normalized = _normalize_identity(value)
    if not normalized:
        return ""
    return f"id:{normalized}"


def _email_token(value: str | None) -> str:
    normalized = _normalize_email(value)
    if not normalized:
        return ""
    return f"email:{normalized}"


def _normalize_profile_key(value: str | None) -> str:
    raw = (value or "").strip().lower()
    return re.sub(r"[^a-z0-9_]+", "", raw)


def _parse_email_list(raw_value: str) -> tuple[list[str], list[str]]:
    seen = set()
    valid: list[str] = []
    invalid: list[str] = []
    for part in EMAIL_SPLIT_RE.split(raw_value or ""):
        candidate = _normalize_email(part)
        if not candidate:
            continue
        try:
            validate_email(candidate)
        except ValidationError:
            invalid.append(part.strip() or candidate)
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        valid.append(candidate)
    return valid, invalid


def _build_profile_key(identity_norm: str, email_norm: str, app_id: int) -> str:
    if identity_norm:
        return _normalize_profile_key(f"id_{identity_norm.lower()}")
    if email_norm:
        email_fragment = re.sub(r"[^a-z0-9]+", "", email_norm.lower())
        if email_fragment:
            return _normalize_profile_key(f"email_{email_fragment}")
    return _normalize_profile_key(f"app_{app_id}")


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
        email_raw = _pick_value(normalized, CSV_EMAIL_KEYS)
        tokens = {token for token in (_id_token(identity_raw), _email_token(email_raw)) if token}
        if not tokens:
            continue

        created_marker = _parse_created_at(normalized.get("createdat", ""))
        row_data = {
            "identity_raw": identity_raw,
            "email_raw": email_raw,
            "created_marker": created_marker,
            "recommendation": _pick_value(normalized, CSV_RECOMMENDATION_KEYS),
            "overall_score": _pick_value(normalized, CSV_OVERALL_SCORE_KEYS),
            "tablestakes_score": _pick_value(normalized, CSV_TABLESTAKES_KEYS),
            "commitment_score": _pick_value(normalized, CSV_COMMITMENT_KEYS),
            "nice_to_have_score": _pick_value(normalized, CSV_NICE_TO_HAVE_KEYS),
        }
        for token in tokens:
            current = out.get(token)
            if current and current["created_marker"] > created_marker:
                continue
            out[token] = row_data

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
    apps = list(
        Application.objects.select_related("form", "form__group")
        .prefetch_related("answers__question")
        .order_by("-created_at", "-id")
    )
    if not apps:
        return []

    app_data_by_id: dict[int, dict] = {}
    for app in apps:
        answer_map = {}
        for ans in app.answers.all():
            slug = getattr(ans.question, "slug", "")
            if slug:
                answer_map[slug] = (ans.value or "").strip()

        id_values = [answer_map.get(slug, "") for slug in IDENTITY_SLUGS]
        email_values = [app.email or ""] + [answer_map.get(slug, "") for slug in EMAIL_SLUGS]

        id_display = next((val for val in id_values if (val or "").strip()), "")
        email_display = next((val for val in email_values if (val or "").strip()), "")

        id_norm = _normalize_identity(id_display)
        email_norm = _normalize_email(email_display)
        tokens = {
            token
            for token in (
                *(_id_token(value) for value in id_values),
                *(_email_token(value) for value in email_values),
            )
            if token
        }

        app_data_by_id[app.id] = {
            "app": app,
            "answer_map": answer_map,
            "id_display": id_display,
            "email_display": email_display,
            "id_norm": id_norm,
            "email_norm": email_norm,
            "tokens": tokens,
        }

    # Union applications by shared identity token (cedula or email).
    parent = {app_id: app_id for app_id in app_data_by_id.keys()}

    def _find(app_id: int) -> int:
        while parent[app_id] != app_id:
            parent[app_id] = parent[parent[app_id]]
            app_id = parent[app_id]
        return app_id

    def _union(a: int, b: int) -> None:
        ra = _find(a)
        rb = _find(b)
        if ra != rb:
            parent[rb] = ra

    token_owner: dict[str, int] = {}
    for app_id, payload in app_data_by_id.items():
        for token in payload["tokens"]:
            owner = token_owner.get(token)
            if owner is None:
                token_owner[token] = app_id
            else:
                _union(app_id, owner)

    clusters: dict[int, list[int]] = defaultdict(list)
    for app_id in app_data_by_id.keys():
        clusters[_find(app_id)].append(app_id)

    latest_app_id_by_cluster: dict[int, int] = {}
    target_groups: set[int] = set()
    for root, app_ids in clusters.items():
        latest_app_id = max(
            app_ids,
            key=lambda app_id: (
                app_data_by_id[app_id]["app"].created_at,
                app_id,
            ),
        )
        latest_app_id_by_cluster[root] = latest_app_id
        latest_app = app_data_by_id[latest_app_id]["app"]
        group_num = getattr(getattr(latest_app, "form", None), "group_id", None)
        if group_num:
            group_num = getattr(latest_app.form.group, "number", None)
        else:
            group_num = _group_number_from_slug(getattr(latest_app.form, "slug", ""))
        if group_num:
            target_groups.add(group_num)

    latest_by_group_track, latest_by_group, rows_by_file_id = _build_grading_lookup(target_groups)

    profiles = []
    for root, app_ids in clusters.items():
        latest_payload = app_data_by_id[latest_app_id_by_cluster[root]]
        app = latest_payload["app"]
        answer_map = latest_payload["answer_map"]

        group_num = getattr(getattr(app, "form", None), "group_id", None)
        if group_num:
            group_num = getattr(app.form.group, "number", None)
        else:
            group_num = _group_number_from_slug(getattr(app.form, "slug", ""))
        track = _track_from_slug(getattr(app.form, "slug", ""))

        cluster_tokens: set[str] = set()
        cluster_id_values: list[str] = []
        cluster_email_values: list[str] = []
        for app_id in app_ids:
            payload = app_data_by_id[app_id]
            cluster_tokens.update(payload["tokens"])
            if payload["id_display"]:
                cluster_id_values.append(payload["id_display"])
            if payload["email_display"]:
                cluster_email_values.append(payload["email_display"])

        grade_file = None
        if group_num and track:
            grade_file = latest_by_group_track.get((group_num, track))
        if not grade_file and group_num:
            grade_file = latest_by_group.get(group_num)

        grade_row = {}
        if grade_file:
            token_map = rows_by_file_id.get(grade_file.id, {})
            for token in cluster_tokens:
                row = token_map.get(token)
                if not row:
                    continue
                if not grade_row or row["created_marker"] > grade_row["created_marker"]:
                    grade_row = row
            if not grade_row and group_num:
                fallback_file = latest_by_group.get(group_num)
                if fallback_file and fallback_file.id != grade_file.id:
                    token_map = rows_by_file_id.get(fallback_file.id, {})
                    for token in cluster_tokens:
                        row = token_map.get(token)
                        if not row:
                            continue
                        if not grade_row or row["created_marker"] > grade_row["created_marker"]:
                            grade_row = row
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
            latest_payload["id_display"]
            or (cluster_id_values[0] if cluster_id_values else "")
            or (grade_row.get("identity_raw") or "").strip()
            or latest_payload["email_display"]
            or (cluster_email_values[0] if cluster_email_values else "")
            or "—"
        )
        display_email = (
            latest_payload["email_display"]
            or (cluster_email_values[0] if cluster_email_values else "")
            or (grade_row.get("email_raw") or "").strip()
            or "—"
        )

        identity_norm = (
            latest_payload["id_norm"]
            or _normalize_identity(cluster_id_values[0] if cluster_id_values else "")
            or _normalize_identity(grade_row.get("identity_raw"))
        )
        email_norm = (
            latest_payload["email_norm"]
            or _normalize_email(cluster_email_values[0] if cluster_email_values else "")
            or _normalize_email(grade_row.get("email_raw"))
        )
        profile_key = _build_profile_key(identity_norm, email_norm, app.id)

        overview_rows = []
        for slug, label in PROFILE_OVERVIEW_FIELDS:
            value = answer_map.get(slug, "")
            if value:
                overview_rows.append({"label": label, "value": value})

        profile = {
            "profile_key": profile_key,
            "identity_key": identity_norm or email_norm or profile_key,
            "identity_display": display_identity,
            "applicant_name": answer_map.get("full_name") or app.name or "—",
            "email": display_email,
            "group_num": group_num,
            "track": track or "—",
            "form_slug": getattr(app.form, "slug", "—"),
            "form_name": getattr(app.form, "name", "—"),
            "applied_at": app.created_at,
            "application_id": app.id,
            "application_count": len(app_ids),
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
                str(profile["identity_key"]),
                str(profile["applicant_name"]),
                str(profile["email"]),
                str(profile["form_slug"]),
                str(profile["group_num"] or ""),
                str(profile["calificacion_status"]),
                " ".join(cluster_email_values),
                " ".join(cluster_id_values),
            ]
        ).lower()
        profiles.append(profile)

    profiles.sort(key=lambda p: (p["applied_at"], p["application_id"]), reverse=True)
    return profiles


@staff_member_required
def profiles_list(request):
    if request.method == "POST":
        raw_emails = (request.POST.get("emails") or "").strip()
        participated_raw = (request.POST.get("participated") or "yes").strip().lower()
        participated_flag = participated_raw in {"yes", "true", "1", "y"}
        valid_emails, invalid_emails = _parse_email_list(raw_emails)

        if not valid_emails:
            messages.error(request, "Enter at least one valid email.")
        else:
            created_count = 0
            updated_count = 0
            unchanged_count = 0
            for email in valid_emails:
                obj, created = ParticipantEmailStatus.objects.get_or_create(
                    email=email,
                    defaults={"participated": participated_flag},
                )
                if created:
                    created_count += 1
                    continue
                if obj.participated != participated_flag:
                    obj.participated = participated_flag
                    obj.save(update_fields=["participated", "updated_at"])
                    updated_count += 1
                else:
                    unchanged_count += 1

            state_label = "Yes" if participated_flag else "No"
            messages.success(
                request,
                (
                    f"Participation updated to {state_label}: "
                    f"{created_count} new, {updated_count} changed, {unchanged_count} unchanged."
                ),
            )

        if invalid_emails:
            preview = ", ".join(invalid_emails[:8])
            extra = "" if len(invalid_emails) <= 8 else ", ..."
            messages.warning(
                request,
                f"Ignored invalid emails ({len(invalid_emails)}): {preview}{extra}",
            )

        redirect_url = reverse("admin_profiles_list")
        query_string = (request.META.get("QUERY_STRING") or "").strip()
        if query_string:
            redirect_url = f"{redirect_url}?{query_string}"
        return redirect(redirect_url)

    profiles = _build_profiles()
    participation_map = {
        _email_status_key(row.email): row.participated
        for row in ParticipantEmailStatus.objects.only("email", "participated")
    }
    for profile in profiles:
        profile_email_key = _email_status_key(profile.get("email"))
        profile["participated"] = bool(participation_map.get(profile_email_key, False))

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
        "participated_profiles": sum(1 for p in profiles if p["participated"]),
        "bulk_participation_default": "yes",
        "bulk_email_text": "",
    }
    return render(request, "admin_dash/profiles_list.html", context)


@staff_member_required
def profile_detail(request, identity_key: str):
    requested_key = _normalize_profile_key(identity_key)
    if not requested_key:
        return render(request, "admin_dash/profile_detail.html", {"profile": None})

    profiles = _build_profiles()
    profile = next((p for p in profiles if p["profile_key"] == requested_key), None)
    if profile:
        email_key = _email_status_key(profile.get("email"))
        participation_value = None
        if email_key:
            participation_value = (
                ParticipantEmailStatus.objects.filter(email=email_key)
                .values_list("participated", flat=True)
                .first()
            )
        profile["participated"] = bool(participation_value) if participation_value is not None else False
    return render(request, "admin_dash/profile_detail.html", {"profile": profile})
