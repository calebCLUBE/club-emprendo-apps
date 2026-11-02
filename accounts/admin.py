# accounts/admin.py

import logging

from django.conf import settings
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.http import Http404
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.views.decorators.debug import sensitive_post_parameters
from django.utils.decorators import method_decorator

from .models import User

logger = logging.getLogger(__name__)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """
    Basic admin for your custom user model.
    Adjust fields if your User model differs.
    """

    model = User
    change_form_template = "admin/accounts/user/change_form.html"

    @method_decorator(sensitive_post_parameters())
    def user_change_password(self, request, id, form_url=""):
        user = self.get_object(request, id)
        if user is None:
            raise Http404("User not found")
        if not self.has_change_permission(request, user):
            raise PermissionDenied

        if request.method == "POST":
            if not user.is_active or not user.email:
                self.message_user(request, "An active account with an email address is required.", messages.ERROR)
            else:
                reset_url = request.build_absolute_uri(reverse(
                    "password_reset_confirm",
                    kwargs={
                        "uidb64": urlsafe_base64_encode(force_bytes(user.pk)),
                        "token": default_token_generator.make_token(user),
                    },
                ))
                try:
                    sent = send_mail(
                        "Reset your Club Emprendo password",
                        render_to_string("emails/password_reset.txt", {"user": user, "reset_url": reset_url}),
                        settings.DEFAULT_FROM_EMAIL,
                        [user.email],
                        fail_silently=False,
                    )
                    if not sent:
                        raise RuntimeError("Email backend did not accept the message")
                except Exception:
                    logger.exception("Password reset email failed for user %s", user.pk)
                    self.message_user(request, "The reset email could not be sent. Please try again or check the email configuration.", messages.ERROR)
                else:
                    self.log_change(request, user, "Sent password reset email.")
                    self.message_user(request, f"Password reset email sent to {user.email}.", messages.SUCCESS)
                    return redirect("admin:accounts_user_change", user.pk)

        context = {
            **self.admin_site.each_context(request),
            "title": "Send password reset email",
            "opts": self.model._meta,
            "original": user,
        }
        return TemplateResponse(request, "admin/accounts/user/send_password_reset.html", context)
    list_display = ("id", "display_name", "email", "is_staff", "is_superuser", "is_active")
    list_filter = ("is_staff", "is_superuser", "is_active")
    ordering = ("email",)

    fieldsets = (
        (None, {"fields": ("email", "password", "full_name", "first_name", "last_name")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login",)}),
    )

    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": (
                "email",
                "full_name",
                "first_name",
                "last_name",
                "password1",
                "password2",
                "is_staff",
                "is_superuser",
                "is_active",
            ),
        }),
    )

    search_fields = ("email", "full_name", "first_name", "last_name")
