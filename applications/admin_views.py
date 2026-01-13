# applications/admin_views.py

import csv
import re
from typing import List, Tuple
from urllib.parse import urlparse
from applications.grading import grade_from_answers
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone

from django import forms
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Model, Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse   # ✅ added
from django.views.decorators.http import require_POST
from django.db.models.functions import Lower

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
# applications/admin_views.py

from django.views.decorators.http import require_POST
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect

from applications.models import FormDefinition

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

    # If you prefer a dedicated field like is_open, swap this to fd.is_open
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

    # ✅ Submission counts
    counts = {
        row["form__slug"]: row["c"]
        for row in Application.objects.values("form__slug").annotate(c=Count("id"))
    }

    # ✅ Attach counts + admin edit links (surveys + forms)
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
# ----------------------------
# Grading (Admin)
# ----------------------------
@staff_member_required
def grading_home(request):
    """
    Admin grading dashboard:
    - Optional ?group=5 filter
    - Lists applications with a Grade button per row
    """
    group = (request.GET.get("group") or "").strip()

    qs = (
        Application.objects.filter(
            form__slug__regex=r"^(G\d+_)?(E_A2|M_A2)$"
        )
        .select_related("form")
        .order_by("-created_at", "-id")
    )


    # Filter by group if provided
    if group:
        # If Application has a group_num field, prefer that
        if _model_has_field(Application, "group_num"):
            qs = qs.filter(group_num=group)
        else:
            # Fallback: filter by form slug like "G5_..."
            qs = qs.filter(form__slug__startswith=f"G{group}_")

    # Pending = blank/NULL recommendation
    pending_qs = qs.filter(recommendation__isnull=True) | qs.filter(recommendation="")

    return render(
        request,
        "admin_dash/grading_home.html",
        {
            "group": group,
            "applications": qs[:300],  # safety cap in case you have tons
            "pending_count": pending_qs.count(),
        },
    )


@staff_member_required
@require_POST
def grade_application(request, app_id: int):
    """
    Grade one application and save the result to the Application fields.
    """
    app = get_object_or_404(
        Application.objects.select_related("form").prefetch_related("answers__question"),
        id=app_id,
    )

    scores = grade_from_answers(app)

    # Save back onto the Application model
    app.tablestakes_score = scores["tablestakes_score"]
    app.commitment_score = scores["commitment_score"]
    app.nice_to_have_score = scores["nice_to_have_score"]
    app.overall_score = scores["overall_score"]
    app.recommendation = scores["recommendation"]

    app.save(
        update_fields=[
            "tablestakes_score",
            "commitment_score",
            "nice_to_have_score",
            "overall_score",
            "recommendation",
        ]
    )
    if not re.match(r"^(G\d+_)?(E_A2|M_A2)$", app.form.slug):
        messages.error(request, "This application is not eligible for grading.")
        return redirect(reverse("admin_grading_home"))


    # Preserve group filter on redirect (so you stay on Group 5 while grading)
    group = (request.GET.get("group") or "").strip()
    url = reverse("admin_grading_home")
    if group:
        url = f"{url}?group={group}"
    return redirect(url)

    return redirect("admin_database_submission_detail", app_id=app.id)


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


GROUP_SLUG_RE = re.compile(r"^G(?P<num>\d+)_(?P<master>E_A1|E_A2|M_A1|M_A2)$")


