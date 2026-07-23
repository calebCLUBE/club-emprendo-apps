from datetime import date, datetime, timedelta, timezone as dt_timezone
import uuid
from django.test import TestCase, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib import admin as django_admin
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.utils import timezone
import json
import re
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from applications import admin_dashboard_views, drive_sync
from applications import admin_profiles_views
from applications.grader_m import MENTORA_EXACT_OUTPUT_COLUMNS
from applications import meta_marketing
from applications.admin import (
    FormDefinitionAdmin,
    PairingRuleAdminForm,
    QuestionAdminForm,
    QuestionInlineFormSet,
    SectionAdminForm,
)
from applications.admin_views import (
    _build_second_stage_reminder_payload,
    _clone_form,
    _combined_application_entries,
    _application_email_recipients_for_form,
    _parse_bulk_email_recipients,
    _sync_group_form_names,
)
from applications.email_templates import build_form_email_context, resolve_form_email_template
from applications.forms import build_application_form
from applications.grading_config import runtime_grading_config_for_form_slug
from applications.grader_e import _disqualification_reasons as emprendedora_disqualification_reasons
from applications.grader_m import _disqualification_reasons as mentor_disqualification_reasons
from applications.templatetags.app_extras import format_help_text, format_rich_text
from applications.mentora_application_schema import apply_mentora_schema
from applications.emprendedora_a1_autograde import (
    autograde_and_email_emprendedora_a1,
    emprendedora_a1_passes,
)
from applications.views import (
    _mentor_a1_autograde_and_email,
    _mentor_a1_is_eligible,
    _schedule_a1_to_a2_reminder,
    _thanks_override_payload,
)
from applications.models import (
    Answer,
    Application,
    ApplicationDraft,
    Choice,
    DropboxSignWebhookEvent,
    FormDefinition,
    FormGroup,
    GradedFile,
    ApplicationGradingConfig,
    GradingCriterion,
    GradingResponseWeight,
    GroupParticipantList,
    PairingAIComparison,
    PairingConfig,
    PairingPriorityRule,
    ParticipantSheetVersion,
    ParticipantEmailStatus,
    Question,
    Section,
    StoredEmailTemplate,
)


