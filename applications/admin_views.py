# applications/admin_views.py
import re

from django import forms
from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST

from .models import FormDefinition, Question, Choice

MASTER_SLUGS = ["E_A1", "E_A2", "M_A1", "M_A2"]
GROUP_RE = re.compile(r"^G(?P<num>\d+)_(?P<master>E_A1|E_A2|M_A1|M_A2)$")


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
    Replace the #(…) placeholders with group info.
    Notes:
      - Your text uses #(month) twice; we fill first with start_month, second with end_month.
    """
    if not text:
        return text

    out = text.replace("#(group number)", str(group_num))
    # replace first #(month) then second #(month)
    if "#(month)" in out:
        out = out.replace("#(month)", start_month, 1)
    if "#(month)" in out:
        out = out.replace("#(month)", end_month, 1)
    out = out.replace("#(year)", str(year))
    return out


def _clone_form(master_fd: FormDefinition, group_num: int, start_month: str, end_month: str, year: int) -> FormDefinition:
    """
    Clone a master FormDefinition into a specific group:
      E_A1 -> G6_E_A1, etc.
    Also clones Questions and Choices.
    Ensures Question.slug is unique within the cloned form.
    """
    new_slug = f"G{group_num}_{master_fd.slug}"
    new_name = f"Grupo {group_num} — {master_fd.name}"

    # prevent duplicate group build
    existing = FormDefinition.objects.filter(slug=new_slug).first()
    if existing:
        return existing

    # Create clone form
    clone = FormDefinition.objects.create(
        slug=new_slug,
        name=new_name,
    )

    # If your FormDefinition has extra text fields (like intro/description), try to copy+fill them.
    # We do this defensively so it won't crash if your model doesn't have these fields.
    for field_name in ("intro", "description", "header", "body"):
        if hasattr(master_fd, field_name) and hasattr(clone, field_name):
            setattr(
                clone,
                field_name,
                _fill_placeholders(getattr(master_fd, field_name), group_num, start_month, end_month, year),
            )
    clone.save()

    # Clone questions (preserving order if you have it, otherwise by id)
    qs = Question.objects.filter(form=master_fd).order_by("order", "id")

    for idx, q in enumerate(qs, start=1):
        # copy only safe fields that exist; exclude PK + FK + slug (we regenerate)
        q_fields = {}
        for f in q._meta.fields:
            if f.name in ("id", "form", "slug"):
                continue
            q_fields[f.name] = getattr(q, f.name)

        # fill placeholders in question label/text if present
        for label_field in ("label", "text", "help_text"):
            if label_field in q_fields and isinstance(q_fields[label_field], str):
                q_fields[label_field] = _fill_placeholders(q_fields[label_field], group_num, start_month, end_month, year)

        q_clone = Question.objects.create(
            form=clone,
            slug=f"q{idx:03d}",  # ✅ unique per-form
            **q_fields,
        )

        # Clone choices (if any)
        # Your model might have Choice(question=...) with related_name choice_set or choices; this uses default.
        choices = Choice.objects.filter(question=q).order_by("id")
        for c in choices:
            c_fields = {}
            for f in c._meta.fields:
                if f.name in ("id", "question"):
                    continue
                c_fields[f.name] = getattr(c, f.name)
            Choice.objects.create(question=q_clone, **c_fields)

    return clone


def _get_group_numbers() -> list[int]:
    nums: set[int] = set()
    for fd in FormDefinition.objects.exclude(slug__in=MASTER_SLUGS).only("slug"):
        m = GROUP_RE.match(fd.slug or "")
        if m:
            nums.add(int(m.group("num")))
    return sorted(nums)


# ----------------------------
# Views
# ----------------------------
@staff_member_required
def apps_list(request):
    """
    /admin/apps/
    Shows:
      - Master applications (E_A1, E_A2, M_A1, M_A2)
      - Groups underneath
      - Create group form (group # + dates)
    """
    masters = list(FormDefinition.objects.filter(slug__in=MASTER_SLUGS).order_by("slug"))

    # Build grouped list: {group_num: [forms...]}
    group_numbers = _get_group_numbers()
    group_list: list[tuple[int, list[FormDefinition]]] = []

    for gnum in group_numbers:
        forms_in_group = list(
            FormDefinition.objects.filter(slug__startswith=f"G{gnum}_").order_by("slug")
        )
        group_list.append((gnum, forms_in_group))

    create_group_form = CreateGroupForm()

    return render(
        request,
        "admin_dash/apps_list.html",
        {
            "masters": masters,
            "group_list": group_list,
            "create_group_form": create_group_form,
        },
    )


@staff_member_required
@require_POST
@transaction.atomic
def create_group(request):
    """
    POST /admin/apps/create-group/
    Creates a group (e.g. G6_) by cloning all 4 master forms.
    """
    form = CreateGroupForm(request.POST)
    if not form.is_valid():
        return redirect("admin_apps_list")

    gnum = form.cleaned_data["group_num"]
    start_month = form.cleaned_data["start_month"].strip()
    end_month = form.cleaned_data["end_month"].strip()
    year = form.cleaned_data["year"]

    # Don't recreate if already exists
    if FormDefinition.objects.filter(slug__startswith=f"G{gnum}_").exists():
        return redirect("admin_apps_list")

    masters = {fd.slug: fd for fd in FormDefinition.objects.filter(slug__in=MASTER_SLUGS)}
    for slug in MASTER_SLUGS:
        master_fd = masters.get(slug)
        if master_fd:
            _clone_form(master_fd, gnum, start_month, end_month, year)

    return redirect("admin_apps_list")


@staff_member_required
def app_form_detail(request, form_id: int):
    """
    Optional detail page for one form (master or group copy).
    """
    form = get_object_or_404(FormDefinition, id=form_id)
    questions = Question.objects.filter(form=form).order_by("order", "id")
    return render(
        request,
        "admin_dash/app_form_detail.html",
        {"form": form, "questions": questions},
    )


@staff_member_required
def database_home(request):
    """
    Placeholder route for your "Database" button.
    """
    return render(request, "admin_dash/database_home.html")
