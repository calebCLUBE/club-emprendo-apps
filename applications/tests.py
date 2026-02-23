from django.test import TestCase
from django.urls import reverse
import re

from applications.admin import QuestionAdminForm
from applications.models import FormDefinition, Question


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
