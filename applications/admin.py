from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse

from .models import FormDefinition, Question, Choice, Application

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import AdminPasswordChangeForm
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.crypto import get_random_string
from django.core.mail import send_mail
from django.conf import settings

User = get_user_model()


# Unregister default admin (Django registers it automatically)
try:
    admin.site.unregister(User)
except admin.sites.NotRegistered:
    pass


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """
    Custom User admin:
    - cleaner list display
    - optional "Invite user" flow (email them a password-setup link)
    """

    list_display = ("username", "email", "is_staff", "is_active", "last_login", "invite_link")
    list_filter = ("is_staff", "is_superuser", "is_active")
    search_fields = ("username", "email", "first_name", "last_name")
    ordering = ("email",)

    def invite_link(self, obj):
        # only show if user has an email
        if not obj.email:
            return "-"
        url = reverse("admin:auth_user_password_change", args=[obj.pk])
        return format_html('<a class="button" href="{}">Set/Reset password</a>', url)

    invite_link.short_description = "Password"

    # Optional: add a bulk action that emails a reset link
    actions = ["send_password_setup_email"]

    def send_password_setup_email(self, request, queryset):
        """
        Sends password setup instructions.
        This uses Django’s "password reset" flow (recommended), not plain passwords.
        """
        sent = 0
        for user in queryset:
            if not user.email:
                continue

            # Create a one-time password reset link using Django’s built-in mechanism:
            # We don’t generate it manually here; easiest is to point them to the reset page.
            # (If you want a direct tokenized link, we can implement it next.)
            reset_url = request.build_absolute_uri(reverse("admin:password_reset"))
            subject = "Set up your Club Emprendo admin password"
            message = (
                f"Hi {user.get_full_name() or user.username},\n\n"
                f"Please set your password using this link:\n{reset_url}\n\n"
                f"Use your email address ({user.email}).\n"
            )

            send_mail(
                subject,
                message,
                getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@clubemprendo.org"),
                [user.email],
                fail_silently=False,
            )
            sent += 1

        self.message_user(request, f"Sent {sent} password setup email(s).")

    send_password_setup_email.short_description = "Send password setup email"

# ---------- FormDefinition admin (with Preview button) ----------

@admin.register(FormDefinition)
class FormDefinitionAdmin(admin.ModelAdmin):
    """
    Admin for the four form definitions: E_A1, E_A2, M_A1, M_A2.
    Shows a Preview button that opens the public form in a new tab.
    """
    list_display = ("__str__", "slug", "preview_link")
    search_fields = ("slug", "name")
    readonly_fields = ("preview_link",)  # also show in the detail page

    def preview_link(self, obj):
        """
        Map each slug to the correct public/preview URL.
        """
        slug = getattr(obj, "slug", None)
        if not slug:
            return "-"

        if slug == "E_A1":
            url_name = "apply_emprendedora_first"
        elif slug == "E_A2":
            url_name = "preview_emprendedora_second"
        elif slug == "M_A1":
            url_name = "apply_mentora_first"
        elif slug == "M_A2":
            url_name = "preview_mentora_second"
        else:
            return "-"

        try:
            url = reverse(url_name)
        except Exception:
            return "-"

        return format_html(
            '<a href="{}" target="_blank" '
            'style="padding:4px 10px; '
            'background:#163108; color:white; '
            'border-radius:6px; text-decoration:none; '
            'font-size:12px; font-weight:600;">'
            'Preview</a>',
            url,
        )

    preview_link.short_description = "Preview"


# ---------- Very simple admin for the other models ----------

@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("id", "__str__")
    search_fields = ("id",)


@admin.register(Choice)
class ChoiceAdmin(admin.ModelAdmin):
    list_display = ("id", "__str__")
    search_fields = ("id",)


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("id", "__str__")
    search_fields = ("id",)
