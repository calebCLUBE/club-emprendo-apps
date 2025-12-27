from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_POST
from django.http import HttpResponse

from applications.models import FormDefinition, Question, Choice


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
        form=form_def,          # IMPORTANT: if your FK isn't named 'form', change it
        text="Nueva pregunta",
        slug=f"q{Question.objects.count()+1}",
        field_type="short_text",
        required=False,
        active=True,
    )
    questions = form_def.questions.all()
    return render(request, "builder/partials/question_list.html", {"questions": questions})


@staff_member_required
def question_panel(request, question_id):
    q = get_object_or_404(Question, id=question_id)
    return render(request, "builder/partials/question_panel.html", {"q": q})


@staff_member_required
@require_POST
def question_update(request, question_id):
    q = get_object_or_404(Question, id=question_id)
    q.text = request.POST.get("text", q.text)
    q.slug = request.POST.get("slug", q.slug)
    q.field_type = request.POST.get("field_type", q.field_type)
    q.required = request.POST.get("required") == "on"
    q.active = request.POST.get("active") == "on"
    q.save()
    return HttpResponse("✅ Guardado")


@staff_member_required
@require_POST
def question_delete(request, question_id):
    q = get_object_or_404(Question, id=question_id)
    form_def = q.form         # IMPORTANT: if FK isn't named 'form', change this too
    q.delete()
    questions = form_def.questions.all()
    return render(request, "builder/partials/question_list.html", {"questions": questions})
