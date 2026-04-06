# applications/forms_admin.py
from django import forms
from django.contrib.auth import get_user_model

from .models import UserTask

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
            "task_type",
            "status",
            "follow_up_requested",
            "due_date",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user_model = get_user_model()
        self.fields["assigned_to"].queryset = user_model.objects.order_by("email")


class WebsiteRevisionRequestForm(forms.ModelForm):
    class Meta:
        model = UserTask
        fields = [
            "requested_by",
            "assigned_to",
            "title",
            "description",
            "due_date",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user_model = get_user_model()
        ordered_users = user_model.objects.order_by("email")
        self.fields["requested_by"].queryset = ordered_users
        self.fields["assigned_to"].queryset = ordered_users
        self.fields["assigned_to"].required = False
