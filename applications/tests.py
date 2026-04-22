from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
import json
import re

from applications.admin import QuestionAdminForm
from applications.models import (
    Application,
    DropboxSignWebhookEvent,
    FormDefinition,
    FormGroup,
    GroupParticipantList,
    ParticipantEmailStatus,
    Question,
)


class QuestionAdminFormTests(TestCase):
    def setUp(self):
        self.form_def = FormDefinition.objects.create(
            slug="test_form",
            name="Test Form",
        )
        self.controller = Question.objects.create(
            form=self.form_def,
            text="Controller",
            slug="controller",
            field_type=Question.BOOLEAN,
            required=False,
            position=1,
        )

    def test_new_question_form_uses_initial_form_for_show_if_queryset(self):
        form = QuestionAdminForm(instance=Question(), initial={"form": self.form_def.id})
        self.assertIn(self.controller, form.fields["show_if_question"].queryset)

    def test_new_question_post_with_show_if_question_is_valid(self):
        form = QuestionAdminForm(
            data={
                "form": str(self.form_def.id),
                "text": "Dependent",
                "slug": "dependent",
                "field_type": Question.SHORT_TEXT,
                "required": "on",
                "position": "2",
                "active": "on",
                "show_if_question": str(self.controller.id),
                "show_if_value": "yes",
                "show_if_conditions": "[]",
            },
            instance=Question(),
        )
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save(commit=False)
        self.assertEqual(obj.show_if_question_id, self.controller.id)
        self.assertEqual(obj.show_if_value, "yes")

    def test_new_question_post_preserves_show_if_question_without_value(self):
        form = QuestionAdminForm(
            data={
                "form": str(self.form_def.id),
                "text": "Dependent 2",
                "slug": "dependent_2",
                "field_type": Question.SHORT_TEXT,
                "required": "on",
                "position": "3",
                "active": "on",
                "show_if_question": str(self.controller.id),
                "show_if_value": "",
                "show_if_conditions": "[]",
            },
            instance=Question(),
        )
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save(commit=False)
        self.assertEqual(obj.show_if_question_id, self.controller.id)
        self.assertEqual(obj.show_if_value, "")
        self.assertEqual(obj.show_if_conditions, [])

    def test_existing_question_updates_first_condition_from_legacy_fields(self):
        dependent = Question.objects.create(
            form=self.form_def,
            text="Dependent existing",
            slug="dependent_existing",
            field_type=Question.SHORT_TEXT,
            required=True,
            active=True,
            position=2,
            show_if_question=self.controller,
            show_if_value="yes",
            show_if_conditions=[{"question_id": self.controller.id, "value": "yes"}],
        )
        original_conditions = '[{"question_id": %d, "value": "yes"}]' % self.controller.id

        form = QuestionAdminForm(
            data={
                "form": str(self.form_def.id),
                "text": "Dependent existing",
                "slug": "dependent_existing",
                "field_type": Question.SHORT_TEXT,
                "required": "on",
                "position": "2",
                "active": "on",
                "show_if_question": str(self.controller.id),
                "show_if_value": "no",
                "show_if_conditions": original_conditions,
            },
            instance=dependent,
        )
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save(commit=False)
        self.assertEqual(obj.show_if_question_id, self.controller.id)
        self.assertEqual(obj.show_if_value, "no")
        self.assertEqual(obj.show_if_conditions[0]["value"], "no")

    def test_existing_question_keeps_widget_value_when_conditions_json_changes(self):
        dependent = Question.objects.create(
            form=self.form_def,
            text="Dependent widget",
            slug="dependent_widget",
            field_type=Question.SHORT_TEXT,
            required=True,
            active=True,
            position=3,
            show_if_question=self.controller,
            show_if_value="yes",
            show_if_conditions=[{"question_id": self.controller.id, "value": "yes"}],
        )
        changed_conditions = '[{"question_id": %d, "value": "no"}]' % self.controller.id

        form = QuestionAdminForm(
            data={
                "form": str(self.form_def.id),
                "text": "Dependent widget",
                "slug": "dependent_widget",
                "field_type": Question.SHORT_TEXT,
                "required": "on",
                "position": "3",
                "active": "on",
                "show_if_question": str(self.controller.id),
                "show_if_value": "yes",
                "show_if_conditions": changed_conditions,
            },
            instance=dependent,
        )
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save(commit=False)
        self.assertEqual(obj.show_if_value, "no")
        self.assertEqual(obj.show_if_conditions[0]["value"], "no")

    def test_legacy_first_condition_wins_when_both_inputs_change(self):
        dependent = Question.objects.create(
            form=self.form_def,
            text="Dependent both",
            slug="dependent_both",
            field_type=Question.SHORT_TEXT,
            required=True,
            active=True,
            position=4,
            show_if_question=self.controller,
            show_if_value="yes",
            show_if_conditions=[{"question_id": self.controller.id, "value": "yes"}],
        )

        form = QuestionAdminForm(
            data={
                "form": str(self.form_def.id),
                "text": "Dependent both",
                "slug": "dependent_both",
                "field_type": Question.SHORT_TEXT,
                "required": "on",
                "position": "4",
                "active": "on",
                "show_if_question": str(self.controller.id),
                "show_if_value": "no",
                "show_if_conditions": '[{"question_id": %d, "value": "yes"}, {"question_id": %d, "value": "yes"}]'
                % (self.controller.id, self.controller.id),
            },
            instance=dependent,
        )
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save(commit=False)
        self.assertEqual(obj.show_if_value, "no")
        self.assertEqual(obj.show_if_conditions[0]["value"], "no")


