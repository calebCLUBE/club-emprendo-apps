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

                # ✅ IMPORTANT RULE:
                # If the question has choices, we ALWAYS render it as Choice / MultipleChoice,
                # even if someone accidentally set the field_type to BOOLEAN.
                choices_qs = q.choices.all().order_by("position", "id")
                has_choices = choices_qs.exists()

                if has_choices:
                    choices = [(c.value, c.label) for c in choices_qs]

                    # Multi-choice uses checkboxes
                    if q.field_type == Question.MULTI_CHOICE:
                        field = forms.MultipleChoiceField(
                            choices=choices,
                            widget=forms.CheckboxSelectMultiple,
                            **common,
                        )
                    else:
                        # Default single-choice uses radio buttons
                        field = forms.ChoiceField(
                            choices=choices,
                            widget=forms.RadioSelect,
                            **common,
                        )

                else:
                    # No choices: use field_type normally
                    if q.field_type == Question.SHORT_TEXT:
                        field = forms.CharField(**common)

                    elif q.field_type == Question.LONG_TEXT:
                        field = forms.CharField(widget=forms.Textarea, **common)

                    elif q.field_type == Question.INTEGER:
                        field = forms.IntegerField(**common)

                    elif q.field_type == Question.BOOLEAN:
                        # ✅ Better UX: show Sí/No explicitly (not a lonely checkbox)
                        # Also respects required=True properly.
                        field = forms.ChoiceField(
                            label=q.text,
                            help_text=q.help_text,
                            required=q.required,
                            choices=[("yes", "Sí"), ("no", "No")],
                            widget=forms.RadioSelect,
                        )

                    else:
                        continue

                self.fields[field_name] = field

    return DynamicApplicationForm
