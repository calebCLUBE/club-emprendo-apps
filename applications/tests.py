from datetime import date
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone
import json
import re
from unittest.mock import patch

from applications.admin import QuestionAdminForm
from applications.admin_views import _build_second_stage_reminder_payload, _clone_form, _sync_group_form_names
from applications.email_templates import build_form_email_context, resolve_form_email_template
from applications.forms import build_application_form
from applications.emprendedora_a1_autograde import autograde_and_email_emprendedora_a1
from applications.views import _thanks_override_payload, _mentor_a1_autograde_and_email, _schedule_a1_to_a2_reminder
from applications.models import (
    Answer,
    Application,
    DropboxSignWebhookEvent,
    FormDefinition,
    FormGroup,
    GroupParticipantList,
    ParticipantEmailStatus,
    Question,
    Section,
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


class GroupFormNamingTests(TestCase):
    def setUp(self):
        self.master_e_a1 = FormDefinition.objects.create(
            slug="E_A1",
            name="Aplicacion Emprendedora 1",
            is_master=True,
            is_public=True,
            accepting_responses=True,
        )
        self.master_m_a1 = FormDefinition.objects.create(
            slug="M_A1",
            name="Aplicacion Mentora 1",
            is_master=True,
            is_public=True,
            accepting_responses=True,
        )

    def test_clone_form_uses_custom_group_name_token_for_form_name(self):
        group = FormGroup.objects.create(
            number=811,
            start_day=1,
            start_month="abril",
            end_month="abril",
            year=2026,
            custom_name="April Group",
            use_combined_application=True,
        )

        clone = _clone_form(self.master_m_a1, group)

        self.assertEqual(clone.slug, "G811_M_A1")
        self.assertEqual(clone.name, "april_group_m_1")

    def test_sync_group_form_names_updates_existing_group_forms_after_rename(self):
        group = FormGroup.objects.create(
            number=812,
            start_day=1,
            start_month="abril",
            end_month="abril",
            year=2026,
            custom_name="",
            use_combined_application=True,
        )
        clone_e = _clone_form(self.master_e_a1, group)
        clone_m = _clone_form(self.master_m_a1, group)

        self.assertEqual(clone_e.name, "Grupo 812 — Aplicacion Emprendedora 1")
        self.assertEqual(clone_m.name, "Grupo 812 — Aplicacion Mentora 1")

        group.custom_name = "April Group"
        group.save(update_fields=["custom_name"])
        _sync_group_form_names(group)

        clone_e.refresh_from_db()
        clone_m.refresh_from_db()
        self.assertEqual(clone_e.name, "april_group_e_1")
        self.assertEqual(clone_m.name, "april_group_m_1")


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

    def test_default_section_renders_form_description_intro(self):
        form_def = FormDefinition.objects.create(
            slug="test_default_section_intro",
            name="Test Default Section Intro",
            description="Descripción visible para sección por defecto.",
            is_public=True,
            accepting_responses=True,
        )
        explicit_section = Section.objects.create(
            form=form_def,
            title="Sección explícita",
            description="Intro explícita",
            position=1,
        )

        Question.objects.create(
            form=form_def,
            text="Pregunta en sección explícita",
            slug="q_in_section",
            field_type=Question.SHORT_TEXT,
            required=False,
            position=1,
            active=True,
            section=explicit_section,
        )
        Question.objects.create(
            form=form_def,
            text="Pregunta sin sección",
            slug="q_unassigned",
            field_type=Question.SHORT_TEXT,
            required=False,
            position=2,
            active=True,
            section=None,
        )

        response = self.client.get(
            reverse("apply_by_slug", kwargs={"form_slug": form_def.slug}),
            HTTP_HOST="localhost",
        )

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn("Descripción visible para sección por defecto.", html)


class ConfirmValueNormalizationTests(TestCase):
    def test_confirm_email_accepts_case_and_spacing_differences(self):
        form_def = FormDefinition.objects.create(
            slug="test_confirm_email",
            name="Test Confirm Email",
            is_public=True,
            accepting_responses=True,
        )
        Question.objects.create(
            form=form_def,
            text="Correo",
            slug="correo",
            field_type=Question.SHORT_TEXT,
            required=True,
            confirm_value=True,
            position=1,
            active=True,
        )

        FormCls = build_application_form(form_def.slug)
        form = FormCls(
            data={
                "q_correo": "  Ana.Example@Gmail.com ",
                "q_correo__confirm": "ana.example@gmail.com",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_confirm_phone_accepts_punctuation_differences(self):
        form_def = FormDefinition.objects.create(
            slug="test_confirm_phone",
            name="Test Confirm Phone",
            is_public=True,
            accepting_responses=True,
        )
        Question.objects.create(
            form=form_def,
            text="WhatsApp",
            slug="whatsapp",
            field_type=Question.SHORT_TEXT,
            required=True,
            confirm_value=True,
            position=1,
            active=True,
        )

        FormCls = build_application_form(form_def.slug)
        form = FormCls(
            data={
                "q_whatsapp": "+57 311-234-5678",
                "q_whatsapp__confirm": "+57 (311) 234 5678",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)


class ThanksOverrideTests(TestCase):
    def test_custom_rejected_message_renders_placeholders(self):
        form_def = FormDefinition.objects.create(
            slug="G8_E_A1",
            name="Aplicación Emprendedoras A1",
            is_public=True,
            accepting_responses=True,
            thanks_rejected_title="Resultado para {{ group_label }}",
            thanks_rejected_message=(
                "No quedaste en {{ group_label }} de {{ track_label }}.\n"
                "Formulario: {{ form_name }}."
            ),
        )

        payload = _thanks_override_payload(
            form_def=form_def,
            kind="a1",
            approved=False,
            disqualified=False,
            group_num="8",
            track="emprendedoras",
        )

        self.assertEqual(payload.get("custom_message_title"), "Resultado para Grupo 8")
        self.assertIn("Grupo 8 de emprendedoras", payload.get("custom_message_body", ""))
        self.assertIn("Aplicación Emprendedoras A1", payload.get("custom_message_body", ""))
        self.assertEqual(payload.get("custom_message_variant"), "intro")

    def test_custom_message_title_stays_blank_when_not_provided(self):
        form_def = FormDefinition.objects.create(
            slug="G8_M_A1",
            name="Aplicación Mentoras A1",
            is_public=True,
            accepting_responses=True,
            thanks_approved_title="",
            thanks_approved_message="Mensaje aprobado",
        )

        payload = _thanks_override_payload(
            form_def=form_def,
            kind="a1",
            approved=True,
            disqualified=False,
            group_num="8",
            track="mentoras",
        )

        self.assertEqual(payload.get("custom_message_title"), "")
        self.assertEqual(payload.get("custom_message_variant"), "alert")


class EmailTemplateTests(TestCase):
    def test_email_template_replaces_group_deadline_and_link_placeholders(self):
        group = FormGroup.objects.create(
            number=8,
            start_day=1,
            start_month="abril",
            end_month="abril",
            year=2026,
            a2_deadline=date(2026, 5, 20),
        )
        form_def = FormDefinition.objects.create(
            slug="G8_E_A1",
            name="Aplicación Emprendedoras A1",
            group=group,
            email_a1_approved_subject="Paso 2 - {{ group_label }}",
            email_a1_approved_body="Link: {{ a2_link }} | Fecha: {{ deadline_text }}",
        )
        replacements = build_form_email_context(
            form_def=form_def,
            role_word="emprendedora",
            a2_link="https://apply.clubemprendo.org/apply/emprendedora/abc/",
            deadline=group.a2_deadline,
        )

        subject = resolve_form_email_template(
            form_def=form_def,
            field_name="email_a1_approved_subject",
            default_text="Default subject",
            replacements=replacements,
            is_subject=True,
        )
        body = resolve_form_email_template(
            form_def=form_def,
            field_name="email_a1_approved_body",
            default_text="Default body",
            replacements=replacements,
        )

        self.assertEqual(subject, "Paso 2 - Grupo 8")
        self.assertIn("https://apply.clubemprendo.org/apply/emprendedora/abc/", body)
        self.assertIn("20 de mayo de 2026", body)


class A1EmailBehaviorTests(TestCase):
    def test_emprendedora_a1_approved_does_not_send_email(self):
        form_def = FormDefinition.objects.create(
            slug="G8_E_A1",
            name="Aplicación Emprendedoras A1",
            is_public=True,
            accepting_responses=True,
        )
        app = Application.objects.create(
            form=form_def,
            name="Ana",
            email="ana@example.com",
        )
        q_reqs = Question.objects.create(
            form=form_def,
            text="Requisitos",
            slug="meets_requirements",
            field_type=Question.CHOICE,
            required=True,
            position=1,
            active=True,
        )
        q_avail = Question.objects.create(
            form=form_def,
            text="Disponibilidad",
            slug="available_period",
            field_type=Question.CHOICE,
            required=True,
            position=2,
            active=True,
        )
        q_business = Question.objects.create(
            form=form_def,
            text="Emprendimiento activo",
            slug="business_active",
            field_type=Question.CHOICE,
            required=True,
            position=3,
            active=True,
        )
        Answer.objects.create(application=app, question=q_reqs, value="yes")
        Answer.objects.create(application=app, question=q_avail, value="yes")
        Answer.objects.create(application=app, question=q_business, value="yes")

        with patch("applications.emprendedora_a1_autograde._send_html_email") as mocked_send:
            autograde_and_email_emprendedora_a1(None, app)

        app.refresh_from_db()
        self.assertTrue(app.invited_to_second_stage)
        self.assertIsNotNone(app.invite_token)
        mocked_send.assert_not_called()

    def test_mentora_a1_approved_does_not_send_email(self):
        form_def = FormDefinition.objects.create(
            slug="G8_M_A1",
            name="Aplicación Mentoras A1",
            is_public=True,
            accepting_responses=True,
        )
        app = Application.objects.create(
            form=form_def,
            name="Mara",
            email="mara@example.com",
        )
        q_reqs = Question.objects.create(
            form=form_def,
            text="Requisitos",
            slug="meets_requirements",
            field_type=Question.CHOICE,
            required=True,
            position=1,
            active=True,
        )
        q_avail = Question.objects.create(
            form=form_def,
            text="Disponibilidad",
            slug="available_period",
            field_type=Question.CHOICE,
            required=True,
            position=2,
            active=True,
        )
        Answer.objects.create(application=app, question=q_reqs, value="yes")
        Answer.objects.create(application=app, question=q_avail, value="yes")

        with patch("applications.views._send_html_email") as mocked_send:
            _mentor_a1_autograde_and_email(None, app)

        app.refresh_from_db()
        self.assertTrue(app.invited_to_second_stage)
        self.assertIsNotNone(app.invite_token)
        mocked_send.assert_not_called()

    def test_schedule_a1_reminder_clears_existing_values(self):
        form_def = FormDefinition.objects.create(
            slug="G8_E_A1",
            name="Aplicación Emprendedoras A1",
            is_public=True,
            accepting_responses=True,
        )
        app = Application.objects.create(
            form=form_def,
            name="Reminder",
            email="reminder@example.com",
            invited_to_second_stage=True,
            second_stage_reminder_due_at=timezone.now(),
            second_stage_reminder_sent_at=timezone.now(),
        )

        _schedule_a1_to_a2_reminder(app)
        app.refresh_from_db()

        self.assertIsNone(app.second_stage_reminder_due_at)
        self.assertIsNone(app.second_stage_reminder_sent_at)


class A2ReminderRecipientSelectionTests(TestCase):
    def setUp(self):
        self.group = FormGroup.objects.create(
            number=820,
            start_day=1,
            start_month="abril",
            end_month="abril",
            year=2026,
            a2_deadline=date(2026, 5, 25),
        )
        self.form_a1_e = FormDefinition.objects.create(
            slug="G820_E_A1",
            name="G820 Emprendedoras A1",
            group=self.group,
            is_public=True,
            accepting_responses=True,
        )
        self.form_a2_e = FormDefinition.objects.create(
            slug="G820_E_A2",
            name="G820 Emprendedoras A2",
            group=self.group,
            is_public=False,
            accepting_responses=True,
        )
        self.q_reqs = Question.objects.create(
            form=self.form_a1_e,
            text="Requisitos",
            slug="meets_requirements",
            field_type=Question.CHOICE,
            required=True,
            position=1,
            active=True,
        )
        self.q_avail = Question.objects.create(
            form=self.form_a1_e,
            text="Disponibilidad",
            slug="available_period",
            field_type=Question.CHOICE,
            required=True,
            position=2,
            active=True,
        )
        self.q_business = Question.objects.create(
            form=self.form_a1_e,
            text="Emprendimiento activo",
            slug="business_active",
            field_type=Question.CHOICE,
            required=True,
            position=3,
            active=True,
        )

    def _create_a1_submission(self, *, email: str, invited: bool, req: str, avail: str, business: str):
        app = Application.objects.create(
            form=self.form_a1_e,
            name="Applicant",
            email=email,
            invited_to_second_stage=invited,
        )
        Answer.objects.create(application=app, question=self.q_reqs, value=req)
        Answer.objects.create(application=app, question=self.q_avail, value=avail)
        Answer.objects.create(application=app, question=self.q_business, value=business)
        return app

    def test_reminder_targets_include_passed_a1_even_if_invite_flag_is_false(self):
        self._create_a1_submission(
            email="passed@example.com",
            invited=False,
            req="yes",
            avail="yes",
            business="yes",
        )

        payload, error = _build_second_stage_reminder_payload("G820_E_A2")

        self.assertIsNone(error)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["targets"], ["passed@example.com"])

    def test_reminder_keeps_email_if_any_a1_submission_is_eligible(self):
        self._create_a1_submission(
            email="latest-wins@example.com",
            invited=True,
            req="yes",
            avail="yes",
            business="yes",
        )
        self._create_a1_submission(
            email="latest-wins@example.com",
            invited=False,
            req="no",
            avail="yes",
            business="yes",
        )

        payload, error = _build_second_stage_reminder_payload("G820_E_A2")

        self.assertIsNone(error)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["targets"], ["latest-wins@example.com"])


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


class ParticipantsCapacitacionCheckTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.staff_user = user_model.objects.create_superuser(
            email="cap-admin@example.com",
            password="testpass123",
        )
        self.client.force_login(self.staff_user)

        self.group = FormGroup.objects.create(
            number=993,
            start_day=1,
            start_month="abril",
            end_month="abril",
            year=2026,
        )
        self.participant_list = GroupParticipantList.objects.create(
            group=self.group,
            mentoras_sheet_rows=[
                ["", "CP", 1, "Mentora 1", "M1", "m1@example.com", "", "", "", False, False, False, False, False, False, False],
                ["", "CP", 2, "Mentora 2", "M2", "m2@example.com", "", "", "", False, False, False, False, False, False, False],
            ],
        )

    def test_track_sheet_renders_check_capacitacion_button(self):
        response = self.client.get(
            reverse(
                "admin_profiles_participants_track_sheet",
                args=[self.group.number, "mentoras"],
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Check Capacitacion")

    @patch("applications.admin_profiles_views._fetch_wix_capacitacion_completed_emails")
    def test_check_capacitacion_marks_only_matching_rows(self, mock_fetch):
        mock_fetch.return_value = (
            True,
            {"m1@example.com"},
            "Wix completions fetched for mentoras.",
        )

        response = self.client.post(
            reverse(
                "admin_profiles_participants_track_sheet",
                args=[self.group.number, "mentoras"],
            ),
            data={"action": "check_capacitacion"},
        )
        self.assertEqual(response.status_code, 302)

        self.participant_list.refresh_from_db()
        self.assertTrue(bool(self.participant_list.mentoras_sheet_rows[0][11]))
        self.assertFalse(bool(self.participant_list.mentoras_sheet_rows[1][11]))


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
