# applications/admin_views.py

import csv
import io
import re
from typing import List, Tuple
from urllib.parse import urlparse
from django.core.mail import get_connection

from applications.grading import grade_from_answers

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.files.storage import default_storage
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db import transaction
from django.db.models import Model, Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from applications.models import (
    Application,
    Answer,
    Choice,
    FormDefinition,
    FormGroup,
    Question,
)

MASTER_SLUGS = ["E_A1", "E_A2", "M_A1", "M_A2"]
GROUP_SLUG_RE = re.compile(r"^G(?P<num>\d+)_(?P<master>E_A1|E_A2|M_A1|M_A2)$")
TEST_E_A2_SLUG = "TEST_E_A2"
TEST_M_A2_SLUG = "TEST_M_A2"
A2_FORM_RE = re.compile(r"^(G\d+_)?(E_A2|M_A2)$")
TEST_A2_FORM_RE = re.compile(r"^TEST_(E_A2|M_A2)$")

@staff_member_required
@require_POST
def grading_upload_test_csv(request):
    """
    Upload CSV into sandbox A2 forms only:
      - role=E -> TEST_E_A2
      - role=M -> TEST_M_A2

    This is independent from real group applications.
    """
    role = (request.POST.get("role") or "E").strip().upper()
    sandbox_slug = "TEST_E_A2" if role == "E" else "TEST_M_A2"

    fd = FormDefinition.objects.filter(slug=sandbox_slug).first()
    if not fd:
        messages.error(
            request,
            f"Sandbox form {sandbox_slug} does not exist. Create it (or clone it) first."
        )
        return _redirect_back_to_grading(request)

    f = request.FILES.get("csv_file")
    if not f:
        messages.error(request, "No CSV file uploaded.")
        return _redirect_back_to_grading(request)

    try:
        raw = f.read().decode("utf-8-sig")
    except Exception:
        raw = f.read().decode("latin-1")

    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames:
        messages.error(request, "CSV appears to have no header row.")
        return _redirect_back_to_grading(request)

    qmap = {q.slug: q for q in fd.questions.all()}
    created = 0

    with transaction.atomic():
        for row in reader:
            name = (row.get("name") or row.get("full_name") or "").strip()
            email = (row.get("email") or "").strip()

            app = Application.objects.create(
                form=fd,
                name=name,
                email=email,
            )

            for col, val in row.items():
                if col not in qmap:
                    continue
                v = (val or "").strip()
                Answer.objects.create(
                    application=app,
                    question=qmap[col],
                    value=v,
                )
            created += 1

    messages.success(request, f"Imported {created} submissions into sandbox form {sandbox_slug}.")
    return _redirect_back_to_grading(request)


def _redirect_back_to_grading(request):
    group = (request.GET.get("group") or "").strip()
    url = reverse("admin_grading_home")
    if group:
        url = f"{url}?group={group}"
    return redirect(url)
# ----------------------------
# Toggle (accepting submissions)
# ----------------------------
@staff_member_required
@require_POST
def toggle_form_accepting(request, form_slug: str):
    """
    Toggle whether a form accepts new submissions.
    """
    fd = get_object_or_404(FormDefinition, slug=form_slug)
    fd.accepting_responses = not fd.accepting_responses
    fd.save(update_fields=["accepting_responses"])

    state = "OPEN" if fd.accepting_responses else "CLOSED"
    messages.success(request, f"{fd.slug} is now {state} for new submissions.")
    return redirect("admin_apps_list")


# ----------------------------
# Forms
# ----------------------------
class CreateGroupForm(forms.Form):
    group_num = forms.IntegerField(min_value=1, label="Group number")
    start_month = forms.CharField(max_length=30, label="Start month")
    end_month = forms.CharField(max_length=30, label="End month")
    year = forms.IntegerField(min_value=2020, max_value=2100, label="Year")


# ----------------------------
# Helpers
# ----------------------------
def _fill_placeholders(
    text: str | None, group_num: int, start_month: str, end_month: str, year: int
) -> str | None:
    if not text:
        return text

    out = text.replace("#(group number)", str(group_num))

    if "#(month)" in out:
        out = out.replace("#(month)", start_month, 1)
    if "#(month)" in out:
        out = out.replace("#(month)", end_month, 1)

    out = out.replace("#(year)", str(year))
    return out


def _model_has_field(model: type[Model], field_name: str) -> bool:
    try:
        model._meta.get_field(field_name)
        return True
    except Exception:
        return False

