from datetime import date
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone
import json
import re
from unittest.mock import patch

from applications import admin_dashboard_views
from applications import admin_profiles_views
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
    ParticipantSheetVersion,
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

        self.assertEqual(clone.slug, "april_group_M_A1")
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
        self.q_a2 = Question.objects.create(
            form=self.form_a2_e,
            text="Motivación",
            slug="motivation",
            field_type=Question.SHORT_TEXT,
            required=True,
            position=1,
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

    def test_placeholder_a2_row_without_answers_does_not_count_as_completed(self):
        self._create_a1_submission(
            email="needs-reminder@example.com",
            invited=True,
            req="yes",
            avail="yes",
            business="yes",
        )
        Application.objects.create(
            form=self.form_a2_e,
            name="Needs Reminder",
            email="needs-reminder@example.com",
        )

        payload, error = _build_second_stage_reminder_payload("G820_E_A2")

        self.assertIsNone(error)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["targets"], ["needs-reminder@example.com"])

    def test_a2_row_marked_as_submitted_counts_as_completed(self):
        a1_app = self._create_a1_submission(
            email="done@example.com",
            invited=True,
            req="yes",
            avail="yes",
            business="yes",
        )
        a1_app.name = "Done Person"
        a1_app.save(update_fields=["name"])
        a2_app = Application.objects.create(
            form=self.form_a2_e,
            name="Done Person",
            email="done@example.com",
            second_stage_reminder_sent_at=timezone.now(),
        )
        Answer.objects.create(application=a2_app, question=self.q_a2, value="Ya contesté")

        payload, error = _build_second_stage_reminder_payload("G820_E_A2")

        self.assertIsNone(error)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["targets"], [])

    def test_completed_a2_match_uses_name_when_available(self):
        a1_app = self._create_a1_submission(
            email="candidate@example.com",
            invited=True,
            req="yes",
            avail="yes",
            business="yes",
        )
        a1_app.name = "María Pérez"
        a1_app.save(update_fields=["name"])

        Application.objects.create(
            form=self.form_a2_e,
            name="Maria Perez",
            email="different@example.com",
            second_stage_reminder_sent_at=timezone.now(),
        )

        payload, error = _build_second_stage_reminder_payload("G820_E_A2")

        self.assertIsNone(error)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["targets"], [])

    def test_name_mismatch_does_not_mark_completed_even_if_email_matches(self):
        a1_app = self._create_a1_submission(
            email="shared@example.com",
            invited=True,
            req="yes",
            avail="yes",
            business="yes",
        )
        a1_app.name = "Ana Uno"
        a1_app.save(update_fields=["name"])

        Application.objects.create(
            form=self.form_a2_e,
            name="Otra Persona",
            email="shared@example.com",
            second_stage_reminder_sent_at=timezone.now(),
        )

        payload, error = _build_second_stage_reminder_payload("G820_E_A2")

        self.assertIsNone(error)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["targets"], ["shared@example.com"])

    def test_legacy_scored_a2_row_counts_as_completed(self):
        a1_app = self._create_a1_submission(
            email="legacy-complete@example.com",
            invited=True,
            req="yes",
            avail="yes",
            business="yes",
        )
        a1_app.name = "Legacy Person"
        a1_app.save(update_fields=["name"])
        a2_app = Application.objects.create(
            form=self.form_a2_e,
            name="Legacy Person",
            email="legacy-complete@example.com",
            overall_score=7.5,
            recommendation="CP",
        )
        Answer.objects.create(application=a2_app, question=self.q_a2, value="Completado")

        payload, error = _build_second_stage_reminder_payload("G820_E_A2")

        self.assertIsNone(error)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["targets"], [])

    def test_combined_group_ignores_a2_identity_matching_and_uses_a1_pending(self):
        self.group.use_combined_application = True
        self.group.save(update_fields=["use_combined_application"])

        a1_app = self._create_a1_submission(
            email="combined@example.com",
            invited=True,
            req="yes",
            avail="yes",
            business="yes",
        )
        a1_app.name = "Combined Person"
        a1_app.save(update_fields=["name"])

        Application.objects.create(
            form=self.form_a2_e,
            name="Combined Person",
            email="combined@example.com",
            second_stage_reminder_sent_at=timezone.now(),
        )

        payload, error = _build_second_stage_reminder_payload("G820_E_A2")

        self.assertIsNone(error)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["targets"], ["combined@example.com"])


