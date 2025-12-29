from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, get_object_or_404
from .models import FormDefinition
# applications/admin_views.py
import re
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST

from .models import FormDefinition, Question, Choice


MASTER_SLUGS = ["E_A1", "E_A2", "M_A1", "M_A2"]
GROUP_RE = re.compile(r"^G(?P<num>\d+)_(?P<master>E_A1|E_A2|M_A1|M_A2)$")


def _clone_form(master_fd, new_slug, new_name):
    # Create new FormDefinition
    clone = FormDefinition.objects.create(slug=new_slug, name=new_name)

    # Clone questions + choices
    master_questions = Question.objects.filter(form=master_fd).order_by("id")
    for q in master_questions:
        q_clone = Question.objects.create(
            form=clone,
            **{
                # safest: only copy fields that exist
                f.name: getattr(q, f.name)
                for f in q._meta.fields
                if f.name not in ("id", "form")
            },
        )
        # choices
        if hasattr(q, "choice_set"):
            for c in q.choice_set.all().order_by("id"):
                Choice.objects.create(
                    question=q_clone,
                    **{
                        f.name: getattr(c, f.name)
                        for f in c._meta.fields
                        if f.name not in ("id", "question")
                    },
                )
    return clone


@staff_member_required
def apps_list(request):
    masters = FormDefinition.objects.filter(slug__in=MASTER_SLUGS).order_by("slug")

    # Grouped copies: detect slugs like G6_E_A1
    groups = {}
    for fd in FormDefinition.objects.exclude(slug__in=MASTER_SLUGS):
        m = GROUP_RE.match(fd.slug or "")
        if not m:
            continue
        gnum = int(m.group("num"))
        groups.setdefault(gnum, []).append(fd)

    # sort each group and group numbers
    group_list = []
    for gnum in sorted(groups.keys()):
        group_list.append((gnum, sorted(groups[gnum], key=lambda x: x.slug)))

    return render(
        request,
        "admin_dash/apps_list.html",
        {"masters": masters, "group_list": group_list},
    )


@staff_member_required
@require_POST
def create_group(request):
    """
    Creates a group (e.g., G6_) by cloning all 4 masters.
    """
    group_num = (request.POST.get("group_num") or "").strip()
    if not group_num.isdigit():
        return redirect("admin_apps_list")

    gnum = int(group_num)

    # Don’t recreate if already exists
    existing = FormDefinition.objects.filter(slug__startswith=f"G{gnum}_").exists()
    if existing:
        return redirect("admin_apps_list")

    masters = {fd.slug: fd for fd in FormDefinition.objects.filter(slug__in=MASTER_SLUGS)}
    for slug in MASTER_SLUGS:
        master_fd = masters.get(slug)
        if not master_fd:
            continue
        new_slug = f"G{gnum}_{slug}"
        new_name = f"Group {gnum} - {master_fd.name}"
        _clone_form(master_fd, new_slug, new_name)

    return redirect("admin_apps_list")

@staff_member_required
def apps_list(request):
    forms = FormDefinition.objects.order_by("name")
    return render(request, "admin_dash/apps_list.html", {"forms": forms})

@staff_member_required
def app_form_detail(request, form_id):
    form = get_object_or_404(FormDefinition, id=form_id)
    return render(request, "admin_dash/app_form_detail.html", {"form": form})


@staff_member_required
def submissions_list(request):
    """
    Optional: shows raw submissions (Application rows) across all forms.
    """
    apps = Application.objects.select_related("form").order_by("-created_at")
    return render(request, "admin_dash/submissions_list.html", {"applications": apps})


@staff_member_required
def submission_detail(request, app_id):
    """
    Shows one submission.
    """
    app = get_object_or_404(Application, id=app_id)
    return render(request, "admin_dash/submission_detail.html", {"app": app})


@staff_member_required
def database_home(request):
    return render(request, "admin_dash/database_home.html")
