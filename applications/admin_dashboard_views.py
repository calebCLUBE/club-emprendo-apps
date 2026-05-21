import re
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Avg, Count, Q
from django.db.models.functions import TruncDay, TruncMonth, TruncWeek
from django.shortcuts import render
from django.utils import timezone

from .admin_views import _load_database_encuestas_grid
from .models import Application, FormGroup

GROUP_SLUG_RE = re.compile(r"^G(?P<num>\d+)_")
IMPACT_EMAIL_HEADERS = {"email", "correo", "correoelectronico", "mail"}
IMPACT_EMAIL_TOKENS = ("email", "correo")
IMPACT_TIMESTAMP_HEADERS = {
    "timestamp",
    "marcatemporal",
    "fecha",
    "fechadeenvio",
    "submittedat",
    "submissiondate",
    "createdat",
    "date",
}
IMPACT_METADATA_HEADERS = {
    "timestamp",
    "marcatemporal",
    "fecha",
    "fechadeenvio",
    "submittedat",
    "submissiondate",
    "createdat",
    "date",
    "email",
    "correo",
    "correoelectronico",
    "mail",
    "name",
    "nombre",
    "full_name",
    "fullname",
    "id",
    "applicationid",
    "applicacionid",
    "telefono",
    "phone",
    "whatsapp",
}


