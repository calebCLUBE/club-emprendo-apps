# applications/forms.py
from django import forms
from .models import FormDefinition, Question


def build_application_form(form_slug: str):
    """
    Build a dynamic Django Form from DB questions for a given FormDefinition.slug.

    IMPORTANT:
    - We do NOT hardcode name/email fields here.
    - If you want "Nombre completo" and "Correo electrónico", create them as Questions
      with slugs: full_name and email (recommended).
    """

    class DynamicApplicationForm(forms.Form):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)

            form_def = FormDefinition.objects.get(slug=form_slug)

            # Use ordering by position so the form is predictable
            questions = form_def.questions.filter(active=True).order_by("position", "id")

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
                    field = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), **common)

                elif q.field_type == Question.INTEGER:
                    field = forms.IntegerField(**common)

                elif q.field_type == Question.BOOLEAN:
                    # checkbox; if required=True, enforce it
                    field = forms.BooleanField(
                        label=q.text,
                        help_text=q.help_text,
                        required=q.required,
                    )

                elif q.field_type == Question.CHOICE:
                    choices = [(c.value, c.label) for c in q.choices.all().order_by("position", "id")]
                    field = forms.ChoiceField(
                        choices=choices,
                        widget=forms.RadioSelect,
                        **common,
                    )

                elif q.field_type == Question.MULTI_CHOICE:
                    choices = [(c.value, c.label) for c in q.choices.all().order_by("position", "id")]
                    field = forms.MultipleChoiceField(
                        choices=choices,
                        widget=forms.CheckboxSelectMultiple,
                        **common,
                    )

                else:
                    # Unknown type → skip safely
                    continue

                self.fields[field_name] = field

    return DynamicApplicationForm
