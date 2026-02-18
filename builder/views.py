import re

from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_POST
from django.http import HttpResponse

from applications.models import FormDefinition, Question, Choice, Section
from django.db.models import Max


_PRE_RE = re.compile(
    r"^\s*\[\[PRE(?P<attrs>[^\]]*)\]\]\s*\n(?P<body>.*?)\n\s*\[\[/PRE\]\]\s*\n?(?P<rest>.*)$",
    re.DOTALL,
)

def split_help_text(raw: str):
    raw = raw or ""
    m = _PRE_RE.match(raw)
    if not m:
        return "", False, raw

    attrs = (m.group("attrs") or "").strip()
    body = (m.group("body") or "").strip()
    rest = (m.group("rest") or "").lstrip()

    pre_hr = bool(re.search(r"\bhr\s*=\s*(1|true|yes)\b", attrs, flags=re.IGNORECASE))
    return body, pre_hr, rest


def pack_help_text(pre_text: str, pre_hr: bool, rest_help_text: str) -> str:
    pre_text = (pre_text or "").strip()
    rest_help_text = (rest_help_text or "").strip()

    if not pre_text and not pre_hr:
        return rest_help_text

    hr_val = "1" if pre_hr else "0"
    header = f"[[PRE hr={hr_val}]]\n{pre_text}\n[[/PRE]]"
    return header + (("\n\n" + rest_help_text) if rest_help_text else "")


@staff_member_required
def builder_home(request):
    forms = FormDefinition.objects.all().order_by("slug")
    return render(request, "builder/home.html", {"forms": forms})


@staff_member_required
def form_editor(request, form_id):
    form_def = get_object_or_404(FormDefinition, id=form_id)
    questions = form_def.questions.select_related("section").all()
    sections = form_def.sections.all()
    return render(
        request,
        "builder/form_editor.html",
        {"form_def": form_def, "questions": questions, "sections": sections},
    )


@staff_member_required
@require_POST
def question_add(request, form_id):
    form_def = get_object_or_404(FormDefinition, id=form_id)
    q = Question.objects.create(
        form=form_def,
        text="Nueva pregunta",
        slug=f"q{Question.objects.count()+1}",
        field_type="short_text",
        required=False,
        active=True,
        help_text="",   # ✅ keep existing field available
    )
    questions = form_def.questions.select_related("section").all()
    sections = form_def.sections.all()
    return render(
        request,
        "builder/partials/question_list.html",
        {"questions": questions, "sections": sections, "form_def": form_def},
    )


@staff_member_required
def question_panel(request, question_id):
    q = get_object_or_404(Question, id=question_id)

    # ✅ expose separate fields for editing
    pre_text, pre_hr, help_text_clean = split_help_text(q.help_text)

    return render(
        request,
        "builder/partials/question_panel.html",
        {
            "q": q,
            "pre_text": pre_text,
            "pre_hr": pre_hr,
            "help_text_clean": help_text_clean,
            "sections": q.form.sections.all(),
            "form_def": q.form,
        },
    )


@staff_member_required
@require_POST
def question_update(request, question_id):
    q = get_object_or_404(Question, id=question_id)

    # Existing fields (unchanged behavior)
    q.text = request.POST.get("text", q.text)
    q.slug = request.POST.get("slug", q.slug)
    field_type = request.POST.get("field_type", q.field_type)
    if field_type == "single_choice":  # legacy alias
        field_type = Question.CHOICE
    q.field_type = field_type
    q.required = request.POST.get("required") == "on"
    q.active = request.POST.get("active") == "on"
    q.confirm_value = request.POST.get("confirm_value") == "on"

    # ✅ NEW: store “text above + hr” inside q.help_text
    pre_text = request.POST.get("pre_text", "")
    pre_hr = request.POST.get("pre_hr") == "on"
    help_text_clean = request.POST.get("help_text", "")

    section_id = request.POST.get("section_id") or ""

    q.help_text = pack_help_text(pre_text, pre_hr, help_text_clean)

    if section_id.strip():
        try:
            q.section = Section.objects.get(id=int(section_id), form=q.form)
        except (Section.DoesNotExist, ValueError):
            q.section = None
    else:
        q.section = None

    q.save()
    resp = HttpResponse("✅ Guardado")
    resp["HX-Trigger"] = "section-changed"
    return resp


