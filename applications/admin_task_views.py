from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render

from .forms_admin import UserTaskAssignForm, WebsiteRevisionRequestForm
from .models import UserTask


@staff_member_required
def task_manager_home(request):
    user_model = get_user_model()
    users = user_model.objects.order_by("email").annotate(
        total_tasks=Count("assigned_tasks"),
        open_tasks=Count(
            "assigned_tasks",
            filter=~Q(assigned_tasks__status=UserTask.STATUS_DONE),
        ),
        follow_up_tasks=Count(
            "assigned_tasks",
            filter=Q(assigned_tasks__follow_up_requested=True)
            & ~Q(assigned_tasks__status=UserTask.STATUS_DONE),
        ),
    )
    return render(request, "admin_dash/task_manager_home.html", {"users": users})


@staff_member_required
def task_manager_user_tasks(request, user_id: int):
    user_model = get_user_model()
    user_obj = get_object_or_404(user_model, id=user_id)

    if request.method == "POST":
        task_id = request.POST.get("task_id")
        action = (request.POST.get("action") or "").strip().lower()
        task = get_object_or_404(UserTask, id=task_id, assigned_to=user_obj)

        if action == "mark_done":
            task.status = UserTask.STATUS_DONE
            task.save(update_fields=["status", "updated_at"])
            messages.success(request, "Task marked as done.")
        elif action == "reopen":
            task.status = UserTask.STATUS_OPEN
            task.save(update_fields=["status", "updated_at"])
            messages.success(request, "Task reopened.")

        return redirect("admin_task_manager_user_tasks", user_id=user_obj.id)

    tasks = (
        UserTask.objects.filter(assigned_to=user_obj)
        .select_related("assigned_to", "created_by", "requested_by")
        .order_by("-created_at")
    )
    return render(
        request,
        "admin_dash/task_manager_user_tasks.html",
        {"target_user": user_obj, "tasks": tasks},
    )


@staff_member_required
def task_manager_assign(request):
    if request.method == "POST":
        form = UserTaskAssignForm(request.POST)
        if form.is_valid():
            task = form.save(commit=False)
            task.created_by = request.user
            task.save()
            messages.success(request, "Task assigned successfully.")
            return redirect("admin_task_manager_user_tasks", user_id=task.assigned_to_id)
    else:
        form = UserTaskAssignForm()

    return render(request, "admin_dash/task_assign.html", {"form": form})


@staff_member_required
def task_manager_follow_up(request):
    tasks = (
        UserTask.objects.filter(follow_up_requested=True)
        .exclude(status=UserTask.STATUS_DONE)
        .select_related("assigned_to", "created_by", "requested_by")
        .order_by("due_date", "-created_at")
    )
    return render(request, "admin_dash/task_follow_up.html", {"tasks": tasks})


@staff_member_required
def task_manager_website_revisions(request):
    if request.method == "POST":
        form = WebsiteRevisionRequestForm(request.POST)
        if form.is_valid():
            task = form.save(commit=False)
            task.task_type = UserTask.TYPE_WEBSITE_REVISION
            task.status = UserTask.STATUS_OPEN
            task.follow_up_requested = True
            task.created_by = request.user
            if not task.assigned_to:
                task.assigned_to = task.requested_by or request.user
            task.save()
            messages.success(request, "Website revision request created.")
            return redirect("admin_task_manager_website_revisions")
    else:
        form = WebsiteRevisionRequestForm(
            initial={"requested_by": request.user, "assigned_to": request.user}
        )

    revisions = (
        UserTask.objects.filter(task_type=UserTask.TYPE_WEBSITE_REVISION)
        .select_related("assigned_to", "created_by", "requested_by")
        .order_by("-created_at")
    )
    return render(
        request,
        "admin_dash/task_website_revisions.html",
        {"form": form, "revisions": revisions},
    )
