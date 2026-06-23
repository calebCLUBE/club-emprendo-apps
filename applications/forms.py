# applications/forms.py
import json
import re
import unicodedata
from django import forms
from .models import FormDefinition, Question


_PRE_RE = re.compile(
    r"^\s*\[\[PRE(?P<attrs>[^\]]*)\]\]\s*\n(?P<body>.*?)\n\s*\[\[/PRE\]\]\s*\n?(?P<rest>.*)$",
    re.DOTALL,
)


def _confirm_text_canonical(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        text = " ".join(str(v or "") for v in value)
    else:
        text = str(value or "")
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00a0", " ").replace("\u2007", " ").replace("\u202f", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _normalize_confirm_value(value, *, source_field_name: str) -> str:
    canonical = _confirm_text_canonical(value)
    if not canonical:
        return ""

    field_key = (source_field_name or "").lower()

    if "email" in field_key or "correo" in field_key:
        return canonical.replace(" ", "").lower()

    if any(
        token in field_key
        for token in ("whatsapp", "telefono", "celular", "phone", "movil")
    ):
        return re.sub(r"[^0-9+]", "", canonical.lower().replace(" ", ""))

    return canonical


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


class MultipleChoiceGridWidget(forms.Widget):
    template_name = "applications/widgets/multiple_choice_grid.html"

    def __init__(self, rows, columns, attrs=None):
        super().__init__(attrs)
        self.rows = list(rows)
        self.columns = list(columns)

    def value_from_datadict(self, data, files, name):
        return [
            data.getlist(f"{name}__row_{index}")
            if hasattr(data, "getlist")
            else data.get(f"{name}__row_{index}", [])
            for index in range(len(self.rows))
        ]

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        selected = list(value) if isinstance(value, (list, tuple)) else []
        grid_rows = []
        for index, label in enumerate(self.rows):
            raw_selected = selected[index] if index < len(selected) else []
            if isinstance(raw_selected, (list, tuple)):
                selected_values = {str(item) for item in raw_selected if str(item)}
            elif raw_selected:
                # Backward-compatible rendering for the previous one-value-per-row shape.
                selected_values = {str(raw_selected)}
            else:
                selected_values = set()
            grid_rows.append({
                "index": index,
                "label": label,
                "cells": [
                    {
                        "value": column_value,
                        "label": column_label,
                        "checked": str(column_value) in selected_values,
                    }
                    for column_value, column_label in self.columns
                ],
            })
        context["widget"]["grid_rows"] = grid_rows
        context["widget"]["grid_columns"] = self.columns
        context["widget"]["grid_required"] = bool(attrs and attrs.get("required"))
        context["widget"]["grid_base_required"] = (attrs or {}).get("data-base-required", "")
        return context


class MultipleChoiceGridField(forms.Field):
    default_error_messages = {
        "required": "Selecciona al menos una opción en la cuadrícula.",
        "invalid": "Una de las respuestas de la cuadrícula no es válida.",
    }

    def __init__(self, *, rows, columns, **kwargs):
        self.rows = list(rows)
        self.columns = list(columns)
        kwargs["widget"] = MultipleChoiceGridWidget(self.rows, self.columns)
        super().__init__(**kwargs)

    def clean(self, value):
        raw_values = list(value or [])
        raw_values += [[]] * max(0, len(self.rows) - len(raw_values))
        values = []
        for raw_row in raw_values[:len(self.rows)]:
            if isinstance(raw_row, (list, tuple)):
                row_values = [str(item) for item in raw_row if str(item)]
            elif raw_row:
                row_values = [str(raw_row)]
            else:
                row_values = []
            # Preserve checkbox order while ignoring duplicate submitted values.
            values.append(list(dict.fromkeys(row_values)))
        allowed = {str(column[0]) for column in self.columns}

        if self.required and not any(values):
            raise forms.ValidationError(self.error_messages["required"], code="required")
        if any(item not in allowed for row_values in values for item in row_values):
            raise forms.ValidationError(self.error_messages["invalid"], code="invalid")
        if not any(values):
            return ""

        labels = {str(value): label for value, label in self.columns}
        answers = [
            {
                "row": row,
                "value": selected,
                "label": labels.get(selected, ""),
            }
            for row, selected_values in zip(self.rows, values)
            for selected in selected_values
        ]
        return json.dumps(answers, ensure_ascii=False)


def build_application_form(form_slug: str, additional_form_slugs: list[str] | tuple[str, ...] | None = None):
    form_slugs = [form_slug] + [slug for slug in (additional_form_slugs or []) if slug and slug != form_slug]

    class DynamicApplicationForm(forms.Form):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)

            form_defs = list(FormDefinition.objects.filter(slug__in=form_slugs))
            by_slug = {fd.slug: fd for fd in form_defs}
            form_defs = [by_slug[slug] for slug in form_slugs if slug in by_slug]
            questions = []
            for form_def in form_defs:
                questions.extend(
                    form_def.questions.filter(active=True)
                    .select_related("show_if_question")
                    .prefetch_related("choices")
                    .order_by("position", "id")
                )
            id_to_slug = {q.id: q.slug for q in questions}

            self._confirm_pairs: list[tuple[str, str]] = []
            seen_field_names: set[str] = set()

            for q in questions:
                field_name = f"q_{q.slug}"
                # Combined A1+A2 forms often repeat identity questions. Ask once and
                # reuse that answer for both question records at save time.
                if field_name in seen_field_names:
                    continue
                seen_field_names.add(field_name)

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
                    question_key = f"{q.slug} {q.text}".lower()
                    if "email" in question_key or "correo" in question_key:
                        field = forms.EmailField(
                            initial="",
                            error_messages={"invalid": "Ingresa una dirección de correo electrónico válida."},
                            **common,
                        )
                    else:
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

                elif field_type == Question.MULTIPLE_CHOICE_GRID:
                    rows = [line.strip() for line in (q.grid_rows or "").splitlines() if line.strip()]
                    columns = [(c.value, c.label) for c in q.choices.all().order_by("position", "id")]
                    field = MultipleChoiceGridField(
                        rows=rows,
                        columns=columns,
                        initial=[],
                        **common,
                    )

                else:
                    continue

                # Expose “pre” content to templates via widget attrs
                field.widget.attrs["pre_text"] = pre_text
                field.widget.attrs["pre_hr"] = "1" if pre_hr else ""
                field.widget.attrs["section_id"] = str(q.section_id or "")
                field.widget.attrs["source_form_id"] = str(q.form_id)
                field.widget.attrs["data-base-required"] = "1" if q.required else ""
                if getattr(q, "end_form_rules", None):
                    field.widget.attrs["end_form_rules"] = json.dumps(q.end_form_rules)
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
                    confirm_field.widget.attrs["source_form_id"] = str(q.form_id)
                    confirm_field.widget.attrs["data-base-required"] = "1" if q.required else ""
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
                v1 = _normalize_confirm_value(
                    data.get(original, ""),
                    source_field_name=original,
                )
                v2 = _normalize_confirm_value(
                    data.get(confirm, ""),
                    source_field_name=original,
                )
                if (v1 or v2) and v1 != v2:
                    key = original.lower()
                    if "email" in key or "correo" in key:
                        msg = "Los correos no coinciden."
                    elif "whatsapp" in key or "telefono" in key or "celular" in key:
                        msg = "Los números de WhatsApp no coinciden."
                    elif "cedula" in key or "document" in key:
                        msg = "Los números de documento no coinciden."
                    else:
                        msg = "Los valores no coinciden."
                    self.add_error(confirm, msg)
                    self.add_error(original, msg)
            return data

    return DynamicApplicationForm