class ImpactDashboardMetricTests(TestCase):
    def setUp(self):
        self.group1 = FormGroup.objects.create(
            number=981,
            start_day=1,
            start_month="enero",
            end_month="marzo",
            year=2026,
        )
        self.group2 = FormGroup.objects.create(
            number=982,
            start_day=1,
            start_month="abril",
            end_month="junio",
            year=2026,
        )
        self.e_a1 = FormDefinition.objects.create(
            slug="G981_E_A1",
            name="G981 E A1",
            group=self.group1,
        )
        self.e_a2 = FormDefinition.objects.create(
            slug="G981_E_A2",
            name="G981 E A2",
            group=self.group1,
        )
        self.m_g1 = FormDefinition.objects.create(
            slug="G981_M_A1",
            name="G981 M A1",
            group=self.group1,
        )
        self.m_g2 = FormDefinition.objects.create(
            slug="G982_M_A1",
            name="G982 M A1",
            group=self.group2,
        )

        Application.objects.create(form=self.e_a1, name="Founder", email="founder@example.com")
        Application.objects.create(form=self.e_a2, name="Founder Repeat", email="founder@example.com")
        Application.objects.create(form=self.e_a1, name="No Start", email="no-start@example.com")
        Application.objects.create(form=self.m_g1, name="Mentor", email="mentor@example.com")
        Application.objects.create(form=self.m_g1, name="Repeated Mentor", email="repeat@example.com")
        Application.objects.create(form=self.m_g2, name="Founder Mentor", email="founder@example.com")

        GroupParticipantList.objects.create(
            group=self.group1,
            emprendedoras_sheet_rows=[
                ["", "G", 1, "Founder", "E1", "founder@example.com", "", "Colombia", "", True, True, True, True, True],
                ["", "NFA", 2, "No Start", "E2", "no-start@example.com", "", "Peru", "", False, False, False, False, False],
            ],
            mentoras_sheet_rows=[
                ["", "G", 1, "Mentor", "M1", "mentor@example.com", "", "Venezuela", "", True, True, True, True, True],
                ["", "A", 2, "Repeated Mentor", "M2", "repeat@example.com", "", "Colombia", "", True, True, True, True, False],
            ],
        )
        GroupParticipantList.objects.create(
            group=self.group2,
            mentoras_sheet_rows=[
                ["", "A", 1, "Founder Mentor", "M3", "founder@example.com", "", "Colombia", "", True, True, True, True, False],
                ["", "CP", 2, "Repeated Mentor", "M2", "repeat@example.com", "", "Peru", "", True, False, True, False, False],
            ],
        )

    def _participant_source_csv(self):
        return "\n".join(
            [
                "Grupo,Track,Estatus,Nombre,Id,Email,WhatsApp,Reside,Edad,Acta,Website,Capacitacion,Encuesta inicial,Encuesta final",
                "981,Emprendedoras,Graduada,Founder From Sheet,E1,founder@example.com,+57,Colombia,31,true,true,true,true,true",
                "981,Emprendedoras,No Firmo A,No Start From Sheet,E2,no-start@example.com,+51,Peru,29,false,false,false,false,false",
                "981,Mentoras,Graduada,Mentor From Sheet,M1,mentor@example.com,+58,Venezuela,35,true,true,true,true,true",
                "981,Mentoras,Activa,Repeated Mentor From Sheet,M2,repeat@example.com,+57,Colombia,36,true,true,true,true,false",
                "982,Mentoras,Activa,Founder Mentor From Sheet,M3,founder@example.com,+57,Colombia,37,true,true,true,true,false",
                "982,Mentoras,Cambio de pareja,Repeated Mentor G2 From Sheet,M2,repeat@example.com,+51,Peru,38,true,false,true,false,false",
            ]
        )

    def test_participant_sheet_status_options_use_requested_default_labels(self):
        expected = [
            "No Firmo A",
            "No Capacitacion",
            "No Continua P",
            "No Continua PP",
            "Siguiente grupo",
            "Cambio de grupo",
            "Cambio de pareja",
            "DIficil/No contacto",
            "Exelente",
            "Graduada",
            "Activa",
        ]

        self.assertEqual(admin_profiles_views.MENTORAS_STATUS_OPTIONS, expected)
        self.assertEqual(admin_profiles_views.EMPRENDEDORAS_STATUS_OPTIONS, expected)

    def test_status_codes_and_sheet_labels_drive_started_metric_semantics(self):
        status_group = FormGroup.objects.create(
            number=983,
            start_day=1,
            start_month="julio",
            end_month="septiembre",
            year=2026,
        )
        GroupParticipantList.objects.create(
            group=status_group,
            emprendedoras_sheet_rows=[
                ["", "NCP", 1, "Started by status", "E3", "started-status@example.com", "", "Colombia", "", False, False, False, False, False],
                ["", "NC", 2, "Not started by status", "E4", "not-started-status@example.com", "", "Colombia", "", False, False, False, False, False],
                ["", "Graduada", 3, "Graduated by status", "E5", "graduated-status@example.com", "", "Colombia", "", False, False, False, False, False],
            ],
        )

        target_emails = {
            "started-status@example.com",
            "not-started-status@example.com",
            "graduated-status@example.com",
        }
        records_by_email = {
            record["email"]: record
            for record in admin_dashboard_views._participant_records()
            if record["email"] in target_emails
        }

        self.assertTrue(records_by_email["started-status@example.com"]["started"])
        self.assertFalse(records_by_email["not-started-status@example.com"]["started"])
        self.assertTrue(records_by_email["graduated-status@example.com"]["started"])
        self.assertTrue(records_by_email["graduated-status@example.com"]["graduated"])

        participant_summary = admin_dashboard_views._participant_summary(records_by_email.values())
        status_labels = {
            row["status"]: row["label"]
            for row in participant_summary["tracks"]["e"]["status_rows"]
        }
        self.assertEqual(status_labels["NCP"], "No Continua P")
        self.assertEqual(status_labels["NC"], "No Capacitacion")
        self.assertEqual(status_labels["G"], "Graduada")

        chart_labels = {
            row["label"]
            for row in admin_dashboard_views._participant_status_chart_data(participant_summary)["e"]
        }
        self.assertIn("No Continua P", chart_labels)
        self.assertIn("No Capacitacion", chart_labels)
        self.assertIn("Graduada", chart_labels)

        status_key = {
            row["code"]: row["label"]
            for row in admin_dashboard_views._participant_status_key()
        }
        self.assertEqual(status_key["G"], "Graduada")
        self.assertEqual(status_key["A"], "Activa")

    def test_program_metric_summaries_use_participant_rows_and_deduped_applicants(self):
        records = admin_dashboard_views._participant_records()
        participant_summary = admin_dashboard_views._participant_summary(records)
        application_summary = admin_dashboard_views._application_summary()
        conversion_rows = admin_dashboard_views._conversion_summary(
            participant_summary,
            application_summary,
        )
        alumni_summary = admin_dashboard_views._alumni_mentor_summary(records)
        group_source_rows = admin_dashboard_views._group_recruitment_source_rows(records)

        self.assertEqual(participant_summary["overall"]["rows"], 6)
        self.assertEqual(participant_summary["overall"]["started"], 5)
        self.assertEqual(participant_summary["overall"]["graduated"], 2)
        self.assertEqual(participant_summary["overall"]["graduation_rate"], 40.0)
        self.assertEqual(participant_summary["tracks"]["e"]["started"], 1)
        self.assertEqual(participant_summary["tracks"]["m"]["graduated"], 1)

        self.assertEqual(application_summary["overall"]["raw"], 6)
        self.assertEqual(application_summary["overall"]["unique"], 4)
        self.assertEqual(application_summary["overall"]["duplicate_or_repeat"], 2)
        self.assertEqual(application_summary["tracks"][0]["unique"], 2)
        group_application_summary = admin_dashboard_views._application_summary({981})
        self.assertEqual(group_application_summary["overall"]["raw"], 5)
        self.assertEqual(group_application_summary["overall"]["unique"], 4)

        e_conversion = next(row for row in conversion_rows if row["track"] == "Emprendedoras")
        self.assertEqual(e_conversion["unique_applicants"], 2)
        self.assertEqual(e_conversion["started_from_app"], 2)
        self.assertEqual(e_conversion["listed_from_app"], 2)
        self.assertEqual(e_conversion["graduated_from_app"], 1)
        self.assertEqual(e_conversion["app_to_start_rate"], 100.0)
        self.assertEqual(e_conversion["app_to_listed_rate"], 100.0)

        self.assertEqual(alumni_summary["returnee_count"], 1)
        self.assertEqual(alumni_summary["later_returnee_count"], 1)
        self.assertEqual(alumni_summary["repeated_mentor_count"], 1)
        self.assertEqual(alumni_summary["repeated_mentors"][0]["email"], "repeat@example.com")
        self.assertEqual(alumni_summary["repeated_mentors"][0]["groups"], [981, 982])
        group1_source = next(row for row in group_source_rows if row["group_number"] == 981)
        group2_source = next(row for row in group_source_rows if row["group_number"] == 982)
        self.assertEqual(group1_source["source_label"], "Group 981")
        self.assertEqual(group2_source["source_label"], "Group 981")

    def test_survey_nps_and_quality_of_life_rows_exclude_financial_columns(self):
        headers = [
            "Timestamp",
            "Email",
            "Que tan probable es que recomiendes Club Emprendo NPS",
            "Bienestar financiero",
            "¿Te sientes satisfecha con tu vida en general?",
            "¿Qué tanta confianza tienes en este momento en la gestión de tu emprendimiento?",
            "Other",
        ]
        rows = [
            ["2026-01-01", "one@example.com", "10", "500", "3", "5", "x"],
            ["2026-01-02", "two@example.com", "9", "700", "5", "5", "x"],
            ["2026-01-03", "three@example.com", "6", "900", "4", "5", "x"],
        ]
        metadata_indices = {0, 1}

        nps_rows = admin_dashboard_views._build_nps_rows(headers, rows, metadata_indices)
        wellbeing_rows = admin_dashboard_views._build_wellbeing_rows(
            headers,
            rows,
            metadata_indices,
        )

        self.assertEqual(len(nps_rows), 1)
        self.assertEqual(nps_rows[0]["score"], 33.3)
        self.assertEqual(nps_rows[0]["promoters"], 2)
        self.assertEqual(nps_rows[0]["detractors"], 1)
        self.assertEqual(len(wellbeing_rows), 1)
        self.assertEqual(
            wellbeing_rows[0]["label"],
            "¿Te sientes satisfecha con tu vida en general?",
        )
        self.assertEqual(wellbeing_rows[0]["avg"], 4.0)

    def test_nps_rows_ignore_open_ended_recommendation_change_fields(self):
        headers = [
            "Timestamp",
            "Email",
            "¿Qué recomendarías cambiar (si es que hay algo) en la capacitación de mentores?",
            "¿Qué tan probable es que recomiendes este programa de mentoría a una amiga?",
        ]
        rows = [
            ["2026-01-01", "one@example.com", "1 cosa", "10"],
            ["2026-01-02", "two@example.com", "2 cambios", "9"],
            ["2026-01-03", "three@example.com", "3 temas", "6"],
        ]

        nps_rows = admin_dashboard_views._build_nps_rows(headers, rows, {0, 1})

        self.assertEqual(len(nps_rows), 1)
        self.assertEqual(
            nps_rows[0]["label"],
            "¿Qué tan probable es que recomiendes este programa de mentoría a una amiga?",
        )
        self.assertEqual(nps_rows[0]["score"], 33.3)

    @patch("applications.admin_dashboard_views._build_impact_dataset")
    def test_impact_dashboard_renders_requested_metric_sections(self, mock_build_dataset):
        def fake_dataset(kind, title, sheet_url_name, scoped_emails=None):
            return (
                {
                    "kind": kind,
                    "title": title,
                    "label": title,
                    "sheet_url_name": sheet_url_name,
                    "source_name": "test.csv",
                    "source_file_id": "test-file",
                    "responses_count": 0,
                    "headers_count": 0,
                    "question_count": 0,
                    "unique_emails_count": 0,
                    "email_column_label": "",
                    "completion_rows": [],
                    "nps_rows": [],
                    "wellbeing_rows": [],
                },
                set(),
            )

        mock_build_dataset.side_effect = fake_dataset
        user_model = get_user_model()
        staff_user = user_model.objects.create_superuser(
            email="impact-admin@example.com",
            password="testpass123",
        )
        self.client.force_login(staff_user)

        response = self.client.get(reverse("admin_impact_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Number of Participants")
        self.assertContains(response, "Application Conversion")
        self.assertContains(response, "Number of Groups")
        self.assertContains(response, "Emprendedoras Returning as Mentoras")
        self.assertContains(response, "Repeat Mentoras")
        self.assertContains(response, "Quality of Life")
        self.assertContains(response, "Survey Response Rate")
        self.assertContains(response, "Group scope")
        self.assertContains(response, "Download PDF report")

    @patch("applications.admin_dashboard_views._build_impact_dataset")
    def test_impact_dashboard_pdf_download_for_specific_group(self, mock_build_dataset):
        def fake_dataset(kind, title, sheet_url_name, scoped_emails=None):
            return (
                {
                    "kind": kind,
                    "title": title,
                    "label": title,
                    "sheet_url_name": sheet_url_name,
                    "source_name": "test.csv",
                    "source_file_id": "test-file",
                    "responses_count": 0,
                    "headers_count": 0,
                    "question_count": 0,
                    "unique_emails_count": 0,
                    "email_column_label": "",
                    "completion_rows": [],
                    "nps_rows": [],
                    "wellbeing_rows": [],
                },
                set(),
            )

        mock_build_dataset.side_effect = fake_dataset
        user_model = get_user_model()
        staff_user = user_model.objects.create_superuser(
            email="impact-pdf-admin@example.com",
            password="testpass123",
        )
        self.client.force_login(staff_user)

        response = self.client.get(
            reverse("admin_impact_dashboard_pdf"),
            {"groups": [str(self.group1.number)]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("impact_report_groups_981.pdf", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF"))


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
            emprendedoras_sheet_rows=[
                ["", "CP", 1, "Emprendedora 1", "E1", "e1@example.com", "", "", "", False, False, False, False, False, False, False],
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
        self.assertContains(response, "Check Encuesta final")
        self.assertContains(response, "Saved versions")

    def test_track_sheet_renders_exact_status_validation_options(self):
        expected = [
            "No Firmo A",
            "No Capacitacion",
            "No Continua P",
            "No Continua PP",
            "Siguiente grupo",
            "Cambio de grupo",
            "Cambio de pareja",
            "DIficil/No contacto",
            "Exelente",
            "Graduada",
            "Activa",
        ]
        response = self.client.get(
            reverse(
                "admin_profiles_participants_track_sheet",
                args=[self.group.number, "mentoras"],
            )
        )
        self.assertEqual(response.status_code, 200)

        content = response.content.decode()
        match = re.search(
            r'<script id="participants-track-sheet-xsheet-status-options" type="application/json">(.*?)</script>',
            content,
            flags=re.S,
        )
        self.assertIsNotNone(match)
        self.assertEqual(json.loads(match.group(1)), expected)
        self.assertNotContains(response, '"NFA"')
        self.assertNotContains(response, '"NC"')
        self.assertNotContains(response, '"NCC"')
        self.assertNotContains(response, '"INCP"')
        self.assertNotContains(response, '"INCPP"')
        self.assertNotContains(response, '"E/T"')

    def test_participants_page_links_to_combined_workbook(self):
        response = self.client.get(
            f"{reverse('admin_profiles_participants')}?group={self.group.number}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Open participant workbook")
        self.assertContains(response, f"/profiles/participants/{self.group.number}/all/")
        self.assertContains(response, reverse("admin_profiles_participants_google_sheet"))
        self.assertNotContains(response, "Open Mentoras sheet")
        self.assertNotContains(response, "Open Emprendedoras sheet")

    @patch("applications.admin_profiles_views.fetch_drive_csv_file_text")
    def test_participants_page_auto_refreshes_database_from_google_sheet(self, mock_fetch):
        mock_fetch.return_value = (
            "\n".join(
                [
                    "Grupo,Track,Estatus,Nombre,Id,Email,WhatsApp,Reside,Edad,Acta,Website,Capacitacion,Encuesta inicial,Encuesta final",
                    "993,Mentoras,Activa,Mentora Auto,M9,mentor-auto@example.com,+57,Colombia,30,true,false,true,false,true",
                    "994,Emprendedoras,Graduada,Founder Auto,E9,founder-auto@example.com,+51,Peru,40,true,true,true,true,true",
                ]
            ),
            "drive-file-id",
            "Participant source",
        )

        response = self.client.get(reverse("admin_profiles_participants"))

        self.assertEqual(response.status_code, 200)
        synced_existing = GroupParticipantList.objects.get(group=self.group)
        synced_new = GroupParticipantList.objects.get(group__number=994)
        self.assertEqual(synced_existing.mentoras_sheet_rows[0][3], "Mentora Auto")
        self.assertEqual(synced_existing.mentoras_sheet_rows[0][5], "mentor-auto@example.com")
        self.assertEqual(synced_new.emprendedoras_sheet_rows[0][3], "Founder Auto")
        self.assertEqual(synced_new.emprendedoras_sheet_rows[0][5], "founder-auto@example.com")

    @patch("applications.admin_profiles_views.fetch_drive_csv_file_text")
    def test_participant_google_sheet_view_renders_complete_source_sheet(self, mock_fetch):
        mock_fetch.return_value = (
            "Grupo,Track,Nombre,Email\n993,Mentoras,Mentora Sheet,mentor-sheet@example.com\n994,Emprendedoras,Founder Sheet,founder-sheet@example.com\n",
            "drive-file-id",
            "Participant source",
        )

        response = self.client.get(reverse("admin_profiles_participants_google_sheet"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Participant Google Sheet")
        self.assertContains(response, "Participant source")
        self.assertContains(response, "drive-file-id")
        self.assertContains(response, "participants-google-sheet-headers")
        self.assertContains(response, "mentor-sheet@example.com")
        self.assertContains(response, "founder-sheet@example.com")

    @patch("applications.admin_profiles_views.fetch_drive_csv_file_text")
    def test_participants_page_syncs_database_from_google_sheet(self, mock_fetch):
        mock_fetch.return_value = (
            "\n".join(
                [
                    "Grupo,Track,Estatus,Nombre,Id,Email,WhatsApp,Reside,Edad,Acta,Website,Capacitacion,Encuesta inicial,Encuesta final",
                    "993,Mentoras,Activa,Mentora Sheet,M9,mentor-sheet@example.com,+57,Colombia,30,true,false,true,false,true",
                    "994,Emprendedoras,Graduada,Founder Sheet,E9,founder-sheet@example.com,+51,Peru,40,true,true,true,true,true",
                ]
            ),
            "drive-file-id",
            "Participant source",
        )

        response = self.client.post(
            reverse("admin_profiles_participants"),
            data={"action": "sync_from_google_sheet"},
        )

        self.assertEqual(response.status_code, 302)
        group_994 = FormGroup.objects.get(number=994)
        synced_existing = GroupParticipantList.objects.get(group=self.group)
        synced_new = GroupParticipantList.objects.get(group=group_994)
        self.assertEqual(synced_existing.mentoras_sheet_rows[0][1], "Activa")
        self.assertEqual(synced_existing.mentoras_sheet_rows[0][3], "Mentora Sheet")
        self.assertEqual(synced_existing.mentoras_sheet_rows[0][5], "mentor-sheet@example.com")
        self.assertTrue(synced_existing.mentoras_sheet_rows[0][9])
        self.assertFalse(synced_existing.mentoras_sheet_rows[0][10])
        self.assertTrue(synced_existing.mentoras_sheet_rows[0][11])
        self.assertEqual(synced_new.emprendedoras_sheet_rows[0][1], "Graduada")
        self.assertEqual(synced_new.emprendedoras_sheet_rows[0][3], "Founder Sheet")
        self.assertEqual(synced_new.emprendedoras_sheet_rows[0][5], "founder-sheet@example.com")
        self.assertTrue(
            ParticipantEmailStatus.objects.filter(email="mentor-sheet@example.com", participated=True).exists()
        )
        self.assertTrue(
            ParticipantEmailStatus.objects.filter(email="founder-sheet@example.com", participated=True).exists()
        )

    def test_combined_track_sheet_renders_both_tabs(self):
        response = self.client.get(
            reverse(
                "admin_profiles_participants_track_sheet",
                args=[self.group.number, "all"],
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "participants-track-sheet-xsheet-tabs")
        self.assertContains(response, "Mentoras")
        self.assertContains(response, "Emprendedoras")
        self.assertContains(response, "ce-mentoras-sheet-data")
        self.assertContains(response, "ce-emprendedoras-sheet-data")

    def test_combined_track_sheet_autosave_updates_both_tracks(self):
        url = reverse(
            "admin_profiles_participants_track_sheet",
            args=[self.group.number, "all"],
        )
        mentoras_rows = [
            ["", "CP", 1, "Mentora Combined", "M1", "m1@example.com", "", "", "", False, False, False, False, False, False, False],
        ]
        emprendedoras_rows = [
            ["", "CP", 1, "Emprendedora Combined", "E1", "e1@example.com", "", "", "", False, False, False, False, False, False, False],
            ["", "CP", 2, "Emprendedora 2", "E2", "e2@example.com", "", "", "", False, False, False, False, False, False, False],
        ]
        response = self.client.post(
            url,
            data={
                "action": "save_sheet",
                "mentoras_sheet_data": json.dumps(mentoras_rows),
                "emprendedoras_sheet_data": json.dumps(emprendedoras_rows),
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.participant_list.refresh_from_db()
        self.assertEqual(self.participant_list.mentoras_sheet_rows[0][3], "Mentora Combined")
        self.assertEqual(
            self.participant_list.emprendedoras_sheet_rows[0][3],
            "Emprendedora Combined",
        )
        self.assertEqual(len(self.participant_list.emprendedoras_sheet_rows), 2)
        self.assertTrue(
            ParticipantSheetVersion.objects.filter(group=self.group, track="mentoras").exists()
        )
        self.assertTrue(
            ParticipantSheetVersion.objects.filter(group=self.group, track="emprendedoras").exists()
        )

    def test_track_sheet_autosave_creates_version_and_restore_reloads_it(self):
        url = reverse(
            "admin_profiles_participants_track_sheet",
            args=[self.group.number, "mentoras"],
        )
        first_rows = [
            ["", "CP", 1, "Mentora Version 1", "M1", "m1@example.com", "", "", "", False, False, False, False, False, False, False],
        ]
        response = self.client.post(
            url,
            data={"action": "save_sheet", "sheet_data": json.dumps(first_rows)},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        version = ParticipantSheetVersion.objects.get(group=self.group, track="mentoras")
        self.assertEqual(version.action, "autosave")

        second_rows = [
            ["", "CP", 1, "Mentora Version 2", "M1", "m1@example.com", "", "", "", False, False, False, False, False, False, False],
        ]
        response = self.client.post(
            url,
            data={"action": "save_sheet", "sheet_data": json.dumps(second_rows)},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            ParticipantSheetVersion.objects.filter(group=self.group, track="mentoras").count(),
            2,
        )

        response = self.client.post(
            url,
            data={"action": "restore_version", "version_id": str(version.id)},
        )
        self.assertEqual(response.status_code, 302)
        self.participant_list.refresh_from_db()
        self.assertEqual(self.participant_list.mentoras_sheet_rows[0][3], "Mentora Version 1")

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
        self.assertTrue(
            ParticipantSheetVersion.objects.filter(
                group=self.group,
                track="mentoras",
                action="check_capacitacion",
            ).exists()
        )

    @patch("applications.admin_profiles_views._fetch_encuestas_emails_for_group")
    def test_check_encuesta_final_marks_only_matching_rows(self, mock_fetch):
        mock_fetch.return_value = (
            True,
            {"m1@example.com"},
            "Encuesta final source scanned.",
        )

        response = self.client.post(
            reverse(
                "admin_profiles_participants_track_sheet",
                args=[self.group.number, "mentoras"],
            ),
            data={"action": "check_encuestas_final"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(mock_fetch.called)
        self.assertEqual(mock_fetch.call_args.kwargs.get("survey_stage"), "final")

        self.participant_list.refresh_from_db()
        self.assertTrue(bool(self.participant_list.mentoras_sheet_rows[0][13]))
        self.assertFalse(bool(self.participant_list.mentoras_sheet_rows[1][13]))
        self.assertTrue(
            ParticipantSheetVersion.objects.filter(
                group=self.group,
                track="mentoras",
                action="check_encuesta_final",
            ).exists()
        )


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
