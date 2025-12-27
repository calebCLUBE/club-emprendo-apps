# applications/forms.py
from django import forms
from .models import FormDefinition, Question


def build_application_form(form_slug: str):
    """
    Build a dynamic form class for a given FormDefinition.slug.
    """

    class DynamicApplicationForm(forms.Form):
        name = forms.CharField(label="Nombre completo", max_length=200)
        email = forms.EmailField(label="Correo electrónico")

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)

            form_def = FormDefinition.objects.get(slug=form_slug)
            questions = form_def.questions.filter(active=True)

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
                    field = forms.BooleanField(
                        label=q.text,
                        help_text=q.help_text,
                        required=False,
                    )
                    if q.required:
                        field.required = True
                elif q.field_type == Question.CHOICE:
                    choices = [
                        (c.value, c.label) for c in q.choices.all()
                    ]
                    field = forms.ChoiceField(
                        choices=choices,
                        widget=forms.RadioSelect,
                        **common,
                    )
                elif q.field_type == Question.MULTI_CHOICE:
                    choices = [
                        (c.value, c.label) for c in q.choices.all()
                    ]
                    field = forms.MultipleChoiceField(
                        choices=choices,
                        widget=forms.CheckboxSelectMultiple,
                        **common,
                    )
                else:
                    continue

                self.fields[field_name] = field

    return DynamicApplicationForm