def _ensure_test_a2_form(role: str) -> FormDefinition:
    """
    role: "E" or "M"
    Creates TEST_E_A2 or TEST_M_A2 if missing by cloning from the master E_A2 / M_A2.
    """
    if role not in ("E", "M"):
        raise ValueError("role must be 'E' or 'M'")

    test_slug = TEST_E_A2_SLUG if role == "E" else TEST_M_A2_SLUG
    master_slug = "E_A2" if role == "E" else "M_A2"

    existing = FormDefinition.objects.filter(slug=test_slug).first()
    if existing:
        return existing

    master_fd = get_object_or_404(FormDefinition, slug=master_slug)

    # Create new FormDefinition
    clone = FormDefinition.objects.create(
        slug=test_slug,
        name=f"TEST — {master_fd.name}",
        description=(master_fd.description or "") + "\n\n(Imported test CSV submissions.)",
        is_master=False,
        group=None,
        is_public=False,             # keep it out of public view
        accepting_responses=False,   # prevent real submissions
    )

    # Clone questions + choices
    for q in master_fd.questions.all().order_by("position", "id"):
        q_clone = Question.objects.create(
            form=clone,
            text=q.text,
            help_text=q.help_text,
            field_type=q.field_type,
            required=q.required,
            position=q.position,
            slug=q.slug,
            active=q.active,
        )
        for c in q.choices.all().order_by("position", "id"):
            Choice.objects.create(
                question=q_clone,
                label=c.label,
                value=c.value,
                position=c.position,
            )

    return clone

def _clone_form(master_fd: FormDefinition, group: FormGroup) -> FormDefinition:
    group_num = group.number
    start_month = group.start_month
    end_month = group.end_month
    year = group.year

    new_slug = f"G{group_num}_{master_fd.slug}"
    new_name = f"Grupo {group_num} — {master_fd.name}"

    existing = FormDefinition.objects.filter(slug=new_slug).first()
    if existing:
        return existing

    clone = FormDefinition.objects.create(
        slug=new_slug,
        name=new_name,
        description=_fill_placeholders(
            master_fd.description, group_num, start_month, end_month, year
        )
        or "",
        is_master=False,
        group=group,
        is_public=master_fd.is_public,
    )

    for q in master_fd.questions.all().order_by("position", "id"):
        q_clone = Question.objects.create(
            form=clone,
            text=_fill_placeholders(q.text, group_num, start_month, end_month, year) or q.text,
            help_text=_fill_placeholders(q.help_text, group_num, start_month, end_month, year) or q.help_text,
            field_type=q.field_type,
            required=q.required,
            position=q.position,
            slug=q.slug,  # IMPORTANT: stable
            active=q.active,
        )
        for c in q.choices.all().order_by("position", "id"):
            Choice.objects.create(
                question=q_clone,
                label=_fill_placeholders(c.label, group_num, start_month, end_month, year) or c.label,
                value=c.value,
                position=c.position,
            )

    return clone


def _build_csv_for_form(form_def: FormDefinition) -> Tuple[List[str], List[List[str]]]:
    apps = (
        Application.objects.filter(form=form_def)
        .prefetch_related("answers__question")
        .order_by("created_at", "id")
    )

    questions = list(form_def.questions.filter(active=True).order_by("position", "id"))
    headers = ["created_at", "application_id", "name", "email"] + [q.slug for q in questions]

    rows: List[List[str]] = []
    for app in apps:
        amap = {a.question.slug: (a.value or "") for a in app.answers.all()}
        row = [
            app.created_at.isoformat(),
            str(app.id),
            app.name,
            app.email,
        ] + [amap.get(q.slug, "") for q in questions]
        rows.append(row)

    return headers, rows


def _csv_http_response(filename: str, headers: List[str], rows: List[List[str]]) -> HttpResponse:
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    w = csv.writer(resp)
    w.writerow(headers)
    w.writerows(rows)
    return resp


def _csv_preview_html(headers: List[str], rows: List[List[str]], max_rows: int = 25) -> str:
    def esc(s: str) -> str:
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    preview = rows[:max_rows]

    ths = "".join(
        f"<th style='text-align:left;padding:6px;border-bottom:1px solid #ddd;'>{esc(h)}</th>"
        for h in headers
    )

    body = []
    for r in preview:
        tds = "".join(
            f"<td style='padding:6px;border-bottom:1px solid #eee;vertical-align:top;'>{esc(str(v))}</td>"
            for v in r
        )
        body.append(f"<tr>{tds}</tr>")

    return (
        "<div style='overflow:auto;border:1px solid #ddd;border-radius:8px;'>"
        "<table style='border-collapse:collapse;width:100%;font-size:13px;'>"
        f"<thead><tr>{ths}</tr></thead>"
        f"<tbody>{''.join(body) if body else '<tr><td style=\"padding:8px;\">No submissions yet.</td></tr>'}</tbody>"
        "</table></div>"
        f"<p style='margin-top:8px;color:#666;font-size:12px;'>Showing up to {max_rows} rows.</p>"
    )