class ApplicationFormRenderTests(TestCase):
    def test_question_wrapper_carries_multi_show_if_conditions(self):
        form_def = FormDefinition.objects.create(
            slug="test_render_form",
            name="Test Render Form",
            is_public=True,
            accepting_responses=True,
        )
        controller_1 = Question.objects.create(
            form=form_def,
            text="Controller 1",
            slug="controller_1",
            field_type=Question.BOOLEAN,
            required=False,
            position=1,
            active=True,
        )
        controller_2 = Question.objects.create(
            form=form_def,
            text="Controller 2",
            slug="controller_2",
            field_type=Question.BOOLEAN,
            required=False,
            position=2,
            active=True,
        )
        Question.objects.create(
            form=form_def,
            text="Dependent",
            slug="dependent",
            field_type=Question.SHORT_TEXT,
            required=False,
            position=3,
            active=True,
            show_if_question=controller_1,
            show_if_value="yes",
            show_if_conditions=[
                {"question_id": controller_1.id, "value": "yes"},
                {"question_id": controller_2.id, "value": "yes"},
            ],
        )

        response = self.client.get(
            reverse("apply_by_slug", kwargs={"form_slug": form_def.slug}),
            HTTP_HOST="localhost",
        )

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertRegex(
            html,
            re.compile(
                r'class="form-question"[^>]*data-show-if-question="q_controller_1"[^>]*data-show-if-conditions=',
                re.DOTALL,
            ),
        )


class ParticipantsPageSafetyTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.staff_user = user_model.objects.create_superuser(
            email="admin@example.com",
            password="testpass123",
        )
        self.client.force_login(self.staff_user)

        self.group = FormGroup.objects.create(
            number=991,
            start_day=1,
            start_month="abril",
            end_month="abril",
            year=2026,
        )
        self.form_def = FormDefinition.objects.create(
            slug="G991_E_A1",
            name="Group 991 E A1",
            is_master=False,
            group=self.group,
            is_public=True,
            accepting_responses=True,
        )
        self.app = Application.objects.create(
            form=self.form_def,
            name="Applicant One",
            email="applicant@example.com",
        )
        self.participant_list = GroupParticipantList.objects.create(
            group=self.group,
            mentoras_emails_text="mentor@example.com",
            emprendedoras_emails_text="founder@example.com",
            mentoras_sheet_rows=[["", "CP", 1, "Mentor", "111", "mentor@example.com"]],
            emprendedoras_sheet_rows=[["", "CP", 1, "Founder", "222", "founder@example.com"]],
        )

    def test_clear_group_participants_only_clears_participant_sheet_data(self):
        response = self.client.post(
            reverse("admin_profiles_participants"),
            data={
                "group": str(self.group.number),
                "action": "clear_group_participants",
            },
        )
        self.assertEqual(response.status_code, 302)

        self.participant_list.refresh_from_db()
        self.assertEqual(self.participant_list.mentoras_emails_text, "")
        self.assertEqual(self.participant_list.emprendedoras_emails_text, "")
        self.assertEqual(self.participant_list.mentoras_sheet_rows, [])
        self.assertEqual(self.participant_list.emprendedoras_sheet_rows, [])

        self.assertTrue(FormGroup.objects.filter(id=self.group.id).exists())
        self.assertTrue(FormDefinition.objects.filter(id=self.form_def.id).exists())
        self.assertTrue(Application.objects.filter(id=self.app.id).exists())
        self.group.refresh_from_db()
        if hasattr(self.group, "is_active"):
            self.assertFalse(self.group.is_active)

        list_response = self.client.get(reverse("admin_profiles_participants"))
        self.assertEqual(list_response.status_code, 200)
        self.assertNotContains(list_response, f"?group={self.group.number}")

    def test_participants_page_rejects_group_delete_actions(self):
        response = self.client.post(
            reverse("admin_profiles_participants"),
            data={
                "group": str(self.group.number),
                "action": "delete_group",
            },
        )
        self.assertEqual(response.status_code, 302)

        self.assertTrue(FormGroup.objects.filter(id=self.group.id).exists())
        self.assertTrue(FormDefinition.objects.filter(id=self.form_def.id).exists())
        self.assertTrue(Application.objects.filter(id=self.app.id).exists())
        self.group.refresh_from_db()
        if hasattr(self.group, "is_active"):
            self.assertTrue(self.group.is_active)

        self.participant_list.refresh_from_db()
        self.assertEqual(self.participant_list.mentoras_emails_text, "mentor@example.com")
        self.assertEqual(self.participant_list.emprendedoras_emails_text, "founder@example.com")


