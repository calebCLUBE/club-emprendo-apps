# applications/forms_admin.py
from django import forms
from django.contrib.auth import get_user_model

from .models import TaskType, UserTask, ensure_default_task_types


def _user_label(user) -> str:
    full_name = (getattr(user, "full_name", "") or "").strip()
    if full_name:
        return full_name
    first_name = (getattr(user, "first_name", "") or "").strip()
    last_name = (getattr(user, "last_name", "") or "").strip()
    composed = f"{first_name} {last_name}".strip()
    if composed:
        return composed
    return (getattr(user, "email", "") or "").strip() or str(user)

class InviteUserForm(forms.Form):
    email = forms.EmailField(label="Email")
    first_name = forms.CharField(label="First name", required=False)
    last_name = forms.CharField(label="Last name", required=False)
    is_staff = forms.BooleanField(label="Admin access (staff)", required=True, initial=True)
    is_superuser = forms.BooleanField(label="Super admin", required=False, initial=False)


class UserTaskAssignForm(forms.ModelForm):
    assignees = forms.ModelMultipleChoiceField(
        queryset=get_user_model().objects.none(),
        label="Assign to",
        required=True,
        help_text="Choose one or more users.",
        widget=forms.SelectMultiple(attrs={"size": 10}),
    )

    class Meta:
        model = UserTask
        fields = [
            "requested_by",
            "assignees",
            "title",
            "description",
            "task_type_ref",
            "priority",
            "hours_estimate",
            "impact",
            "status",
            "due_date",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "hours_estimate": forms.NumberInput(attrs={"step": "0.25", "min": "0"}),
            "impact": forms.Textarea(attrs={"rows": 3}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ensure_default_task_types()
        user_model = get_user_model()
        ordered_users = user_model.objects.order_by("full_name", "first_name", "last_name", "email")
        self.fields["requested_by"].queryset = ordered_users
        self.fields["requested_by"].label = "Requested by"
        self.fields["requested_by"].required = True
        self.fields["requested_by"].label_from_instance = _user_label
        self.fields["assignees"].queryset = ordered_users
        self.fields["assignees"].label_from_instance = _user_label
        self.fields["task_type_ref"].queryset = TaskType.objects.filter(is_active=True).order_by("position", "name")
        self.fields["task_type_ref"].label = "Task type"
        self.fields["task_type_ref"].required = True
        self.fields["hours_estimate"].label = "Hours Estimate"
        self.fields["impact"].label = "Impact"
        self.fields["impact"].help_text = ""
        if not self.instance.pk and not self.initial.get("task_type_ref"):
            default_type = self.fields["task_type_ref"].queryset.first()
            if default_type:
                self.fields["task_type_ref"].initial = default_type


class UserTaskEditForm(forms.ModelForm):
    class Meta:
        model = UserTask
        fields = [
            "requested_by",
            "assigned_to",
            "title",
            "description",
            "task_type_ref",
            "priority",
            "hours_estimate",
            "impact",
            "status",
            "due_date",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "hours_estimate": forms.NumberInput(attrs={"step": "0.25", "min": "0"}),
            "impact": forms.Textarea(attrs={"rows": 3}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ensure_default_task_types()
        user_model = get_user_model()
        ordered_users = user_model.objects.order_by("full_name", "first_name", "last_name", "email")
        self.fields["requested_by"].queryset = ordered_users
        self.fields["requested_by"].required = False
        self.fields["requested_by"].label_from_instance = _user_label
        self.fields["assigned_to"].queryset = ordered_users
        self.fields["assigned_to"].label_from_instance = _user_label
        self.fields["task_type_ref"].queryset = TaskType.objects.filter(is_active=True).order_by("position", "name")
        self.fields["task_type_ref"].label = "Task type"
        self.fields["task_type_ref"].required = True
        self.fields["hours_estimate"].label = "Hours Estimate"
        self.fields["impact"].label = "Impact"
        if not self.instance.task_type_ref_id and self.instance.task_type:
            self.initial["task_type_ref"] = (
                TaskType.objects.filter(slug=self.instance.task_type).values_list("id", flat=True).first()
            )


class WebsiteRevisionRequestForm(forms.ModelForm):
    class Meta:
        model = UserTask
        fields = [
            "requested_by",
            "assigned_to",
            "title",
            "description",
            "priority",
            "hours_estimate",
            "impact",
            "due_date",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "hours_estimate": forms.NumberInput(attrs={"step": "0.25", "min": "0"}),
            "impact": forms.Textarea(attrs={"rows": 3}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ensure_default_task_types()
        user_model = get_user_model()
        ordered_users = user_model.objects.order_by("full_name", "first_name", "last_name", "email")
        self.fields["requested_by"].queryset = ordered_users
        self.fields["requested_by"].label_from_instance = _user_label
        self.fields["assigned_to"].queryset = ordered_users
        self.fields["assigned_to"].required = False
        self.fields["assigned_to"].label_from_instance = _user_label
        self.fields["hours_estimate"].label = "Hours Estimate"
        self.fields["impact"].label = "Impact"


class TaskTypeAdminForm(forms.ModelForm):
    class Meta:
        model = TaskType
        fields = ["name", "slug", "position", "is_active", "is_revision_type"]