# ----------------------------
# Toggle (open/closed) for display — your "toggle-form" URL
# ----------------------------
@staff_member_required
@require_POST
def toggle_form_open(request, form_slug: str):
    """
    Toggle whether a form accepts new submissions.
    Uses FormDefinition.is_public as the "open" flag.
    - True  => open
    - False => closed (no new applications)
    """
    fd = get_object_or_404(FormDefinition, slug=form_slug)

    fd.is_public = not bool(fd.is_public)
    fd.save(update_fields=["is_public"])

    if fd.is_public:
        messages.success(request, f"{fd.slug} is now OPEN (accepting new submissions).")
    else:
        messages.warning(request, f"{fd.slug} is now CLOSED (no new submissions will be accepted).")

    return redirect("admin_apps_list")


def _soft_archive_group(group: FormGroup) -> None:
    FormDefinition.objects.filter(group=group).update(is_public=False)
    if _model_has_field(FormGroup, "is_active"):
        FormGroup.objects.filter(id=group.id).update(is_active=False)


def _extract_storage_path_from_value(value: str) -> str | None:
    if not value:
        return None

    v = value.strip()

    if v.startswith("http://") or v.startswith("https://"):
        parsed = urlparse(v)
        v = parsed.path

    if v.startswith("/media/"):
        v = v[len("/media/"):]

    v = v.lstrip("/")
    return v or None


def _looks_like_file_value(value: str) -> bool:
    if not value:
        return False

    s = value.strip().lower()
    if not s:
        return False

    if s.startswith("http://") or s.startswith("https://"):
        return True
    if s.startswith("/media/") or s.startswith("media/"):
        return True
    if "uploads/" in s:
        return True

    exts = (
        ".pdf", ".png", ".jpg", ".jpeg", ".webp",
        ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".txt", ".csv",
    )
    return s.endswith(exts)


# ----------------------------
# Admin "Apps" dashboard
# ----------------------------
@staff_member_required
def apps_list(request):
    masters = list(FormDefinition.objects.filter(slug__in=MASTER_SLUGS).order_by("slug"))

    groups = list(FormGroup.objects.order_by("number"))
    group_list = []
    for g in groups:
        forms_for_group = list(FormDefinition.objects.filter(group=g).order_by("slug"))
        group_list.append((g, forms_for_group))

    return render(
        request,
        "admin_dash/apps_list.html",
        {
            "masters": masters,
            "create_group_form": CreateGroupForm(),
            "group_list": group_list,
        },
    )


@staff_member_required
@require_POST
def create_group(request):
    form = CreateGroupForm(request.POST)
    if not form.is_valid():
        masters = list(FormDefinition.objects.filter(slug__in=MASTER_SLUGS).order_by("slug"))
        groups = list(FormGroup.objects.order_by("number"))
        group_list = []
        for g in groups:
            forms_for_group = list(FormDefinition.objects.filter(group=g).order_by("slug"))
            group_list.append((g, forms_for_group))

        return render(
            request,
            "admin_dash/apps_list.html",
            {
                "masters": masters,
                "create_group_form": form,
                "group_list": group_list,
            },
        )

    group_num = form.cleaned_data["group_num"]
    start_month = form.cleaned_data["start_month"]
    end_month = form.cleaned_data["end_month"]
    year = form.cleaned_data["year"]

    with transaction.atomic():
        group, _created = FormGroup.objects.get_or_create(
            number=group_num,
            defaults={"start_month": start_month, "end_month": end_month, "year": year},
        )
        group.start_month = start_month
        group.end_month = end_month
        group.year = year
        group.save()

        masters = FormDefinition.objects.filter(slug__in=MASTER_SLUGS).order_by("slug")
        for master_fd in masters:
            _clone_form(master_fd, group)

    messages.success(request, f"Grupo {group_num} creado/actualizado y formularios clonados.")
    return redirect("admin_apps_list")


