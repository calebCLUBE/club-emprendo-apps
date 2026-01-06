# applications/admin_views.py

import csv
import re
from typing import List, Tuple
from urllib.parse import urlparse

from django import forms
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Model
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from applications.models import (
    Answer,
    Application,
    Choice,
    FormDefinition,
    FormGroup,
    Question,
)

MASTER_SLUGS = ["E_A1", "E_A2", "M_A1", "M_A2"]
GROUP_SLUG_RE = re.compile(r"^G(?P<num>\d+)_(?P<master>E_A1|E_A2|M_A1|M_A2)$")


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
    """
    Replace placeholders like:
      #(group number), #(month) (twice), #(year)
    """
    if not text:
        return text

    out = text.replace("#(group number)", str(group_num))

    # Replace first #(month) with start_month, second with end_month (if present)
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


def _clone_form(master_fd: FormDefinition, group: FormGroup) -> FormDefinition:
    """
    Clone a master FormDefinition into a specific group:
      M_A1 -> G6_M_A1, etc.

    - Preserves Question.slug from master (so grading relies on stable slugs).
    - Sets clone.group = group, clone.is_master = False
    """
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
        description=_fill_placeholders(master_fd.description, group_num, start_month, end_month, year) or "",
        is_master=False,
        group=group,
        is_public=master_fd.is_public,
    )

    # Clone questions + choices
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
    """
    Returns (headers, rows) for all applications under this form_def.
    """
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
    """
    Tiny HTML table preview (no external deps).
    """

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


def _soft_archive_group(group: FormGroup) -> None:
    """
    Hide forms for a group without deleting anything (safe if submissions exist).
    - Always sets FormDefinition.is_public=False (field exists).
    - If FormGroup has is_active, set it False too.
    """
    FormDefinition.objects.filter(group=group).update(is_public=False)

    if _model_has_field(FormGroup, "is_active"):
        FormGroup.objects.filter(id=group.id).update(is_active=False)


def _safe_next_url(request) -> str | None:
    """
    Allows delete buttons from *any* page without hardcoding redirect targets.
    Pass a hidden input named 'next' with request.get_full_path.

    Only allows internal paths (starting with '/').
    """
    nxt = (request.POST.get("next") or "").strip()
    if nxt.startswith("/"):
        return nxt
    return None


def _extract_storage_path_from_value(value: str) -> str | None:
    """
    Convert Answer.value into a storage-relative path if possible.
    Handles:
      - "uploads/file.pdf"
      - "/media/uploads/file.pdf"
      - "http(s)://.../media/uploads/file.pdf"
    Returns a path suitable for default_storage.delete(), or None if we can't safely map it.
    """
    if not value:
        return None

    v = value.strip()

    # If it's a URL, extract path
    if v.startswith("http://") or v.startswith("https://"):
        parsed = urlparse(v)
        v = parsed.path  # e.g. "/media/uploads/x.pdf"

    # Normalize "/media/..." -> "uploads/..." (relative)
    if v.startswith("/media/"):
        v = v[len("/media/") :]

    # Remove leading slash (storage paths are usually relative)
    v = v.lstrip("/")

    if not v:
        return None

    return v


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
    """
    Behavior:
    - If group has submissions:
        - default: archive (hide) and refuse hard delete
        - if POST includes force=1: permanently delete submissions + answers + forms + group
    - If group has no submissions: hard delete forms + group
    """
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

    return render(
        request,
        "admin_dash/database_home.html",
        {"masters": masters, "group_blocks": group_blocks},
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
    # Backwards-compatible route name used in database_home.html
    form_def = get_object_or_404(FormDefinition, slug=form_slug)
    headers, rows = _build_csv_for_form(form_def)
    return _csv_http_response(f"{form_slug}.csv", headers, rows)


@staff_member_required
@require_POST
def delete_answer_file_value(request, answer_id: int):
    """
    Deletes the file pointed to by Answer.value (if possible), then clears Answer.value.

    IMPORTANT CHANGE:
    - We no longer block based on question.field_type (your Question choices don't include "file").
    - We attempt deletion based on whether we can map Answer.value -> storage path.
    - We ALWAYS clear the DB reference so it disappears from the database admin pages.

    Redirect behavior:
    - If POST includes 'next' (internal path), redirect there (lets you delete from database_home without layout changes).
    - Otherwise fall back to the submission detail page.
    """
    ans = get_object_or_404(
        Answer.objects.select_related("application", "question"),
        id=answer_id,
    )

    storage_path = _extract_storage_path_from_value(ans.value)

    # Try deleting from storage if we can map a path
    if storage_path:
        try:
            if default_storage.exists(storage_path):
                default_storage.delete(storage_path)
        except Exception:
            # Storage might be remote/misconfigured; still clear DB pointer
            pass

    ans.value = ""
    ans.save(update_fields=["value"])

    messages.success(request, "Archivo eliminado (y referencia borrada).")

    nxt = _safe_next_url(request)
    if nxt:
        return redirect(nxt)

    return redirect("admin_database_submission_detail", app_id=ans.application_id)


@staff_member_required
@require_POST
def delete_application_files(request, app_id: int):
    """
    Deletes all Answer.value entries that look like files for an application
    (and deletes storage objects if possible).

    Same redirect behavior as delete_answer_file_value:
    - honors POST 'next' (internal)
    - else goes back to submission detail
    """
    app = get_object_or_404(Application, id=app_id)

    answers = Answer.objects.select_related("question").filter(application=app)
    deleted_count = 0

    for ans in answers:
        if not ans.value:
            continue

        storage_path = _extract_storage_path_from_value(ans.value)

        # Only treat as "file" if it maps to a plausible path
        if not storage_path:
            continue

        try:
            if default_storage.exists(storage_path):
                default_storage.delete(storage_path)
        except Exception:
            pass

        ans.value = ""
        ans.save(update_fields=["value"])
        deleted_count += 1

    messages.success(request, f"Archivos eliminados: {deleted_count}")

    nxt = _safe_next_url(request)
    if nxt:
        return redirect(nxt)

    return redirect("admin_database_submission_detail", app_id=app.id)
