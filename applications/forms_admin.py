# applications/forms_admin.py
from django import forms
from django.contrib.auth import get_user_model

from .models import TaskType, UserTask, ensure_default_task_types

class InviteUserForm(forms.Form):
    email = forms.EmailField(label="Email")
    first_name = forms.CharField(label="First name", required=False)
    last_name = forms.CharField(label="Last name", required=False)
    is_staff = forms.BooleanField(label="Admin access (staff)", required=True, initial=True)
    is_superuser = forms.BooleanField(label="Super admin", required=False, initial=False)


class UserTaskAssignForm(forms.ModelForm):
    class Meta:
        model = UserTask
        fields = [
            "assigned_to",
            "title",
            "description",
            "task_type_ref",
            "priority",
            "impact",
            "status",
            "follow_up_requested",
            "due_date",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "impact": forms.Textarea(attrs={"rows": 3}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ensure_default_task_types()
        user_model = get_user_model()
        self.fields["assigned_to"].queryset = user_model.objects.order_by("email")
        self.fields["task_type_ref"].queryset = TaskType.objects.filter(is_active=True).order_by("position", "name")
        self.fields["task_type_ref"].label = "Task type"
        self.fields["task_type_ref"].required = True
        self.fields["impact"].label = "Impact"
        if not self.instance.pk and not self.initial.get("task_type_ref"):
            default_type = self.fields["task_type_ref"].queryset.first()
            if default_type:
                self.fields["task_type_ref"].initial = default_type


class WebsiteRevisionRequestForm(forms.ModelForm):
    class Meta:
        model = UserTask
        fields = [
            "requested_by",
            "assigned_to",
            "title",
            "description",
            "priority",
            "impact",
            "due_date",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "impact": forms.Textarea(attrs={"rows": 3}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ensure_default_task_types()
        user_model = get_user_model()
        ordered_users = user_model.objects.order_by("email")
        self.fields["requested_by"].queryset = ordered_users
        self.fields["assigned_to"].queryset = ordered_users
        self.fields["assigned_to"].required = False
        self.fields["impact"].label = "Impact"


class TaskTypeAdminForm(forms.ModelForm):
    class Meta:
        model = TaskType
        fields = ["name", "slug", "position", "is_active", "is_revision_type"]
