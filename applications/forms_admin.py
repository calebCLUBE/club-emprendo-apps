# applications/forms_admin.py
from django import forms

class InviteUserForm(forms.Form):
    email = forms.EmailField(label="Email")
    first_name = forms.CharField(label="First name", required=False)
    last_name = forms.CharField(label="Last name", required=False)
    is_staff = forms.BooleanField(label="Admin access (staff)", required=True, initial=True)
    is_superuser = forms.BooleanField(label="Super admin", required=False, initial=False)