@staff_member_required
@require_POST
def delete_group(request, group_num: int):
    group = get_object_or_404(FormGroup, number=group_num)

    qs_apps = Application.objects.filter(form__group=group)
    has_apps = qs_apps.exists()
    force = request.POST.get("force") == "1"

    if has_apps and not force:
        _soft_archive_group(group)
        messages.warning(
            request,
            "Este grupo tiene postulaciones guardadas, así que no se puede eliminar. "
            "Lo archivamos (formularios ocultos) para proteger el historial. "
            "Si realmente quieres borrarlo todo, usa 'force delete'."
        )
        return redirect("admin_apps_list")

    with transaction.atomic():
        if has_apps and force:
            Answer.objects.filter(application__in=qs_apps).delete()
            qs_apps.delete()

        FormDefinition.objects.filter(group=group).delete()
        group.delete()

    if has_apps and force:
        messages.success(request, "Grupo eliminado PERMANENTEMENTE junto con todas las postulaciones.")
    else:
        messages.success(request, "Grupo eliminado correctamente.")

    return redirect("admin_apps_list")


# ----------------------------
# Database
# ----------------------------
@staff_member_required
def database_home(request):
    masters = list(FormDefinition.objects.filter(slug__in=MASTER_SLUGS).order_by("slug"))

    groups = list(FormGroup.objects.order_by("number"))
    group_blocks = []
    for g in groups:
        forms_for_group = list(FormDefinition.objects.filter(group=g).order_by("slug"))
        group_blocks.append((g, forms_for_group))

    SURVEY_SLUGS = ["PRIMER_E", "FINAL_E", "PRIMER_M", "FINAL_M"]
    surveys = list(FormDefinition.objects.filter(slug__in=SURVEY_SLUGS).order_by("slug"))
    surveys_e = [s for s in surveys if s.slug.endswith("_E")]
    surveys_m = [s for s in surveys if s.slug.endswith("_M")]

    counts = {
        row["form__slug"]: row["c"]
        for row in Application.objects.values("form__slug").annotate(c=Count("id"))
    }

    for fd in masters:
        fd.submission_count = counts.get(fd.slug, 0)
        fd.admin_edit_url = reverse("admin:applications_formdefinition_change", args=[fd.id])

    for _g, forms_for_group in group_blocks:
        for fd in forms_for_group:
            fd.submission_count = counts.get(fd.slug, 0)
            fd.admin_edit_url = reverse("admin:applications_formdefinition_change", args=[fd.id])

    for s in surveys:
        s.submission_count = counts.get(s.slug, 0)
        s.admin_edit_url = reverse("admin:applications_formdefinition_change", args=[s.id])

    return render(
        request,
        "admin_dash/database_home.html",
        {
            "masters": masters,
            "master_forms": masters,  # template compatibility
            "group_blocks": group_blocks,
            "surveys": surveys,
            "surveys_e": surveys_e,
            "surveys_m": surveys_m,
        },
    )


@staff_member_required
def database_form_detail(request, form_slug: str):
    form_def = get_object_or_404(FormDefinition, slug=form_slug)

    apps = (
        Application.objects.filter(form=form_def)
        .prefetch_related("answers__question", "form")
        .order_by("-created_at", "-id")
    )

    headers, rows = _build_csv_for_form(form_def)
    preview_html = _csv_preview_html(headers, rows, max_rows=25)

    return render(
        request,
        "admin_dash/database_form_detail.html",
        {
            "form_def": form_def,
            "apps": apps,
            "preview_html": preview_html,
        },
    )


@staff_member_required
def database_form_master_csv(request, form_slug: str):
    form_def = get_object_or_404(FormDefinition, slug=form_slug)
    headers, rows = _build_csv_for_form(form_def)
    return _csv_http_response(f"{form_slug}_MASTER.csv", headers, rows)


@staff_member_required
def database_submission_detail(request, app_id: int):
    app = get_object_or_404(
        Application.objects.select_related("form").prefetch_related("answers__question"),
        id=app_id,
    )

    questions = list(app.form.questions.filter(active=True).order_by("position", "id"))
    amap = {a.question.slug: a.value for a in app.answers.all()}
    ordered_answers = [(q, amap.get(q.slug, "")) for q in questions]

    return render(
        request,
        "admin_dash/database_submission_detail.html",
        {"app": app, "ordered_answers": ordered_answers},
    )


@staff_member_required
def export_form_csv(request, form_slug: str):
    form_def = get_object_or_404(FormDefinition, slug=form_slug)
    headers, rows = _build_csv_for_form(form_def)
    return _csv_http_response(f"{form_slug}.csv", headers, rows)


