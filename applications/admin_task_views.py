import logging

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db.models import Count, Q
from django.conf import settings
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms_admin import UserTaskAssignForm, UserTaskEditForm
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


def _send_requester_completion_email(request, task: UserTask) -> None:
    requester = task.requested_by
    requester_email = (getattr(requester, "email", "") or "").strip()
    if not requester_email:
        return

    task_url = request.build_absolute_uri(
        reverse("admin_task_manager_edit", kwargs={"task_id": task.id})
    )

    subject = f"Task completed: {task.title}"
    body = (
        f"Hi,\n\n"
        f"The task you requested has been marked as completed.\n\n"
        f"Title: {task.title}\n"
        f"Assigned to: {task.assigned_to}\n"
        f"Type: {task.task_type_name}\n"
        f"Priority: {task.get_priority_display()}\n"
        f"Completed status: {task.get_status_display()}\n\n"
        f"Impact:\n{task.impact or 'Not specified'}\n\n"
        f"Description:\n{task.description or 'No description provided'}\n\n"
        f"View/edit task: {task_url}\n"
    )

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            recipient_list=[requester_email],
            fail_silently=False,
        )
    except Exception:
        logger.exception(
            "Requester completion email failed for task_id=%s requester_id=%s",
            task.id,
            getattr(requester, "id", None),
        )
        messages.warning(
            request,
            "Task was completed, but the requester notification email could not be sent.",
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
def task_manager_my_tasks(request):
    return redirect("admin_task_manager_user_tasks", user_id=request.user.id)


@staff_member_required
def task_manager_overview(request):
    ensure_default_task_types()
    user_model = get_user_model()
    users = user_model.objects.order_by("email")

    selected_user_id = (request.GET.get("assignee") or "").strip()
    selected_user = None
    tasks = UserTask.objects.all()
    if selected_user_id:
        try:
            selected_user = users.get(id=int(selected_user_id))
            tasks = tasks.filter(assigned_to_id=selected_user.id)
        except (ValueError, user_model.DoesNotExist):
            selected_user_id = ""

    tasks = (
        tasks.select_related("assigned_to", "created_by", "requested_by", "task_type_ref")
        .order_by("-created_at")
    )
    return render(
        request,
        "admin_dash/task_overview.html",
        {
            "tasks": tasks,
            "users": users,
            "selected_user_id": selected_user_id,
            "selected_user": selected_user,
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
            was_done = task.status == UserTask.STATUS_DONE
            task.status = UserTask.STATUS_DONE
            task.save(update_fields=["status", "updated_at"])
            if not was_done:
                _send_requester_completion_email(request, task)
                messages.success(request, "Task marked as done and requester notified.")
            else:
                messages.success(request, "Task is already done.")
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
def task_manager_edit(request, task_id: int):
    ensure_default_task_types()
    task = get_object_or_404(
        UserTask.objects.select_related("assigned_to", "requested_by", "task_type_ref"),
        id=task_id,
    )

    if request.method == "POST":
        old_status = task.status
        form = UserTaskEditForm(request.POST, instance=task)
        if form.is_valid():
            task = form.save(commit=False)
            if task.task_type_ref_id:
                task.task_type = task.task_type_ref.slug
            task.save()
            if old_status != UserTask.STATUS_DONE and task.status == UserTask.STATUS_DONE:
                _send_requester_completion_email(request, task)
                messages.success(request, "Task updated and requester notified of completion.")
            else:
                messages.success(request, "Task updated.")
            return redirect("admin_task_manager_user_tasks", user_id=task.assigned_to_id)
    else:
        form = UserTaskEditForm(instance=task)

    return render(
        request,
        "admin_dash/task_edit.html",
        {
            "task": task,
            "form": form,
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
            task.created_by = form.cleaned_data.get("created_by") or request.user
            if task.task_type_ref_id:
                task.task_type = task.task_type_ref.slug
            task.save()
            _send_assignment_email(request, task)
            messages.success(request, "Task assigned successfully.")
            return redirect("admin_task_manager_user_tasks", user_id=task.assigned_to_id)
    else:
        form = UserTaskAssignForm(initial={"created_by": request.user})

    return render(
        request,
        "admin_dash/task_assign.html",
        {
            "form": form,
            "task_type_admin_url": _task_type_link(),
        },
    )


@staff_member_required
def task_manager_website_revisions(request):
    ensure_default_task_types()

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
            "revisions": revisions,
            "task_type_admin_url": _task_type_link(),
        },
    )
