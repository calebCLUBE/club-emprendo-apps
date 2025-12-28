# applications/admin_views.py
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordResetForm
from django.shortcuts import render, redirect
from django.contrib import messages

from .forms_admin import InviteUserForm

User = get_user_model()


@staff_member_required
def users_list(request):
    users = User.objects.order_by("email", "username")
    return render(request, "admin_dash/users_list.html", {"users": users})


@staff_member_required
def invite_user(request):
    if request.method == "POST":
        form = InviteUserForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"].strip().lower()
            first_name = form.cleaned_data["first_name"].strip()
            last_name = form.cleaned_data["last_name"].strip()
            is_staff = form.cleaned_data["is_staff"]
            is_superuser = form.cleaned_data["is_superuser"]

            # Create user if not exists
            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    # Django's default User model still needs username unless you changed it.
                    # We'll safely set username = email.
                    "username": email,
                    "first_name": first_name,
                    "last_name": last_name,
                    "is_staff": is_staff,
                    "is_superuser": is_superuser,
                },
            )

            if not created:
                # Update basic fields if user already exists
                user.first_name = first_name
                user.last_name = last_name
                user.is_staff = is_staff
                user.is_superuser = is_superuser
                user.save()

            # Force “no password yet”
            user.set_unusable_password()
            user.save()

            # Send password setup email using Django's password reset flow
            reset_form = PasswordResetForm({"email": email})
            if reset_form.is_valid():
                reset_form.save(
                    request=request,
                    use_https=True,
                    from_email=None,
                    email_template_name="admin_dash/emails/invite_set_password.txt",
                    subject_template_name="admin_dash/emails/invite_subject.txt",
                )
                messages.success(request, f"Invite sent to {email}.")
            else:
                messages.error(request, f"Could not send invite to {email} (email form invalid).")

            return redirect("admin_users_list")
    else:
        form = InviteUserForm()

    return render(request, "admin_dash/invite_user.html", {"form": form})