# ----------------------------
# Delete submission (REAL delete)
# ----------------------------
@staff_member_required
@require_POST
def delete_submission(request, app_id: int):
    app = get_object_or_404(Application.objects.select_related("form"), id=app_id)
    form_slug = app.form.slug

    answers = Answer.objects.filter(application=app)
    deleted_storage = 0

    for ans in answers:
        v = (ans.value or "").strip()
        if not v:
            continue
        if not _looks_like_file_value(v):
            continue

        storage_path = _extract_storage_path_from_value(v)
        if storage_path:
            try:
                if default_storage.exists(storage_path):
                    default_storage.delete(storage_path)
                    deleted_storage += 1
            except Exception:
                pass

    app.delete()

    msg = "Submission eliminada de la base de datos."
    if deleted_storage:
        msg += f" Archivos eliminados del storage: {deleted_storage}."
    messages.success(request, msg)

    return redirect("admin_database_form_detail", form_slug=form_slug)


# ----------------------------
# Delete file(s) actions (admin database)
# ----------------------------
@staff_member_required
@require_POST
def delete_answer_file_value(request, answer_id: int):
    ans = get_object_or_404(
        Answer.objects.select_related("application", "question"),
        id=answer_id,
    )

    if not _looks_like_file_value(ans.value or ""):
        messages.info(request, "Esta respuesta no parece ser un archivo. No se hizo ningún cambio.")
        return redirect("admin_database_submission_detail", app_id=ans.application_id)

    storage_path = _extract_storage_path_from_value(ans.value or "")

    deleted_from_storage = False
    storage_error = None

    if storage_path:
        try:
            if default_storage.exists(storage_path):
                default_storage.delete(storage_path)
                deleted_from_storage = True
        except Exception as e:
            storage_error = str(e)

    ans.value = ""
    ans.save(update_fields=["value"])

    if deleted_from_storage:
        messages.success(request, "Archivo eliminado del storage y referencia borrada.")
    else:
        msg = "Referencia borrada."
        if storage_path:
            msg += f" No se pudo borrar del storage (path: {storage_path})."
        else:
            msg += " No se pudo mapear el valor a un archivo del storage."
        if storage_error:
            msg += f" Error: {storage_error}"
        messages.warning(request, msg)

    return redirect("admin_database_submission_detail", app_id=ans.application_id)


@staff_member_required
@require_POST
def delete_application_files(request, app_id: int):
    app = get_object_or_404(Application, id=app_id)

    answers = Answer.objects.filter(application=app)

    cleared_count = 0
    deleted_storage_count = 0
    skipped_count = 0

    for ans in answers:
        v = (ans.value or "").strip()
        if not v:
            continue

        if not _looks_like_file_value(v):
            skipped_count += 1
            continue

        storage_path = _extract_storage_path_from_value(v)
        deleted_from_storage = False

        if storage_path:
            try:
                if default_storage.exists(storage_path):
                    default_storage.delete(storage_path)
                    deleted_from_storage = True
            except Exception:
                deleted_from_storage = False

        ans.value = ""
        ans.save(update_fields=["value"])
        cleared_count += 1
        if deleted_from_storage:
            deleted_storage_count += 1

    if cleared_count == 0:
        messages.info(
            request,
            f"No se encontraron archivos para borrar. (Se omitieron {skipped_count} respuestas que no eran archivos.)"
        )
    else:
        messages.success(
            request,
            f"Referencias de archivo borradas: {cleared_count} "
            f"(archivos eliminados del storage: {deleted_storage_count}). "
            f"Omitidas (no-archivo): {skipped_count}."
        )

    return redirect("admin_database_submission_detail", app_id=app.id)


# ============================================================
# Grading (Admin) — BATCH PER FORM + CSV UPLOAD + MASTER CSV
# ============================================================

A2_FORM_RE = re.compile(r"^(G\d+_)?(E_A2|M_A2)$")


def _redirect_back_to_grading(request):
    group = (request.GET.get("group") or "").strip()
    url = reverse("admin_grading_home")
    if group:
        url = f"{url}?group={group}"
    return redirect(url)


@staff_member_required
def grading_home(request):
    """
    Shows A2 forms available for grading (one row per form slug).
    Optional filter: ?group=6
    """
    group = (request.GET.get("group") or "").strip()

    fds = FormDefinition.objects.filter(slug__regex=r"^(G\d+_)?(E_A2|M_A2)$").order_by("slug")
    if group:
        fds = fds.filter(slug__startswith=f"G{group}_")

    # totals
    totals = {
        row["form__slug"]: row["c"]
        for row in Application.objects.filter(form__in=fds)
        .values("form__slug")
        .annotate(c=Count("id"))
    }

    # pending = recommendation NULL or ""
    pending = {
        row["form__slug"]: row["c"]
        for row in (
            Application.objects.filter(form__in=fds)
            .filter(Q(recommendation__isnull=True) | Q(recommendation=""))
            .values("form__slug")
            .annotate(c=Count("id"))
        )
    }

    rows = []
    for fd in fds:
        rows.append({
            "slug": fd.slug,
            "name": fd.name,
            "total": totals.get(fd.slug, 0),
            "pending": pending.get(fd.slug, 0),
        })

    return render(
        request,
        "admin_dash/grading_home.html",
        {
            "group": group,
            "forms": rows,
        },
    )