@staff_member_required
def send_second_stage_reminders(request, form_slug: str):
    """
    Sends A2 reminder emails for a given *A2* form slug (e.g., G6_E_A2, G6_M_A2).

    Rules:
    - Only email people who completed A1 AND were invited_to_second_stage=True AND have invite_token.
    - Only email those who have NOT submitted A2 yet.
    - Deduplicate by email (send once per email).
    - TEST MODE: if POST includes test_only=1, only send to test_email.
    """
    if request.method != "POST":
        return redirect("admin_apps_list")

    a2_form = get_object_or_404(FormDefinition, slug=form_slug)

    if not form_slug.endswith("_A2"):
        messages.error(request, "This button is only for A2 forms.")
        return redirect("admin_apps_list")

    # Determine cohort group number and whether it's mentora or emprendedora
    # Expected slugs: G6_E_A2 or G6_M_A2 (but also allow master E_A2/M_A2 if needed)
    is_mentora = "M_A2" in form_slug
    role_word = "mentora" if is_mentora else "emprendedora"

    # Link requested by you (group-based direct link)
    a2_link = f"https://apply.clubemprendo.org/apply/{form_slug}/"

    # Find matching A1 slug for this A2 slug
    # G6_M_A2 -> G6_M_A1 ; G6_E_A2 -> G6_E_A1 ; M_A2 -> M_A1 ; E_A2 -> E_A1
    a1_slug = form_slug.replace("_A2", "_A1")
    a1_form = FormDefinition.objects.filter(slug=a1_slug).first()
    if not a1_form:
        messages.error(request, f"Could not find A1 form for {form_slug} (expected {a1_slug}).")
        return redirect("admin_apps_list")

    # TEST MODE
    test_only = request.POST.get("test_only") == "1"
    test_email = (request.POST.get("test_email") or "").strip().lower()

    if test_only and not test_email:
        messages.error(request, "Test mode requires a test_email.")
        return redirect("admin_apps_list")

    # Eligible A1 applicants: invited + token + email exists
    a1_apps = (
        Application.objects
        .filter(form=a1_form, invited_to_second_stage=True)
        .exclude(invite_token__isnull=True)
        .exclude(email__isnull=True)
        .exclude(email__exact="")
        .order_by("-created_at", "-id")
    )

    # People who already submitted A2 (dedupe by email)
    a2_emails = set(
        Application.objects
        .filter(form=a2_form)
        .exclude(email__isnull=True)
        .exclude(email__exact="")
        .values_list("email", flat=True)
    )
    a2_emails = {e.strip().lower() for e in a2_emails if e}

    # Build recipient list: A1 invited, not in A2, dedupe by email
    recipients = {}
    for app in a1_apps:
        email = (app.email or "").strip().lower()
        if not email:
            continue
        if email in a2_emails:
            continue
        # Keep one app per email (most recent wins due to ordering)
        if email not in recipients:
            recipients[email] = app

    # Apply TEST override
    if test_only:
        # Only send if this email is eligible; if not eligible, still allow sending a test copy (use latest app if any)
        if test_email in recipients:
            recipients = {test_email: recipients[test_email]}
        else:
            # fallback: send test email anyway using any one eligible app to construct link/token logic
            sample = next(iter(recipients.values()), None)
            if not sample:
                messages.warning(request, "No eligible recipients found to use for a test. (No invited A1s pending A2.)")
                return redirect("admin_apps_list")
            recipients = {test_email: sample}

        messages.warning(request, f"TEST MODE: sending reminder ONLY to {test_email}")

    if not recipients:
        messages.info(request, "No eligible people found to remind (everyone already completed A2 or no invited A1s).")
        return redirect("admin_apps_list")

    subject = "Últimos días para completar la segunda aplicacion"

    # HTML body (your copy)
    def build_html(_role_word: str, _a2_link: str):
        return f"""
<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;margin:0 auto;">
  <p>Hola,</p>
  <p>Esperamos que te encuentres muy bien.</p>
  <p>
    Queremos recordarte que, según la primera aplicación que completaste, cumples con el perfil para ser
    <strong>{_role_word}</strong>, y nos encantaría que continúes con el proceso.
  </p>
  <p>
    Te recordamos que es necesario completar la segunda aplicación, ya que la fecha límite es el
    <strong>11 de enero de 2026</strong>. Estamos en los últimos días para aplicar.
  </p>
  <p>A continuación, te dejamos nuevamente el enlace y las instrucciones:</p>
  <ol>
    <li>Haz clic en el enlace: 👉 <a href="{_a2_link}">{_a2_link}</a></li>
    <li>Responde las preguntas (no toma más de 10 minutos).</li>
    <li>Haz clic en <strong>Enviar</strong> para completar tu aplicación.</li>
  </ol>
  <p>
    Tu participación es muy valiosa para nosotras, y esperamos contar contigo en esta nueva etapa del programa.
    Si tienes alguna pregunta o inconveniente, no dudes en escribirnos.
  </p>
  <p>Con cariño,<br><strong>Melanie Guzmán</strong></p>
</div>
""".strip()

    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "contacto@clubemprendo.org")

    sent = 0
    failed = 0

    for email, _app in recipients.items():
        try:
            html_body = build_html(role_word, a2_link)

            msg = EmailMultiAlternatives(
                subject=subject,
                body="",
                from_email=from_email,
                to=[email],
            )
            msg.attach_alternative(html_body, "text/html")
            msg.send(fail_silently=False)
            sent += 1
        except Exception:
            failed += 1

    if test_only:
        messages.success(request, f"TEST reminder sent to {test_email}.")
    else:
        messages.success(request, f"Reminders sent: {sent}. Failed: {failed}. Unique emails: {len(recipients)}.")

    return redirect("admin_apps_list")