@staff_member_required
@require_POST
def question_delete(request, question_id):
    q = get_object_or_404(Question, id=question_id)
    form_def = q.form
    q.delete()
    questions = form_def.questions.select_related("section").all()
    sections = form_def.sections.all()
    return render(
        request,
        "builder/partials/question_list.html",
        {"questions": questions, "sections": sections, "form_def": form_def},
    )


@staff_member_required
def question_list(request, form_id):
    form_def = get_object_or_404(FormDefinition, id=form_id)
    questions = form_def.questions.select_related("section").all()
    sections = form_def.sections.all()
    return render(
        request,
        "builder/partials/question_list.html",
        {"questions": questions, "sections": sections, "form_def": form_def},
    )


@staff_member_required
@require_POST
def question_assign_section(request, question_id):
    q = get_object_or_404(Question.objects.select_related("form"), id=question_id)
    section_id = request.POST.get("section_id") or ""

    if section_id.strip():
        try:
            section = Section.objects.get(id=int(section_id), form=q.form)
        except (Section.DoesNotExist, ValueError):
            section = None
    else:
        section = None

    q.section = section
    q.save(update_fields=["section"])

    questions = q.form.questions.select_related("section").all()
    sections = q.form.sections.all()
    resp = render(
        request,
        "builder/partials/question_list.html",
        {"questions": questions, "sections": sections, "form_def": q.form},
    )
    resp["HX-Trigger"] = "section-changed"
    return resp


@staff_member_required
@require_POST
def update_default_section_title(request, form_id):
    form_def = get_object_or_404(FormDefinition, id=form_id)
    title = (request.POST.get("default_section_title") or "").strip()
    if title:
        form_def.default_section_title = title
        form_def.save(update_fields=["default_section_title"])
    resp = HttpResponse("")
    resp["HX-Trigger"] = "section-changed"
    return resp


@staff_member_required
@require_POST
def section_add(request, form_id):
    form_def = get_object_or_404(FormDefinition, id=form_id)
    max_pos = form_def.sections.aggregate(max_pos=Max("position")).get("max_pos") or 0
    Section.objects.create(
        form=form_def,
        title="Nueva sección",
        description="",
        position=max_pos + 1,
    )
    sections = form_def.sections.all()
    resp = render(
        request,
        "builder/partials/section_list.html",
        {"sections": sections, "form_def": form_def},
    )
    resp["HX-Trigger"] = "section-changed"
    return resp


@staff_member_required
@require_POST
def section_update(request, section_id):
    section = get_object_or_404(Section, id=section_id)

    section.title = request.POST.get("title", section.title)
    section.description = request.POST.get("description", section.description)
    try:
        section.position = int(request.POST.get("position", section.position) or section.position)
    except ValueError:
        pass
    sid = request.POST.get("show_if_question") or ""
    try:
        section.show_if_question = Question.objects.get(id=int(sid), form=section.form) if sid else None
    except (Question.DoesNotExist, ValueError):
        section.show_if_question = None
    section.show_if_value = (request.POST.get("show_if_value") or "").strip()
    section.save()

    sections = section.form.sections.select_related("show_if_question").all()
    resp = render(
        request,
        "builder/partials/section_list.html",
        {"sections": sections, "form_def": section.form},
    )
    resp["HX-Trigger"] = "section-changed"
    return resp


@staff_member_required
@require_POST
def section_delete(request, section_id):
    section = get_object_or_404(Section, id=section_id)
    form_def = section.form

    # Unassign questions that belonged to this section
    form_def.questions.filter(section=section).update(section=None)
    section.delete()

    sections = form_def.sections.all()
    resp = render(
        request,
        "builder/partials/section_list.html",
        {"sections": sections, "form_def": form_def},
    )
    resp["HX-Trigger"] = "section-changed"
    return resp