@staff_member_required
@require_POST
def grade_application(request, app_id: int):
    """
    Compatibility endpoint: grade one submission.
    """
    app = get_object_or_404(
        Application.objects.select_related("form").prefetch_related("answers__question"),
        id=app_id,
    )

    if not A2_FORM_RE.match(app.form.slug):
        messages.error(request, "This application is not eligible for grading.")
        return _redirect_back_to_grading(request)

    try:
        scores = grade_from_answers(app)
        app.tablestakes_score = scores.get("tablestakes_score")
        app.commitment_score = scores.get("commitment_score")
        app.nice_to_have_score = scores.get("nice_to_have_score")
        app.overall_score = scores.get("overall_score")
        app.recommendation = scores.get("recommendation")
        app.save(
            update_fields=[
                "tablestakes_score",
                "commitment_score",
                "nice_to_have_score",
                "overall_score",
                "recommendation",
            ]
        )
        messages.success(request, f"Graded submission #{app.id} ({app.form.slug}).")
    except Exception as e:
        messages.error(request, f"Grading failed for #{app.id}: {e}")

    return _redirect_back_to_grading(request)


@staff_member_required
@require_POST
def grade_form_batch(request, form_slug: str):
    """
    Batch grades ALL pending submissions for a given A2 form slug.
    """
    if not A2_FORM_RE.match(form_slug):
        messages.error(request, f"{form_slug} is not an A2 form slug.")
        return _redirect_back_to_grading(request)

    fd = get_object_or_404(FormDefinition, slug=form_slug)

    qs = (
        Application.objects.filter(form=fd)
        .select_related("form")
        .prefetch_related("answers__question")
        .order_by("created_at", "id")
    )

    qs_pending = qs.filter(Q(recommendation__isnull=True) | Q(recommendation=""))

    total = qs.count()
    pending = qs_pending.count()

    if pending == 0:
        messages.info(request, f"{form_slug}: No pending submissions to grade. Total submissions: {total}.")
        return _redirect_back_to_grading(request)

    updated = 0
    failed = 0

    with transaction.atomic():
        for app in qs_pending:
            try:
                scores = grade_from_answers(app)

                app.tablestakes_score = scores.get("tablestakes_score")
                app.commitment_score = scores.get("commitment_score")
                app.nice_to_have_score = scores.get("nice_to_have_score")
                app.overall_score = scores.get("overall_score")
                app.recommendation = scores.get("recommendation")

                app.save(update_fields=[
                    "tablestakes_score",
                    "commitment_score",
                    "nice_to_have_score",
                    "overall_score",
                    "recommendation",
                ])
                updated += 1
            except Exception:
                failed += 1

    if failed:
        messages.warning(request, f"{form_slug}: Graded {updated} (failed {failed}). Total submissions: {total}.")
    else:
        messages.success(request, f"{form_slug}: Graded {updated}. Total submissions: {total}.")

    return _redirect_back_to_grading(request)

@staff_member_required
@require_POST
def grading_upload_test_csv(request):
    """
    Upload CSV for testing only.
    Creates/imports into TEST_E_A2 or TEST_M_A2 based on POST 'role' = 'E' or 'M'.
    """
    role = (request.POST.get("role") or "").strip().upper()
    if role not in ("E", "M"):
        messages.error(request, "Select role (E or M) for the test upload.")
        return _redirect_back_to_grading(request)

    fd = _ensure_test_a2_form(role)

    f = request.FILES.get("csv_file")
    if not f:
        messages.error(request, "No CSV file uploaded.")
        return _redirect_back_to_grading(request)

    try:
        raw = f.read().decode("utf-8-sig")
    except Exception:
        raw = f.read().decode("latin-1")

    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames:
        messages.error(request, "CSV appears to have no header row.")
        return _redirect_back_to_grading(request)

    qmap = {q.slug: q for q in fd.questions.all()}

    created = 0
    with transaction.atomic():
        for row in reader:
            name = (row.get("name") or row.get("full_name") or row.get("Nombre") or "").strip()
            email = (row.get("email") or row.get("Correo") or "").strip()

            app = Application.objects.create(
                form=fd,
                name=name,
                email=email,
            )

            for col, val in row.items():
                if col not in qmap:
                    continue
                v = (val or "").strip()
                Answer.objects.create(application=app, question=qmap[col], value=v)

            created += 1

    messages.success(request, f"Imported {created} submissions into {fd.slug} (sandbox).")
    return _redirect_back_to_grading(request)

