import re
from unittest.mock import patch
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class AdminPasswordResetTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("admin@example.com", "Admin-password-739!")
        self.user = get_user_model().objects.create_user("person@example.com", "Original-password-739!")
        self.client.force_login(self.admin)
        self.url = reverse("admin:auth_user_password_change", args=[self.user.pk])

    def test_user_list_links_names_and_emails_and_shows_filter_choices(self):
        response = self.client.get(reverse("admin:accounts_user_changelist"))
        change_url = reverse("admin:accounts_user_change", args=[self.user.pk])
        self.assertContains(response, f'<a href="{change_url}">{self.user.email}</a>', count=2, html=True)
        self.assertContains(response, "By staff status")
        self.assertContains(response, "?is_active__exact=0")

    def test_account_page_links_to_email_reset_and_get_does_not_send(self):
        response = self.client.get(reverse("admin:accounts_user_change", args=[self.user.pk]))
        self.assertContains(response, "Send password reset email")
        self.assertContains(self.client.get(self.url), self.user.email)
        self.assertEqual(len(mail.outbox), 0)

    def test_email_link_changes_only_target_password_and_cannot_be_reused(self):
        original_password = self.user.password
        response = self.client.post(self.url, secure=True)
        self.assertRedirects(response, reverse("admin:accounts_user_change", args=[self.user.pk]))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])
        self.user.refresh_from_db()
        self.assertEqual(self.user.password, original_password)
        link = re.search(r"https://[^\s]+", mail.outbox[0].body).group()
        self.assertEqual(urlsplit(link).netloc, "testserver")
        visitor = Client()
        response = visitor.get(urlsplit(link).path, follow=True)
        self.assertContains(response, "Choose a new password")
        confirm_path = response.request["PATH_INFO"]
        response = visitor.post(confirm_path, {
            "new_password1": "New-password-4829!",
            "new_password2": "New-password-4829!",
        })
        self.assertRedirects(response, reverse("password_reset_complete"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("New-password-4829!"))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password("Admin-password-739!"))
        self.assertContains(Client().get(urlsplit(link).path), "invalid or has expired")

    def test_accounts_without_a_password_can_set_one(self):
        self.user.set_unusable_password()
        self.user.save()
        self.client.post(self.url)
        self.assertEqual(len(mail.outbox), 1)
        link = re.search(r"http://[^\s]+", mail.outbox[0].body).group()
        self.assertContains(Client().get(urlsplit(link).path, follow=True), "Choose a new password")

    def test_staff_without_change_permission_cannot_send(self):
        staff = get_user_model().objects.create_user("staff@example.com", "Staff-password-739!", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.post(self.url).status_code, 403)
        self.assertEqual(len(mail.outbox), 0)

    def test_staff_with_change_permission_can_send(self):
        staff = get_user_model().objects.create_user("staff@example.com", "Staff-password-739!", is_staff=True)
        staff.user_permissions.add(Permission.objects.get(content_type__app_label="accounts", codename="change_user"))
        self.client.force_login(staff)
        self.assertEqual(self.client.post(self.url).status_code, 302)
        self.assertEqual(len(mail.outbox), 1)

    def test_inactive_account_does_not_send(self):
        self.user.is_active = False
        self.user.save()
        self.assertContains(self.client.post(self.url), "An active account")
        self.assertEqual(len(mail.outbox), 0)

    @patch("accounts.admin.send_mail", side_effect=OSError("SMTP unavailable"))
    def test_email_failure_is_reported_without_changing_password(self, send):
        original_password = self.user.password
        with self.assertLogs("accounts.admin", level="ERROR"):
            response = self.client.post(self.url)
        self.assertContains(response, "The reset email could not be sent")
        self.user.refresh_from_db()
        self.assertEqual(self.user.password, original_password)

    def test_csrf_is_required(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.post(self.url).status_code, 403)
        self.assertEqual(len(mail.outbox), 0)
