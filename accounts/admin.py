# accounts/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import PasswordResetForm
from django.utils.html import format_html
from django.urls import reverse
from django import forms

from .models import User


class UserCreationInviteForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("email", "full_name")

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if not email:
            raise forms.ValidationError("Email is required.")
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    model = User

    # ✅ remove filters (also requested)
    list_filter = ()

    ordering = ("email",)
    search_fields = ("email", "full_name")
    list_display = ("email", "full_name", "is_staff", "is_active", "invite_link")

    # Use our minimal add form
    add_form = UserCreationInviteForm
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "full_name", "is_staff", "is_active")}),
    )

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("full_name",)}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )

    def save_model(self, request, obj, form, change):
        is_new = obj.pk is None
        super().save_model(request, obj, form, change)

        # If created in admin, force password setup via email
        if is_new:
            obj.set_unusable_password()
            obj.save(update_fields=["password"])
            self._send_password_setup_email(request, obj)

    def _send_password_setup_email(self, request, user):
        if not user.email:
            return

        # Uses Django’s password reset email (tokenized link)
        prf = PasswordResetForm({"email": user.email})
        if prf.is_valid():
            prf.save(
                request=request,
                use_https=request.is_secure(),
                from_email=None,  # uses DEFAULT_FROM_EMAIL
                email_template_name="registration/password_reset_email.html",
                subject_template_name="registration/password_reset_subject.txt",
            )

    def invite_link(self, obj):
        # optional manual resend button
        url = reverse("admin:accounts_user_change", args=[obj.pk])
        return format_html('<a class="button" href="{}">Edit</a>', url)

    invite_link.short_description = "Actions"
