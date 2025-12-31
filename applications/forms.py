# applications/forms.py
from django import forms
from .models import FormDefinition, Question


def build_application_form(form_slug: str):
    """
    Build a dynamic Django Form class from FormDefinition + Question rows.
    No hardcoded name/email here (those are normal Question rows in the DB).
    """

    class DynamicApplicationForm(forms.Form):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)

            form_def = FormDefinition.objects.get(slug=form_slug)
            questions = form_def.questions.filter(active=True).order_by("position")

            for q in questions:
                field_name = f"q_{q.slug}"

                common = {
                    "label": q.text,
                    "help_text": q.help_text,
                    "required": q.required,
                }

                if q.field_type == Question.SHORT_TEXT:
                    field = forms.CharField(**common)

                elif q.field_type == Question.LONG_TEXT:
                    field = forms.CharField(widget=forms.Textarea, **common)

                elif q.field_type == Question.INTEGER:
                    field = forms.IntegerField(**common)

                elif q.field_type == Question.BOOLEAN:
                    # Checkbox: required=False by default; if you truly want required,
                    # enforce via clean() or custom validation.
                    field = forms.BooleanField(label=q.text, help_text=q.help_text, required=False)

                elif q.field_type == Question.CHOICE:
                    choices = [(c.value, c.label) for c in q.choices.all().order_by("position")]
                    field = forms.ChoiceField(choices=choices, widget=forms.RadioSelect, **common)

                elif q.field_type == Question.MULTI_CHOICE:
                    choices = [(c.value, c.label) for c in q.choices.all().order_by("position")]
                    field = forms.MultipleChoiceField(
                        choices=choices,
                        widget=forms.CheckboxSelectMultiple,
                        **common,
                    )
                else:
                    continue

                self.fields[field_name] = field

    return DynamicApplicationForm
