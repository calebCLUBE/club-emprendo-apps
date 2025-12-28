from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.contrib.sites.shortcuts import get_current_site
from django.core.mail import send_mail
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

@staff_member_required
def invite_user(request, user_id: int):
    user = User.objects.get(pk=user_id)

    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)

    domain = get_current_site(request).domain
    reset_link = f"https://{domain}{reverse('password_reset_confirm', kwargs={'uidb64': uid, 'token': token})}"

    send_mail(
        subject="Set up your Club Emprendo admin password",
        message=f"Hi {user.username},\n\nSet your password here:\n{reset_link}\n\n",
        from_email=None,
        recipient_list=[user.email],
        fail_silently=False,
    )

    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/admin/auth/user/"))