class DropboxSignWebhookActaAutomationTests(TestCase):
    def _mentora_row(self, idx: int, email: str) -> list:
        return ["", "", idx, f"Mentora {idx}", f"M{idx}", email, "", "", "", False, False, False, False, False, False, False]

    def _emprendedora_row(self, idx: int, email: str) -> list:
        return ["", "", idx, f"Emprendedora {idx}", f"E{idx}", email, "", "", "", False, False, False, False, False, False]

    def test_emprendedora_title_with_group_marks_only_signed_rows(self):
        group = FormGroup.objects.create(
            number=950,
            start_day=1,
            start_month="abril",
            end_month="abril",
            year=2026,
        )
        plist = GroupParticipantList.objects.create(
            group=group,
            emprendedoras_sheet_rows=[
                self._emprendedora_row(1, "e1@example.com"),
                self._emprendedora_row(2, "e2@example.com"),
            ],
        )

        payload = {
            "event": {
                "event_type": "signature_request_signed",
                "event_time": "1713744000",
                "event_hash": "nohash",
            },
            "signature_request": {
                "signature_request_id": "req-e-950",
                "title": "Acta de compromiso programa mentoria - emprendedora G950",
                "signatures": [
                    {"signer_email_address": "e1@example.com", "status_code": "signed"},
                    {"signer_email_address": "e2@example.com", "status_code": "awaiting_signature"},
                ],
            },
        }
        response = self.client.post(
            reverse("dropbox_sign_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        plist.refresh_from_db()
        self.assertTrue(bool(plist.emprendedoras_sheet_rows[0][9]))
        self.assertFalse(bool(plist.emprendedoras_sheet_rows[1][9]))

        status_1 = ParticipantEmailStatus.objects.filter(email="e1@example.com").first()
        status_2 = ParticipantEmailStatus.objects.filter(email="e2@example.com").first()
        self.assertIsNotNone(status_1)
        self.assertTrue(status_1.contract_signed)
        self.assertTrue((status_2 is None) or (not status_2.contract_signed))

    def test_mentora_title_matches_group_by_exact_participant_email_list(self):
        target_group = FormGroup.objects.create(
            number=951,
            start_day=1,
            start_month="abril",
            end_month="abril",
            year=2026,
        )
        other_group = FormGroup.objects.create(
            number=952,
            start_day=1,
            start_month="abril",
            end_month="abril",
            year=2026,
        )
        target = GroupParticipantList.objects.create(
            group=target_group,
            mentoras_sheet_rows=[
                self._mentora_row(1, "m1@example.com"),
                self._mentora_row(2, "m2@example.com"),
            ],
        )
        other = GroupParticipantList.objects.create(
            group=other_group,
            mentoras_sheet_rows=[
                self._mentora_row(1, "m1@example.com"),
            ],
        )

        payload = {
            "event": {
                "event_type": "signature_request_signed",
                "event_time": "1713744001",
                "event_hash": "nohash",
            },
            "signature_request": {
                "signature_request_id": "req-m-951",
                "title": "Acta de compromiso para ser Mentora",
                "signatures": [
                    {"signer_email_address": "m1@example.com", "status_code": "signed"},
                    {"signer_email_address": "m2@example.com", "status_code": "awaiting_signature"},
                ],
            },
        }
        response = self.client.post(
            reverse("dropbox_sign_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        target.refresh_from_db()
        other.refresh_from_db()
        self.assertTrue(bool(target.mentoras_sheet_rows[0][9]))
        self.assertFalse(bool(target.mentoras_sheet_rows[1][9]))
        self.assertFalse(bool(other.mentoras_sheet_rows[0][9]))

        event = DropboxSignWebhookEvent.objects.filter(signature_request_id="req-m-951").first()
        self.assertIsNotNone(event)
        self.assertIn("Scope=M951", (event.process_note or ""))
