from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .group_summary import participant_group_summary
from .models import FormGroup, GroupParticipantList


class ParticipantGroupSummaryTests(TestCase):
    def setUp(self):
        self.group = self.make_group(801, 2025)
        self.people = GroupParticipantList.objects.create(
            group=self.group,
            mentoras_sheet_rows=[self.row("Graduada", "mentor@example.com"), self.row("Activa", "active@example.com")],
            emprendedoras_sheet_rows=[
                self.row("Graduada", "founder@example.com"),
                self.row("Graduada", "founder@example.com"),
                self.row("NC - Personal", "drop@example.com"),
                self.row("No firmo acta", "no-start@example.com"),
                self.row("Cambio de Grupo", "transfer@example.com"),
            ],
        )

    def make_group(self, number, year):
        return FormGroup.objects.create(number=number, year=year, start_day=1, start_month="enero", end_month="abril")

    def row(self, status, email):
        return ["", status, 1, email.split("@")[0], "", email, "", "Colombia", "", False, False, False, False, False, False]

    def metrics(self, summary):
        return {r["key"]: r["cells"] for r in summary["rows"]}

    def test_counts_deduplicate_and_rates_use_starters_not_selected(self):
        summary = participant_group_summary(self.group)
        metrics = self.metrics(summary)
        self.assertEqual([c["count"] for c in metrics["selected"]], [2, 3])
        self.assertEqual([c["count"] for c in metrics["started"]], [2, 2])
        self.assertEqual(metrics["graduated"][1], {"count": 1, "pct": 50.0})
        self.assertEqual(metrics["drop_personal"][1], {"count": 1, "pct": 50.0})
        self.assertEqual(metrics["active"][0], {"count": 1, "pct": 50.0})

    def test_later_mentoring_counts_people_once_and_requires_later_started_group(self):
        for number, year, status in ((802, 2026, "Activa"), (803, 2026, "Graduada"), (804, 2099, "Activa"), (805, 2024, "Graduada"), (806, 2025, "Graduada")):
            group = self.make_group(number, year)
            GroupParticipantList.objects.create(group=group, mentoras_sheet_rows=[
                self.row(status, "founder@example.com"), self.row(status, "mentor@example.com"),
                self.row("No firmo acta", "drop@example.com"),
            ])
        summary = participant_group_summary(self.group)
        self.assertEqual(summary["later_entrepreneurs"][0]["groups"], [802, 803])
        self.assertEqual(summary["repeat_mentors"][0]["groups"], [802, 803])
        self.assertEqual([c["count"] for c in self.metrics(summary)["later_mentor"]], [1, 1])

    def test_missing_role_and_zero_starters_show_unavailable_percentage(self):
        self.people.mentoras_sheet_rows = []
        self.people.emprendedoras_sheet_rows = [self.row("No Capacitacion", "person@example.com")]
        self.people.save()
        metrics = self.metrics(participant_group_summary(self.group))
        self.assertEqual(metrics["graduated"], [{"count": 0, "pct": None}, {"count": 0, "pct": None}])
        empty = self.make_group(807, 2025)
        self.assertFalse(participant_group_summary(empty)["has_participants"])

    def test_linked_header_mappings_override_stale_local_status(self):
        self.people.google_sheet_url = "https://docs.google.com/spreadsheets/d/test/edit"
        self.people.google_sheet_tabs = [{
            "track": "emprendedoras", "title": "Emprendedoras",
            "headers": ["Email", "Info", "Estatus", "Nombre"],
            "rows": [["linked@example.com", "Activa", "Graduada", "Linked Person"]],
        }]
        self.people.save()
        metrics = self.metrics(participant_group_summary(self.group))
        self.assertEqual(metrics["selected"][1]["count"], 1)
        self.assertEqual(metrics["graduated"][1], {"count": 1, "pct": 100.0})

    @patch("applications.admin_dashboard_views.timezone.localdate", return_value=date(2025, 3, 1))
    def test_active_and_completed_group_states(self, today):
        self.assertEqual(participant_group_summary(self.group)["state"], "In progress")
        today.return_value = date(2025, 5, 1)
        self.assertEqual(participant_group_summary(self.group)["state"], "Completed")

    def test_summary_is_on_participants_and_both_workbook_views(self):
        admin = get_user_model().objects.create_superuser("admin@example.com", "Test-password-739!")
        self.client.force_login(admin)
        response = self.client.get(reverse("admin_profiles_participants"), {"group": self.group.number})
        self.assertContains(response, "Group summary")
        self.assertContains(response, "50.0%")
        self.assertContains(response, "initial cohort is inferred")
        self.assertEqual(self.metrics(response.context["group_summary"])["graduated"][1]["count"], 1)
        for track in ("all", "mentoras"):
            response = self.client.get(reverse("admin_profiles_participants_track_sheet", args=[self.group.number, track]))
            self.assertContains(response, "Entrepreneurs who later became mentors")
            self.assertEqual(response.context["group_summary"]["columns"], ["Mentoras", "Emprendedoras"])

    def test_each_group_has_its_own_summary_including_archived_groups(self):
        other = self.make_group(808, 2025)
        other.is_active = False
        other.save()
        GroupParticipantList.objects.create(group=other, mentoras_sheet_rows=[self.row("Graduada", "another@example.com")])
        admin = get_user_model().objects.create_superuser("admin@example.com", "Test-password-739!")
        self.client.force_login(admin)
        response = self.client.get(reverse("admin_profiles_participants"), {"group": other.number})
        self.assertContains(response, "Archived group")
        self.assertContains(response, "Group summary")
        self.assertEqual(self.metrics(response.context["group_summary"])["selected"][0]["count"], 1)
        self.assertEqual(self.metrics(response.context["group_summary"])["selected"][1]["count"], 0)
        self.assertNotContains(response, 'value="clear_group_participants"')
        self.assertNotContains(response, 'value="save_google_sheet_link"')
        response = self.client.get(reverse("admin_profiles_participants"), {"group": self.group.number})
        self.assertEqual(self.metrics(response.context["group_summary"])["selected"][0]["count"], 2)
