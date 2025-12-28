from django import forms
from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.sites.shortcuts import get_current_site
from django.urls import reverse

User = get_user_model()


class InviteUserCreationForm(forms.ModelForm):
    # Force email + names on the form
    email = forms.EmailField(required=True)
    first_name = forms.CharField(required=False)
    last_name = forms.CharField(required=False)

    class Meta:
        model = User
        fields = ("email", "first_name", "last_name", "is_staff", "is_superuser", "is_active")

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)

        # Use email as username if your project still uses Django's default User model.
        # (Default User requires username, so we set it automatically.)
        if hasattr(user, "username") and not user.username:
            user.username = self.cleaned_data["email"]

        user.email = self.cleaned_data["email"].strip().lower()

        # Prevent logging in until they set a password
        user.set_unusable_password()

        if commit:
            user.save()
        return user


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """
    Replaces the default auth User admin with an invite-based flow.
    """
    add_form = InviteUserCreationForm

    # What admin shows in list
    list_display = ("email", "first_name", "last_name", "is_staff", "is_active", "last_login")
    ordering = ("email",)

    # Edit form layout (existing users)
    fieldsets = (
        (None, {"fields": ("username", "email", "first_name", "last_name")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )

    # Add form layout (new users)
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "first_name", "last_name", "is_active", "is_staff", "is_superuser"),
        }),
    )

    search_fields = ("email", "first_name", "last_name")

    def response_add(self, request, obj, post_url_continue=None):
        """
        After creating the user, send them a password setup email.
        """
        # Use Django's PasswordResetForm to generate a secure token link
        reset_form = PasswordResetForm(data={"email": obj.email})
        if reset_form.is_valid():
            # Build the domain (use your live domain)
            domain = get_current_site(request).domain  # requires sites framework; see note below
            protocol = "https" if request.is_secure() else "https"  # Render is https

            reset_form.save(
                request=request,
                use_https=(protocol == "https"),
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                email_template_name="emails/invite_set_password_email.txt",
                subject_template_name="emails/invite_set_password_subject.txt",
                domain_override=domain,
            )

        return super().response_add(request, obj, post_url_continue)