@staff_member_required
def grading_master_csv(request, form_slug: str):
    """
    Download MASTER CSV for a form slug, ALWAYS including grade columns.
    """
    fd = get_object_or_404(FormDefinition, slug=form_slug)

    questions = list(fd.questions.filter(active=True).order_by("position", "id"))

    headers = [
        "created_at",
        "application_id",
        "name",
        "email",
        "tablestakes_score",
        "commitment_score",
        "nice_to_have_score",
        "overall_score",
        "recommendation",
    ] + [q.slug for q in questions]

    apps = (
        Application.objects.filter(form=fd)
        .prefetch_related("answers__question")
        .order_by("created_at", "id")
    )

    rows = []
    for app in apps:
        amap = {a.question.slug: (a.value or "") for a in app.answers.all()}

        rows.append([
            app.created_at.isoformat(),
            str(app.id),
            app.name or "",
            app.email or "",
            app.tablestakes_score or "",
            app.commitment_score or "",
            app.nice_to_have_score or "",
            app.overall_score or "",
            app.recommendation or "",
        ] + [amap.get(q.slug, "") for q in questions])

    return _csv_http_response(f"{form_slug}_MASTER.csv", headers, rows)


def _norm(s: str) -> str:
    return (s or "").strip().lower()


@staff_member_required
@require_POST
def grading_upload_csv(request, form_slug: str):
    """
    Upload a CSV and import rows as Applications + Answers for this form_slug.

    Robust mapping:
    - If a CSV column matches Question.slug -> use it
    - Else if it matches Question.text (case-insensitive) -> use it
    - name/email columns recognized in common Spanish/English variants
    Unknown columns are ignored.
    """
    fd = get_object_or_404(FormDefinition, slug=form_slug)

    f = request.FILES.get("csv_file")
    if not f:
        messages.error(request, "No CSV file uploaded.")
        return _redirect_back_to_grading(request)

    # decode
    try:
        raw = f.read().decode("utf-8-sig")
    except Exception:
        raw = f.read().decode("latin-1")

    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames:
        messages.error(request, "CSV appears to have no header row.")
        return _redirect_back_to_grading(request)

    questions = list(fd.questions.all())
    q_by_slug = {_norm(q.slug): q for q in questions}
    q_by_text = {_norm(q.text): q for q in questions}

    # common header variants
    NAME_KEYS = {"name", "nombre", "full_name", "nombre completo", "nombre_completo"}
    EMAIL_KEYS = {"email", "correo", "correo electrónico", "correo electronico", "dirección de correo electrónico", "direccion de correo electronico"}

    created = 0
    with transaction.atomic():
        for row in reader:
            # pull name/email if present
            name = ""
            email = ""

            for k, v in row.items():
                nk = _norm(k)
                if nk in NAME_KEYS and not name:
                    name = (v or "").strip()
                if nk in EMAIL_KEYS and not email:
                    email = (v or "").strip()

            app = Application.objects.create(
                form=fd,
                name=name.strip(),
                email=email.strip(),
            )

            # create answers
            for col, val in row.items():
                col_norm = _norm(col)
                q = q_by_slug.get(col_norm) or q_by_text.get(col_norm)
                if not q:
                    continue
                Answer.objects.create(
                    application=app,
                    question=q,
                    value=(val or "").strip(),
                )

            created += 1

    messages.success(request, f"Imported {created} submissions into {form_slug}.")
    return _redirect_back_to_grading(request)


# ----------------------------
# Email helpers + reminders
# ----------------------------
def _send_html_email(to_email: str, subject: str, html_body: str):
    msg = EmailMultiAlternatives(
        subject=subject,
        body="",
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        to=[to_email],
    )
    msg.attach_alternative(html_body, "text/html")
    msg.send(fail_silently=False)


def _a1_slug_for_a2(form_slug: str) -> str | None:
    """
    Map:
      M_A2 -> M_A1
      E_A2 -> E_A1
      G5_M_A2 -> G5_M_A1
      G5_E_A2 -> G5_E_A1
    Returns None if not an A2 slug.
    """
    if not form_slug:
        return None

    if form_slug.endswith("M_A2"):
        if form_slug.startswith("G") and "_M_A2" in form_slug:
            return form_slug.replace("_M_A2", "_M_A1")
        return "M_A1"

    if form_slug.endswith("E_A2"):
        if form_slug.startswith("G") and "_E_A2" in form_slug:
            return form_slug.replace("_E_A2", "_E_A1")
        return "E_A1"

    return None


