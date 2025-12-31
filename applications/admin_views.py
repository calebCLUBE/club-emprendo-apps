# applications/admin_views.py
import re

from django import forms
from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST

from .models import FormDefinition, Question, Choice, FormGroup, Application
import csv
from django.http import HttpResponse

@staff_member_required
def database_home(request):
    # Pull all forms, grouped by group number (including masters)
    masters = list(FormDefinition.objects.filter(slug__in=MASTER_SLUGS).order_by("slug"))

    groups = list(FormGroup.objects.order_by("number"))
    group_blocks = []
    for g in groups:
        forms = list(FormDefinition.objects.filter(group=g).order_by("slug"))
        group_blocks.append((g, forms))

    return render(
        request,
        "admin_dash/database_home.html",
        {"masters": masters, "group_blocks": group_blocks},
    )


@staff_member_required
def export_form_csv(request, form_slug: str):
    form_def = get_object_or_404(FormDefinition, slug=form_slug)

    # Applications for this form
    apps = (
        Application.objects
        .filter(form=form_def)
        .prefetch_related("answers__question")
        .order_by("created_at")
    )

    # Determine columns from questions in form order
    questions = list(form_def.questions.filter(active=True).order_by("position", "id"))
    headers = ["created_at", "application_id", "name", "email"] + [q.slug for q in questions]

    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{form_slug}.csv"'

    w = csv.writer(resp)
    w.writerow(headers)

    for app in apps:
        amap = {a.question.slug: a.value for a in app.answers.all()}
        row = [app.created_at.isoformat(), app.id, app.name, app.email] + [amap.get(q.slug, "") for q in questions]
        w.writerow(row)

    return resp


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
def _fill_placeholders(text: str | None, group_num: int, start_month: str, end_month: str, year: int) -> str | None:
    """
    Replace placeholders like:
      #(group number), #(month) (twice), #(year)
    """
    if not text:
        return text

    out = text.replace("#(group number)", str(group_num))

    if "#(month)" in out:
        out = out.replace("#(month)", start_month, 1)
    if "#(month)" in out:
        out = out.replace("#(month)", end_month, 1)

    out = out.replace("#(year)", str(year))
    return out


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
        description=_fill_placeholders(
            getattr(master_fd, "description", ""),
            group_num,
            start_month,
            end_month,
            year,
        ) or "",
        is_public=master_fd.is_public,
        is_master=False,
        group=group,
    )

    master_questions = Question.objects.filter(form=master_fd).order_by("position", "id")
    for q in master_questions:
        q_clone = Question.objects.create(
            form=clone,
            text=_fill_placeholders(q.text, group_num, start_month, end_month, year) or q.text,
            help_text=_fill_placeholders(q.help_text, group_num, start_month, end_month, year) or q.help_text,
            field_type=q.field_type,
            required=q.required,
            position=q.position,
            slug=q.slug,   # ✅ keep stable identifier
            active=q.active,
        )

        for c in Choice.objects.filter(question=q).order_by("position", "id"):
            Choice.objects.create(
                question=q_clone,
                label=_fill_placeholders(c.label, group_num, start_month, end_month, year) or c.label,
                value=c.value,
                position=c.position,
            )

    return clone


def _group_numbers_present_in_forms() -> set[int]:
    """
    Detect orphaned groups where FormDefinition has G#_... slugs but no FormGroup row exists.
    """
    nums: set[int] = set()
    for fd in FormDefinition.objects.exclude(slug__in=MASTER_SLUGS).only("slug"):
        m = GROUP_SLUG_RE.match(fd.slug or "")
        if m:
            nums.add(int(m.group("num")))
    return nums


# ----------------------------
# Views
# ----------------------------
@staff_member_required
def apps_list(request):
    """
    /admin/apps/

    Context:
      masters: list[FormDefinition]
      group_list: list[(group_obj_or_int, forms_list)]
        - group_obj_or_int is either FormGroup OR int (orphan)
    """
    masters = list(FormDefinition.objects.filter(slug__in=MASTER_SLUGS).order_by("slug"))
    groups = list(FormGroup.objects.order_by("number"))

    orphan_nums = sorted(_group_numbers_present_in_forms() - {g.number for g in groups})

    group_list: list[tuple[object, list[FormDefinition]]] = []
    for g in groups:
        forms_in_group = list(FormDefinition.objects.filter(group=g).order_by("slug"))
        group_list.append((g, forms_in_group))

    for n in orphan_nums:
        forms_in_group = list(FormDefinition.objects.filter(slug__startswith=f"G{n}_").order_by("slug"))
        group_list.append((n, forms_in_group))

    create_group_form = CreateGroupForm()

    return render(
        request,
        "admin_dash/apps_list.html",
        {
            "masters": masters,
            "group_list": group_list,
            "create_group_form": create_group_form,
            "orphan_nums": orphan_nums,
        },
    )


@staff_member_required
@require_POST
@transaction.atomic
def create_group(request):
    """
    POST /admin/apps/create-group/
    Creates a FormGroup row and clones all 4 masters into that group.
    """
    form = CreateGroupForm(request.POST)
    if not form.is_valid():
        return redirect("admin_apps_list")

    gnum = form.cleaned_data["group_num"]
    start_month = form.cleaned_data["start_month"].strip()
    end_month = form.cleaned_data["end_month"].strip()
    year = form.cleaned_data["year"]

    if FormGroup.objects.filter(number=gnum).exists():
        return redirect("admin_apps_list")

    group = FormGroup.objects.create(
        number=gnum,
        start_month=start_month,
        end_month=end_month,
        year=year,
    )

    masters_by_slug = {fd.slug: fd for fd in FormDefinition.objects.filter(slug__in=MASTER_SLUGS)}
    for slug in MASTER_SLUGS:
        master_fd = masters_by_slug.get(slug)
        if master_fd:
            _clone_form(master_fd, group)

    return redirect("admin_apps_list")


@staff_member_required
@require_POST
@transaction.atomic
def delete_group(request, group_num: int):
    # Delete all applications for forms in this group first (because Application.form is PROTECT)
    group_forms = FormDefinition.objects.filter(slug__startswith=f"G{group_num}_")
    Application.objects.filter(form__in=group_forms).delete()
    # Now delete the forms
    group_forms.delete()
    # And delete the FormGroup record (if present)
    FormGroup.objects.filter(number=group_num).delete()
    return redirect("admin_apps_list")



@staff_member_required
def app_form_detail(request, form_id: int):
    form = get_object_or_404(FormDefinition, id=form_id)
    questions = Question.objects.filter(form=form).order_by("position", "id")
    return render(
        request,
        "admin_dash/app_form_detail.html",
        {"form": form, "questions": questions},
    )


