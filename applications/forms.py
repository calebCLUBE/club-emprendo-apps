# applications/forms.py
import re
from django import forms
from .models import FormDefinition, Question


_PRE_RE = re.compile(
    r"^\s*\[\[PRE(?P<attrs>[^\]]*)\]\]\s*\n(?P<body>.*?)\n\s*\[\[/PRE\]\]\s*\n?(?P<rest>.*)$",
    re.DOTALL,
)


def split_help_text(raw: str):
    """
    Returns (pre_text, pre_hr_bool, remaining_help_text).

    Stored at start of help_text like:
      [[PRE hr=1]]
      text...
      [[/PRE]]
      remaining help text...
    """
    raw = raw or ""
    m = _PRE_RE.match(raw)
    if not m:
        return "", False, raw

    attrs = (m.group("attrs") or "").strip()
    body = (m.group("body") or "").strip()
    rest = (m.group("rest") or "").lstrip()

    pre_hr = bool(re.search(r"\bhr\s*=\s*(1|true|yes)\b", attrs, flags=re.IGNORECASE))
    return body, pre_hr, rest


def build_application_form(form_slug: str):
    class DynamicApplicationForm(forms.Form):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)

            form_def = FormDefinition.objects.get(slug=form_slug)
            questions = (
                form_def.questions.filter(active=True)
                .select_related("show_if_question")
                .prefetch_related("choices")
                .order_by("position", "id")
            )
            id_to_slug = {q.id: q.slug for q in questions}

            self._confirm_pairs: list[tuple[str, str]] = []

            for q in questions:
                field_name = f"q_{q.slug}"

                pre_text, pre_hr, remaining_help = split_help_text(q.help_text)

                field_type = q.field_type
                if field_type == "single_choice":  # legacy alias
                    field_type = Question.CHOICE

                common = {
                    "label": q.text,
                    "help_text": remaining_help,
                    "required": q.required,
                }
                show_if_q = q.show_if_question
                show_if_value = (q.show_if_value or "").strip()
                conds = list(getattr(q, "show_if_conditions", []) or [])
                if show_if_q and show_if_value and not conds:
                    conds = [{"question_id": show_if_q.id, "value": show_if_value}]

                if field_type == Question.SHORT_TEXT:
                    field = forms.CharField(initial="", **common)

                elif field_type == Question.LONG_TEXT:
                    field = forms.CharField(
                        widget=forms.Textarea(attrs={"rows": 4}),
                        initial="",
                        **common,
                    )

                elif field_type == Question.INTEGER:
                    # Keep blank by default (no 0)
                    field = forms.IntegerField(
                        initial=None,
                        widget=forms.NumberInput(),
                        **common,
                    )

                elif field_type == Question.BOOLEAN:
                    # Radio buttons: must include a blank option to avoid preselect
                    choices = [
                        ("", "— Selecciona —"),
                        ("yes", "Sí"),
                        ("no", "No"),
                    ]
                    field = forms.ChoiceField(
                        choices=choices,
                        widget=forms.RadioSelect,
                        initial="",
                        **common,
                    )

                elif field_type == Question.CHOICE:
                    choices = [(c.value, c.label) for c in q.choices.all().order_by("position", "id")]
                    choices = [("", "— Selecciona —")] + choices
                    field = forms.ChoiceField(
                        choices=choices,
                        widget=forms.Select,
                        initial="",
                        **common,
                    )

                elif field_type == Question.MULTI_CHOICE:
                    choices = [(c.value, c.label) for c in q.choices.all().order_by("position", "id")]
                    field = forms.MultipleChoiceField(
                        choices=choices,
                        widget=forms.CheckboxSelectMultiple,
                        initial=[],
                        **common,
                    )

                else:
                    continue

                # Expose “pre” content to templates via widget attrs
                field.widget.attrs["pre_text"] = pre_text
                field.widget.attrs["pre_hr"] = "1" if pre_hr else ""
                field.widget.attrs["section_id"] = str(q.section_id or "")
                field._ce_base_required = q.required
                if show_if_q and show_if_value:
                    field.widget.attrs["show_if_question"] = f"q_{show_if_q.slug}"
                    field.widget.attrs["show_if_value"] = show_if_value
                if conds:
                    processed = []
                    for c in conds:
                        qid = c.get("question_id")
                        val = (c.get("value") or "").strip()
                        slug = id_to_slug.get(qid)
                        if slug and val:
                            processed.append({"field": f"q_{slug}", "value": val})
                    if processed:
                        field.widget.attrs["show_if_conditions"] = json.dumps(processed)

                self.fields[field_name] = field

                # Optional confirmation field (must match original)
                if q.confirm_value:
                    confirm_name = f"{field_name}__confirm"
                    confirm_label = f"Confirma {q.text}"
                    confirm_field = forms.CharField(
                        initial="",
                        label=confirm_label,
                        required=q.required,
                        help_text="Ingresa nuevamente para confirmar",
                    )
                    confirm_field.widget.attrs["section_id"] = str(q.section_id or "")
                    confirm_field.widget.attrs["data-confirm-of"] = field_name
                    confirm_field._ce_base_required = q.required
                    if show_if_q and show_if_value:
                        confirm_field.widget.attrs["show_if_question"] = f"q_{show_if_q.slug}"
                        confirm_field.widget.attrs["show_if_value"] = show_if_value
                    self.fields[confirm_name] = confirm_field
                    self._confirm_pairs.append((field_name, confirm_name))

        def clean(self):
            data = super().clean()
            for original, confirm in getattr(self, "_confirm_pairs", []):
                v1 = data.get(original, "")
                v2 = data.get(confirm, "")
                if (v1 or v2) and v1 != v2:
                    msg = "Los valores no coinciden."
                    self.add_error(confirm, msg)
                    self.add_error(original, msg)
            return data

    return DynamicApplicationForm