@require_POST
def send_second_stage_reminders(request, form_slug: str):
    """
    Sends reminder emails to A1-approved users who have NOT completed A2 yet.
    Works for:
      - G6_E_A2 / G6_M_A2
      - E_A2 / M_A2

    Rules:
    - Only sends to users who were invited_to_second_stage=True
    - Only sends 1 email per unique email address
    - Skips anyone who already submitted this A2 form
    - No max cap
    - Uses one SMTP connection to avoid timeouts
    """

    # Validate form_slug is A2
    if not (form_slug.endswith("E_A2") or form_slug.endswith("M_A2")):
        messages.error(request, "Este botón solo funciona para formularios A2 (E_A2 o M_A2).")
        return redirect("admin_apps_list")

    # Ensure A2 form exists
    _a2_form = get_object_or_404(FormDefinition, slug=form_slug)

    is_emprendedora = form_slug.endswith("E_A2")
    track_word = "emprendedora" if is_emprendedora else "mentora"

    # -------- derive matching A1 slug --------
    m = GROUP_SLUG_RE.match(form_slug)
    if m:
        gnum = m.group("num")
        a1_slug = f"G{gnum}_{'E_A1' if is_emprendedora else 'M_A1'}"
    else:
        a1_slug = "E_A1" if is_emprendedora else "M_A1"

    # ✅ Find approved A1 submissions only (they MUST have been invited)
    a1_apps_qs = (
        Application.objects.filter(
            form__slug=a1_slug,
            invited_to_second_stage=True,
        )
        .exclude(email__isnull=True)
        .exclude(email__exact="")
        .only("id", "email", "created_at")
        .order_by("-created_at", "-id")
    )

    # ✅ Find who already completed THIS A2
    completed_emails = set(
        Application.objects.filter(form__slug=form_slug)
        .exclude(email__isnull=True)
        .exclude(email__exact="")
        .values_list("email", flat=True)
    )

    # ✅ Build unique target list
    targets = []
    seen = set()

    for app in a1_apps_qs:
        email = (app.email or "").strip().lower()
        if not email:
            continue
        if email in seen:
            continue
        if email in completed_emails:
            continue
        seen.add(email)
        targets.append(email)

    if not targets:
        messages.info(request, f"No hay personas pendientes para {form_slug}.")
        return redirect("admin_apps_list")

    # ✅ Link for this group’s A2 form (public slug link)
    a2_link = f"https://apply.clubemprendo.org/apply/{form_slug}/"

    subject = "Últimos días para completar la segunda aplicación"

    html_body = f"""
    <div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;margin:0 auto;">
      <p>Hola,</p>
      <p>Esperamos que te encuentres muy bien.</p>
      <p>
        Queremos recordarte que, según la primera aplicación que completaste, cumples con el perfil para ser
        <strong>{track_word}</strong>, y nos encantaría que continúes con el proceso.
      </p>
      <p>
        Te recordamos que es necesario completar la segunda aplicación, ya que la fecha límite es el
        <strong>18 de enero de 2026</strong>. Estamos en los últimos días para aplicar.
      </p>
      <p>A continuación, te dejamos nuevamente el enlace y las instrucciones:</p>
      <ol>
        <li>Haz clic en el enlace: 👉 <a href="{a2_link}">{a2_link}</a></li>
        <li>Responde las preguntas (no toma más de 10 minutos).</li>
        <li>Haz clic en <strong>Enviar</strong> para completar tu aplicación.</li>
      </ol>
      <p>
        Tu participación es muy valiosa para nosotras, y esperamos contar contigo en esta nueva etapa del programa.
        Si tienes alguna pregunta o inconveniente, no dudes en escribirnos.
      </p>
      <p>Con cariño,<br><strong>Melanie Guzmán</strong></p>
    </div>
    """

    sent = 0
    failed = 0

    # ✅ One shared SMTP connection (much faster + safer on Render)
    connection = get_connection(fail_silently=False)
    try:
        connection.open()

        for email in targets:
            try:
                msg = EmailMultiAlternatives(
                    subject=subject,
                    body="",
                    from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                    to=[email],
                    connection=connection,
                )
                msg.attach_alternative(html_body, "text/html")
                msg.send()
                sent += 1
            except Exception:
                failed += 1

    finally:
        try:
            connection.close()
        except Exception:
            pass

    if failed > 0:
        messages.warning(
            request,
            f"Reminders enviados para {form_slug}: {sent} enviados, {failed} fallidos."
        )
    else:
        messages.success(
            request,
            f"Reminders enviados para {form_slug}: {sent} enviados."
        )

    return redirect("admin_apps_list")