def _parse_iso_date(raw: str | None) -> date | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _safe_int(raw: str | None, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(raw or "")
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _is_truthy(raw: str | None) -> bool:
    return (raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _track_from_slug(slug: str) -> str:
    s = (slug or "").upper()
    if "E_A1" in s or "E_A2" in s:
        return "E"
    if "M_A1" in s or "M_A2" in s:
        return "M"
    return "Other"


def _group_number_from_slug(slug: str) -> int | None:
    match = GROUP_SLUG_RE.match((slug or "").strip().upper())
    if not match:
        return None
    try:
        return int(match.group("num"))
    except (TypeError, ValueError):
        return None


def _pct(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((part / total) * 100, 1)


def _normalized_header_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _non_empty_rows(rows: list[list[str]]) -> list[list[str]]:
    out: list[list[str]] = []
    for row in rows:
        if any(str(cell or "").strip() for cell in row):
            out.append(row)
    return out


def _safe_row_value(row: list[str], index: int | None) -> str:
    if index is None:
        return ""
    if index < 0 or index >= len(row):
        return ""
    return str(row[index] or "").strip()


def _find_header_index(
    headers: list[str],
    exact_keys: set[str],
    contains_tokens: tuple[str, ...] = (),
) -> int | None:
    for idx, header in enumerate(headers):
        normalized = _normalized_header_key(header)
        if normalized in exact_keys:
            return idx
        if contains_tokens and any(token in normalized for token in contains_tokens):
            return idx
    return None


def _extract_unique_emails(rows: list[list[str]], email_index: int | None) -> set[str]:
    emails: set[str] = set()
    if email_index is None:
        return emails
    for row in rows:
        raw = _safe_row_value(row, email_index).lower().strip("<>[](){}\"'").rstrip(".,;:")
        if "@" not in raw:
            continue
        emails.add(raw)
    return emails


def _build_question_completion(
    headers: list[str],
    rows: list[list[str]],
    metadata_indices: set[int],
) -> list[dict]:
    total_rows = len(rows)
    if total_rows <= 0:
        return []

    completion_rows: list[dict] = []
    for idx, header in enumerate(headers):
        if idx in metadata_indices:
            continue
        label = str(header or "").strip()
        if not label:
            continue
        answered = 0
        for row in rows:
            if _safe_row_value(row, idx):
                answered += 1
        completion_rows.append(
            {
                "label": label,
                "answered": answered,
                "pct": _pct(answered, total_rows),
            }
        )
    completion_rows.sort(key=lambda item: (-item["pct"], -item["answered"], item["label"]))
    return completion_rows


def _build_impact_dataset(kind: str, title: str, sheet_url_name: str) -> tuple[dict, set[str]]:
    label, headers, raw_rows, file_name, file_id = _load_database_encuestas_grid(kind)
    rows = _non_empty_rows(raw_rows)
    response_count = len(rows)

    email_index = _find_header_index(headers, IMPACT_EMAIL_HEADERS, IMPACT_EMAIL_TOKENS)
    unique_emails = _extract_unique_emails(rows, email_index)
    timestamp_index = _find_header_index(headers, IMPACT_TIMESTAMP_HEADERS)

    metadata_indices: set[int] = set()
    for idx, header in enumerate(headers):
        normalized = _normalized_header_key(header)
        if normalized in IMPACT_METADATA_HEADERS:
            metadata_indices.add(idx)
    if email_index is not None:
        metadata_indices.add(email_index)
    if timestamp_index is not None:
        metadata_indices.add(timestamp_index)

    completion_rows = _build_question_completion(headers, rows, metadata_indices)

    dataset = {
        "kind": kind,
        "title": title,
        "label": label,
        "sheet_url_name": sheet_url_name,
        "source_name": file_name,
        "source_file_id": file_id,
        "responses_count": response_count,
        "headers_count": len(headers),
        "question_count": len(completion_rows),
        "unique_emails_count": len(unique_emails),
        "email_column_label": headers[email_index] if email_index is not None else "",
        "completion_rows": completion_rows[:10],
    }
    return dataset, unique_emails


def _track_impact_summary(track_label: str, initial_dataset: dict, final_dataset: dict, initial_emails: set[str], final_emails: set[str]) -> dict:
    initial_responses = int(initial_dataset.get("responses_count") or 0)
    final_responses = int(final_dataset.get("responses_count") or 0)
    initial_unique = int(initial_dataset.get("unique_emails_count") or 0)
    final_unique = int(final_dataset.get("unique_emails_count") or 0)

    matched = len(initial_emails & final_emails)
    missing = max(len(initial_emails) - matched, 0)

    return {
        "label": track_label,
        "initial_responses": initial_responses,
        "final_responses": final_responses,
        "response_growth": final_responses - initial_responses,
        "final_vs_initial_pct": _pct(final_responses, initial_responses),
        "initial_unique": initial_unique,
        "final_unique": final_unique,
        "matched_unique": matched,
        "missing_from_final_unique": missing,
        "retention_pct": _pct(matched, len(initial_emails)),
    }


@staff_member_required
def dashboards_home(request):
    return render(request, "admin_dash/dashboards_home.html")


@staff_member_required
def impact_dashboard(request):
    datasets: dict[str, dict] = {}
    email_sets: dict[str, set[str]] = {}
    sections = [
        ("emprendedoras", "Encuesta inicial - Emprendedoras", "admin_database_encuestas_sheet"),
        ("emprendedoras_final", "Encuesta final - Emprendedoras", "admin_database_encuestas_final_sheet"),
        ("mentoras", "Encuesta inicial - Mentoras", "admin_database_encuestas_mentoras_sheet"),
        ("mentoras_final", "Encuesta final - Mentoras", "admin_database_encuestas_mentoras_final_sheet"),
    ]

    for kind, title, sheet_url_name in sections:
        try:
            dataset, email_set = _build_impact_dataset(kind, title, sheet_url_name)
        except Exception as exc:
            dataset = {
                "kind": kind,
                "title": title,
                "sheet_url_name": sheet_url_name,
                "error": str(exc),
                "responses_count": 0,
                "unique_emails_count": 0,
                "question_count": 0,
                "completion_rows": [],
            }
            email_set = set()
            messages.error(request, f"Could not load {title}: {exc}")
        datasets[kind] = dataset
        email_sets[kind] = email_set

    emprendedoras_summary = _track_impact_summary(
        "Emprendedoras",
        datasets["emprendedoras"],
        datasets["emprendedoras_final"],
        email_sets["emprendedoras"],
        email_sets["emprendedoras_final"],
    )
    mentoras_summary = _track_impact_summary(
        "Mentoras",
        datasets["mentoras"],
        datasets["mentoras_final"],
        email_sets["mentoras"],
        email_sets["mentoras_final"],
    )

    initial_union = email_sets["emprendedoras"] | email_sets["mentoras"]
    final_union = email_sets["emprendedoras_final"] | email_sets["mentoras_final"]
    overall_summary = {
        "initial_responses": datasets["emprendedoras"]["responses_count"] + datasets["mentoras"]["responses_count"],
        "final_responses": datasets["emprendedoras_final"]["responses_count"] + datasets["mentoras_final"]["responses_count"],
        "initial_unique": len(initial_union),
        "final_unique": len(final_union),
        "matched_unique": len(initial_union & final_union),
    }
    overall_summary["response_growth"] = overall_summary["final_responses"] - overall_summary["initial_responses"]
    overall_summary["final_vs_initial_pct"] = _pct(overall_summary["final_responses"], overall_summary["initial_responses"])
    overall_summary["retention_pct"] = _pct(overall_summary["matched_unique"], overall_summary["initial_unique"])

    context = {
        "overall": overall_summary,
        "track_summaries": [emprendedoras_summary, mentoras_summary],
        "datasets": datasets,
    }
    return render(request, "admin_dash/impact_dashboard.html", context)


@staff_member_required
def applications_dashboard(request):
    base_qs = Application.objects.select_related("form", "form__group")

    date_from = _parse_iso_date(request.GET.get("date_from"))
    date_to = _parse_iso_date(request.GET.get("date_to"))
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from

    group_filter = (request.GET.get("group") or "").strip()
    track_filter = (request.GET.get("track") or "all").strip().lower()
    if track_filter not in {"all", "e", "m"}:
        track_filter = "all"

    granularity = (request.GET.get("granularity") or "month").strip().lower()
    if granularity not in {"day", "week", "month"}:
        granularity = "month"

    top_n = _safe_int(request.GET.get("top_n"), default=8, minimum=3, maximum=30)
    recent_n = _safe_int(request.GET.get("recent_n"), default=25, minimum=5, maximum=200)

    customize_mode = _is_truthy(request.GET.get("customize"))
    show_cards = _is_truthy(request.GET.get("show_cards")) if customize_mode else True
    show_timeline = _is_truthy(request.GET.get("show_timeline")) if customize_mode else True
    show_pie_charts = _is_truthy(request.GET.get("show_pie_charts")) if customize_mode else True
    show_form_chart = _is_truthy(request.GET.get("show_form_chart")) if customize_mode else True
    show_form_table = _is_truthy(request.GET.get("show_form_table")) if customize_mode else True
    show_group_table = _is_truthy(request.GET.get("show_group_table")) if customize_mode else True
    show_recent_table = _is_truthy(request.GET.get("show_recent_table")) if customize_mode else True

    filtered_qs = base_qs
    if date_from:
        filtered_qs = filtered_qs.filter(created_at__date__gte=date_from)
    if date_to:
        filtered_qs = filtered_qs.filter(created_at__date__lte=date_to)

    if track_filter == "e":
        filtered_qs = filtered_qs.filter(Q(form__slug__contains="E_A1") | Q(form__slug__contains="E_A2"))
    elif track_filter == "m":
        filtered_qs = filtered_qs.filter(Q(form__slug__contains="M_A1") | Q(form__slug__contains="M_A2"))

    pre_group_qs = filtered_qs
    if group_filter == "ungrouped":
        filtered_qs = filtered_qs.filter(form__group__isnull=True)
    elif group_filter.isdigit():
        filtered_qs = filtered_qs.filter(form__group__number=int(group_filter))

    group_rows = (
        pre_group_qs.values("form__group__number")
        .annotate(total=Count("id"))
        .order_by("form__group__number")
    )
    group_options = []
    for row in group_rows:
        group_num = row["form__group__number"]
        total = row["total"] or 0
        if group_num is None:
            group_options.append(
                {"value": "ungrouped", "label": f"Ungrouped ({total})"}
            )
        else:
            group_options.append(
                {"value": str(group_num), "label": f"Group {group_num} ({total})"}
            )
    if not group_options:
        for group_num in FormGroup.objects.order_by("number").values_list("number", flat=True):
            group_options.append({"value": str(group_num), "label": f"Group {group_num} (0)"})

    summary = filtered_qs.aggregate(
        total=Count("id"),
        unique_emails=Count("email", distinct=True),
        invited=Count("id", filter=Q(invited_to_second_stage=True)),
        avg_overall=Avg("overall_score", filter=Q(overall_score__gt=0)),
    )
    total_apps = summary["total"] or 0
    invited = summary["invited"] or 0
    unique_emails = summary["unique_emails"] or 0
    avg_overall = summary["avg_overall"]

    a2_q = Q(form__slug__contains="A2")
    a2_total = filtered_qs.filter(a2_q).count()
    a2_graded = (
        filtered_qs.filter(a2_q)
        .exclude(Q(recommendation__isnull=True) | Q(recommendation=""))
        .count()
    )
    recent_30_days = filtered_qs.filter(
        created_at__date__gte=timezone.localdate() - timedelta(days=29)
    ).count()

    form_rows_qs = (
        filtered_qs.values("form__slug", "form__name", "form__group__number")
        .annotate(
            total=Count("id"),
            invited=Count("id", filter=Q(invited_to_second_stage=True)),
            avg_overall=Avg("overall_score", filter=Q(overall_score__gt=0)),
        )
        .order_by("-total", "form__slug")
    )

    form_rows = []
    for row in form_rows_qs:
        slug = row["form__slug"] or "—"
        group_num = row["form__group__number"] or _group_number_from_slug(slug)
        form_rows.append(
            {
                "slug": slug,
                "name": row["form__name"] or slug,
                "group_num": group_num,
                "track": _track_from_slug(slug),
                "total": row["total"] or 0,
                "invited": row["invited"] or 0,
                "avg_overall": row["avg_overall"],
            }
        )

    top_forms = form_rows[:top_n]
    max_form_total = max([r["total"] for r in top_forms], default=1)
    form_chart_points = [
        {
            "label": row["slug"],
            "count": row["total"],
            "pct": round((row["total"] / max_form_total) * 100, 1) if max_form_total else 0.0,
        }
        for row in top_forms
    ]

    track_totals = {"E": 0, "M": 0, "Other": 0}
    for row in form_rows:
        track = row["track"]
        if track not in track_totals:
            track = "Other"
        track_totals[track] += row["total"] or 0

    track_mix = [
        {"label": "Emprendedoras (E)", "value": track_totals["E"], "color": "#3B82F6"},
        {"label": "Mentoras (M)", "value": track_totals["M"], "color": "#22C55E"},
        {"label": "Other", "value": track_totals["Other"], "color": "#F59E0B"},
    ]

    stage_a1_total = filtered_qs.filter(form__slug__contains="A1").count()
    stage_a2_total = filtered_qs.filter(form__slug__contains="A2").count()
    stage_other_total = max(total_apps - stage_a1_total - stage_a2_total, 0)
    stage_mix = [
        {"label": "A1", "value": stage_a1_total, "color": "#8B5CF6"},
        {"label": "A2", "value": stage_a2_total, "color": "#14B8A6"},
        {"label": "Other", "value": stage_other_total, "color": "#F97316"},
    ]

    trunc_map = {
        "day": TruncDay,
        "week": TruncWeek,
        "month": TruncMonth,
    }
    timeline_rows = (
        filtered_qs.annotate(period=trunc_map[granularity]("created_at"))
        .values("period")
        .annotate(total=Count("id"))
        .order_by("period")
    )
    max_timeline_total = max([row["total"] for row in timeline_rows], default=1)
    timeline_points = []
    for row in timeline_rows:
        period = row["period"]
        if granularity == "month":
            label = period.strftime("%Y-%m")
        else:
            label = period.strftime("%Y-%m-%d")
        total = row["total"] or 0
        timeline_points.append(
            {
                "label": label,
                "count": total,
                "pct": round((total / max_timeline_total) * 100, 1) if max_timeline_total else 0.0,
            }
        )

    grouped = {}
    grouped_rows = (
        filtered_qs.values("form__slug", "form__group__number")
        .annotate(total=Count("id"))
        .order_by("-total")
    )
    for row in grouped_rows:
        slug = row["form__slug"] or ""
        group_num = row["form__group__number"] or _group_number_from_slug(slug)
        key = f"Group {group_num}" if group_num else "Ungrouped"
        if key not in grouped:
            grouped[key] = {
                "group_label": key,
                "total": 0,
                "e_total": 0,
                "m_total": 0,
                "other_total": 0,
            }
        grouped[key]["total"] += row["total"] or 0
        track = _track_from_slug(slug)
        if track == "E":
            grouped[key]["e_total"] += row["total"] or 0
        elif track == "M":
            grouped[key]["m_total"] += row["total"] or 0
        else:
            grouped[key]["other_total"] += row["total"] or 0

    def _group_sort_key(item):
        label = item["group_label"]
        if label == "Ungrouped":
            return (1_000_000,)
        try:
            return (int(label.replace("Group ", "").strip()),)
        except ValueError:
            return (999_999,)

    group_summary_rows = sorted(grouped.values(), key=_group_sort_key)

    recent_apps = (
        filtered_qs.select_related("form")
        .order_by("-created_at", "-id")[:recent_n]
    )

    context = {
        "date_from": date_from.isoformat() if date_from else "",
        "date_to": date_to.isoformat() if date_to else "",
        "group_filter": group_filter,
        "track_filter": track_filter,
        "granularity": granularity,
        "top_n": top_n,
        "recent_n": recent_n,
        "group_options": group_options,
        "show_cards": show_cards,
        "show_timeline": show_timeline,
        "show_pie_charts": show_pie_charts,
        "show_form_chart": show_form_chart,
        "show_form_table": show_form_table,
        "show_group_table": show_group_table,
        "show_recent_table": show_recent_table,
        "total_apps": total_apps,
        "unique_emails": unique_emails,
        "invited": invited,
        "invited_pct": _pct(invited, total_apps),
        "a2_total": a2_total,
        "a2_graded": a2_graded,
        "a2_graded_pct": _pct(a2_graded, a2_total),
        "recent_30_days": recent_30_days,
        "avg_overall": avg_overall,
        "timeline_points": timeline_points,
        "form_chart_points": form_chart_points,
        "track_mix": track_mix,
        "stage_mix": stage_mix,
        "form_rows": form_rows,
        "group_summary_rows": group_summary_rows,
        "recent_apps": recent_apps,
    }
    return render(request, "admin_dash/applications_dashboard.html", context)
