import re

from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_POST
from django.http import HttpResponse

from applications.models import FormDefinition, Question, Choice


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
    questions = form_def.questions.all()
    return render(
        request,
        "builder/form_editor.html",
        {"form_def": form_def, "questions": questions},
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
    questions = form_def.questions.all()
    return render(request, "builder/partials/question_list.html", {"questions": questions})


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
        },
    )


@staff_member_required
@require_POST
def question_update(request, question_id):
    q = get_object_or_404(Question, id=question_id)

    # Existing fields (unchanged behavior)
    q.text = request.POST.get("text", q.text)
    q.slug = request.POST.get("slug", q.slug)
    q.field_type = request.POST.get("field_type", q.field_type)
    q.required = request.POST.get("required") == "on"
    q.active = request.POST.get("active") == "on"

    # ✅ NEW: store “text above + hr” inside q.help_text
    pre_text = request.POST.get("pre_text", "")
    pre_hr = request.POST.get("pre_hr") == "on"
    help_text_clean = request.POST.get("help_text", "")

    q.help_text = pack_help_text(pre_text, pre_hr, help_text_clean)

    q.save()
    return HttpResponse("✅ Guardado")


@staff_member_required
@require_POST
def question_delete(request, question_id):
    q = get_object_or_404(Question, id=question_id)
    form_def = q.form
    q.delete()
    questions = form_def.questions.all()
    return render(request, "builder/partials/question_list.html", {"questions": questions})
