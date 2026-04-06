import logging

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db.models import Count, Q
from django.conf import settings
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms_admin import UserTaskAssignForm, WebsiteRevisionRequestForm
from .models import TaskType, UserTask, ensure_default_task_types

logger = logging.getLogger(__name__)


def _task_type_link() -> str:
    return reverse("admin:applications_tasktype_changelist")


def _website_revision_task_type() -> TaskType | None:
    revision_type = (
        TaskType.objects.filter(is_revision_type=True, is_active=True)
        .order_by("position", "id")
        .first()
    )
    if revision_type:
        return revision_type
    return TaskType.objects.filter(slug=UserTask.TYPE_WEBSITE_REVISION).first()


def _send_assignment_email(request, task: UserTask) -> None:
    assignee_email = (task.assigned_to.email or "").strip()
    if not assignee_email:
        return

    task_url = request.build_absolute_uri(
        reverse("admin_task_manager_user_tasks", kwargs={"user_id": task.assigned_to_id})
    )

    subject = f"New task assigned: {task.title}"
    body = (
        f"Hi,\n\n"
        f"A new task has been assigned to you in Club Emprendo Task Manager.\n\n"
        f"Title: {task.title}\n"
        f"Type: {task.task_type_name}\n"
        f"Priority: {task.get_priority_display()}\n"
        f"Status: {task.get_status_display()}\n"
        f"Due date: {task.due_date or 'Not set'}\n\n"
        f"Impact:\n{task.impact or 'Not specified'}\n\n"
        f"Description:\n{task.description or 'No description provided'}\n\n"
        f"View tasks: {task_url}\n"
    )

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            recipient_list=[assignee_email],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Task assignment email failed for task_id=%s", task.id)
        messages.warning(
            request,
            "Task was assigned, but the email notification could not be sent.",
        )


@staff_member_required
def task_manager_home(request):
    ensure_default_task_types()
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
    return render(
        request,
        "admin_dash/task_manager_home.html",
        {
            "users": users,
            "task_type_admin_url": _task_type_link(),
        },
    )


@staff_member_required
def task_manager_user_tasks(request, user_id: int):
    ensure_default_task_types()
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
        .select_related("assigned_to", "created_by", "requested_by", "task_type_ref")
        .order_by("-created_at")
    )
    return render(
        request,
        "admin_dash/task_manager_user_tasks.html",
        {
            "target_user": user_obj,
            "tasks": tasks,
            "task_type_admin_url": _task_type_link(),
        },
    )


@staff_member_required
def task_manager_assign(request):
    ensure_default_task_types()
    if request.method == "POST":
        form = UserTaskAssignForm(request.POST)
        if form.is_valid():
            task = form.save(commit=False)
            task.created_by = request.user
            if task.task_type_ref_id:
                task.task_type = task.task_type_ref.slug
            task.save()
            _send_assignment_email(request, task)
            messages.success(request, "Task assigned successfully.")
            return redirect("admin_task_manager_user_tasks", user_id=task.assigned_to_id)
    else:
        form = UserTaskAssignForm()

    return render(
        request,
        "admin_dash/task_assign.html",
        {
            "form": form,
            "task_type_admin_url": _task_type_link(),
        },
    )


@staff_member_required
def task_manager_follow_up(request):
    ensure_default_task_types()
    tasks = (
        UserTask.objects.filter(follow_up_requested=True)
        .exclude(status=UserTask.STATUS_DONE)
        .select_related("assigned_to", "created_by", "requested_by", "task_type_ref")
        .order_by("due_date", "-created_at")
    )
    return render(
        request,
        "admin_dash/task_follow_up.html",
        {
            "tasks": tasks,
            "task_type_admin_url": _task_type_link(),
        },
    )


@staff_member_required
def task_manager_website_revisions(request):
    ensure_default_task_types()
    revision_type = _website_revision_task_type()

    if request.method == "POST":
        form = WebsiteRevisionRequestForm(request.POST)
        if form.is_valid():
            task = form.save(commit=False)
            if revision_type:
                task.task_type_ref = revision_type
                task.task_type = revision_type.slug
            else:
                task.task_type = UserTask.TYPE_WEBSITE_REVISION
            task.status = UserTask.STATUS_OPEN
            task.follow_up_requested = True
            task.created_by = request.user
            if not task.assigned_to:
                task.assigned_to = task.requested_by or request.user
            task.save()
            _send_assignment_email(request, task)
            messages.success(request, "Website revision request created.")
            return redirect("admin_task_manager_website_revisions")
    else:
        form = WebsiteRevisionRequestForm(
            initial={
                "requested_by": request.user,
                "assigned_to": request.user,
                "priority": UserTask.PRIORITY_MEDIUM,
            }
        )

    revisions = (
        UserTask.objects.filter(
            Q(task_type_ref__is_revision_type=True)
            | Q(task_type=UserTask.TYPE_WEBSITE_REVISION)
        )
        .select_related("assigned_to", "created_by", "requested_by", "task_type_ref")
        .order_by("-created_at")
    )
    return render(
        request,
        "admin_dash/task_website_revisions.html",
        {
            "form": form,
            "revisions": revisions,
            "task_type_admin_url": _task_type_link(),
        },
    )