class GoogleSheetsCredentialScopeTests(TestCase):
    @patch("applications.drive_sync._upsert_google_sheet_file")
    @patch("applications.drive_sync._build_group_track_rows")
    @patch("applications.drive_sync._resolve_track_target_folder_id")
    @patch("applications.drive_sync._service_and_root")
    def test_group_response_sync_targets_matching_track_folder_and_sheet(
        self,
        mock_service_and_root,
        mock_resolve_folder,
        mock_build_rows,
        mock_upsert_sheet,
    ):
        service = Mock()
        mock_service_and_root.return_value = (service, "groups-root")
        mock_resolve_folder.return_value = "emprendedoras-folder"
        mock_build_rows.return_value = (
            ["email", "business"],
            [["founder@example.com", "Bakery"]],
        )
        mock_upsert_sheet.return_value = {"id": "sheet-1"}

        result = drive_sync.sync_group_track_responses_csv(12, "E")

        self.assertEqual(result.status, "updated")
        mock_resolve_folder.assert_called_once_with(service, "groups-root", 12, "E")
        mock_upsert_sheet.assert_called_once_with(
            service,
            "emprendedoras-folder",
            "G12 Aplicaciones Emprendedoras",
            ["email", "business"],
            [["founder@example.com", "Bakery"]],
        )

    @patch("applications.drive_sync._service_for_sheets")
    @patch("applications.drive_sync._find_child_item_by_name")
    def test_response_snapshot_is_written_as_native_google_sheet(
        self,
        mock_find_item,
        mock_sheets_factory,
    ):
        drive_service = Mock()
        drive_service.files.return_value.create.return_value.execute.return_value = {
            "id": "response-sheet-1",
            "name": "G12 Aplicaciones Mentoras",
            "mimeType": drive_sync.SPREADSHEET_MIMETYPE,
        }
        mock_find_item.return_value = None
        sheets_service = Mock()
        mock_sheets_factory.return_value = sheets_service
        sheets_service.spreadsheets.return_value.get.return_value.execute.return_value = {
            "sheets": [{"properties": {"sheetId": 0, "title": "Sheet1"}}]
        }

        result = drive_sync._upsert_google_sheet_file(
            drive_service,
            "mentoras-folder",
            "G12 Aplicaciones Mentoras",
            ["email", "name"],
            [["mentor@example.com", "Mentora"]],
        )

        self.assertEqual(result["id"], "response-sheet-1")
        create_body = drive_service.files.return_value.create.call_args.kwargs["body"]
        self.assertEqual(create_body["mimeType"], drive_sync.SPREADSHEET_MIMETYPE)
        self.assertEqual(create_body["parents"], ["mentoras-folder"])
        update_call = sheets_service.spreadsheets.return_value.values.return_value.update
        self.assertEqual(update_call.call_args.kwargs["range"], "'Sheet1'!A1")
        self.assertEqual(
            update_call.call_args.kwargs["body"]["values"],
            [["email", "name"], ["mentor@example.com", "Mentora"]],
        )

    @patch("applications.drive_sync._ensure_child_folder")
    @patch("applications.drive_sync._create_folder")
    @patch("applications.drive_sync._find_existing_group_folder")
    @patch("applications.drive_sync._build_service")
    @patch("applications.drive_sync._oauth_env_config")
    @patch("applications.drive_sync._load_config")
    def test_group_drive_tree_matches_requested_folder_contract(
        self,
        mock_load_config,
        mock_oauth_config,
        mock_build_service,
        mock_find_group,
        mock_create_folder,
        mock_ensure_folder,
    ):
        service = Mock()
        mock_load_config.return_value = (
            "",
            '{"type":"service_account"}',
            drive_sync.DEFAULT_GROUPS_ROOT_FOLDER_ID,
        )
        mock_oauth_config.return_value = ("", "", "")
        mock_build_service.return_value = service
        mock_find_group.return_value = None
        mock_create_folder.return_value = {
            "id": "group-folder",
            "name": "G12 Mentorias - Junio a Agosto",
        }

        def ensured_folder(_service, parent_id, name):
            if name == "G12 Aplicaciones":
                return {"id": "applications-folder", "name": name}
            return {"id": f"folder-{name}", "name": name}

        mock_ensure_folder.side_effect = ensured_folder

        result = drive_sync.ensure_group_drive_tree(
            group_num=12,
            start_month="junio",
            end_month="agosto",
            year=2026,
        )

        self.assertEqual(result.status, "created")
        mock_create_folder.assert_called_once_with(
            service,
            "G12 Mentorias - Junio a Agosto",
            drive_sync.DEFAULT_GROUPS_ROOT_FOLDER_ID,
        )
        self.assertEqual(
            [call.args[2] for call in mock_ensure_folder.call_args_list[:6]],
            [
                "Participants",
                "G12 Recursos Usados",
                "G12 Emparejamiento",
                "G12 Certificados",
                "G12 Aplicaciones",
                "Actas de Compromiso",
            ],
        )
        self.assertEqual(
            [(call.args[1], call.args[2]) for call in mock_ensure_folder.call_args_list[6:]],
            [
                ("applications-folder", "Mentoras"),
                ("applications-folder", "Emprendedoras"),
            ],
        )

    @patch("applications.drive_sync._service_for_sheets")
    def test_duplicate_checkbox_columns_are_deleted_rightmost_first(
        self,
        mock_service_factory,
    ):
        service = Mock()
        mock_service_factory.return_value = service

        deleted = drive_sync.delete_google_spreadsheet_columns(
            "https://docs.google.com/spreadsheets/d/sheet-123/edit",
            [
                {"sheet_id": 7, "column_index": 12},
                {"sheet_id": 7, "column_index": 18},
            ],
        )

        self.assertEqual(deleted, 2)
        requests = service.spreadsheets.return_value.batchUpdate.call_args.kwargs[
            "body"
        ]["requests"]
        self.assertEqual(
            [request["deleteDimension"]["range"]["startIndex"] for request in requests],
            [18, 12],
        )

    @patch("applications.drive_sync._service_for_sheets")
    def test_missing_checkbox_column_is_created_and_formatted(self, mock_service_factory):
        service = Mock()
        mock_service_factory.return_value = service

        created = drive_sync.ensure_google_spreadsheet_checkbox_columns(
            "https://docs.google.com/spreadsheets/d/sheet-123/edit",
            [
                {
                    "sheet_id": 7,
                    "column_index": 20,
                    "column_count": 18,
                    "header": "Encuesta inicial",
                }
            ],
        )

        self.assertEqual(created, 1)
        requests = service.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"][
            "requests"
        ]
        self.assertEqual(requests[0]["appendDimension"]["length"], 3)
        self.assertEqual(
            requests[1]["updateCells"]["rows"][0]["values"][0]["userEnteredValue"],
            {"stringValue": "Encuesta inicial"},
        )
        self.assertEqual(
            requests[2]["setDataValidation"]["rule"]["condition"]["type"],
            "BOOLEAN",
        )

    @patch("applications.drive_sync._service_for_sheets")
    def test_tab_read_preserves_exact_google_title_whitespace(self, mock_service_factory):
        service = Mock()
        spreadsheets = service.spreadsheets.return_value
        spreadsheets.get.return_value.execute.return_value = {
            "spreadsheetId": "sheet-123",
            "properties": {"title": "Participants"},
            "sheets": [
                {"properties": {"sheetId": 1, "title": "Mentoras ", "index": 0}},
            ],
        }
        spreadsheets.values.return_value.batchGetByDataFilter.return_value.execute.return_value = {
            "valueRanges": [
                {
                    "dataFilters": [{"gridRange": {"sheetId": 1}}],
                    "valueRange": {"values": [["Email"], ["person@example.com"]]},
                }
            ],
        }
        spreadsheets.getByDataFilter.return_value.execute.return_value = {
            "sheets": [
                {
                    "properties": {"sheetId": 1},
                    "data": [
                        {
                            "startColumn": 0,
                            "rowData": [
                                {
                                    "values": [
                                        {},
                                        {
                                            "dataValidation": {
                                                "condition": {"type": "BOOLEAN"}
                                            }
                                        },
                                    ]
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        mock_service_factory.return_value = service

        result = drive_sync.fetch_google_spreadsheet_tabs(
            "https://docs.google.com/spreadsheets/d/sheet-123/edit"
        )

        self.assertEqual(result["tabs"][0]["title"], "Mentoras ")
        self.assertEqual(result["tabs"][0]["checkbox_column_indexes"], [1])
        self.assertEqual(
            spreadsheets.values.return_value.batchGetByDataFilter.call_args.kwargs["body"]["dataFilters"],
            [{"gridRange": {"sheetId": 1}}],
        )

    @patch("googleapiclient.discovery.build")
    @patch("google.oauth2.credentials.Credentials")
    def test_sheets_service_reuses_existing_drive_oauth_scope(
        self,
        mock_user_credentials,
        mock_build,
    ):
        drive_sync._build_sheets_service(
            "",
            oauth_client_id="client-id",
            oauth_client_secret="client-secret",
            oauth_refresh_token="refresh-token",
        )

        self.assertEqual(
            mock_user_credentials.call_args.kwargs["scopes"],
            ["https://www.googleapis.com/auth/drive"],
        )
        mock_build.assert_called_once_with(
            "sheets",
            "v4",
            credentials=mock_user_credentials.return_value,
            cache_discovery=False,
        )

    def test_disabled_sheets_api_error_is_actionable(self):
        raw_error = (
            "SERVICE_DISABLED: Google Sheets API has not been used in project 1052922067631 "
            "before or it is disabled. service=sheets.googleapis.com "
            "activationUrl=https://console.developers.google.com/apis/api/"
            "sheets.googleapis.com/overview?project=1052922067631"
        )

        message = drive_sync._friendly_sheets_error(RuntimeError(raw_error))

        self.assertIn("Google Sheets API is disabled for project 1052922067631", message)
        self.assertIn("overview?project=1052922067631", message)


class BulkEmailComposeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            email="bulk-admin@example.com",
            password="password",
        )
        self.client.force_login(self.user)

    def test_recipient_parser_deduplicates_and_reports_invalid_addresses(self):
        valid, invalid = _parse_bulk_email_recipients(
            "One@Example.com; two@example.com\none@example.com bad-address"
        )
        self.assertEqual(valid, ["one@example.com", "two@example.com"])
        self.assertEqual(invalid, ["bad-address"])

    def test_bulk_email_hides_recipients_and_sends_in_batches(self):
        recipients = [f"person{index}@example.com" for index in range(41)]
        response = self.client.post(
            reverse("admin_bulk_email_compose"),
            {
                "recipients": "\n".join(recipients),
                "reply_to": "team@example.com",
                "subject": "Program update",
                "message_body": "Hello everyone,\n\nThis is an update.",
                "confirm_send": "yes",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Email queued for 41 recipient(s)")
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(mail.outbox[0].to, [])
        self.assertEqual(len(mail.outbox[0].bcc), 40)
        self.assertEqual(len(mail.outbox[1].bcc), 1)
        self.assertEqual(mail.outbox[0].reply_to, ["team@example.com"])

    def test_send_requires_explicit_confirmation(self):
        response = self.client.post(
            reverse("admin_bulk_email_compose"),
            {
                "recipients": "person@example.com",
                "subject": "Subject",
                "message_body": "Message",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Confirm that you are ready", status_code=400)
        self.assertEqual(len(mail.outbox), 0)

    def test_application_update_email_sends_to_people_who_filled_specific_form(self):
        target_form = FormDefinition.objects.create(slug="G991_M_A1", name="Mentoras junio")
        other_form = FormDefinition.objects.create(slug="G992_M_A1", name="Other")
        Application.objects.create(form=target_form, name="One", email="one@example.com")
        Application.objects.create(form=target_form, name="Duplicate", email="One@Example.com")
        Application.objects.create(form=target_form, name="Two", email="two@example.com")
        Application.objects.create(form=other_form, name="Other", email="other@example.com")

        self.assertEqual(
            _application_email_recipients_for_form(target_form),
            ["one@example.com", "two@example.com"],
        )

        response = self.client.post(
            reverse("admin_send_application_update_email", args=[target_form.slug]),
            {"message_body": "Seguimos trabajando en la selección."},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Correo enviado para G991_M_A1: 2 destinataria(s).")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].from_email, "contacto@clubemprendo.org")
        self.assertEqual(mail.outbox[0].to, [])
        self.assertEqual(mail.outbox[0].bcc, ["one@example.com", "two@example.com"])
        self.assertIn("Seguimos trabajando", mail.outbox[0].body)


class SubmissionApprovalFilterTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_superuser(
            email="submission-filter@example.com",
            password="password",
        )
        self.client.force_login(user)
        self.form_def = FormDefinition.objects.create(
            slug="G990_M_A1",
            name="Approval filter test",
        )
        Application.objects.create(
            form=self.form_def,
            name="Approved Person",
            email="approved@example.com",
            approved_for_grading=True,
        )
        Application.objects.create(
            form=self.form_def,
            name="Rejected Person",
            email="rejected@example.com",
            approved_for_grading=False,
        )

    def test_form_submission_view_filters_for_approval_page(self):
        response = self.client.get(
            reverse("admin_database_form_detail", args=[self.form_def.slug]),
            {"approval": "approved"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Received approval page")
        self.assertContains(response, "approved@example.com")
        self.assertNotContains(response, "rejected@example.com")
        self.assertContains(response, "Showing <strong>1</strong> of <strong>2</strong> submissions.", html=True)

    def test_form_submission_view_filters_for_non_approved_page(self):
        response = self.client.get(
            reverse("admin_database_form_detail", args=[self.form_def.slug]),
            {"approval": "not_approved"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "rejected@example.com")
        self.assertNotContains(response, "approved@example.com")


class MentorGradingRequirementSchemaTests(TestCase):
    def test_current_aggregate_schema_passes_when_both_confirmations_are_yes(self):
        row = {"meets_requirements": "yes", "available_period": "yes"}
        self.assertEqual(mentor_disqualification_reasons(row), [])

    def test_current_aggregate_schema_reports_only_explicit_failed_confirmation(self):
        row = {"meets_requirements": "yes", "availability_ok": "no"}
        self.assertEqual(mentor_disqualification_reasons(row), ["availability_ok"])

    def test_legacy_individual_schema_supports_dated_period_slug(self):
        row = {
            "req_basic_woman": "yes",
            "req_basic_latam": "yes",
            "req_basic_business_exp": "yes",
            "req_basic_punctual": "yes",
            "req_basic_internet_device": "yes",
            "req_basic_training": "yes",
            "req_basic_surveys": "yes",
            "req_avail_sept_dec": "yes",
            "req_avail_2hrs_week": "yes",
            "req_avail_kickoff": "yes",
        }
        self.assertEqual(mentor_disqualification_reasons(row), [])

    def test_unknown_schema_does_not_disqualify_for_missing_legacy_fields(self):
        self.assertEqual(mentor_disqualification_reasons({"email": "person@example.com"}), [])


class EmprendedoraGradingRequirementSchemaTests(TestCase):
    def test_current_aggregate_schema_passes_when_confirmations_are_affirmative(self):
        row = {"meets_requirements": "sí", "available_period": "yes_ok"}
        self.assertEqual(emprendedora_disqualification_reasons(row), [])

    def test_current_aggregate_schema_reports_explicit_failed_confirmation(self):
        row = {"meets_requirements": "yes", "available_period": "no"}
        self.assertEqual(emprendedora_disqualification_reasons(row), ["available_period"])

    def test_missing_legacy_fields_do_not_disqualify_current_form(self):
        self.assertEqual(emprendedora_disqualification_reasons({"email": "person@example.com"}), [])

    def test_legacy_fields_are_checked_when_present(self):
        row = {"internet_access": "no", "commit_3_months": "yes"}
        self.assertEqual(emprendedora_disqualification_reasons(row), ["internet_access"])


class GradedFileDownloadTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            email="graded-download@example.com",
            password="password",
        )
        self.client.force_login(self.user)

    def test_latest_download_routes_use_latest_file_for_slug(self):
        GradedFile.objects.create(
            form_slug="G997_E_A1",
            csv_text="Status,score\nold,1%\n",
        )
        latest = GradedFile.objects.create(
            form_slug="G997_E_A1",
            csv_text="Status,score\nnew,99%\n",
        )

        response = self.client.get(
            reverse("admin_grading_download_latest_csv", args=["G997_E_A1"])
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("new,99%", response.content.decode())
        self.assertNotIn("old,1%", response.content.decode())
        self.assertIn(f'filename="G997_E_A1_graded.csv"', response["Content-Disposition"])

        excel_response = self.client.get(
            reverse("admin_grading_download_latest_excel", args=["G997_E_A1"])
        )
        self.assertEqual(excel_response.status_code, 200)
        self.assertEqual(
            excel_response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn(str(latest.form_slug), excel_response["Content-Disposition"])

    def test_sheet_view_warns_when_saved_graded_file_has_legacy_layout(self):
        GradedFile.objects.create(
            form_slug="G998_E_A1",
            csv_text="Status,score,score_exp,full_name,email,grading_rubric\nN/A,NA,Disqualified,x,x@example.com,old\n",
        )

        response = self.client.get(reverse("admin_grading_live_sheet", args=["G998_E_A1"]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "old grading layout")
        self.assertContains(response, "Grade (live)")


class ApplicationDraftTrackingTests(TestCase):
    def setUp(self):
        self.form_def = FormDefinition.objects.create(slug="draft_test", name="Draft Test")
        self.question = Question.objects.create(
            form=self.form_def,
            text="Tell us about yourself",
            slug="about",
            field_type=Question.SHORT_TEXT,
            required=False,
            position=1,
        )

    def test_autosave_creates_and_updates_same_draft(self):
        url = reverse("application_draft_autosave", args=[self.form_def.slug])
        response = self.client.post(
            url,
            data=json.dumps({
                "answers": {"q_about": "Started answer"},
                "email": "person@example.com",
                "current_section": 2,
                "total_sections": 4,
                "answered_questions": 1,
                "total_questions": 8,
                "progress_percent": 13,
                "last_question_slug": "about",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        token = response.json()["token"]
        response = self.client.post(
            url,
            data=json.dumps({"token": token, "answers": {"q_about": "Updated"}, "progress_percent": 25}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ApplicationDraft.objects.count(), 1)
        draft = ApplicationDraft.objects.get()
        self.assertEqual(draft.answers["q_about"], "Updated")
        self.assertEqual(draft.progress_percent, 25)

    def test_successful_submission_marks_draft_completed(self):
        draft = ApplicationDraft.objects.create(form=self.form_def, progress_percent=50)
        response = self.client.post(
            reverse("apply_by_slug", args=[self.form_def.slug]),
            {"q_about": "Finished", "_draft_token": str(draft.token)},
        )
        self.assertEqual(response.status_code, 302)
        draft.refresh_from_db()
        self.assertIsNotNone(draft.completed_at)
        self.assertIsNotNone(draft.application)
        self.assertEqual(draft.application.answers.get().value, "Finished")

    def test_immediate_submission_creates_completed_draft_without_prior_autosave(self):
        token = uuid.uuid4()
        response = self.client.post(
            reverse("apply_by_slug", args=[self.form_def.slug]),
            {"q_about": "Fast finish", "_draft_token": str(token)},
        )
        self.assertEqual(response.status_code, 302)
        draft = ApplicationDraft.objects.get(token=token)
        self.assertIsNotNone(draft.completed_at)
        self.assertIsNotNone(draft.application_id)

    def test_admin_progress_dashboard_shows_abandoned_location(self):
        draft = ApplicationDraft.objects.create(
            form=self.form_def,
            email="person@example.com",
            current_section=3,
            total_sections=5,
            progress_percent=45,
            last_question_slug="about",
        )
        ApplicationDraft.objects.filter(pk=draft.pk).update(
            updated_at=timezone.now() - timedelta(hours=25)
        )
        user = get_user_model().objects.create_superuser(email="admin@example.com", password="password")
        self.client.force_login(user)
        response = self.client.get(reverse("admin_application_progress_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "person@example.com")
        self.assertContains(response, "Section 3 of 5")
        self.assertContains(response, "Tell us about yourself")


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

    def test_new_question_generates_internal_slug_when_editor_leaves_it_blank(self):
        form = QuestionAdminForm(
            data={
                "form": str(self.form_def.id),
                "text": "¿Cuál es tu experiencia profesional?",
                "slug": "",
                "field_type": Question.SHORT_TEXT,
                "required": "on",
                "position": "2",
                "active": "on",
                "show_if_conditions": "[]",
                "end_form_rules": "[]",
            },
            instance=Question(),
        )

        self.assertTrue(form.is_valid(), form.errors)
        question = form.save()
        self.assertEqual(question.slug, "cual_es_tu_experiencia_profesional")

    @override_settings(
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        }
    )
    def test_admin_editor_saves_new_questions_without_manual_slugs(self):
        empty_form = FormDefinition.objects.create(
            slug="empty_editor_form",
            name="Empty editor form",
        )
        user = get_user_model().objects.create_superuser(
            email="editor-save@example.com",
            password="test-password",
        )
        self.client.force_login(user)
        data = {
            "name": empty_form.name,
            "description": "",
            "slug": empty_form.slug,
            "group": "",
            "default_section_title": "Información",
            "questions-TOTAL_FORMS": "2",
            "questions-INITIAL_FORMS": "0",
            "questions-MIN_NUM_FORMS": "0",
            "questions-MAX_NUM_FORMS": "1000",
            "questions-0-id": "",
            "questions-0-form": str(empty_form.pk),
            "questions-0-position": "1",
            "questions-0-active": "on",
            "questions-0-slug": "",
            "questions-0-text": "New editor question",
            "questions-0-field_type": Question.SHORT_TEXT,
            "questions-0-answer_options": "",
            "questions-0-help_text_clean": "",
            "questions-0-section_token": "",
            "questions-0-required": "on",
            "questions-0-show_if_conditions": "[]",
            "questions-0-end_form_rules": "[]",
            "questions-0-pre_text": "",
            "_save": "Save",
        }
        for key, value in list(data.items()):
            if key.startswith("questions-0-"):
                data[key.replace("questions-0-", "questions-1-")] = value
        for prefix in ("stored_emails", "sections"):
            data.update({
                f"{prefix}-TOTAL_FORMS": "0",
                f"{prefix}-INITIAL_FORMS": "0",
                f"{prefix}-MIN_NUM_FORMS": "0",
                f"{prefix}-MAX_NUM_FORMS": "1000",
            })

        response = self.client.post(
            reverse("admin:applications_formdefinition_change", args=[empty_form.pk]),
            data,
        )

        self.assertEqual(response.status_code, 302, response.context and response.context["errors"])
        questions = list(empty_form.questions.order_by("position", "id"))
        self.assertEqual(len(questions), 2)
        self.assertEqual({question.text for question in questions}, {"New editor question"})
        self.assertEqual(
            {question.slug for question in questions},
            {"new_editor_question", "new_editor_question_2"},
        )

    def test_inline_delete_cleans_answers_choices_and_json_condition_references(self):
        controller = Question.objects.create(
            form=self.form_def,
            text="Controller to delete",
            slug="controller_to_delete",
            field_type=Question.CHOICE,
            position=2,
        )
        Choice.objects.create(question=controller, label="Yes", value="yes", position=1)
        dependent = Question.objects.create(
            form=self.form_def,
            text="Dependent",
            slug="dependent_on_deleted",
            field_type=Question.SHORT_TEXT,
            position=3,
            show_if_conditions=[{"question_id": controller.pk, "value": "yes"}],
        )
        section = Section.objects.create(
            form=self.form_def,
            title="Conditional on deleted question",
            position=1,
            show_if_conditions=[{"question_id": controller.pk, "value": "yes"}],
        )
        application = Application.objects.create(
            form=self.form_def,
            name="Existing applicant",
            email="applicant@example.com",
        )
        Answer.objects.create(application=application, question=controller, value="yes")

        formset = object.__new__(QuestionInlineFormSet)
        formset.delete_existing(controller, commit=True)

        self.assertFalse(Question.objects.filter(pk=controller.pk).exists())
        self.assertFalse(Answer.objects.filter(application=application).exists())
        dependent.refresh_from_db()
        section.refresh_from_db()
        self.assertEqual(dependent.show_if_conditions, [])
        self.assertEqual(section.show_if_conditions, [])
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

    def test_answer_options_preserve_existing_values_and_add_unique_values(self):
        question = Question.objects.create(
            form=self.form_def,
            text="Pick one",
            slug="pick_one",
            field_type=Question.CHOICE,
            position=5,
        )
        Choice.objects.create(
            question=question,
            label="Old label",
            value="stable-grading-value",
            position=0,
        )
        form = QuestionAdminForm(
            data={
                "form": str(self.form_def.id),
                "text": question.text,
                "slug": question.slug,
                "field_type": Question.CHOICE,
                "required": "on",
                "position": "5",
                "active": "on",
                "show_if_conditions": "[]",
                "answer_options": "Updated label\nUpdated label",
            },
            instance=question,
        )

        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        choices = list(question.choices.order_by("position"))
        self.assertEqual([c.label for c in choices], ["Updated label", "Updated label"])
        self.assertEqual(choices[0].value, "stable-grading-value")
        self.assertEqual(choices[1].value, "updated-label")

    @override_settings(
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        }
    )
    def test_form_editor_renders_simplified_builder_assets(self):
        StoredEmailTemplate.objects.create(
            form=self.form_def,
            name="Requirements rejection",
            subject="Your application",
            body="Thank you for applying.",
        )
        user = get_user_model().objects.create_superuser(
            email="builder@example.com",
            password="test-password",
        )
        self.client.force_login(user)

        response = self.client.get(
            reverse("admin:applications_formdefinition_change", args=[self.form_def.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Club Emprendo Forms")
        self.assertContains(response, "applications/css/form_builder.css")
        self.assertContains(response, "applications/js/form_builder.js")
        self.assertContains(response, "Show this question based on an answer")
        self.assertContains(response, "Dropdown")
        self.assertContains(response, "Stored emails")
        self.assertContains(response, "Approval page")
        self.assertContains(response, "Approval email")
        self.assertContains(response, "Rejection page")
        self.assertContains(response, 'name="approval_email_name"')
        self.assertContains(response, "Requirements rejection")
        self.assertContains(response, "End the application based on this answer")
        self.assertContains(response, "Uses the shared Rejection page configured at the top of the editor.")
        self.assertNotContains(response, "Final page message")
        self.assertNotContains(response, "Confirmation messages")
        self.assertNotContains(response, "Email messages")
        self.assertNotContains(
            response,
            reverse("admin:applications_question_change", args=[self.controller.pk]),
        )
        self.assertContains(response, 'name="questions-0-section_token"')
        self.assertNotContains(response, 'name="questions-0-section"')


    def test_section_logic_widget_saves_google_style_answer_rule(self):
        section = Section.objects.create(
            form=self.form_def,
            title="Conditional section",
            position=1,
        )
        form = SectionAdminForm(
            data={
                "form": str(self.form_def.id),
                "title": section.title,
                "description": "",
                "position": "1",
                "show_if_logic": "AND",
                "show_if_conditions": json.dumps([
                    {"question_id": self.controller.id, "value": "yes"}
                ]),
            },
            instance=section,
        )

        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.show_if_conditions, [
            {"question_id": self.controller.id, "value": "yes"}
        ])
        self.assertEqual(saved.show_if_question, self.controller)
        self.assertEqual(saved.show_if_value, "yes")

    def test_new_inline_section_token_assigns_question_after_formsets_save(self):
        section = Section.objects.create(form=self.form_def, title="Inserted section", position=1)
        question = Question.objects.create(
            form=self.form_def,
            text="Question after section",
            slug="question_after_section",
            field_type=Question.SHORT_TEXT,
            position=2,
        )
        section_form = SimpleNamespace(
            cleaned_data={"DELETE": False},
            instance=section,
            prefix="sections-0",
        )
        question_form = SimpleNamespace(
            cleaned_data={"DELETE": False, "section_token": "sections-0"},
            instance=question,
        )
        formsets = [
            SimpleNamespace(model=Section, forms=[section_form]),
            SimpleNamespace(model=Question, forms=[question_form]),
        ]
        model_admin = FormDefinitionAdmin(FormDefinition, django_admin.site)

        with patch("django.contrib.admin.ModelAdmin.save_related"):
            model_admin.save_related(Mock(), Mock(), formsets, True)

        question.refresh_from_db()
        self.assertEqual(question.section, section)


class ApplicationsDashboardPreviewTests(TestCase):
    def test_apps_list_orders_groups_and_forms_by_newest_created(self):
        older_group = FormGroup.objects.create(
            number=901,
            start_day=1,
            start_month="enero",
            end_month="abril",
            year=2026,
        )
        newer_group = FormGroup.objects.create(
            number=902,
            start_day=1,
            start_month="mayo",
            end_month="agosto",
            year=2026,
        )
        FormDefinition.objects.create(slug="G902_E_A1", name="Older form", group=newer_group)
        FormDefinition.objects.create(slug="G902_M_A1", name="Newer form", group=newer_group)
        FormDefinition.objects.create(slug="G901_E_A1", name="Old group form", group=older_group)

        FormGroup.objects.filter(pk=older_group.pk).update(
            created_at=datetime(2026, 1, 1, tzinfo=dt_timezone.utc)
        )
        FormGroup.objects.filter(pk=newer_group.pk).update(
            created_at=datetime(2026, 2, 1, tzinfo=dt_timezone.utc)
        )

        user = get_user_model().objects.create_superuser(
            email="apps-order@example.com",
            password="test-password",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("admin_apps_list"))

        self.assertEqual(response.status_code, 200)
        target_groups = [
            (group, forms)
            for group, forms in response.context["group_list"]
            if group.number in {901, 902}
        ]
        self.assertEqual([group.number for group, _forms in target_groups], [902, 901])
        newest_group_forms = target_groups[0][1]
        self.assertEqual([form.slug for form in newest_group_forms[:2]], ["G902_M_A1", "G902_E_A1"])

    def test_master_preview_uses_current_master_a1_form_only(self):
        a1 = FormDefinition.objects.create(slug="E_A1", name="Current E A1", is_master=True)
        a2 = FormDefinition.objects.create(slug="E_A2", name="Current E A2", is_master=True)
        Question.objects.create(
            form=a1, text="Current master first question", slug="current_first",
            field_type=Question.SHORT_TEXT, position=1,
        )
        Question.objects.create(
            form=a2, text="Current master second question", slug="current_second",
            field_type=Question.SHORT_TEXT, position=1,
        )
        user = get_user_model().objects.create_superuser(
            email="preview-master@example.com",
            password="test-password",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("admin_apps_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/apply/E_A1/?preview=1")
        self.assertContains(response, ">\n                Preview\n              </a>", html=False)
        self.assertNotContains(response, "Preview combined")

        preview = self.client.get("/apply/E_A1/?preview=1")
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, "Current master first question")
        self.assertNotContains(preview, "Current master second question")


class ProfileMemoryBoundedBuildTests(TestCase):
    def setUp(self):
        cache.clear()
        admin_profiles_views._formgroup_number_from_id.cache_clear()

    def test_profile_build_ignores_unneeded_answers_and_uses_latest_grade(self):
        group = FormGroup.objects.create(
            number=903,
            start_day=1,
            start_month="enero",
            end_month="abril",
            year=2026,
        )
        form = FormDefinition.objects.create(slug="G903_E_A1", name="Group 903 E", group=group)
        app = Application.objects.create(form=form, name="Ada", email="ada@example.com")
        name_question = Question.objects.create(
            form=form,
            text="Full name",
            slug="full_name",
            field_type=Question.SHORT_TEXT,
        )
        essay_question = Question.objects.create(
            form=form,
            text="Long essay",
            slug="long_business_essay",
            field_type=Question.LONG_TEXT,
        )
        Answer.objects.create(application=app, question=name_question, value="Ada Lovelace")
        Answer.objects.create(application=app, question=essay_question, value="unused-essay " * 10000)
        GradedFile.objects.create(
            form_slug=form.slug,
            csv_text="email,recommendation,overall_score\nada@example.com,Maybe,50\n",
        )
        GradedFile.objects.create(
            form_slug=form.slug,
            csv_text="email,recommendation,overall_score\nada@example.com,Recommended,95\n",
        )

        profiles = admin_profiles_views._build_profiles_uncached()

        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["applicant_name"], "Ada Lovelace")
        self.assertEqual(profiles[0]["recommendation"], "Recommended")
        self.assertEqual(profiles[0]["overall_score"], "95")
        self.assertNotIn("unused-essay", profiles[0]["search_text"])


class GradingAndPairingConfigEditorTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            email="config-editor@example.com",
            password="test-password",
        )
        self.client.force_login(self.user)

    def test_grading_home_lists_current_a1_forms_and_hides_retired_a2_forms(self):
        FormDefinition.objects.create(slug="E_A1", name="Current entrepreneur application")
        FormDefinition.objects.create(slug="M_A1", name="Current mentor application")
        FormDefinition.objects.create(slug="E_A2", name="Retired entrepreneur application")
        FormDefinition.objects.create(slug="M_A2", name="Retired mentor application")

        response = self.client.get(reverse("admin_grading_home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Grading - Current Application")
        self.assertContains(response, "E_A1")
        self.assertContains(response, "M_A1")
        self.assertNotContains(response, "E_A2")
        self.assertNotContains(response, "M_A2")

    def test_grading_config_editor_creates_default_criteria_for_form(self):
        form = FormDefinition.objects.create(slug="G901_E_A1", name="E A1")

        response = self.client.get(reverse("admin_grading_config_editor", args=[form.slug]))

        self.assertEqual(response.status_code, 200)
        config = ApplicationGradingConfig.objects.get(form=form)
        self.assertContains(response, "Grading rules")
        self.assertContains(response, "Paragraph questions scored by AI")
        self.assertContains(response, "Dropdown, checkbox, and structured question scores")
        self.assertContains(response, "Existing grading flow and OpenAI requests")
        self.assertContains(response, "One user message; no system message")
        flow = response.context["openai_flow"]
        self.assertEqual(flow["model"], "gpt-5.2")
        self.assertEqual(flow["moderation_model"], "omni-moderation-latest")
        self.assertEqual(
            flow["moderation_fields"],
            ["business_description", "growth_how", "biggest_challenge"],
        )
        self.assertEqual(len(flow["ai_requests"]), 3)
        self.assertIn("Be strict but fair", flow["ai_requests"][0]["prompt"])
        self.assertIn(
            "<applicant response for business_description>",
            flow["ai_requests"][0]["prompt"],
        )
        self.assertEqual(config.max_total_score, 64)
        self.assertTrue(GradingCriterion.objects.filter(config=config, question_slug="growth_how").exists())
        self.assertTrue(GradingCriterion.objects.filter(config=config, question_slug="business_age", weight=5).exists())

    def test_grading_config_editor_creates_response_weights_for_dropdown_choices(self):
        form = FormDefinition.objects.create(slug="G902_E_A1", name="E A1")
        Question.objects.create(
            form=form,
            text="Business description",
            slug="business_description",
            field_type=Question.LONG_TEXT,
            position=1,
        )
        Question.objects.create(
            form=form,
            text="Tell us your business story",
            slug="business_story",
            field_type=Question.LONG_TEXT,
            position=2,
        )
        business_age = Question.objects.create(
            form=form,
            text="Business age",
            slug="business_age",
            field_type=Question.CHOICE,
            position=2,
        )
        Choice.objects.create(question=business_age, label="Idea", value="idea", position=1)
        selected_choice = Choice.objects.create(question=business_age, label="1-3 years", value="1_3y", position=2)

        response = self.client.get(reverse("admin_grading_config_editor", args=[form.slug]))

        self.assertEqual(response.status_code, 200)
        config = ApplicationGradingConfig.objects.get(form=form)
        self.assertContains(response, "Business age")
        self.assertEqual(response.content.decode().count("Business age"), 1)
        self.assertContains(response, "Question option")
        self.assertContains(response, "1-3 years")
        self.assertContains(response, "Tell us your business story")
        self.assertNotContains(response, "business_age=1_3y")
        self.assertTrue(
            GradingCriterion.objects.filter(
                config=config,
                question_slug="business_description",
                criterion_type=GradingCriterion.TYPE_AI_TEXT,
            ).exists()
        )
        extra_paragraph = GradingCriterion.objects.get(config=config, question_slug="business_story")
        self.assertEqual(extra_paragraph.criterion_type, GradingCriterion.TYPE_AI_TEXT)
        self.assertFalse(extra_paragraph.active)
        structured_group = next(
            group for group in response.context["structured_groups"]
            if group["question"].slug == "business_age"
        )
        self.assertEqual(structured_group["criterion"].question_slug, "business_age")
        self.assertEqual(
            GradingResponseWeight.objects.filter(config=config, question=business_age).count(),
            2,
        )

        GradingResponseWeight.objects.filter(config=config, choice=selected_choice).update(weight=7.5)
        runtime_config = runtime_grading_config_for_form_slug(form.slug)
        self.assertEqual(runtime_config.response_score("business_age", "1_3y"), 7.5)
        self.assertEqual(runtime_config.response_score("business_age", "idea"), 0.0)

    def test_grading_config_editor_saves_prompt_and_grouped_response_weights(self):
        form = FormDefinition.objects.create(slug="G903_E_A1", name="E A1")
        Question.objects.create(
            form=form,
            text="Growth plan",
            slug="growth_how",
            field_type=Question.LONG_TEXT,
            position=1,
        )
        stage = Question.objects.create(
            form=form,
            text="Stage",
            slug="business_age",
            field_type=Question.CHOICE,
            position=2,
        )
        choice = Choice.objects.create(question=stage, label="1-3 years", value="1_3y", position=1)
        self.client.get(reverse("admin_grading_config_editor", args=[form.slug]))
        config = ApplicationGradingConfig.objects.get(form=form)
        paragraph = GradingCriterion.objects.get(config=config, question_slug="growth_how")
        response_weight = GradingResponseWeight.objects.get(config=config, question=stage, choice=choice)

        response = self.client.post(
            reverse("admin_grading_config_editor", args=[form.slug]),
            {
                "model_name": "gpt-test",
                "max_total_score": "42",
                "rubric_note": "Custom rubric",
                f"criterion_{paragraph.id}_active": "on",
                f"criterion_{paragraph.id}_weight": "8",
                f"criterion_{paragraph.id}_prompt": "Score {{ response }}",
                f"response_weight_{response_weight.id}_active": "on",
                f"response_weight_{response_weight.id}_weight": "9.5",
            },
        )

        self.assertEqual(response.status_code, 302)
        config.refresh_from_db()
        paragraph.refresh_from_db()
        response_weight.refresh_from_db()
        self.assertEqual(config.model_name, "gpt-test")
        self.assertEqual(config.rubric_note, "Custom rubric")
        self.assertEqual(paragraph.prompt, "Score {{ response }}")
        self.assertEqual(str(response_weight.weight), "9.50")

        preview_response = self.client.get(reverse("admin_grading_config_editor", args=[form.slug]))
        flow = preview_response.context["openai_flow"]
        growth_preview = next(item for item in flow["ai_requests"] if item["slug"] == "growth_how")
        self.assertEqual(flow["model"], "gpt-test")
        self.assertTrue(flow["using_override"])
        self.assertTrue(growth_preview["uses_custom_prompt"])
        self.assertEqual(growth_preview["prompt"], "Score <applicant response for growth_how>")

    def test_grading_config_editor_saves_no_code_prompt_around_automatic_answer(self):
        form = FormDefinition.objects.create(slug="G905_E_A1", name="E A1")
        self.client.get(reverse("admin_grading_config_editor", args=[form.slug]))
        config = ApplicationGradingConfig.objects.get(form=form)
        criterion = GradingCriterion.objects.get(config=config, question_slug="growth_how")

        response = self.client.post(
            reverse("admin_grading_config_editor", args=[form.slug]),
            {
                "max_total_score": "64",
                f"criterion_{criterion.id}_active": "on",
                f"criterion_{criterion.id}_weight": "4",
                f"criterion_{criterion.id}_instructions": "Evaluate whether the growth plan is specific and realistic.",
            },
        )

        self.assertEqual(response.status_code, 302)
        criterion.refresh_from_db()
        self.assertEqual(
            criterion.prompt,
            "Evaluate whether the growth plan is specific and realistic.",
        )

        editor = self.client.get(reverse("admin_grading_config_editor", args=[form.slug]))
        self.assertContains(editor, "No code, placeholders, response labels, or special formatting are needed.")
        saved = next(
            item for item in editor.context["openai_flow"]["ai_requests"]
            if item["slug"] == "growth_how"
        )
        self.assertEqual(
            saved["prompt"],
            "\nCriterion: growth_how\n\nInstructions:\n"
            "Evaluate whether the growth plan is specific and realistic.\n\n"
            "Response:\n\"\"\"<applicant response for growth_how>\"\"\"\n\n"
            "Output EXACTLY:\nScore: <int>\n"
            "Explanation: <2–3 sentences justifying the score>\n",
        )

    def test_mentor_grading_editor_shows_exact_openai_fields_and_requirements(self):
        form = FormDefinition.objects.create(slug="G904_M_A1", name="M A1")

        response = self.client.get(reverse("admin_grading_config_editor", args=[form.slug]))

        self.assertEqual(response.status_code, 200)
        flow = response.context["openai_flow"]
        self.assertEqual(
            flow["moderation_fields"],
            [
                "business_description",
                "mentoring_exp_detail",
                "motivation",
                "professional_expertise",
            ],
        )
        self.assertEqual(len(flow["ai_requests"]), 4)
        self.assertIn("req_basic_woman", flow["disqualification_rules"])
        self.assertIn("Use a negative score only when it is justified", flow["ai_requests"][0]["prompt"])

    def test_enabled_additional_paragraph_is_used_by_runtime_grader(self):
        from applications import grader_e

        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(
                content="Score: 4\nExplanation: Strong and specific story."
            ))]
        )
        config = SimpleNamespace(
            weights={**grader_e.W, "business_story": 2},
            max_total_score=100,
            model_name="",
            rubric_note="",
            ai_criteria=("business_story",),
            structured_criteria=(),
            prompt=lambda slug: "Evaluate how specific and credible the story is." if slug == "business_story" else "",
            response_score=lambda _slug, _value, default=None: default,
        )

        result = grader_e.grade_single_row(
            {
                "internet_access": "yes_ok",
                "commit_3_months": "yes",
                "business_age": "lt_1",
                "business_story": "I started selling products locally two years ago.",
            },
            client,
            grading_config=config,
        )

        self.assertEqual(result[1], "8.60%")
        self.assertIn("business_story - Strong and specific story.", result[2])
        request = client.chat.completions.create.call_args.kwargs
        self.assertIn("Evaluate how specific and credible the story is.", request["messages"][0]["content"])
        self.assertIn("I started selling products locally two years ago.", request["messages"][0]["content"])

    def test_mentor_runtime_ignores_stale_fields_and_uses_current_form_denominator(self):
        import pandas as pd
        from applications import grader_m
        from applications.grading_config import ensure_grading_config_for_form

        form = FormDefinition.objects.create(slug="G904_M_A1", name="Current mentor form")
        for position, slug in enumerate(("descripcion_del_negocio", "motivacion_para_ser_mentora"), start=1):
            Question.objects.create(
                form=form,
                text=slug.replace("_", " "),
                slug=slug,
                field_type=Question.LONG_TEXT,
                position=position,
            )
        config_model = ensure_grading_config_for_form(form)
        config_model.criteria.filter(
            question_slug__in={"descripcion_del_negocio", "motivacion_para_ser_mentora"}
        ).update(active=True)

        runtime = runtime_grading_config_for_form_slug(form.slug)
        self.assertEqual(
            set(runtime.ai_criteria),
            {"descripcion_del_negocio", "motivacion_para_ser_mentora"},
        )
        self.assertEqual(runtime.max_total_score, 10)

        source = pd.DataFrame([{
            "application_id": 1,
            "full_name": "Current Applicant",
            "email": "current@example.com",
            "meets_requirements": "yes",
            "available_period": "yes",
            "descripcion_del_negocio": "Detailed business response",
            "motivacion_para_ser_mentora": "Detailed motivation response",
        }])
        with patch("applications.grader_m.detect_red_flags", return_value=""), patch(
            "applications.grader_m.grade_unstructured",
            return_value=(4.0, "Relevant current response."),
        ):
            result = grader_m.grade_from_dataframe(source, Mock(), grading_config=runtime)

        self.assertEqual(result.iloc[0]["score"], "80.00%")
        self.assertNotIn("business_description - Blank", result.iloc[0]["score_exp"])
        self.assertEqual(result.iloc[0]["descripcion_del_negocio"], "Detailed business response")
        self.assertEqual(list(result.columns), MENTORA_EXACT_OUTPUT_COLUMNS)
        self.assertNotIn("motivacion_para_ser_mentora", result.columns)
        self.assertNotIn("business_description", result.columns)

    def test_mentor_grading_removes_duplicate_submission_rows_after_scoring(self):
        import pandas as pd
        from applications import grader_m

        runtime = SimpleNamespace(
            uses_configured_criteria=True,
            weights={"current_story": 5},
            max_total_score=5,
            ai_criteria=("current_story",),
            structured_criteria=(),
            prompt=lambda _slug: "",
            allows_negative=lambda _slug, fallback=False: fallback,
            response_score=lambda _slug, _value, default=None: default,
            rubric_note="",
            model_name="",
        )
        source = pd.DataFrame([
            {
                "application_id": 1,
                "full_name": "Duplicate Applicant",
                "email": "duplicate@example.com",
                "meets_requirements": "yes",
                "available_period": "yes",
                "current_story": "First response",
            },
            {
                "application_id": 2,
                "full_name": "Duplicate Applicant",
                "email": "duplicate@example.com",
                "meets_requirements": "yes",
                "available_period": "yes",
                "current_story": "Second response",
            },
        ])

        logs = []
        with patch("applications.grader_m.detect_red_flags", return_value=""), patch(
            "applications.grader_m.grade_unstructured",
            side_effect=[(3.0, "Lower response."), (5.0, "Higher response.")],
        ):
            result = grader_m.grade_from_dataframe(source, Mock(), log_fn=logs.append, grading_config=runtime)

        self.assertEqual(len(result), 1)
        self.assertEqual(list(result["application_id"]), [2])
        self.assertEqual(list(result.columns), MENTORA_EXACT_OUTPUT_COLUMNS)
        self.assertTrue(
            any("Removed 1 duplicate mentora row(s) after grading" in msg for msg in logs)
        )

    def test_mentor_grading_uses_exact_selection_column_order(self):
        import pandas as pd
        from applications import grader_m

        runtime = SimpleNamespace(
            uses_configured_criteria=True,
            weights={"descripcion_del_negocio": 5},
            max_total_score=5,
            ai_criteria=("descripcion_del_negocio",),
            structured_criteria=(),
            prompt=lambda _slug: "",
            allows_negative=lambda _slug, fallback=False: fallback,
            response_score=lambda _slug, _value, default=None: default,
            rubric_note="Rubric note",
            model_name="",
        )
        source = pd.DataFrame([{
            "created_at": "2026-07-08T12:00:00",
            "application_id": 99,
            "full_name": "Fallback Name",
            "email": "fallback@example.com",
            "nombre_completo": "Applicant Name",
            "correo_electronico": "applicant@example.com",
            "whatsapp": "+573000000000",
            "ID": "ABC123",
            "Reside": "Colombia",
            "Nacionalidad": "Venezuela",
            "edad": "31-40",
            "meets_requirements": "yes",
            "available_period": "yes",
            "descripcion_del_negocio": "Detailed business response",
            "acepto_que_los_datos_proporcionados_sean_tratados": "yes",
        }])

        with patch("applications.grader_m.detect_red_flags", return_value=""), patch(
            "applications.grader_m.grade_unstructured",
            return_value=(4.0, "Relevant response."),
        ):
            result = grader_m.grade_from_dataframe(source, Mock(), grading_config=runtime)

        self.assertEqual(list(result.columns), MENTORA_EXACT_OUTPUT_COLUMNS)
        self.assertEqual(result.iloc[0]["nombre_completo"], "Applicant Name")
        self.assertEqual(result.iloc[0]["correo_electronico"], "applicant@example.com")
        self.assertEqual(result.iloc[0]["full_name"], "Fallback Name")
        self.assertEqual(result.iloc[0]["email"], "fallback@example.com")
        self.assertEqual(result.iloc[0]["grading_rubric"], "Rubric note")

    def test_emprendedora_runtime_exports_source_columns_and_dedupes_people(self):
        import pandas as pd
        from applications import grader_e

        runtime = SimpleNamespace(
            uses_configured_criteria=True,
            weights={"descripcion_del_negocio": 5},
            max_total_score=5,
            ai_criteria=("descripcion_del_negocio",),
            structured_criteria=(),
            prompt=lambda _slug: "",
            response_score=lambda _slug, _value, default=None: default,
            rubric_note="",
            model_name="",
        )
        source = pd.DataFrame([
            {
                "application_id": 1,
                "full_name": "Applicant One",
                "email": "same@example.com",
                "internet_access": "yes_ok",
                "commit_3_months": "yes",
                "business_age": "1_3y",
                "descripcion_del_negocio": "Respuesta detallada uno",
            },
            {
                "application_id": 2,
                "full_name": "Applicant Two",
                "email": "same@example.com",
                "internet_access": "yes_ok",
                "commit_3_months": "yes",
                "business_age": "1_3y",
                "descripcion_del_negocio": "Respuesta detallada dos",
            },
        ])

        logs = []
        with patch("applications.grader_e.detect_red_flags", return_value=""), patch(
            "applications.grader_e.grade_unstructured",
            side_effect=[(3.0, "Lower response."), (5.0, "Higher response.")],
        ):
            result = grader_e.grade_from_dataframe(source, Mock(), log_fn=logs.append, grading_config=runtime)

        self.assertEqual(len(result), 1)
        self.assertEqual(
            list(result.columns[:6]),
            ["Status", "score", "score_exp", "application_id", "full_name", "email"],
        )
        self.assertEqual(result.iloc[0]["application_id"], 2)
        self.assertEqual(result.iloc[0]["descripcion_del_negocio"], "Respuesta detallada dos")
        self.assertNotIn("business_description", result.columns)
        self.assertTrue(
            any("Removed 1 duplicate emprendedora row(s) after grading" in msg for msg in logs)
        )

    def test_emprendedora_legacy_output_has_no_duplicate_columns(self):
        import pandas as pd
        from applications import grader_e

        source = pd.DataFrame([{
            "full_name": "Applicant",
            "email": "applicant@example.com",
            "internet_access": "yes_ok",
            "commit_3_months": "yes",
            "business_age": "1_3y",
            "business_description": "Business description",
            "growth_how": "Growth plan",
            "biggest_challenge": "Challenge",
        }])

        with patch("applications.grader_e.detect_red_flags", return_value=""), patch(
            "applications.grader_e.grade_unstructured",
            return_value=(4.0, "Relevant response."),
        ):
            result = grader_e.grade_from_dataframe(source, Mock())

        self.assertEqual(result.columns.tolist().count("business_age"), 1)
        self.assertEqual(len(result.columns), len(set(result.columns)))

    def test_pairing_config_editor_creates_default_priority_and_ai_rules(self):
        group = FormGroup.objects.create(
            number=901,
            start_day=1,
            start_month="enero",
            end_month="abril",
            year=2026,
        )

        response = self.client.get(reverse("admin_pairing_config_editor", args=[group.number]))

        self.assertEqual(response.status_code, 302)
        config = PairingConfig.objects.get(group=group)
        self.assertIn(f"/admin/applications/pairingconfig/{config.id}/change/", response["Location"])
        self.assertTrue(
            PairingPriorityRule.objects.filter(
                config=config,
                comparison_type="availability_overlap",
                required=True,
            ).exists()
        )
        self.assertTrue(
            PairingAIComparison.objects.filter(
                config=config,
                emprendedora_question_slug="growth_how",
                mentora_question_slug="professional_expertise",
            ).exists()
        )

    def test_pairing_editor_uses_question_dropdowns_from_current_group_a1_forms(self):
        group = FormGroup.objects.create(
            number=906,
            start_day=1,
            start_month="enero",
            end_month="abril",
            year=2026,
        )
        other_group = FormGroup.objects.create(
            number=907,
            start_day=1,
            start_month="enero",
            end_month="abril",
            year=2026,
        )
        entrepreneur_form = FormDefinition.objects.create(
            slug="G906_E_A1", name="Group 906 Emprendedora", group=group
        )
        mentor_form = FormDefinition.objects.create(
            slug="G906_M_A1", name="Group 906 Mentora", group=group
        )
        retired_form = FormDefinition.objects.create(
            slug="G906_E_A2", name="Retired Group 906 form", group=group
        )
        other_form = FormDefinition.objects.create(
            slug="G907_E_A1", name="Other group form", group=other_group
        )
        stage = Question.objects.create(
            form=entrepreneur_form,
            text="Current business stage",
            slug="business_stage_current",
            field_type=Question.CHOICE,
            position=1,
        )
        Choice.objects.create(question=stage, label="Idea", value="idea", position=1)
        Choice.objects.create(question=stage, label="Operating", value="operating", position=2)
        Question.objects.create(
            form=mentor_form,
            text="Current mentoring expertise",
            slug="mentoring_expertise_current",
            field_type=Question.LONG_TEXT,
            position=1,
        )
        Question.objects.create(
            form=retired_form,
            text="Retired application question",
            slug="retired_question",
            field_type=Question.SHORT_TEXT,
        )
        Question.objects.create(
            form=other_form,
            text="Other group question",
            slug="other_group_question",
            field_type=Question.SHORT_TEXT,
        )

        response = self.client.get(
            reverse("admin_pairing_config_editor", args=[group.number]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Group 906 pairing rules")
        self.assertContains(response, 'class="ce-pairing-question-select"')
        self.assertContains(response, "Current business stage")
        self.assertContains(response, "Current mentoring expertise")
        self.assertContains(response, "Idea (stored as idea) / Operating (stored as operating)")
        self.assertContains(response, "Group 906 Emprendedora (G906_E_A1)")
        self.assertContains(response, "Group 906 Mentora (G906_M_A1)")
        self.assertNotContains(response, "Retired application question")
        self.assertNotContains(response, "Other group question")
        self.assertContains(response, "ce-pairing-guide")
        self.assertContains(response, "ce-pairing-answer-preview")

    def test_pairing_home_prefills_selected_groups_participant_emails(self):
        group = FormGroup.objects.create(
            number=908,
            start_day=1,
            start_month="enero",
            end_month="abril",
            year=2026,
        )
        other_group = FormGroup.objects.create(
            number=909,
            start_day=1,
            start_month="enero",
            end_month="abril",
            year=2026,
        )
        GroupParticipantList.objects.create(
            group=group,
            mentoras_sheet_rows=[
                ["", "A", 1, "Mentor One", "M1", "mentor.one@example.com"],
                ["", "A", 2, "Mentor Two", "M2", "mentor.two@example.com"],
            ],
            emprendedoras_sheet_rows=[
                ["", "A", 1, "Founder", "E1", "founder@example.com"],
            ],
        )
        GroupParticipantList.objects.create(
            group=other_group,
            mentoras_emails_text="other.mentor@example.com",
            emprendedoras_emails_text="other.founder@example.com",
        )
        selected_output = GradedFile.objects.create(
            form_slug="PAIR_G908",
            csv_text="mentor,founder\nmentor.one@example.com,founder@example.com",
        )
        GradedFile.objects.create(
            form_slug="PAIR_G909",
            csv_text="mentor,founder\nother.mentor@example.com,other.founder@example.com",
        )

        response = self.client.get(
            reverse("admin_emparejamiento_home"),
            {"group": group.number},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "mentor.one@example.com")
        self.assertContains(response, "mentor.two@example.com")
        self.assertContains(response, "founder@example.com")
        self.assertNotContains(response, "other.mentor@example.com")
        self.assertContains(response, "2 emails")
        self.assertContains(response, "1 email")
        self.assertContains(response, reverse("admin_grading_live_sheet_file", args=[selected_output.id]))
        self.assertContains(response, "These emails are loaded from Group 908")
        self.assertContains(response, 'id="ce-pairing-group"')

    def test_pairing_rule_form_accepts_only_selected_groups_current_questions(self):
        group = FormGroup.objects.create(
            number=910,
            start_day=1,
            start_month="enero",
            end_month="abril",
            year=2026,
        )
        other_group = FormGroup.objects.create(
            number=911,
            start_day=1,
            start_month="enero",
            end_month="abril",
            year=2026,
        )
        entrepreneur_form = FormDefinition.objects.create(
            slug="G910_E_A1", name="Group 910 Emprendedora", group=group
        )
        mentor_form = FormDefinition.objects.create(
            slug="G910_M_A1", name="Group 910 Mentora", group=group
        )
        other_form = FormDefinition.objects.create(
            slug="G911_E_A1", name="Group 911 Emprendedora", group=other_group
        )
        entrepreneur_question = Question.objects.create(
            form=entrepreneur_form,
            text="Business goal",
            slug="group_910_business_goal",
            field_type=Question.LONG_TEXT,
        )
        mentor_question = Question.objects.create(
            form=mentor_form,
            text="Mentoring experience",
            slug="group_910_mentoring_experience",
            field_type=Question.LONG_TEXT,
        )
        other_question = Question.objects.create(
            form=other_form,
            text="Other group question",
            slug="group_911_question",
            field_type=Question.LONG_TEXT,
        )
        config = PairingConfig.objects.create(group=group)
        form_data = {
            "config": config.id,
            "label": "Goals and experience",
            "emprendedora_question_slug": entrepreneur_question.slug,
            "mentora_question_slug": mentor_question.slug,
            "comparison_type": PairingPriorityRule.COMPARE_EXACT,
            "weight": "2.00",
            "required": False,
            "output_key": "goals_experience",
            "active": True,
            "position": 1,
        }

        form = PairingRuleAdminForm(data=form_data, pairing_config=config)
        self.assertTrue(form.is_valid(), form.errors)
        saved_rule = form.save()
        self.assertEqual(saved_rule.config, config)

        form_data["emprendedora_question_slug"] = other_question.slug
        cross_group_form = PairingRuleAdminForm(data=form_data, pairing_config=config)
        self.assertFalse(cross_group_form.is_valid())
        self.assertIn("emprendedora_question_slug", cross_group_form.errors)

    def test_pairing_job_uses_current_a1_forms_instead_of_retired_a2_forms(self):
        from applications.admin_views import _pair_one_group

        group = FormGroup.objects.create(
            number=912,
            start_day=1,
            start_month="enero",
            end_month="abril",
            year=2026,
        )
        current_e = FormDefinition.objects.create(
            slug="G912_E_A1", name="Current entrepreneur application", group=group
        )
        current_m = FormDefinition.objects.create(
            slug="G912_M_A1", name="Current mentor application", group=group
        )
        FormDefinition.objects.create(
            slug="G912_E_A2", name="Retired entrepreneur application", group=group
        )
        FormDefinition.objects.create(
            slug="G912_M_A2", name="Retired mentor application", group=group
        )
        Question.objects.create(
            form=current_e,
            text="Current entrepreneur answer",
            slug="current_entrepreneur_answer",
            field_type=Question.LONG_TEXT,
        )
        Question.objects.create(
            form=current_m,
            text="Current mentor answer",
            slug="current_mentor_answer",
            field_type=Question.LONG_TEXT,
        )
        logs = []

        result = _pair_one_group(
            group_num=group.number,
            emp_emails=["founder@example.com"],
            mentor_emails=["mentor@example.com"],
            log_fn=logs.append,
        )

        self.assertTrue(result.empty)
        self.assertIn("current_entrepreneur_answer_emprendedora", result.columns)
        self.assertIn("current_mentor_answer_mentora", result.columns)
        self.assertTrue(
            any("G912_E_A1 and G912_M_A1" in message for message in logs),
            logs,
        )
        self.assertFalse(any("G912_E_A2" in message or "G912_M_A2" in message for message in logs))

    def test_pairing_uses_configured_a1_grid_questions_for_availability(self):
        from applications.admin_views import _pair_one_group, _parse_emp_availability

        group = FormGroup.objects.create(
            number=913,
            start_day=1,
            start_month="enero",
            end_month="abril",
            year=2026,
        )
        entrepreneur_form = FormDefinition.objects.create(
            slug="G913_E_A1", name="Current entrepreneur application", group=group
        )
        mentor_form = FormDefinition.objects.create(
            slug="G913_M_A1", name="Current mentor application", group=group
        )
        entrepreneur_schedule = Question.objects.create(
            form=entrepreneur_form,
            text="When can you participate?",
            slug="current_entrepreneur_schedule",
            field_type=Question.MULTIPLE_CHOICE_GRID,
            grid_rows="Mañana\nTarde\nNoche",
        )
        mentor_schedule = Question.objects.create(
            form=mentor_form,
            text="When can you participate?",
            slug="current_mentor_schedule",
            field_type=Question.MULTIPLE_CHOICE_GRID,
            grid_rows="Mañana\nTarde\nNoche",
        )
        entrepreneur_industry = Question.objects.create(
            form=entrepreneur_form,
            text="Business industry",
            slug="business_active",
            field_type=Question.SHORT_TEXT,
        )
        mentor_industry = Question.objects.create(
            form=mentor_form,
            text="Business industry",
            slug="industria_de_tu_emprendimiento",
            field_type=Question.SHORT_TEXT,
        )
        entrepreneur_country = Question.objects.create(
            form=entrepreneur_form,
            text="Country",
            slug="pais_donde_vives_ahora_2",
            field_type=Question.SHORT_TEXT,
        )
        mentor_country = Question.objects.create(
            form=mentor_form,
            text="Country",
            slug="pais_donde_vives_ahora",
            field_type=Question.SHORT_TEXT,
        )
        entrepreneur_age = Question.objects.create(
            form=entrepreneur_form,
            text="Business age",
            slug="comment",
            field_type=Question.SHORT_TEXT,
        )
        mentor_age = Question.objects.create(
            form=mentor_form,
            text="Business age",
            slug="cuanto_tiempo_has_estado_operando_o_por_cuanto_tie",
            field_type=Question.SHORT_TEXT,
        )
        entrepreneur_growth = Question.objects.create(
            form=entrepreneur_form,
            text="Growth plan",
            slug="como_crees_que_este_programa_puede_ayudarte_a_crec",
            field_type=Question.LONG_TEXT,
        )
        mentor_expertise = Question.objects.create(
            form=mentor_form,
            text="Professional expertise",
            slug="cual_es_tu_area_de_experiencia_profesional_mas_rel",
            field_type=Question.LONG_TEXT,
        )
        entrepreneur_challenge = Question.objects.create(
            form=entrepreneur_form,
            text="Biggest challenge",
            slug="cual_es_tu_mayor_desafio_actualmente_como_emprende",
            field_type=Question.LONG_TEXT,
        )
        mentor_motivation = Question.objects.create(
            form=mentor_form,
            text="Mentor motivation",
            slug="que_te_motiva_a_ser_mentora_en_este_programa_de_cl",
            field_type=Question.LONG_TEXT,
        )
        entrepreneur = Application.objects.create(
            form=entrepreneur_form,
            name="Founder",
            email="founder@example.com",
        )
        mentor = Application.objects.create(
            form=mentor_form,
            name="Mentor",
            email="mentor@example.com",
        )
        second_mentor = Application.objects.create(
            form=mentor_form,
            name="Second Mentor",
            email="second.mentor@example.com",
        )
        Answer.objects.create(
            application=entrepreneur,
            question=entrepreneur_schedule,
            value=json.dumps([
                {"row": "Tarde", "value": "martes", "label": "Martes"},
            ]),
        )
        Answer.objects.create(
            application=mentor,
            question=mentor_schedule,
            value=json.dumps([
                {"row": "Tarde", "value": "martes", "label": "Martes"},
                {"row": "Noche", "value": "viernes", "label": "Viernes"},
            ]),
        )
        Answer.objects.create(
            application=second_mentor,
            question=mentor_schedule,
            value=json.dumps([
                {"row": "Tarde", "value": "martes", "label": "Martes"},
            ]),
        )
        for application, answers in (
            (
                entrepreneur,
                (
                    (entrepreneur_industry, "services"),
                    (entrepreneur_country, "colombia"),
                    (entrepreneur_age, "1-5-anos"),
                    (entrepreneur_growth, "I need a practical growth plan."),
                    (entrepreneur_challenge, "I need to find more customers."),
                ),
            ),
            (
                mentor,
                (
                    (mentor_industry, "services"),
                    (mentor_country, "colombia"),
                    (mentor_age, "5-10nanos"),
                    (mentor_expertise, "I help companies grow their sales."),
                    (mentor_motivation, "I want to help founders find customers."),
                ),
            ),
            (
                second_mentor,
                (
                    (mentor_industry, "services"),
                    (mentor_country, "colombia"),
                    (mentor_age, "5-10nanos"),
                    (mentor_expertise, "I help companies build growth plans."),
                    (mentor_motivation, "I want to help founders solve challenges."),
                ),
            ),
        ):
            for question, value in answers:
                Answer.objects.create(
                    application=application,
                    question=question,
                    value=value,
                )
        config = PairingConfig.objects.create(
            group=group,
            availability_required=True,
            top_k_for_ai=2,
        )
        PairingPriorityRule.objects.create(
            config=config,
            label="Availability",
            emprendedora_question_slug=entrepreneur_schedule.slug,
            mentora_question_slug=mentor_schedule.slug,
            comparison_type=PairingPriorityRule.COMPARE_AVAILABILITY,
            weight=10,
            required=True,
            output_key="availability",
        )
        PairingPriorityRule.objects.create(
            config=config,
            label="Industry",
            emprendedora_question_slug="industry",
            mentora_question_slug="business_industry",
            comparison_type=PairingPriorityRule.COMPARE_EXACT,
            weight=10,
            output_key="industry",
        )
        PairingPriorityRule.objects.create(
            config=config,
            label="Country",
            emprendedora_question_slug="country_residence",
            mentora_question_slug="country_residence",
            comparison_type=PairingPriorityRule.COMPARE_EXACT,
            weight=10,
            output_key="country",
        )
        PairingPriorityRule.objects.create(
            config=config,
            label="Business age",
            emprendedora_question_slug="business_age",
            mentora_question_slug="business_years",
            comparison_type=PairingPriorityRule.COMPARE_BUSINESS_AGE,
            weight=10,
            output_key="biz_age",
        )
        PairingAIComparison.objects.create(
            config=config,
            label="Expertise and growth",
            emprendedora_question_slug="growth_how",
            mentora_question_slug="professional_expertise",
            weight=1,
            output_key="llm1",
        )
        PairingAIComparison.objects.create(
            config=config,
            label="Motivation and challenge",
            emprendedora_question_slug="biggest_challenge",
            mentora_question_slug="motivation",
            weight=1,
            output_key="llm2",
        )
        logs = []

        def score_in_one_batch(_client, candidates, **_kwargs):
            return {
                (candidate["candidate_id"], comparison["index"]): (
                    1,
                    "Compatible schedules.",
                )
                for candidate in candidates
                for comparison in candidate["comparisons"]
            }

        with override_settings(OPENAI_API_KEY="test-key"), patch(
            "applications.admin_views.OpenAI"
        ), patch(
            "applications.admin_views._llm_batch_fit_scores",
            side_effect=score_in_one_batch,
        ) as mock_batch:
            result = _pair_one_group(
                group_num=group.number,
                emp_emails=[entrepreneur.email],
                mentor_emails=[mentor.email, second_mentor.email],
                log_fn=logs.append,
            )

        self.assertEqual(len(result), 1)
        self.assertIn(
            result.iloc[0]["mentora_email"],
            {mentor.email, second_mentor.email},
        )
        self.assertEqual(result.iloc[0]["matching_availability"], "tue_afternoon")
        self.assertNotEqual(result.iloc[0]["matching_availability"], "NO MATCH FOUND")
        self.assertEqual(result.iloc[0]["matching_industry"], "services")
        self.assertEqual(result.iloc[0]["emprendedora_industry"], "services")
        self.assertEqual(result.iloc[0]["mentora_industry"], "services")
        self.assertEqual(result.iloc[0]["matching_country"], "colombia")
        self.assertEqual(result.iloc[0]["business_age_matching"], "mentor_max=10 >= emp_min=1")
        self.assertEqual(result.iloc[0]["expertise_growth_matching"], "Compatible schedules.")
        self.assertEqual(result.iloc[0]["motivation_challenge_match"], "Compatible schedules.")
        self.assertEqual(mock_batch.call_count, 1)
        batch_candidates = mock_batch.call_args.args[1]
        self.assertEqual(len(batch_candidates), 2)
        self.assertEqual(
            sum(len(candidate["comparisons"]) for candidate in batch_candidates),
            4,
        )
        self.assertTrue(
            any(
                "emprendedora=current_entrepreneur_schedule, "
                "mentora=current_mentor_schedule" in message
                for message in logs
            ),
            logs,
        )
        self.assertTrue(
            any("Resolved legacy standard rule 'Industry'" in message for message in logs),
            logs,
        )
        self.assertTrue(
            any("Resolved legacy AI comparison 'Expertise and growth'" in message for message in logs),
            logs,
        )
        self.assertTrue(
            any("one AI batch for 2 candidate(s) and 4 comparison(s)" in message for message in logs),
            logs,
        )
        self.assertEqual(
            _parse_emp_availability(
                '[{"row":"Mañana","value":"test","label":"Lunes"}]'
            ),
            {"mon_morning"},
        )

    def test_batched_pairing_ai_parses_all_candidate_scores_from_one_request(self):
        from applications.admin_views import _llm_batch_fit_scores

        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
                "results": [
                    {
                        "candidate_id": "0",
                        "comparisons": [
                            {"index": 1, "score": 4, "reasoning": "Strong expertise fit."},
                            {"index": 2, "score": 3, "reasoning": "Useful motivation fit."},
                        ],
                    },
                    {
                        "candidate_id": "1",
                        "comparisons": [
                            {"index": 1, "score": 2, "reasoning": "Partial expertise fit."},
                            {"index": 2, "score": 5, "reasoning": "Excellent challenge fit."},
                        ],
                    },
                ],
            })))]
        )
        candidates = [
            {
                "candidate_id": "0",
                "comparisons": [
                    {
                        "index": 1,
                        "label": "Expertise",
                        "mentor_response": "Sales experience",
                        "entrepreneur_response": "Needs sales help",
                    },
                ],
            },
            {
                "candidate_id": "1",
                "comparisons": [
                    {
                        "index": 1,
                        "label": "Expertise",
                        "mentor_response": "Marketing experience",
                        "entrepreneur_response": "Needs sales help",
                    },
                ],
            },
        ]

        result = _llm_batch_fit_scores(client, candidates)

        self.assertEqual(result[("0", 1)], (4, "Strong expertise fit."))
        self.assertEqual(result[("0", 2)], (3, "Useful motivation fit."))
        self.assertEqual(result[("1", 1)], (2, "Partial expertise fit."))
        self.assertEqual(result[("1", 2)], (5, "Excellent challenge fit."))
        client.chat.completions.create.assert_called_once()


class HelpTextFormattingTests(TestCase):
    def test_pasted_help_text_keeps_paragraphs_and_auto_links_url(self):
        rendered = str(format_help_text(
            "Soy mujer.\n\n"
            "Hablo espanol.\n\n"
            "Revisé el PDF que ofrece una breve introducción.\n\n"
            "https://drive.google.com/file/d/1MPN5JD6WoAsEnkgyUOtEEzfUtiMytD3s/view"
        ))

        self.assertIn("<p>Soy mujer.</p>", rendered)
        self.assertIn("<p>Hablo espanol.</p>", rendered)
        self.assertIn(
            'href="https://drive.google.com/file/d/1MPN5JD6WoAsEnkgyUOtEEzfUtiMytD3s/view"',
            rendered,
        )
        self.assertIn('target="_blank"', rendered)

    def test_legacy_anchor_help_text_is_normalized_without_showing_html(self):
        rendered = str(format_help_text(
            '📄 <a href="https://example.com/file">Abrir PDF</a>'
        ))

        self.assertIn("📄 Abrir PDF", rendered)
        self.assertIn('href="https://example.com/file"', rendered)
        self.assertNotIn("&lt;a", rendered)

    def test_toolbar_rich_text_keeps_safe_formatting_and_removes_scripts(self):
        rendered = str(format_rich_text(
            '<div data-ce-rich-text="1"><div style="line-height: 1.5">'
            '<font size="5"><strong>Important</strong></font>'
            '<script>alert(1)</script></div></div>'
        ))

        self.assertIn('style="line-height: 1.5;"', rendered)
        self.assertIn('<span style="font-size: 1.5em;"><strong>Important</strong></span>', rendered)
        self.assertNotIn("<script", rendered)


class ApplicationEmailValidationTests(TestCase):
    def test_email_and_correo_questions_reject_random_text(self):
        form_def = FormDefinition.objects.create(slug="email_validation", name="Email validation")
        Question.objects.create(
            form=form_def,
            text="Correo electrónico",
            slug="correo_electronico",
            field_type=Question.SHORT_TEXT,
            position=1,
        )
        ApplicationForm = build_application_form(form_def.slug)

        invalid = ApplicationForm({"q_correo_electronico": "random characters"})
        self.assertFalse(invalid.is_valid())
        self.assertIn("correo electrónico válida", str(invalid.errors))

        valid = ApplicationForm({"q_correo_electronico": "person@example.com"})
        self.assertTrue(valid.is_valid(), valid.errors)


class MultipleChoiceGridTests(TestCase):
    def setUp(self):
        self.form_def = FormDefinition.objects.create(
            slug="grid_form",
            name="Grid form",
            is_public=True,
            accepting_responses=True,
            manual_open_override=True,
        )
        self.question = Question.objects.create(
            form=self.form_def,
            text="Califica cada área",
            slug="area_grid",
            field_type=Question.MULTIPLE_CHOICE_GRID,
            grid_rows="Ventas\nFinanzas",
            required=True,
            position=1,
        )
        Choice.objects.create(question=self.question, value="low", label="Bajo", position=1)
        Choice.objects.create(question=self.question, value="high", label="Alto", position=2)

    def test_grid_renders_independent_checkboxes_for_every_cell(self):
        form = build_application_form(self.form_def.slug)()
        html = str(form["q_area_grid"])

        self.assertIn("Ventas", html)
        self.assertIn("Finanzas", html)
        self.assertIn("Bajo", html)
        self.assertIn("Alto", html)
        self.assertIn('name="q_area_grid__row_0"', html)
        self.assertIn('name="q_area_grid__row_1"', html)
        self.assertEqual(html.count('type="checkbox"'), 4)
        self.assertNotIn('type="radio"', html)
        self.assertEqual(html.count('class="ce-grid-choice__mark"'), 4)

    def test_required_grid_accepts_one_checkbox_anywhere_and_multiple_per_row(self):
        ApplicationForm = build_application_form(self.form_def.slug)
        empty = ApplicationForm({})
        self.assertFalse(empty.is_valid())
        self.assertIn("q_area_grid", empty.errors)

        one_selection = ApplicationForm({"q_area_grid__row_1": "high"})
        self.assertTrue(one_selection.is_valid(), one_selection.errors)

        multiple = ApplicationForm({
            "q_area_grid__row_0": ["low", "high"],
        })
        self.assertTrue(multiple.is_valid(), multiple.errors)
        answers = json.loads(multiple.cleaned_data["q_area_grid"])
        self.assertEqual(answers, [
            {"row": "Ventas", "value": "low", "label": "Bajo"},
            {"row": "Ventas", "value": "high", "label": "Alto"},
        ])

    def test_admin_requires_grid_rows_and_columns(self):
        form = QuestionAdminForm(
            data={
                "form": str(self.form_def.pk),
                "text": "Grid without configuration",
                "slug": "grid_without_configuration",
                "field_type": Question.MULTIPLE_CHOICE_GRID,
                "grid_rows": "",
                "answer_options": "",
                "required": "on",
                "active": "on",
                "position": "2",
                "show_if_conditions": "[]",
                "end_form_rules": "[]",
            },
            instance=Question(),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("grid_rows", form.errors)
        self.assertIn("answer_options", form.errors)

    def test_form_description_is_a_question_free_intro_page(self):
        self.form_def.description = "Description shown only on page one."
        self.form_def.save(update_fields=["description"])
        first = Section.objects.create(form=self.form_def, title="First section", position=1)
        second = Section.objects.create(form=self.form_def, title="Second section", position=2)
        self.question.section = first
        self.question.save(update_fields=["section"])
        Question.objects.create(
            form=self.form_def,
            section=second,
            text="Second-page question",
            slug="second_page_question",
            field_type=Question.SHORT_TEXT,
            position=2,
        )

        response = self.client.get(reverse("apply_by_slug", args=[self.form_def.slug]))
        html = response.content.decode()
        first_panel = html.index('data-section-index="0"')
        description = html.index("Description shown only on page one.")
        second_panel = html.index('data-section-index="1"')
        first_question = html.index('name="q_area_grid__row_0"')

        self.assertLess(first_panel, description)
        self.assertLess(description, second_panel)
        self.assertLess(second_panel, first_question)
        self.assertEqual(html.count("Description shown only on page one."), 1)
        self.assertContains(response, "Antes de comenzar")


class SingleCombinedApplicationTests(TestCase):
    def setUp(self):
        self.group = FormGroup.objects.create(
            number=990,
            start_day=1,
            start_month="junio",
            end_month="junio",
            year=2026,
            use_combined_application=True,
        )
        self.a1 = FormDefinition.objects.create(
            slug="G990_E_A1",
            name="Combined application",
            group=self.group,
            is_public=True,
            accepting_responses=True,
            manual_open_override=True,
        )
        self.a2 = FormDefinition.objects.create(
            slug="G990_E_A2",
            name="Combined application details",
            group=self.group,
            is_public=True,
            accepting_responses=True,
            manual_open_override=True,
        )
        for position, slug in enumerate(
            ("full_name", "email", "meets_requirements", "available_period", "business_active"),
            start=1,
        ):
            Question.objects.create(
                form=self.a1,
                text=slug.replace("_", " ").title(),
                slug=slug,
                field_type=Question.BOOLEAN if position > 2 else Question.SHORT_TEXT,
                position=position,
            )
        self.a2_question = Question.objects.create(
            form=self.a2,
            text="Tell us about the business",
            slug="business_story",
            field_type=Question.LONG_TEXT,
            position=1,
        )

    def test_current_group_get_renders_a1_only_even_when_a2_exists(self):
        response = self.client.get(reverse("apply_by_slug", args=[self.a1.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="q_meets_requirements"')
        self.assertNotContains(response, 'name="q_business_story"')
        self.assertContains(response, "data-application-progress")
        self.assertEqual(response.content.count(b'type="submit"'), 1)

    @patch("applications.views.schedule_group_track_responses_sync")
    @patch("applications.views._send_a2_submission_email")
    @patch("applications.views.autograde_and_email_emprendedora_a1")
    def test_current_group_post_creates_one_a1_application_without_a2_answers(
        self, mock_a1_grade, mock_a2_email, mock_sync
    ):
        response = self.client.post(
            reverse("apply_by_slug", args=[self.a1.slug]),
            {
                "q_full_name": "One Applicant",
                "q_email": "one@example.com",
                "q_meets_requirements": "yes",
                "q_available_period": "yes",
                "q_business_active": "yes",
                "q_business_story": "A running business",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Application.objects.count(), 1)
        app = Application.objects.get()
        self.assertEqual(app.form, self.a1)
        self.assertFalse(app.invited_to_second_stage)
        self.assertEqual(app.answers.count(), 5)
        self.assertFalse(app.answers.filter(question=self.a2_question).exists())
        mock_a1_grade.assert_called_once()
        mock_a2_email.assert_not_called()
        mock_sync.assert_called_with(self.group.number, "E")


class TerminalAnswerRuleTests(TestCase):
    def test_matching_answer_ends_form_shows_message_and_sends_plain_email(self):
        form_def = FormDefinition.objects.create(
            slug="terminal_rule_test",
            name="Terminal rule test",
            is_public=True,
            accepting_responses=True,
            manual_open_override=True,
        )
        Question.objects.create(
            form=form_def,
            text="Name",
            slug="full_name",
            field_type=Question.SHORT_TEXT,
            position=1,
        )
        Question.objects.create(
            form=form_def,
            text="Email",
            slug="email",
            field_type=Question.SHORT_TEXT,
            position=2,
        )
        gate = Question.objects.create(
            form=form_def,
            text="Do you meet the requirements?",
            slug="requirements",
            field_type=Question.CHOICE,
            position=3,
            end_form_rules=[{
                "value": "no",
                "email_name": "Requirements rejection",
                "page_title": "Application ended",
                "page_message": "You do not currently meet the requirements.",
            }],
        )
        Choice.objects.create(question=gate, value="yes", label="Yes", position=1)
        Choice.objects.create(question=gate, value="no", label="No", position=2)
        later = Question.objects.create(
            form=form_def,
            text="Required later question",
            slug="later_required",
            field_type=Question.LONG_TEXT,
            position=4,
            required=True,
        )
        StoredEmailTemplate.objects.create(
            form=form_def,
            name="Requirements rejection",
            subject="Update for {{ name }}",
            body="Hello {{ name }},\n\nYou do not meet the requirements.",
        )

        response = self.client.post(
            reverse("apply_by_slug", args=[form_def.slug]),
            {
                "q_full_name": "Applicant One",
                "q_email": "applicant@example.com",
                "q_requirements": "no",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Application ended")
        self.assertContains(response, "You do not currently meet the requirements.")
        app = Application.objects.get(form=form_def)
        self.assertFalse(app.approved_for_grading)
        self.assertEqual(app.answers.get(question=later).value, "")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Update for Applicant One")
        self.assertEqual(mail.outbox[0].content_subtype, "plain")

    def test_normal_completion_uses_default_approval_page_and_email(self):
        form_def = FormDefinition.objects.create(
            slug="default_approval_test",
            name="Default approval test",
            is_public=True,
            accepting_responses=True,
            manual_open_override=True,
            thanks_approved_title="Approved for consideration",
            thanks_approved_message="We received your application.\nWe will review it shortly.",
            approval_email_name="Application received",
        )
        Question.objects.create(
            form=form_def,
            text="Name",
            slug="full_name",
            field_type=Question.SHORT_TEXT,
            position=1,
        )
        Question.objects.create(
            form=form_def,
            text="Email",
            slug="email",
            field_type=Question.SHORT_TEXT,
            position=2,
        )
        StoredEmailTemplate.objects.create(
            form=form_def,
            name="Application received",
            subject="Application received for {{ name }}",
            body=(
                '<div data-ce-rich-text="1"><div style="line-height: 1.5">'
                '<strong>Hello {{ name }}</strong><br>Your application is under consideration.'
                '</div></div>'
            ),
        )

        response = self.client.post(
            reverse("apply_by_slug", args=[form_def.slug]),
            {"q_full_name": "Applicant Two", "q_email": "two@example.com"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Approved for consideration")
        self.assertContains(response, "We received your application.")
        self.assertTrue(Application.objects.get(form=form_def).approved_for_grading)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Application received for Applicant Two")
        self.assertIn("Hello Applicant Two", mail.outbox[0].body)
        self.assertEqual(len(mail.outbox[0].alternatives), 1)
        self.assertIn("<strong>Hello Applicant Two</strong>", mail.outbox[0].alternatives[0].content)

    def test_a1_success_sends_nothing_when_approval_email_is_disabled(self):
        form_def = FormDefinition.objects.create(
            slug="G991_E_A1",
            name="Configured entrepreneur application",
            is_public=True,
            accepting_responses=True,
            manual_open_override=True,
            approval_email_name="",
        )
        Question.objects.create(
            form=form_def,
            text="Name",
            slug="full_name",
            field_type=Question.SHORT_TEXT,
            position=1,
        )
        Question.objects.create(
            form=form_def,
            text="Email",
            slug="email",
            field_type=Question.SHORT_TEXT,
            position=2,
        )
        gate = Question.objects.create(
            form=form_def,
            text="Do you meet the requirements?",
            slug="meets_requirements",
            field_type=Question.CHOICE,
            position=3,
            end_form_rules=[{
                "value": "no",
                "email_name": "Rechazo E",
                "page_title": "Not eligible",
                "page_message": "The application ended.",
            }],
        )
        Choice.objects.create(question=gate, value="yes", label="Yes", position=1)
        Choice.objects.create(question=gate, value="no", label="No", position=2)
        StoredEmailTemplate.objects.create(
            form=form_def,
            name="Rechazo E",
            subject="Not eligible",
            body="Not eligible",
        )

        response = self.client.post(
            reverse("apply_by_slug", args=[form_def.slug]),
            {
                "q_full_name": "Successful Applicant",
                "q_email": "success@example.com",
                "q_meets_requirements": "yes",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

    def test_a1_rejection_rule_still_sends_its_selected_email(self):
        form_def = FormDefinition.objects.create(
            slug="G992_E_A1",
            name="Configured entrepreneur rejection",
            is_public=True,
            accepting_responses=True,
            manual_open_override=True,
            approval_email_name="",
        )
        Question.objects.create(
            form=form_def,
            text="Name",
            slug="full_name",
            field_type=Question.SHORT_TEXT,
            position=1,
        )
        Question.objects.create(
            form=form_def,
            text="Email",
            slug="email",
            field_type=Question.SHORT_TEXT,
            position=2,
        )
        gate = Question.objects.create(
            form=form_def,
            text="Do you meet the requirements?",
            slug="meets_requirements",
            field_type=Question.CHOICE,
            position=3,
            end_form_rules=[{
                "value": "no",
                "email_name": "Rechazo E",
                "page_title": "Not eligible",
                "page_message": "The application ended.",
            }],
        )
        Choice.objects.create(question=gate, value="yes", label="Yes", position=1)
        Choice.objects.create(question=gate, value="no", label="No", position=2)
        StoredEmailTemplate.objects.create(
            form=form_def,
            name="Rechazo E",
            subject="Not eligible",
            body="Not eligible",
        )

        response = self.client.post(
            reverse("apply_by_slug", args=[form_def.slug]),
            {
                "q_full_name": "Rejected Applicant",
                "q_email": "rejected@example.com",
                "q_meets_requirements": "no",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Not eligible")
        self.assertFalse(Application.objects.get(form=form_def).approved_for_grading)
        self.assertEqual([message.subject for message in mail.outbox], ["Not eligible"])

    def test_terminal_completion_does_not_send_default_approval_email(self):
        form_def = FormDefinition.objects.create(
            slug="terminal_overrides_approval",
            name="Terminal overrides approval",
            is_public=True,
            accepting_responses=True,
            manual_open_override=True,
            thanks_approved_message="Approved message",
            thanks_rejected_title="Shared rejection",
            thanks_rejected_message="This shared page is used for every rejection rule.",
            approval_email_name="Approval",
        )
        Question.objects.create(
            form=form_def,
            text="Name",
            slug="full_name",
            field_type=Question.SHORT_TEXT,
            position=1,
        )
        Question.objects.create(
            form=form_def,
            text="Email",
            slug="email",
            field_type=Question.SHORT_TEXT,
            position=2,
        )
        gate = Question.objects.create(
            form=form_def,
            text="Eligible?",
            slug="eligible",
            field_type=Question.CHOICE,
            position=3,
            end_form_rules=[{
                "value": "no",
                "email_name": "Rejection",
                "page_title": "Not eligible",
                "page_message": "The application ended.",
            }],
        )
        Choice.objects.create(question=gate, value="no", label="No", position=1)
        StoredEmailTemplate.objects.create(
            form=form_def, name="Approval", subject="Approved", body="Approved"
        )
        StoredEmailTemplate.objects.create(
            form=form_def, name="Rejection", subject="Not eligible", body="Not eligible"
        )

        response = self.client.post(
            reverse("apply_by_slug", args=[form_def.slug]),
            {
                "q_full_name": "Applicant Three",
                "q_email": "three@example.com",
                "q_eligible": "no",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Shared rejection")
        self.assertContains(response, "This shared page is used for every rejection rule.")
        self.assertNotContains(response, "The application ended.")
        self.assertNotContains(response, "Approved message")
        self.assertEqual([message.subject for message in mail.outbox], ["Not eligible"])


class CurrentEmprendedoraApplicationSchemaTests(TestCase):
    def test_command_updates_masters_and_empty_groups_but_preserves_history(self):
        master_a1 = FormDefinition.objects.create(slug="E_A1", name="Old A1", is_master=True)
        master_a2 = FormDefinition.objects.create(slug="E_A2", name="Old A2", is_master=True)
        empty_group = FormGroup.objects.create(
            number=991, start_day=3, start_month="julio", end_month="octubre", year=2026
        )
        empty_a1 = FormDefinition.objects.create(slug="G991_E_A1", name="Old", group=empty_group)
        empty_a2 = FormDefinition.objects.create(slug="G991_E_A2", name="Old", group=empty_group)
        historical_group = FormGroup.objects.create(
            number=992, start_day=1, start_month="enero", end_month="abril", year=2025
        )
        historical_a1 = FormDefinition.objects.create(slug="G992_E_A1", name="Historical", group=historical_group)
        FormDefinition.objects.create(slug="G992_E_A2", name="Historical", group=historical_group)
        sentinel = Question.objects.create(
            form=historical_a1,
            text="Historical question",
            slug="historical_question",
            field_type=Question.SHORT_TEXT,
        )
        Application.objects.create(form=historical_a1, name="Past", email="past@example.com")

        call_command("apply_emprendedora_application", stdout=StringIO())

        master_a1.refresh_from_db()
        self.assertEqual(master_a1.name, "Aplicación para emprendedoras")
        self.assertEqual(master_a1.sections.count() + master_a2.sections.count(), 7)
        self.assertTrue(master_a1.questions.get(slug="cedula").confirm_value)
        self.assertTrue(master_a1.questions.get(slug="email").confirm_value)
        self.assertTrue(master_a1.questions.get(slug="whatsapp").confirm_value)
        self.assertEqual(empty_a1.sections.count() + empty_a2.sections.count(), 7)
        empty_a1.refresh_from_db()
        self.assertIn("julio", empty_a1.description)
        self.assertTrue(Question.objects.filter(pk=sentinel.pk).exists())

    def test_docx_mentor_schema_uses_conditional_business_questions(self):
        m1 = FormDefinition.objects.create(slug="M_A1", name="Old M1", is_master=True)
        m2 = FormDefinition.objects.create(slug="M_A2", name="Old M2", is_master=True)

        apply_mentora_schema(m1, m2)

        self.assertEqual(m1.sections.count() + m2.sections.count(), 7)
        requirements = m1.questions.get(slug="meets_requirements")
        self.assertIn("mínimo de 2 horas", requirements.help_text)
        self.assertNotIn("reunión de lanzamiento", requirements.help_text)
        self.assertTrue(all(section.show_if_logic == "AND" for section in m2.sections.all()))
        self.assertTrue(all(len(section.show_if_conditions) == 2 for section in m2.sections.all()))
        controller = m2.questions.get(slug="owned_business")
        for slug in ("business_name", "industry", "business_description", "business_age", "has_employees"):
            question = m2.questions.get(slug=slug)
            self.assertEqual(question.show_if_question, controller)
            self.assertEqual(question.show_if_value, "yes")
        self.assertEqual(
            m2.questions.get(slug="professional_expertise").text,
            "¿Cuál es tu área de experiencia profesional más relevante para la mentoría de mujeres microempresarias? (Ej. Marketing, Finanzas, etc.)",
        )

    def test_docx_emprendedora_schema_keeps_exact_commented_questions(self):
        e1 = FormDefinition.objects.create(slug="E_A1", name="Old E1", is_master=True)
        e2 = FormDefinition.objects.create(slug="E_A2", name="Old E2", is_master=True)

        from applications.emprendedora_application_schema import apply_emprendedora_schema
        apply_emprendedora_schema(e1, e2)

        requirements = e1.questions.get(slug="meets_requirements")
        requirements_section = e1.sections.get(title="Confirmación de cumplimiento de requisitos")
        self.assertIn(
            "Estoy disponible el lunes #(day) de #(month) del #(year) para asistir a la reunión de lanzamiento",
            requirements_section.description,
        )
        self.assertIn("Hablo espanol.", requirements_section.description)
        self.assertEqual(requirements.help_text, "")
        self.assertTrue(all(len(section.show_if_conditions) == 2 for section in e2.sections.all()))
        self.assertFalse(e1.questions.filter(slug="business_active").exists())
        self.assertTrue(emprendedora_a1_passes({
            "meets_requirements": "yes",
            "available_period": "yes",
        }))
        self.assertTrue(e2.questions.filter(text="¿Tienes empleados?").exists())
        self.assertEqual(
            e2.questions.get(slug="community_contribution").text,
            "¿Qué crees que aportarás de manera única a la comunidad de emprendedoras si eres aceptada?",
        )


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

    @patch("applications.admin_views.sync_group_track_responses_csv")
    @patch("applications.admin_views.ensure_group_drive_tree")
    def test_create_group_clones_only_current_single_app_masters(
        self,
        mock_drive,
        mock_response_sync,
    ):
        FormDefinition.objects.create(slug="E_A2", name="Retired E A2", is_master=True)
        FormDefinition.objects.create(slug="M_A2", name="Retired M A2", is_master=True)
        user = get_user_model().objects.create_superuser(
            email="create-group@example.com",
            password="test-password",
        )
        self.client.force_login(user)
        mock_drive.return_value = SimpleNamespace(
            status="created",
            folder_name="G1 Mentorias - Junio a Agosto",
            detail="created",
        )
        mock_response_sync.return_value = SimpleNamespace(
            status="updated",
            detail="sheet synced",
        )

        response = self.client.post(
            reverse("admin_create_group"),
            {
                "group_name": "June Group",
                "start_day": "1",
                "start_month": "junio",
                "end_month": "agosto",
                "year": "2026",
            },
        )

        self.assertEqual(response.status_code, 302)
        group = FormGroup.objects.get(custom_name="June Group")
        slugs = set(FormDefinition.objects.filter(group=group).values_list("slug", flat=True))
        self.assertEqual(slugs, {"june_group_E_A1", "june_group_M_A1"})
        mock_drive.assert_called_once_with(
            group_num=group.number,
            start_month="junio",
            end_month="agosto",
            year=2026,
        )
        self.assertEqual(
            [call.args for call in mock_response_sync.call_args_list],
            [(group.number, "E"), (group.number, "M")],
        )

    @patch("applications.admin_views.ensure_group_drive_tree")
    def test_create_group_skips_drive_when_name_does_not_contain_group(self, mock_drive):
        user = get_user_model().objects.create_superuser(
            email="create-cohort@example.com",
            password="test-password",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("admin_create_group"),
            {
                "group_name": "Cohorte Junio",
                "start_day": "1",
                "start_month": "junio",
                "end_month": "agosto",
                "year": "2026",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(FormGroup.objects.filter(custom_name="Cohorte Junio").exists())
        mock_drive.assert_not_called()

    def test_response_rows_include_custom_group_name_form_slugs(self):
        group = FormGroup.objects.create(
            number=812,
            start_day=1,
            start_month="junio",
            end_month="agosto",
            year=2026,
            custom_name="June Group",
            use_combined_application=True,
        )
        form = FormDefinition.objects.create(
            slug="june_group_M_A1",
            name="June Group Mentoras",
            group=group,
            is_master=False,
        )
        question = Question.objects.create(
            form=form,
            text="Experience",
            slug="experience",
            field_type=Question.LONG_TEXT,
            position=1,
        )
        application = Application.objects.create(
            form=form,
            name="Mentora Example",
            email="mentor@example.com",
        )
        Answer.objects.create(
            application=application,
            question=question,
            value="Ten years",
        )

        headers, rows = drive_sync._build_group_track_rows(group.number, "M")

        self.assertIn("experience", headers)
        self.assertEqual(len(rows), 1)
        self.assertIn("mentor@example.com", rows[0])
        self.assertIn("Ten years", rows[0])

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

    def test_combined_group_exposes_one_application_per_track(self):
        group = FormGroup.objects.create(
            number=813,
            start_day=1,
            start_month="abril",
            end_month="julio",
            year=2026,
            use_combined_application=True,
        )
        forms = [
            FormDefinition.objects.create(
                slug=f"G813_{suffix}", name=suffix, group=group
            )
            for suffix in ("E_A1", "E_A2", "M_A1", "M_A2")
        ]

        entries = _combined_application_entries(forms)

        self.assertEqual(len(entries), 2)
        self.assertEqual(
            [entry.combined_display_name for entry in entries],
            ["Aplicación para emprendedoras", "Aplicación para mentoras"],
        )
        self.assertIsNone(entries[0].companion_form)
        self.assertIsNone(entries[1].companion_form)


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

    def test_mentora_a1_individual_requirements_are_eligible(self):
        answers = {
            "req_basic_woman": "yes",
            "req_basic_latam": "yes",
            "req_basic_business_exp": "yes",
            "req_basic_punctual": "yes",
            "req_basic_internet_device": "yes",
            "req_basic_training": "yes",
            "req_basic_surveys": "yes",
            "req_avail_period": "yes",
            "req_avail_2hrs_week": "yes",
            "req_avail_kickoff": "yes",
        }

        self.assertTrue(_mentor_a1_is_eligible(answers))
        answers["req_avail_2hrs_week"] = "no"
        self.assertFalse(_mentor_a1_is_eligible(answers))

    def test_emprendedora_a1_individual_requirements_are_eligible(self):
        answers = {
            "req_basic_woman": "yes",
            "req_basic_latam": "yes",
            "req_basic_business_active": "yes",
            "req_basic_internet_device": "yes",
            "req_avail_period": "yes",
            "req_avail_3hrs_week": "yes",
        }

        self.assertTrue(emprendedora_a1_passes(answers))
        answers["req_basic_business_active"] = "no"
        self.assertFalse(emprendedora_a1_passes(answers))

    def test_a1_unknown_question_layout_does_not_send_false_rejection(self):
        answers = {"full_name": "Configuración nueva", "email": "test@example.com"}

        self.assertTrue(_mentor_a1_is_eligible(answers))
        self.assertTrue(emprendedora_a1_passes(answers))

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
        cache.clear()
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
        self.non_program_group = FormGroup.objects.create(
            number=984,
            start_day=1,
            start_month="julio",
            end_month="agosto",
            year=2025,
            custom_name="Pilot Cohort",
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
        self.non_program_form = FormDefinition.objects.create(
            slug="G984_E_A1",
            name="G984 E A1",
            group=self.non_program_group,
        )

        Application.objects.create(form=self.e_a1, name="Founder", email="founder@example.com")
        Application.objects.create(form=self.e_a2, name="Founder Repeat", email="founder@example.com")
        Application.objects.create(form=self.e_a1, name="No Start", email="no-start@example.com")
        Application.objects.create(form=self.m_g1, name="Mentor", email="mentor@example.com")
        Application.objects.create(form=self.m_g1, name="Repeated Mentor", email="repeat@example.com")
        Application.objects.create(form=self.m_g2, name="Founder Mentor", email="founder@example.com")
        Application.objects.create(form=self.non_program_form, name="Pilot", email="pilot@example.com")

        GroupParticipantList.objects.create(
            group=self.group1,
            google_sheet_url="https://docs.google.com/spreadsheets/d/group981/edit",
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
            google_sheet_url="https://docs.google.com/spreadsheets/d/group982/edit",
            mentoras_sheet_rows=[
                ["", "A", 1, "Founder Mentor", "M3", "founder@example.com", "", "Colombia", "", True, True, True, True, False],
                ["", "CP", 2, "Repeated Mentor", "M2", "repeat@example.com", "", "Peru", "", True, False, True, False, False],
            ],
        )
        GroupParticipantList.objects.create(
            group=self.non_program_group,
            google_sheet_url="https://docs.google.com/spreadsheets/d/group984/edit",
            emprendedoras_sheet_rows=[
                ["", "G", 1, "Pilot", "P1", "pilot@example.com", "", "Colombia", "", True, True, True, True, True],
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

    @patch("applications.admin_dashboard_views._load_database_encuestas_grid")
    def test_impact_dataset_reuses_cached_drive_grid(self, mock_load_grid):
        mock_load_grid.return_value = (
            "Initial survey",
            ["Email", "Quality of life"],
            [["person@example.com", "8"]],
            "survey.csv",
            "drive-id",
        )

        first, _emails = admin_dashboard_views._build_impact_dataset(
            "emprendedoras",
            "Initial survey",
            "admin_database_encuestas_sheet",
        )
        second, _emails = admin_dashboard_views._build_impact_dataset(
            "emprendedoras",
            "Initial survey",
            "admin_database_encuestas_sheet",
        )

        self.assertEqual(first["responses_count"], 1)
        self.assertEqual(second["responses_count"], 1)
        mock_load_grid.assert_called_once_with("emprendedoras")

    @patch("applications.admin_dashboard_views._load_database_encuestas_grid")
    def test_impact_dataset_uses_stale_cache_when_drive_fails(self, mock_load_grid):
        mock_load_grid.return_value = (
            "Initial survey",
            ["Email"],
            [["person@example.com"]],
            "survey.csv",
            "drive-id",
        )
        admin_dashboard_views._build_impact_dataset(
            "emprendedoras",
            "Initial survey",
            "admin_database_encuestas_sheet",
        )
        cache.delete("admin:impact:grid:emprendedoras:v1")
        mock_load_grid.side_effect = RuntimeError("Drive timed out")

        dataset, _emails = admin_dashboard_views._build_impact_dataset(
            "emprendedoras",
            "Initial survey",
            "admin_database_encuestas_sheet",
        )

        self.assertTrue(dataset["stale"])
        self.assertEqual(dataset["responses_count"], 1)

    def test_participant_aggregates_only_use_linked_group_sheets(self):
        unlinked_group = FormGroup.objects.create(
            number=985,
            custom_name="Group 985",
            start_day=1,
            start_month="enero",
            end_month="marzo",
            year=2026,
        )
        GroupParticipantList.objects.create(
            group=unlinked_group,
            mentoras_emails_text="unlinked@example.com",
            mentoras_sheet_rows=[
                ["", "A", 1, "Unlinked", "U1", "unlinked@example.com"]
            ],
        )

        records = admin_dashboard_views._participant_records()
        profile_email_keys = admin_profiles_views._participant_list_email_keys()

        self.assertNotIn("unlinked@example.com", {row["email"] for row in records})
        self.assertNotIn("unlinked@example.com", profile_email_keys)
        self.assertIn("founder@example.com", profile_email_keys)

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
            google_sheet_url="https://docs.google.com/spreadsheets/d/group983/edit",
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
        self.assertEqual(participant_summary["overall"]["graduation_started"], 3)
        self.assertEqual(participant_summary["overall"]["graduation_graduated"], 2)
        self.assertEqual(participant_summary["overall"]["graduation_completed_groups"], 1)
        self.assertEqual(participant_summary["overall"]["graduation_rate"], 66.7)
        self.assertEqual(participant_summary["tracks"]["e"]["started"], 1)
        self.assertEqual(participant_summary["tracks"]["e"]["graduation_rate"], 100.0)
        self.assertEqual(participant_summary["tracks"]["m"]["graduated"], 1)
        self.assertEqual(participant_summary["tracks"]["m"]["graduation_started"], 2)
        self.assertEqual(participant_summary["tracks"]["m"]["graduation_rate"], 50.0)

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

    def test_impact_dashboard_scope_filters_group_year_and_track(self):
        records = admin_dashboard_views._participant_records()

        filtered = admin_dashboard_views._filter_records_by_impact_scope(
            records,
            group_numbers={981},
            year=2026,
            track_filter="e",
        )

        self.assertEqual(len(filtered), 2)
        self.assertTrue(all(record["group_number"] == 981 for record in filtered))
        self.assertTrue(all(record["group_year"] == 2026 for record in filtered))
        self.assertTrue(all(record["track"] == "e" for record in filtered))

        app_summary = admin_dashboard_views._application_summary({981}, track_filter="e")
        self.assertEqual(app_summary["overall"]["raw"], 3)
        self.assertEqual(app_summary["tracks"][0]["track"], "Emprendedoras")
        self.assertEqual(app_summary["tracks"][0]["raw"], 3)
        self.assertEqual(app_summary["tracks"][1]["track"], "Mentoras")
        self.assertEqual(app_summary["tracks"][1]["raw"], 0)

    def test_impact_dashboard_only_includes_group_labeled_groups(self):
        records = admin_dashboard_views._participant_records()
        self.assertNotIn("pilot@example.com", {record["email"] for record in records})

        group_options = admin_dashboard_views._impact_group_options()
        self.assertIn(self.group1.number, {option["number"] for option in group_options})
        self.assertNotIn(self.non_program_group.number, {option["number"] for option in group_options})
        self.assertNotIn(2025, admin_dashboard_views._impact_year_options())
        self.assertEqual(
            admin_dashboard_views._impact_allowed_group_filter({self.non_program_group.number}),
            set(),
        )

        application_summary = admin_dashboard_views._application_summary()
        self.assertEqual(application_summary["overall"]["raw"], 6)

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

    def test_quality_of_life_summary_splits_initial_final_and_change(self):
        summary = admin_dashboard_views._wellbeing_comparison_summary(
            [
                {"label": "Life", "responses": 2, "avg": 4.0},
                {"label": "Life", "responses": 2, "avg": 3.0},
            ],
            [
                {"label": "Life", "responses": 3, "avg": 4.5},
            ],
        )

        self.assertEqual(summary["initial"]["avg"], 3.5)
        self.assertEqual(summary["initial"]["responses"], 4)
        self.assertEqual(summary["final"]["avg"], 4.5)
        self.assertEqual(summary["final"]["responses"], 3)
        self.assertEqual(summary["change"], 1.0)
        self.assertEqual(
            [row["label"] for row in summary["chart_data"]],
            ["Initial", "Final completed groups"],
        )

    def test_survey_response_rate_uses_survey_email_matches_not_workbook_checks(self):
        records = [
            {
                "track": "e",
                "email": "founder@example.com",
                "group_number": 1,
                "graduated": True,
                "initial_survey": False,
                "final_survey": False,
            },
            {
                "track": "e",
                "email": "active-founder@example.com",
                "group_number": 2,
                "graduated": False,
                "initial_survey": False,
                "final_survey": False,
            },
            {
                "track": "m",
                "email": "mentor@example.com",
                "group_number": 1,
                "graduated": False,
                "initial_survey": False,
                "final_survey": False,
            },
        ]
        rows, summary = admin_dashboard_views._survey_response_rate_data(
            records,
            {
                "emprendedoras": {"founder@example.com", "active-founder@example.com"},
                "mentoras": {"mentor@example.com"},
                "emprendedoras_final": {"founder@example.com", "active-founder@example.com"},
                "mentoras_final": {"mentor@example.com"},
            },
        )

        self.assertEqual(summary["initial_rate"], 100.0)
        self.assertEqual(summary["initial_responses"], 3)
        self.assertEqual(summary["initial_eligible"], 3)
        self.assertEqual(summary["final_rate"], 100.0)
        self.assertEqual(summary["final_responses"], 2)
        self.assertEqual(summary["final_eligible"], 2)
        final_all = next(row for row in rows if row["label"] == "All: final check-in")
        self.assertEqual(final_all["eligible"], 2)
        self.assertEqual(final_all["responses"], 2)

    @patch("applications.admin_dashboard_views._load_impact_survey_datasets")
    def test_final_quality_of_life_rows_use_completed_group_email_scope(self, mock_load):
        completed_emails = {"founder@example.com", "mentor@example.com"}
        mock_load.return_value = (
            {
                "emprendedoras": {
                    "wellbeing_rows": [{"label": "Initial ignored", "responses": 9, "avg": 2.0}]
                },
                "emprendedoras_final": {
                    "title": "Final E",
                    "wellbeing_rows": [{"label": "Final E Life", "responses": 1, "avg": 5.0}],
                },
                "mentoras_final": {
                    "title": "Final M",
                    "wellbeing_rows": [{"label": "Final M Life", "responses": 1, "avg": 4.0}],
                },
            },
            {},
        )

        rows = admin_dashboard_views._final_completed_wellbeing_rows(
            top_n=10,
            completed_emails=completed_emails,
        )

        mock_load.assert_called_once_with(
            top_n=10,
            scoped_emails=completed_emails,
            request=None,
        )
        self.assertEqual([row["dataset"] for row in rows], ["Final E", "Final M"])
        self.assertEqual([row["avg"] for row in rows], [5.0, 4.0])

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


class MarketingDashboardTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.staff_user = user_model.objects.create_superuser(
            email="marketing-admin@example.com",
            password="testpass123",
        )
        self.client.force_login(self.staff_user)

    def test_meta_ad_summary_helpers(self):
        rows = [
            {
                "campaign_name": "Campaign A",
                "spend": "10.50",
                "impressions": "1000",
                "reach": "800",
                "clicks": "25",
            },
            {
                "campaign_name": "Campaign A",
                "spend": "4.50",
                "impressions": "500",
                "reach": "300",
                "clicks": "5",
            },
        ]

        summary = meta_marketing.summarize_ad_insights(rows)
        campaign_rows = meta_marketing.campaign_rows(rows)

        self.assertEqual(summary["spend"], 15.0)
        self.assertEqual(summary["impressions"], 1500)
        self.assertEqual(summary["clicks"], 30)
        self.assertEqual(summary["ctr"], 2.0)
        self.assertEqual(campaign_rows[0]["name"], "Campaign A")
        self.assertEqual(campaign_rows[0]["spend"], 15.0)

    def test_zernio_account_resolution_and_campaign_normalization(self):
        config = meta_marketing.ZernioMarketingConfig(api_key="test-key")
        client = meta_marketing.ZernioMarketingClient(config)
        client.accounts = Mock(
            return_value=[
                {"id": "fb_1", "platform": "facebook"},
                {"id": "ads_1", "platform": "metaads"},
            ]
        )
        client._get = Mock(
            return_value={
                "data": [
                    {
                        "name": "Zernio Campaign",
                        "metrics": {
                            "spend": "30",
                            "impressions": "3000",
                            "reach": "2100",
                            "clicks": "60",
                        },
                    }
                ]
            }
        )

        rows = client.ad_insights(
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
        )

        self.assertEqual(rows[0]["campaign_name"], "Zernio Campaign")
        self.assertEqual(rows[0]["spend"], "30")
        client._get.assert_called_once()
        self.assertEqual(client._get.call_args.args[0], "ads/campaigns")
        self.assertEqual(client._get.call_args.args[1]["accountId"], "ads_1")
        self.assertEqual(client._get.call_args.args[1]["source"], "all")
        self.assertEqual(client._get.call_args.args[1]["fromDate"], "2026-01-01")
        self.assertEqual(client._get.call_args.args[1]["toDate"], "2026-01-31")

    def test_zernio_campaign_reporting_follows_pagination(self):
        config = meta_marketing.ZernioMarketingConfig(api_key="test-key", account_id="fb_1")
        client = meta_marketing.ZernioMarketingClient(config)
        first_page = [
            {"name": f"Campaign {idx}", "metrics": {"spend": 1}}
            for idx in range(100)
        ]
        client._get = Mock(side_effect=[
            {"data": first_page, "pagination": {"hasNextPage": True}},
            {
                "data": [{"name": "Last Campaign", "metrics": {"spend": 2}}],
                "pagination": {"hasNextPage": False},
            },
        ])

        rows = client.ad_insights(
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
        )

        self.assertEqual(len(rows), 101)
        self.assertEqual(rows[-1]["campaign_name"], "Last Campaign")
        self.assertEqual(client._get.call_args_list[1].args[1]["page"], 2)

    def test_zernio_extracts_nested_campaign_tree(self):
        rows = meta_marketing._extract_zernio_campaign_nodes(
            {
                "tree": {
                    "adAccounts": [
                        {
                            "campaigns": [
                                {
                                    "name": "Nested Campaign",
                                    "metrics": {"spend": "12"},
                                }
                            ]
                        }
                    ]
                }
            }
        )

        self.assertEqual(rows[0]["name"], "Nested Campaign")

    def test_zernio_account_analytics_summary(self):
        config = meta_marketing.ZernioMarketingConfig(api_key="test-key")
        client = meta_marketing.ZernioMarketingClient(config)
        client._get = Mock(
            side_effect=[
                {
                    "metrics": {
                        "page_media_view": {"total": 1000},
                        "page_post_engagements": {"total": 80},
                        "followers_gained": {"total": 12},
                    }
                },
                {
                    "metrics": {
                        "reach": {"total": 500},
                        "views": {"total": 900},
                        "total_interactions": {"total": 45},
                        "profile_links_taps": {"total": 7},
                    }
                },
            ]
        )

        summary = client.account_analytics(
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            accounts=[
                {"_id": "fb_1", "platform": "facebook", "name": "Facebook Page"},
                {"_id": "ig_1", "platform": "instagram", "name": "Instagram"},
            ],
        )

        self.assertEqual(summary["account_count"], 2)
        self.assertEqual(summary["media_views"], 1000)
        self.assertEqual(summary["reach"], 500)
        self.assertEqual(summary["views"], 900)
        self.assertEqual(summary["engagements"], 125)
        self.assertEqual(summary["followers_gained"], 12)
        self.assertEqual(summary["clicks"], 7)

    def test_zernio_posting_analytics_matches_dashboard_metrics(self):
        summary = meta_marketing.summarize_zernio_posting_analytics(
            daily_data={
                "dailyData": [
                    {
                        "date": "2026-06-02",
                        "postCount": 2,
                        "metrics": {"reach": 1000, "likes": 9, "comments": 1, "shares": 0, "saves": 0},
                    },
                    {
                        "date": "2026-06-09",
                        "postCount": 3,
                        "metrics": {"reach": 2000, "likes": 21, "comments": 2, "shares": 1, "saves": 1},
                    },
                ],
                "platformBreakdown": [
                    {"platform": "facebook", "postCount": 5, "reach": 3000, "likes": 30},
                ],
            },
            follower_data={"accounts": [{"currentFollowers": 582, "growth": 79}]},
            best_post_data={
                "overview": {"engagementRate": 0.9, "totalReach": 4800, "totalPosts": 11},
                "posts": [{
                    "content": "Best post",
                    "platformPostUrl": "https://example.com/post",
                    "analytics": {"likes": 7, "comments": 2},
                }],
            },
        )

        self.assertEqual(summary["post_count"], 11)
        self.assertEqual(summary["reach"], 4800)
        self.assertEqual(summary["followers"], 582)
        self.assertEqual(summary["follower_growth"], 79)
        self.assertEqual(summary["engagement_rate"], 0.9)
        self.assertEqual(summary["best_post"]["engagements"], 9)
        self.assertEqual(summary["platforms"][0]["post_width"], 100.0)

    @patch.dict(
        "os.environ",
        {
            "META_ACCESS_TOKEN": "test-token",
            "META_AD_ACCOUNT_ID": "123456",
            "META_INSTAGRAM_BUSINESS_ACCOUNT_ID": "987654",
        },
    )
    @patch("applications.admin_dashboard_views.MetaMarketingClient")
    def test_marketing_dashboard_renders_with_mocked_meta_data(self, mock_client_cls):
        mock_client = mock_client_cls.return_value
        mock_client.ad_insights.return_value = [
            {
                "campaign_name": "Campaign A",
                "spend": "20",
                "impressions": "2000",
                "reach": "1500",
                "clicks": "40",
            }
        ]
        mock_client.instagram_user_insights.return_value = [
            {"name": "reach", "values": [{"value": 100}]},
            {"name": "profile_views", "values": [{"value": 12}]},
        ]

        response = self.client.get(reverse("admin_marketing_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Marketing Dashboard")
        self.assertContains(response, "Campaign A")
        self.assertContains(response, "$20.0")
        mock_client.ad_insights.assert_called_once()

    def test_marketing_dashboard_shows_setup_message_without_env(self):
        response = self.client.get(reverse("admin_marketing_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ZERNIO_API_KEY")

    @patch.dict("os.environ", {"ZERNIO_API_KEY": "test-key"}, clear=True)
    @patch("applications.admin_dashboard_views.ZernioMarketingClient")
    def test_marketing_dashboard_prefers_zernio_when_configured(self, mock_client_cls):
        mock_client = mock_client_cls.return_value
        mock_client.accounts.return_value = []
        mock_client.posting_analytics.return_value = {
            "engagement_rate": 0.9,
            "reach": 4800,
            "followers": 582,
            "follower_growth": 79,
            "post_count": 11,
            "platforms": [],
            "daily": [],
            "best_post": {},
        }

        response = self.client.get(reverse("admin_marketing_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Posting performance from Zernio Analytics")
        self.assertContains(response, "4800")
        self.assertContains(response, "582")
        self.assertContains(response, "11")
        mock_client.posting_analytics.assert_called_once()
        mock_client.ad_insights.assert_not_called()


class WixCapacitacionPayloadTests(TestCase):
    def test_extracts_nested_contact_email_from_completed_enrollment(self):
        payload = {
            "enrollments": [
                {
                    "status": "COMPLETED",
                    "member": {"contactDetails": {"email": "nested@example.com"}},
                },
                {
                    "status": "IN_PROGRESS",
                    "member": {"contactDetails": {"email": "pending@example.com"}},
                },
            ]
        }

        completed = admin_profiles_views._extract_completed_emails_from_wix_payload(payload)

        self.assertEqual(completed, {"nested@example.com"})

    def test_recognizes_percentage_and_completed_step_shapes(self):
        payload = {
            "participants": [
                {"email": "percent@example.com", "completionPercentage": 100},
                {"email": "steps@example.com", "completedSteps": 8, "totalSteps": 8},
                {"email": "partial@example.com", "completedSteps": 7, "totalSteps": 8},
            ]
        }

        completed = admin_profiles_views._extract_completed_emails_from_wix_payload(payload)

        self.assertEqual(completed, {"percent@example.com", "steps@example.com"})

    def test_completed_aggregate_count_does_not_mark_every_nested_email(self):
        payload = {
            "completed": 2,
            "participants": [
                {"email": "one@example.com"},
                {"email": "two@example.com"},
            ],
        }

        completed = admin_profiles_views._extract_completed_emails_from_wix_payload(payload)

        self.assertEqual(completed, set())

    def test_extracts_explicit_completed_members_list(self):
        payload = {
            "completedMembers": [
                {"contact": {"primaryEmail": "member@example.com"}},
            ]
        }

        completed = admin_profiles_views._extract_completed_emails_from_wix_payload(payload)

        self.assertEqual(completed, {"member@example.com"})


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
        self.assertContains(response, "assembled only from each group's linked Google Sheet")
        self.assertNotContains(response, reverse("admin_profiles_participants_google_sheet"))
        self.assertNotContains(response, "11TXU8gmcTPqfoEhc4JKIaRuOjhKnVLTnhAJfDR2Lxr8")
        self.assertNotContains(response, "Open Mentoras sheet")
        self.assertNotContains(response, "Open Emprendedoras sheet")

    @patch("applications.admin_profiles_views.fetch_drive_csv_file_text")
    def test_participants_page_uses_saved_data_until_manual_refresh(self, mock_fetch):
        response = self.client.get(reverse("admin_profiles_participants"))

        self.assertEqual(response.status_code, 200)
        mock_fetch.assert_not_called()
        self.assertFalse(FormGroup.objects.filter(number=994).exists())

    @patch("applications.admin_profiles_views.fetch_drive_csv_file_text")
    def test_retired_global_participant_sheet_view_redirects_without_fetching(self, mock_fetch):
        response = self.client.get(reverse("admin_profiles_participants_google_sheet"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("admin_profiles_participants"))
        mock_fetch.assert_not_called()

    @patch("applications.admin_profiles_views.fetch_drive_csv_file_text")
    def test_retired_global_participant_sync_action_does_not_import(self, mock_fetch):
        response = self.client.post(
            reverse("admin_profiles_participants"),
            data={"action": "sync_from_google_sheet"},
        )

        self.assertEqual(response.status_code, 302)
        mock_fetch.assert_not_called()
        self.assertFalse(FormGroup.objects.filter(number=994).exists())

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

    @patch("applications.admin_profiles_views._fetch_encuestas_emails_for_group")
    def test_check_encuesta_uses_posted_current_sheet_before_marking(self, mock_fetch):
        mock_fetch.return_value = (
            True,
            {"new@example.com"},
            "Encuesta inicial source scanned.",
        )
        posted_rows = [
            ["", "CP", 1, "Unsaved Current", "M9", "new@example.com", "", "", "", False, False, False, False, False, False, False],
        ]

        response = self.client.post(
            reverse(
                "admin_profiles_participants_track_sheet",
                args=[self.group.number, "mentoras"],
            ),
            data={
                "action": "check_encuestas",
                "sheet_data": json.dumps(posted_rows),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.participant_list.refresh_from_db()
        self.assertEqual(self.participant_list.mentoras_sheet_rows[0][3], "Unsaved Current")
        self.assertTrue(self.participant_list.mentoras_sheet_rows[0][12])
        self.assertTrue(
            ParticipantSheetVersion.objects.filter(
                group=self.group,
                track="mentoras",
                action="pre_check_save",
            ).exists()
        )
        self.assertTrue(
            ParticipantSheetVersion.objects.filter(
                group=self.group,
                track="mentoras",
                action="check_encuesta_inicial",
            ).exists()
        )

    @patch("applications.admin_profiles_views._fetch_encuestas_emails_for_group")
    def test_combined_check_encuesta_uses_posted_current_tabs(self, mock_fetch):
        mock_fetch.return_value = (
            True,
            {"m-new@example.com", "e-new@example.com"},
            "Encuesta final source scanned.",
        )
        mentoras_rows = [
            ["", "CP", 1, "Mentora Current", "M9", "m-new@example.com", "", "", "", False, False, False, False, False, False, False],
        ]
        emprendedoras_rows = [
            ["", "CP", 1, "Emprendedora Current", "E9", "e-new@example.com", "", "", "", False, False, False, False, False, False, False],
        ]

        response = self.client.post(
            reverse(
                "admin_profiles_participants_track_sheet",
                args=[self.group.number, "all"],
            ),
            data={
                "action": "check_encuestas_final",
                "mentoras_sheet_data": json.dumps(mentoras_rows),
                "emprendedoras_sheet_data": json.dumps(emprendedoras_rows),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.participant_list.refresh_from_db()
        self.assertEqual(self.participant_list.mentoras_sheet_rows[0][3], "Mentora Current")
        self.assertEqual(
            self.participant_list.emprendedoras_sheet_rows[0][3],
            "Emprendedora Current",
        )
        self.assertTrue(self.participant_list.mentoras_sheet_rows[0][13])
        self.assertTrue(self.participant_list.emprendedoras_sheet_rows[0][13])

    def _linked_google_workbook_payload(self):
        mentora_row = [
            "Google info",
            "CP",
            99,
            "Mentora from Google",
            "M1",
            "m1@example.com",
            "+57",
            "Colombia",
            "30",
            True,
            False,
            True,
            True,
            True,
            True,
            True,
            True,
            True,
        ]
        emprendedora_row = [
            "Google info E",
            "CP",
            42,
            "Emprendedora from Google",
            "E1",
            "e1@example.com",
            "+57",
            "Colombia",
            "31",
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            True,
        ]
        return {
            "spreadsheet_id": "linked-sheet-123",
            "title": "Group 993 Participants",
            "tabs": [
                {
                    "title": "Mentoras",
                    "sheet_id": 1,
                    "values": [admin_profiles_views.MENTORAS_HEADERS, mentora_row],
                },
                {
                    "title": "Emprendedoras",
                    "sheet_id": 2,
                    "values": [admin_profiles_views.EMPRENDEDORAS_HEADERS, emprendedora_row],
                },
                {
                    "title": "Notes",
                    "sheet_id": 3,
                    "values": [["Custom heading", "Owner"], ["Mirrored extra tab", "Admin"]],
                },
            ],
        }

    @patch("applications.admin_profiles_views.update_google_spreadsheet_values")
    @patch("applications.admin_profiles_views.fetch_google_spreadsheet_tabs")
    def test_group_can_link_multitab_google_sheet_and_preserve_website_checkboxes(
        self,
        mock_fetch_tabs,
        mock_update_values,
    ):
        self.participant_list.mentoras_sheet_rows[0][10] = True
        self.participant_list.save(update_fields=["mentoras_sheet_rows", "updated_at"])
        mock_fetch_tabs.return_value = self._linked_google_workbook_payload()
        mock_update_values.return_value = 17
        sheet_url = "https://docs.google.com/spreadsheets/d/linked-sheet-123/edit"

        response = self.client.post(
            reverse("admin_profiles_participants"),
            data={
                "action": "save_google_sheet_link",
                "group": str(self.group.number),
                "google_sheet_url": sheet_url,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.participant_list.refresh_from_db()
        self.assertEqual(self.participant_list.google_sheet_url, sheet_url)
        self.assertEqual(self.participant_list.google_sheet_id, "linked-sheet-123")
        self.assertEqual(len(self.participant_list.google_sheet_tabs), 3)
        self.assertEqual(self.participant_list.mentoras_sheet_rows[0][3], "Mentora from Google")
        self.assertTrue(self.participant_list.mentoras_sheet_rows[0][10])
        self.assertFalse(self.participant_list.mentoras_sheet_rows[0][11])
        self.assertFalse(self.participant_list.mentoras_sheet_rows[0][12])
        self.assertTrue(mock_update_values.called)
        written_ranges = {
            item["range"]
            for item in mock_update_values.call_args.args[1]
        }
        self.assertIn("'Mentoras'!J2:J2", written_ranges)
        self.assertIn("'Mentoras'!R2:R2", written_ranges)

        response = self.client.get(
            reverse(
                "admin_profiles_participants_track_sheet",
                args=[self.group.number, "all"],
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Notes")
        self.assertContains(response, "Mirrored extra tab")
        self.assertContains(response, "Google Sheet linked · website is read-only")
        self.assertContains(response, "const sheetReadonly = true")

    @patch("applications.admin_profiles_views.update_google_spreadsheet_values")
    @patch("applications.admin_profiles_views.fetch_google_spreadsheet_tabs")
    def test_group_link_only_imports_tabs_matching_selected_group(
        self,
        mock_fetch_tabs,
        mock_update_values,
    ):
        payload = self._linked_google_workbook_payload()
        payload["tabs"][0]["title"] = "G993 M"
        payload["tabs"][1]["title"] = "G993 E"
        payload["tabs"][2]["title"] = "G993 Emparejamiento"

        other_mentoras = json.loads(json.dumps(payload["tabs"][0]))
        other_mentoras["title"] = "G994 M"
        other_mentoras["sheet_id"] = 4
        other_mentoras["values"][1][3] = "Wrong-group Mentora"
        other_mentoras["values"][1][5] = "wrong-m@example.com"
        other_emprendedoras = json.loads(json.dumps(payload["tabs"][1]))
        other_emprendedoras["title"] = "G994 E"
        other_emprendedoras["sheet_id"] = 5
        other_emprendedoras["values"][1][3] = "Wrong-group Emprendedora"
        other_emprendedoras["values"][1][5] = "wrong-e@example.com"
        other_pairing = {
            "title": "G994 Emparejamiento",
            "sheet_id": 6,
            "values": [["Mentora", "Emprendedora"], ["Wrong", "Group"]],
        }
        payload["tabs"].extend([other_mentoras, other_emprendedoras, other_pairing])
        mock_fetch_tabs.return_value = payload
        mock_update_values.return_value = 17

        response = self.client.post(
            reverse("admin_profiles_participants"),
            data={
                "action": "save_google_sheet_link",
                "group": str(self.group.number),
                "google_sheet_url": "https://docs.google.com/spreadsheets/d/multi-group/edit",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.participant_list.refresh_from_db()
        self.assertEqual(
            [tab["title"] for tab in self.participant_list.google_sheet_tabs],
            ["G993 M", "G993 E", "G993 Emparejamiento"],
        )
        self.assertEqual(self.participant_list.mentoras_sheet_rows[0][5], "m1@example.com")
        self.assertEqual(self.participant_list.emprendedoras_sheet_rows[0][5], "e1@example.com")
        imported_blob = json.dumps(self.participant_list.google_sheet_tabs)
        self.assertNotIn("wrong-m@example.com", imported_blob)
        self.assertNotIn("wrong-e@example.com", imported_blob)
        written_ranges = {
            item["range"]
            for item in mock_update_values.call_args.args[1]
        }
        self.assertTrue(written_ranges)
        self.assertTrue(all("G993" in cell_range for cell_range in written_ranges))
        self.assertFalse(any("G994" in cell_range for cell_range in written_ranges))

    @patch("applications.admin_profiles_views.update_google_spreadsheet_values")
    @patch("applications.admin_profiles_views.fetch_google_spreadsheet_tabs")
    def test_link_uses_google_checkbox_validation_when_headers_are_custom(
        self,
        mock_fetch_tabs,
        mock_update_values,
    ):
        payload = self._linked_google_workbook_payload()
        mentoras_tab = payload["tabs"][0]
        headers = list(mentoras_tab["values"][0])
        checkbox_indexes = list(admin_profiles_views.MENTORAS_BOOLEAN_COLS)
        for position, column_index in enumerate(checkbox_indexes, start=1):
            headers[column_index] = f"Custom checkbox {position}"
        mentoras_tab["values"][0] = headers
        mentoras_tab["checkbox_column_indexes"] = checkbox_indexes
        mock_fetch_tabs.return_value = payload
        mock_update_values.return_value = len(checkbox_indexes)

        response = self.client.post(
            reverse("admin_profiles_participants"),
            data={
                "action": "save_google_sheet_link",
                "group": str(self.group.number),
                "google_sheet_url": "https://docs.google.com/spreadsheets/d/linked-sheet-123/edit",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.participant_list.refresh_from_db()
        stored_mentoras_tab = self.participant_list.google_sheet_tabs[0]
        self.assertEqual(
            [item["index"] for item in stored_mentoras_tab["checkbox_columns"]],
            checkbox_indexes,
        )

    @patch("applications.admin_profiles_views.ensure_google_spreadsheet_checkbox_columns")
    @patch("applications.admin_profiles_views.update_google_spreadsheet_values")
    @patch("applications.admin_profiles_views.fetch_google_spreadsheet_tabs")
    def test_link_adds_missing_google_checkbox_columns_instead_of_rejecting(
        self,
        mock_fetch_tabs,
        mock_update_values,
        mock_ensure_columns,
    ):
        payload = self._linked_google_workbook_payload()
        mentoras_tab = payload["tabs"][0]
        headers = list(mentoras_tab["values"][0])
        missing_indexes = [10, 12, 14, 15]
        for column_index in missing_indexes:
            headers[column_index] = f"Ordinary source column {column_index}"
        mentoras_tab["values"][0] = headers
        mentoras_tab["column_count"] = len(headers)
        mock_fetch_tabs.return_value = payload
        mock_update_values.return_value = 20
        mock_ensure_columns.return_value = len(missing_indexes)

        response = self.client.post(
            reverse("admin_profiles_participants"),
            data={
                "action": "save_google_sheet_link",
                "group": str(self.group.number),
                "google_sheet_url": "https://docs.google.com/spreadsheets/d/linked-sheet-123/edit",
            },
        )

        self.assertEqual(response.status_code, 302)
        requested_headers = {
            item["header"] for item in mock_ensure_columns.call_args.args[1]
        }
        self.assertEqual(
            requested_headers,
            {"Website", "Encuesta inicial", "Plazo extra", "Lanzamiento"},
        )

    @patch("applications.admin_profiles_views.ensure_google_spreadsheet_checkbox_columns")
    @patch("applications.admin_profiles_views.delete_google_spreadsheet_columns")
    @patch("applications.admin_profiles_views.update_google_spreadsheet_values")
    @patch("applications.admin_profiles_views.fetch_google_spreadsheet_tabs")
    def test_link_recognizes_enuesta_inicial_and_removes_appended_duplicate(
        self,
        mock_fetch_tabs,
        mock_update_values,
        mock_delete_columns,
        mock_ensure_columns,
    ):
        payload = self._linked_google_workbook_payload()
        mentoras_tab = payload["tabs"][0]
        headers = list(mentoras_tab["values"][0])
        headers[12] = "Enuesta inicial"
        headers.append("Encuesta inicial")
        mentoras_tab["values"][0] = headers
        mentoras_tab["values"][1].append(False)
        mentoras_tab["column_count"] = len(headers)
        mock_fetch_tabs.return_value = payload
        mock_update_values.return_value = 17
        mock_delete_columns.return_value = 1
        mock_ensure_columns.return_value = 0

        response = self.client.post(
            reverse("admin_profiles_participants"),
            data={
                "action": "save_google_sheet_link",
                "group": str(self.group.number),
                "google_sheet_url": "https://docs.google.com/spreadsheets/d/linked-sheet-123/edit",
            },
        )

        self.assertEqual(response.status_code, 302)
        mock_delete_columns.assert_called_once_with(
            "https://docs.google.com/spreadsheets/d/linked-sheet-123/edit",
            [{"sheet_id": 1, "column_index": 18}],
        )
        self.assertEqual(mock_ensure_columns.call_args.args[1], [])
        self.participant_list.refresh_from_db()
        stored_headers = self.participant_list.google_sheet_tabs[0]["headers"]
        self.assertEqual(stored_headers[12], "Enuesta inicial")
        self.assertNotIn("Encuesta inicial", stored_headers)

    @patch("applications.admin_profiles_views._fetch_encuestas_emails_for_group")
    @patch("applications.admin_profiles_views.update_google_spreadsheet_values")
    @patch("applications.admin_profiles_views.fetch_google_spreadsheet_tabs")
    def test_linked_encuesta_check_writes_checkbox_result_back_to_google(
        self,
        mock_fetch_tabs,
        mock_update_values,
        mock_fetch_encuestas,
    ):
        mock_fetch_tabs.return_value = self._linked_google_workbook_payload()
        mock_update_values.return_value = 17
        mock_fetch_encuestas.return_value = (
            True,
            {"m1@example.com"},
            "Encuesta inicial source scanned.",
        )
        sheet_url = "https://docs.google.com/spreadsheets/d/linked-sheet-123/edit"
        self.client.post(
            reverse("admin_profiles_participants"),
            data={
                "action": "save_google_sheet_link",
                "group": str(self.group.number),
                "google_sheet_url": sheet_url,
            },
        )

        response = self.client.post(
            reverse(
                "admin_profiles_participants_track_sheet",
                args=[self.group.number, "all"],
            ),
            data={"action": "check_encuestas"},
        )

        self.assertEqual(response.status_code, 302)
        self.participant_list.refresh_from_db()
        self.assertTrue(self.participant_list.mentoras_sheet_rows[0][12])
        encuesta_writes = []
        for call in mock_update_values.call_args_list:
            for item in call.args[1]:
                if item["range"] == "'Mentoras'!M2:M2":
                    encuesta_writes.append(item["values"])
        self.assertIn([[True]], encuesta_writes)

    @patch("applications.admin_profiles_views.update_google_spreadsheet_values")
    @patch("applications.admin_profiles_views.fetch_google_spreadsheet_tabs")
    def test_linked_google_workbook_rejects_website_autosave(
        self,
        mock_fetch_tabs,
        mock_update_values,
    ):
        mock_fetch_tabs.return_value = self._linked_google_workbook_payload()
        mock_update_values.return_value = 17
        self.client.post(
            reverse("admin_profiles_participants"),
            data={
                "action": "save_google_sheet_link",
                "group": str(self.group.number),
                "google_sheet_url": "https://docs.google.com/spreadsheets/d/linked-sheet-123/edit",
            },
        )
        response = self.client.post(
            reverse(
                "admin_profiles_participants_track_sheet",
                args=[self.group.number, "all"],
            ),
            data={"action": "save_sheet", "mentoras_sheet_data": "[]", "emprendedoras_sheet_data": "[]"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 409)


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
