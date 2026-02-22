from django.test import TestCase

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
