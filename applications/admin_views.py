# applications/admin_views.py
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, get_object_or_404
from .models import Application, FormDefinition


@staff_member_required
def apps_list(request):
    """
    Shows *forms* (E_A1, E_A2, M_A1, M_A2 etc) so you can manage each application type.
    This is usually what people mean by "each application".
    """
    forms = FormDefinition.objects.all().order_by("slug")
    return render(request, "admin_dash/apps_list.html", {"forms": forms})


@staff_member_required
def app_form_detail(request, form_id):
    """
    Admin page for a specific FormDefinition (application type).
    We'll add buttons here: preview, responder link, edit, export CSV, duplicate, rename, archive.
    """
    form_def = get_object_or_404(FormDefinition, id=form_id)
    submissions = (
        Application.objects.filter(form=form_def)
        .order_by("-created_at")
    )
    return render(
        request,
        "admin_dash/app_form_detail.html",
        {"form_def": form_def, "submissions": submissions},
    )


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
