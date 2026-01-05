# applications/forms.py
from django import forms
from .models import FormDefinition, Question


def build_application_form(form_slug: str):
    class DynamicApplicationForm(forms.Form):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)

            form_def = FormDefinition.objects.get(slug=form_slug)
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
                    field = forms.CharField(widget=forms.Textarea, **common)

                elif q.field_type == Question.INTEGER:
                    field = forms.IntegerField(**common)

                elif q.field_type == Question.BOOLEAN:
                    # ✅ Better UX than a single checkbox:
                    # show explicit Sí/No radios
                    field = forms.ChoiceField(
                        choices=[("yes", "Sí"), ("no", "No")],
                        widget=forms.RadioSelect,
                        **common,
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
                    continue

                self.fields[field_name] = field

    return DynamicApplicationForm
