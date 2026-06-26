# applications/admin.py
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from django.utils.text import slugify
import re
from django import forms
from django.forms.models import BaseInlineFormSet
import json
from django.http import HttpResponseRedirect

from .forms_admin import TaskTypeAdminForm
from .models import (
    Application,
    Choice,
    DropboxSignWebhookEvent,
    FormDefinition,
    ApplicationGradingConfig,
    GradingCriterion,
    GradingResponseWeight,
    GroupParticipantList,
    PairingAIComparison,
    PairingConfig,
    PairingPriorityRule,
    ParticipantSheetVersion,
    ParticipantEmailStatus,
    Question,
    Section,
    StoredEmailTemplate,
    TaskType,
)


# =========================
# PRE BLOCK PARSE / PACK
# =========================
_PRE_RE = re.compile(
    r"^\s*\[\[PRE(?P<attrs>[^\]]*)\]\]\s*\n(?P<body>.*?)\n\s*\[\[/PRE\]\]\s*\n?(?P<rest>.*)$",
    re.DOTALL,
)


def _split_help_text(raw: str):
    raw = raw or ""
    m = _PRE_RE.match(raw)
    if not m:
        return "", False, raw

    attrs = (m.group("attrs") or "").strip()
    body = (m.group("body") or "").strip()
    rest = (m.group("rest") or "").lstrip()

    pre_hr = bool(re.search(r"\bhr\s*=\s*(1|true|yes)\b", attrs, flags=re.IGNORECASE))
    return body, pre_hr, rest


def _pack_help_text(pre_text: str, pre_hr: bool, rest_help_text: str) -> str:
    pre_text = (pre_text or "").strip()
    rest_help_text = (rest_help_text or "").strip()

    if not pre_text and not pre_hr:
        return rest_help_text

    hr_val = "1" if pre_hr else "0"
    header = f"[[PRE hr={hr_val}]]\n{pre_text}\n[[/PRE]]"
    return header + (("\n\n" + rest_help_text) if rest_help_text else "")


def _condition_questions_json(questions) -> str:
    return json.dumps([
        {
            "id": q.id,
            "text": q.text or q.slug,
            "field_type": q.field_type,
            "choices": (
                [{"value": c.value, "label": c.label or c.value} for c in q.choices.all()]
                if q.field_type in (Question.CHOICE, Question.MULTI_CHOICE)
                else [{"value": "yes", "label": "Sí"}, {"value": "no", "label": "No"}]
                if q.field_type == Question.BOOLEAN
                else []
            ),
        }
        for q in questions
        if q.field_type in (Question.CHOICE, Question.MULTI_CHOICE, Question.BOOLEAN)
    ])


def _available_question_slug(base: str, used: set[str], max_length: int = 50) -> str:
    """Return a stable question slug that does not collide with existing questions."""
    base = (base or "question")[:max_length].rstrip("_-") or "question"
    candidate = base
    suffix = 2
    while candidate in used:
        ending = f"_{suffix}"
        candidate = f"{base[:max_length - len(ending)].rstrip('_-')}{ending}"
        suffix += 1
    return candidate


# =========================
# CUSTOM ADMIN FORM FOR QUESTION
# =========================
class QuestionAdminForm(forms.ModelForm):
    class Media:
        css = {"all": ("applications/css/form_builder.css",)}
        js = (
            "applications/js/admin_show_if_value.js",
            "applications/js/form_builder.js",
        )

    answer_options = forms.CharField(
        required=False,
        label="Answer options / grid columns",
        help_text="One option per line. For a multiple choice grid, these are the columns.",
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Option 1\nOption 2"}),
    )
    section_token = forms.CharField(required=False, widget=forms.HiddenInput())

    pre_hr = forms.BooleanField(
        required=False,
        label="Show horizontal line above",
    )
    pre_text = forms.CharField(
        required=False,
        label="Text above question",
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    help_text_clean = forms.CharField(
        required=False,
        label="Help text (below question)",
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    section = forms.ModelChoiceField(
        required=False,
        queryset=Section.objects.none(),
        label="Section",
        help_text="Optional: group this question into a section/page.",
    )

    class Meta:
        model = Question
        fields = "__all__"
        widgets = {
            # Hide raw storage so admin users don't see [[PRE...]] tags
            "help_text": forms.HiddenInput(),
            "grid_rows": forms.Textarea(attrs={"rows": 4, "placeholder": "Row 1\nRow 2"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # The simplified editor does not require users to manage internal IDs.
        # Existing slugs stay stable; new ones are generated in clean().
        if "slug" in self.fields:
            self.fields["slug"].required = False

        if "field_type" in self.fields:
            self.fields["field_type"].choices = [
                (Question.SHORT_TEXT, "Short answer"),
                (Question.LONG_TEXT, "Paragraph"),
                (Question.INTEGER, "Number"),
                (Question.BOOLEAN, "Yes / No"),
                (Question.CHOICE, "Dropdown"),
                (Question.MULTI_CHOICE, "Checkboxes"),
                (Question.MULTIPLE_CHOICE_GRID, "Multiple choice grid"),
            ]

        if "grid_rows" in self.fields:
            self.fields["grid_rows"].label = "Grid rows"
            self.fields["grid_rows"].help_text = "Enter one row per line. Columns are edited below."

        # Parse existing help_text into admin-friendly pieces
        pre_text, pre_hr, rest = _split_help_text(getattr(self.instance, "help_text", "") or "")
        self.fields["pre_text"].initial = pre_text
        self.fields["pre_hr"].initial = pre_hr
        self.fields["help_text_clean"].initial = rest
        if getattr(self.instance, "pk", None):
            self.fields["answer_options"].initial = "\n".join(
                self.instance.choices.order_by("position", "id").values_list("label", flat=True)
            )

        form_obj = getattr(self.instance, "form", None)
        if not form_obj:
            raw_form_id = (
                self.data.get(self.add_prefix("form"))
                or self.initial.get("form")
                or self.data.get("form")
            )
            try:
                form_id = int(getattr(raw_form_id, "pk", raw_form_id) or 0)
            except (TypeError, ValueError):
                form_id = 0
            if form_id:
                form_obj = FormDefinition.objects.filter(pk=form_id).first()
        qs = Section.objects.none()
        show_if_qs = Question.objects.none()
        if form_obj:
            qs = form_obj.sections.all()
            show_if_qs = (
                form_obj.questions
                .exclude(id=getattr(self.instance, "id", None))
                .prefetch_related("choices")
            )
        if "section" in self.fields:
            self.fields["section"].queryset = qs
            self.fields["section"].initial = getattr(self.instance, "section_id", None)
        if "section_token" in self.fields:
            self.fields["section_token"].initial = (
                f"id:{self.instance.section_id}" if getattr(self.instance, "section_id", None) else ""
            )

        # ----- Multi controlling questions (OR) -----
        questions_json = _condition_questions_json(show_if_qs)

        self.fields["show_if_conditions"] = forms.JSONField(
            required=False,
            widget=ShowIfConditionsWidget(questions_json=questions_json),
            label="Conditional logic",
            help_text="Works with multiple-choice, checkbox, and Yes/No questions.",
        )

        # initial conditions from stored JSON or legacy single
        if not self.data:
            conds = list(getattr(self.instance, "show_if_conditions", []) or [])
            if not conds and getattr(self.instance, "show_if_question_id", None) and self.instance.show_if_value:
                conds.append({"question_id": self.instance.show_if_question_id, "value": self.instance.show_if_value})
            self.fields["show_if_conditions"].initial = conds

        # ----- Legacy single fields (visible; mirrored to first condition) -----
        if "show_if_question" in self.fields:
            self.fields["show_if_question"].queryset = show_if_qs
            self.fields["show_if_question"].label = "Pregunta que controla visibilidad (regla principal)"
            self.fields["show_if_question"].help_text = (
                "Opcional. Usa esto como versión simple de la primera regla."
            )
            self.fields["show_if_question"].widget.attrs["data-show-if-choices-map"] = json.dumps({
                str(q.id): [
                    (c.value, c.label or c.value) for c in q.choices.all()
                ] if q.field_type in (Question.CHOICE, Question.MULTI_CHOICE) else [("yes", "si"), ("no", "no")] if q.field_type == Question.BOOLEAN else []
                for q in show_if_qs
            })

        if "show_if_value" in self.fields:
            choice_map: dict[str, list[tuple[str, str]]] = {}
            for q in show_if_qs:
                opts: list[tuple[str, str]] = []
                if q.field_type == Question.BOOLEAN:
                    opts = [("yes", "si"), ("no", "no")]
                elif q.field_type in (Question.CHOICE, Question.MULTI_CHOICE):
                    opts = [(c.value, c.label or c.value) for c in q.choices.all()]
                if opts:
                    choice_map[str(q.id)] = opts

            sel_qid = ""
            if self.data.get(self.add_prefix("show_if_question")):
                sel_qid = str(self.data.get(self.add_prefix("show_if_question")) or "")
            elif getattr(self.instance, "show_if_question_id", None):
                sel_qid = str(self.instance.show_if_question_id or "")

            current_val = (
                (self.data.get(self.add_prefix("show_if_value")) or "").strip()
                if self.data
                else (getattr(self.instance, "show_if_value", "") or "")
            )

            opts = [("", "— elige una respuesta —")]
            opts += choice_map.get(sel_qid, [])
            if current_val and current_val not in [v for v, _ in opts]:
                opts.append((current_val, f"{current_val} (actual)"))

            self.fields["show_if_value"] = forms.ChoiceField(
                required=False,
                label="Respuesta esperada (regla principal)",
                help_text="Si la pregunta de arriba tiene esta respuesta, se mostrará esta pregunta.",
                choices=opts,
            )

        answer_options = []
        if getattr(self.instance, "field_type", None) == Question.BOOLEAN:
            answer_options = [{"value": "yes", "label": "Sí"}, {"value": "no", "label": "No"}]
        elif getattr(self.instance, "field_type", None) in (Question.CHOICE, Question.MULTI_CHOICE):
            answer_options = (
                [{"value": c.value, "label": c.label or c.value} for c in self.instance.choices.all()]
                if getattr(self.instance, "pk", None)
                else []
            )
        stored_email_names = (
            list(form_obj.stored_emails.order_by("position", "id").values_list("name", flat=True))
            if form_obj and getattr(form_obj, "pk", None)
            else []
        )
        self.fields["end_form_rules"] = forms.JSONField(
            required=False,
            label="End application based on an answer",
            help_text="Show a final rejection page and optionally send a stored email.",
            widget=EndFormRulesWidget(
                answer_options_json=json.dumps(answer_options),
                stored_emails_json=json.dumps(stored_email_names),
            ),
        )
        if not self.data:
            self.fields["end_form_rules"].initial = list(
                getattr(self.instance, "end_form_rules", []) or []
            )

    def clean(self):
        cleaned_data = super().clean()
        if not (cleaned_data.get("slug") or "").strip():
            text = (cleaned_data.get("text") or "").strip()
            if text:
                max_length = Question._meta.get_field("slug").max_length
                base = (slugify(text).replace("-", "_") or "question")[:max_length]
                form_obj = getattr(self.instance, "form", None)
                used = set()
                if form_obj and getattr(form_obj, "pk", None):
                    existing = form_obj.questions.all()
                    if getattr(self.instance, "pk", None):
                        existing = existing.exclude(pk=self.instance.pk)
                    used = set(existing.values_list("slug", flat=True))
                generated = _available_question_slug(base, used, max_length)
                cleaned_data["slug"] = generated
                self.instance.slug = generated
                self._generated_slug_base = base

        if cleaned_data.get("field_type") == Question.MULTIPLE_CHOICE_GRID:
            rows = [line.strip() for line in (cleaned_data.get("grid_rows") or "").splitlines() if line.strip()]
            columns = [line.strip() for line in (cleaned_data.get("answer_options") or "").splitlines() if line.strip()]
            if not rows:
                self.add_error("grid_rows", "Add at least one grid row.")
            if not columns:
                self.add_error("answer_options", "Add at least one grid column.")
        return cleaned_data

    def save(self, commit=True):
        obj = super().save(commit=False)

        pre_text = self.cleaned_data.get("pre_text", "")
        pre_hr = bool(self.cleaned_data.get("pre_hr"))
        rest = self.cleaned_data.get("help_text_clean", "")
        section = (
            self.cleaned_data.get("section")
            if "section" in self.cleaned_data
            else getattr(self.instance, "section", None)
        )

        obj.help_text = _pack_help_text(pre_text, pre_hr, rest)
        obj.section = section

        # Sync multi-conditions back to model + legacy fields (first condition)
        conds = list(self.cleaned_data.get("show_if_conditions") or [])
        legacy_qid = self.cleaned_data.get("show_if_question")
        legacy_qid = getattr(legacy_qid, "id", legacy_qid) if legacy_qid else None
        legacy_val = (self.cleaned_data.get("show_if_value") or "").strip()
        legacy_changed = (
            "show_if_question" in self.changed_data
            or "show_if_value" in self.changed_data
        )

        try:
            legacy_qid_int = int(legacy_qid) if legacy_qid else None
        except (TypeError, ValueError):
            legacy_qid_int = None

        # If the visible legacy pair was edited, mirror it into condition #1.
        # This keeps admin saves predictable even when the hidden JSON field
        # is re-serialized by browser JS.
        if legacy_changed and legacy_qid_int and legacy_val:
            first = {"question_id": legacy_qid_int, "value": legacy_val}
            if conds:
                conds[0] = first
            else:
                conds = [first]

        obj.show_if_conditions = conds
        obj.show_if_question = None
        obj.show_if_value = ""
        if conds:
            try:
                obj.show_if_question_id = int(conds[0].get("question_id") or 0) or None
                obj.show_if_value = conds[0].get("value", "")
            except Exception:
                pass
        elif legacy_qid_int:
            # Keep the selected controlling question even when triggering value is blank.
            # This prevents admin edits from dropping the selection before the user picks a value.
            obj.show_if_question_id = legacy_qid_int
            obj.show_if_value = legacy_val

        if commit:
            obj.save()
            self._save_answer_options(obj)
            self.save_m2m()
        return obj

    def _save_answer_options(self, obj):
        """Keep choice editing as simple as Google Forms without changing old values."""
        if "answer_options" not in self.changed_data or not obj.pk:
            return

        labels = [
            line.strip()
            for line in (self.cleaned_data.get("answer_options") or "").splitlines()
            if line.strip()
        ]
        existing = list(obj.choices.order_by("position", "id"))
        used_values = {choice.value for choice in existing}

        for position, label in enumerate(labels):
            if position < len(existing):
                choice = existing[position]
                choice.label = label
                choice.position = position
                choice.save(update_fields=["label", "position"])
                continue

            base = slugify(label) or f"option-{position + 1}"
            value = base
            suffix = 2
            while value in used_values:
                value = f"{base}-{suffix}"
                suffix += 1
            used_values.add(value)
            Choice.objects.create(
                question=obj,
                label=label,
                value=value,
                position=position,
            )

        if len(existing) > len(labels):
            Choice.objects.filter(pk__in=[c.pk for c in existing[len(labels):]]).delete()


# ---------- Inlines so you can edit questions + choices inside a form ----------
class ChoiceInline(admin.TabularInline):
    model = Choice
    extra = 0
    fields = ("position", "label", "value")
    ordering = ("position", "id")


class ShowIfConditionsWidget(forms.Widget):
    template_name = "admin/widgets/show_if_conditions.html"

    class Media:
        js = ("applications/js/admin_conditional_logic.js",)

    def __init__(self, *args, **kwargs):
        self.questions_json = kwargs.pop("questions_json", "[]")
        self.target_label = kwargs.pop("target_label", "question")
        self.conjunction = kwargs.pop("conjunction", "OR")
        super().__init__(*args, **kwargs)

    def get_context(self, name, value, attrs):
        ctx = super().get_context(name, value, attrs)
        ctx["widget"]["questions_json"] = self.questions_json
        ctx["widget"]["target_label"] = self.target_label
        ctx["widget"]["conjunction"] = self.conjunction
        if isinstance(value, str):
            ctx["widget"]["value_json"] = value
        else:
            ctx["widget"]["value_json"] = json.dumps(value or [])
        return ctx


class EndFormRulesWidget(forms.Widget):
    template_name = "admin/widgets/end_form_rules.html"

    class Media:
        js = ("applications/js/admin_end_form_rules.js",)

    def __init__(self, *args, **kwargs):
        self.answer_options_json = kwargs.pop("answer_options_json", "[]")
        self.stored_emails_json = kwargs.pop("stored_emails_json", "[]")
        super().__init__(*args, **kwargs)

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["widget"]["answer_options_json"] = self.answer_options_json
        context["widget"]["stored_emails_json"] = self.stored_emails_json
        context["widget"]["value_json"] = value if isinstance(value, str) else json.dumps(value or [])
        return context


class SectionAdminForm(forms.ModelForm):
    class Meta:
        model = Section
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        form_obj = getattr(self.instance, "form", None)
        if form_obj:
            qs = form_obj.questions.prefetch_related("choices").all()
        else:
            qs = Question.objects.none()
        self.fields["show_if_conditions"] = forms.JSONField(
            required=False,
            label="Conditional logic",
            help_text="Choose the answer that makes this section appear.",
            widget=ShowIfConditionsWidget(
                questions_json=_condition_questions_json(qs),
                target_label="section",
                conjunction="AND",
            ),
        )
        if not self.data:
            conditions = list(getattr(self.instance, "show_if_conditions", []) or [])
            if not conditions and getattr(self.instance, "show_if_question_id", None) and self.instance.show_if_value:
                conditions = [{
                    "question_id": self.instance.show_if_question_id,
                    "value": self.instance.show_if_value,
                }]
            self.fields["show_if_conditions"].initial = conditions

    def save(self, commit=True):
        obj = super().save(commit=False)
        conds = list(self.cleaned_data.get("show_if_conditions") or [])
        obj.show_if_conditions = conds
        obj.show_if_question = None
        obj.show_if_value = ""
        obj.show_if_question_2 = None
        obj.show_if_value_2 = ""
        if conds:
            obj.show_if_question_id = int(conds[0].get("question_id") or 0) or None
            obj.show_if_value = conds[0].get("value", "")
        if len(conds) > 1:
            obj.show_if_question_2_id = int(conds[1].get("question_id") or 0) or None
            obj.show_if_value_2 = conds[1].get("value", "")

        if commit:
            obj.save()
            self.save_m2m()
        return obj


class SectionInline(admin.StackedInline):
    model = Section
    extra = 0
    form = SectionAdminForm
    fields = (
        "position",
        "title",
        "description",
        "show_if_conditions",
    )
    ordering = ("position", "id")


class StoredEmailInline(admin.StackedInline):
    model = StoredEmailTemplate
    extra = 0
    fields = ("position", "name", "subject", "body")
    ordering = ("position", "id")
    classes = ("collapse",)
    verbose_name = "Stored email"
    verbose_name_plural = "Stored emails"


class FormDefinitionAdminForm(forms.ModelForm):
    """Expose the normal-completion email as a named stored-email dropdown."""

    class Meta:
        model = FormDefinition
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        form_obj = self.instance if getattr(self.instance, "pk", None) else None
        names = (
            list(form_obj.stored_emails.order_by("position", "id").values_list("name", flat=True))
            if form_obj
            else []
        )
        current = ""
        if self.is_bound:
            current = str(self.data.get(self.add_prefix("approval_email_name")) or "").strip()
        elif form_obj:
            current = str(form_obj.approval_email_name or "").strip()
        if current and current not in names:
            names.append(current)
        self.fields["approval_email_name"] = forms.ChoiceField(
            required=False,
            label="Default approval email",
            help_text=(
                "Sent after Send when no answer ends the application early. "
                "Create and name the message under Stored emails first."
            ),
            choices=[("", "— Do not send an approval email —")] + [(name, name) for name in names],
        )

        self.fields["thanks_approved_title"].label = "Page title"
        self.fields["thanks_approved_message"].label = "Page message"
        self.fields["thanks_approved_message"].help_text = (
            "Shown after Send when no answer ends the application early. Line breaks are preserved."
        )
        self.fields["thanks_rejected_title"].label = "Page title"
        self.fields["thanks_rejected_message"].label = "Page message"
        self.fields["thanks_rejected_message"].help_text = (
            "Shared by every answer rule that ends this application. Line breaks are preserved."
        )


class QuestionInlineFormSet(BaseInlineFormSet):
    """Resolve duplicate auto-generated slugs across questions added in one save."""

    @staticmethod
    def _remove_condition_reference(instance, question_id):
        conditions = list(getattr(instance, "show_if_conditions", []) or [])
        filtered = [
            condition for condition in conditions
            if str(condition.get("question_id") or "") != str(question_id)
        ]
        if filtered != conditions:
            instance.show_if_conditions = filtered
            instance.save(update_fields=["show_if_conditions"])

    def delete_existing(self, obj, commit=True):
        if commit:
            # Delete dependent rows explicitly before the question. Django's
            # collector normally cascades these, but an older production FK can
            # otherwise turn an inline delete into a database-level 500.
            obj.answer_set.all().delete()
            obj.choices.all().delete()

            # JSON conditions aren't database foreign keys, so remove stale
            # references before deleting their controlling question.
            for question in Question.objects.exclude(show_if_conditions=[]).iterator():
                if question.pk != obj.pk:
                    self._remove_condition_reference(question, obj.pk)
            for section in Section.objects.exclude(show_if_conditions=[]).iterator():
                self._remove_condition_reference(section, obj.pk)

        super().delete_existing(obj, commit=commit)

    def clean(self):
        submitted_ids = {
            form.instance.pk for form in self.forms if getattr(form.instance, "pk", None)
        }
        used = (
            set(self.instance.questions.exclude(pk__in=submitted_ids).values_list("slug", flat=True))
            if getattr(self.instance, "pk", None)
            else set()
        )
        max_length = Question._meta.get_field("slug").max_length

        for form in self.forms:
            cleaned_data = getattr(form, "cleaned_data", None)
            if not cleaned_data or cleaned_data.get("DELETE"):
                continue
            slug = (cleaned_data.get("slug") or "").strip()
            generated_base = getattr(form, "_generated_slug_base", "")
            if generated_base:
                slug = _available_question_slug(generated_base, used, max_length)
                cleaned_data["slug"] = slug
                form.instance.slug = slug
            if slug:
                used.add(slug)

        super().clean()


class QuestionInline(admin.StackedInline):
    model = Question
    form = QuestionAdminForm  # ✅ IMPORTANT: make inline use the custom form
    formset = QuestionInlineFormSet
    extra = 0
    show_change_link = False
    ordering = ("position", "id")

    # ✅ Show the new admin-friendly fields instead of raw help_text
    fields = (
        "position",
        "active",
        "slug",
        "text",
        "field_type",
        "grid_rows",
        "answer_options",
        "help_text_clean",
        "section_token",
        "required",
        "show_if_conditions",
        "end_form_rules",
        "confirm_value",
        "pre_hr",
        "pre_text",
    )


# ---------- FormDefinition admin ----------
@admin.register(FormDefinition)
class FormDefinitionAdmin(admin.ModelAdmin):
    """
    Admin for form definitions (E_A1, E_A2, M_A1, M_A2, plus surveys like PRIMER_E, etc.)

    Adds:
      - Preview button (existing)
      - Survey (public) button: /survey/<slug>/
      - Survey data button: Admin Application changelist filtered by this form
      - Inline edit of questions
    """
    list_display = ("__str__", "slug", "submission_count", "preview_link", "survey_public_link", "survey_data_link")
    search_fields = ("slug", "name")
    form = FormDefinitionAdminForm
    readonly_fields = ("preview_link", "survey_public_link", "survey_data_link")
    base_fields = (
        "slug",
        "name",
        "description",
        "is_master",
        "group",
        "is_public",
        "accepting_responses",
        "default_section_title",
    )
    thanks_fields = (
        "thanks_approved_title",
        "thanks_approved_message",
        "thanks_rejected_title",
        "thanks_rejected_message",
    )
    a1_email_fields = (
        "email_a1_rejected_subject",
        "email_a1_rejected_body",
    )
    a2_email_fields = (
        "email_a2_received_subject",
        "email_a2_received_body",
        "email_a2_rejected_subject",
        "email_a2_rejected_body",
        "email_a2_final_reminder_subject",
        "email_a2_final_reminder_body",
    )
    link_fields = (
        "preview_link",
        "survey_public_link",
        "survey_data_link",
    )

    def get_fields(self, request, obj=None):
        fields = list(self.base_fields + self.thanks_fields)
        slug = ((getattr(obj, "slug", "") or "").strip().upper()) if obj else ""

        if slug.endswith("E_A1") or slug.endswith("M_A1"):
            fields += list(self.a1_email_fields)
        elif slug.endswith("E_A2") or slug.endswith("M_A2"):
            fields += list(self.a2_email_fields)
        else:
            fields += list(self.a1_email_fields)
            fields += list(self.a2_email_fields)

        fields += list(self.link_fields)
        return fields

    def get_fieldsets(self, request, obj=None):
        fields = self.get_fields(request, obj)
        basics = [
            name for name in (
                "name",
                "description",
                "accepting_responses",
            ) if name in fields
        ]
        settings = [
            name for name in (
                "slug",
                "group",
                "is_public",
                "is_master",
                "default_section_title",
            ) if name in fields
        ]
        links = [name for name in self.link_fields if name in fields]
        return (
            (None, {"fields": basics}),
            ("Approval page", {
                "fields": ("thanks_approved_title", "thanks_approved_message"),
                "classes": ("collapse",),
                "description": "Default page shown after a completed application is sent.",
            }),
            ("Approval email", {
                "fields": ("approval_email_name",),
                "classes": ("collapse",),
                "description": "Default stored email sent when no end-application answer is triggered.",
            }),
            ("Rejection page", {
                "fields": ("thanks_rejected_title", "thanks_rejected_message"),
                "classes": ("collapse",),
                "description": (
                    "Shared final page used whenever a question's answer ends the application."
                ),
            }),
            ("Form settings", {"fields": settings, "classes": ("collapse",)}),
            ("Preview and responses", {"fields": links, "classes": ("collapse",)}),
        )

    def _should_follow_default(self, request):
        return any(
            key in request.POST
            for key in ("_continue", "_addanother", "_popup")
        )

    def response_change(self, request, obj):
        if self._should_follow_default(request):
            return super().response_change(request, obj)
        return HttpResponseRedirect(reverse("admin_apps_list"))

    def response_add(self, request, obj, post_url_continue=None):
        if self._should_follow_default(request):
            return super().response_add(request, obj, post_url_continue)
        return HttpResponseRedirect(reverse("admin_apps_list"))
    inlines = [StoredEmailInline, SectionInline, QuestionInline]
    change_form_template = "admin/applications/formdefinition/change_form.html"

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)

        section_formset = next((fs for fs in formsets if fs.model is Section), None)
        question_formset = next((fs for fs in formsets if fs.model is Question), None)
        if not section_formset or not question_formset:
            return

        section_by_token = {}
        for section_form in section_formset.forms:
            if not getattr(section_form, "cleaned_data", None) or section_form.cleaned_data.get("DELETE"):
                continue
            section = section_form.instance
            if not section.pk:
                continue
            section_by_token[section_form.prefix] = section
            section_by_token[f"id:{section.pk}"] = section

        for question_form in question_formset.forms:
            if not getattr(question_form, "cleaned_data", None) or question_form.cleaned_data.get("DELETE"):
                continue
            question = question_form.instance
            if not question.pk:
                continue
            token = (question_form.cleaned_data.get("section_token") or "").strip()
            section = section_by_token.get(token)
            section_id = section.pk if section else None
            if question.section_id != section_id:
                Question.objects.filter(pk=question.pk).update(section_id=section_id)
                question.section_id = section_id
    def submission_count(self, obj):
        return obj.applications.count()
    submission_count.short_description = "Submissions"

    def preview_link(self, obj):
        slug = getattr(obj, "slug", None)
        if not slug:
            return "-"

        try:
            url = reverse("apply_by_slug", kwargs={"form_slug": slug})
        except Exception:
            return "-"

        url = f"{url}?preview=1"
        return format_html('<a href="{}" target="_blank" class="button">Preview</a>', url)

    preview_link.short_description = "Preview"

    def survey_public_link(self, obj):
        """
        Only show this if it's a survey slug you actually route via /survey/<slug>/
        (If you want it for all, remove the if.)
        """
        if not obj.slug:
            return "-"

        if obj.slug.upper() not in {"PRIMER_E", "PRIMER_M", "FINAL_E", "FINAL_M"}:
            return "-"

        url = reverse("survey_by_slug", kwargs={"form_slug": obj.slug})
        return format_html('<a href="{}" target="_blank" class="button">Open Survey</a>', url)

    survey_public_link.short_description = "Survey (public)"

    def survey_data_link(self, obj):
        """
        Link to the admin Applications list filtered by this form (survey submissions).
        """
        if not obj.pk:
            return "-"

        app_list_url = reverse("admin:applications_application_changelist")
        url = f"{app_list_url}?form__id__exact={obj.pk}"
        return format_html('<a href="{}" class="button">Survey data</a>', url)

    survey_data_link.short_description = "Survey data"


# ---------- Question admin ----------
@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    form = QuestionAdminForm  # ✅ IMPORTANT: also applies to direct Question editing
    list_display = ("id", "__str__", "form", "field_type", "active", "required", "position")
    list_filter = ("active", "required", "field_type", "form")
    search_fields = ("id", "slug", "text", "help_text")
    ordering = ("form", "position", "id")

    # ✅ Show the friendly fields here too
    fields = (
        "form",
        "position",
        "active",
        "slug",
        "text",
        "field_type",
        "grid_rows",
        "answer_options",
        "help_text_clean",
        "section",
        "required",
        "show_if_conditions",
        "end_form_rules",
        "confirm_value",
        "pre_hr",
        "pre_text",
    )

    def has_module_permission(self, request):
        # Questions are edited directly inside their form cards.
        return False

    def _follow_default(self, request):
        return any(
            key in request.POST
            for key in ("_continue", "_addanother", "_popup")
        )

    def _redirect_to_form(self, obj):
        if getattr(obj, "form_id", None):
            return HttpResponseRedirect(
                reverse("admin:applications_formdefinition_change", args=[obj.form_id])
            )
        return HttpResponseRedirect(reverse("admin:applications_question_changelist"))

    def response_change(self, request, obj):
        if self._follow_default(request):
            return super().response_change(request, obj)
        return self._redirect_to_form(obj)

    def response_add(self, request, obj, post_url_continue=None):
        if self._follow_default(request):
            return super().response_add(request, obj, post_url_continue)
        return self._redirect_to_form(obj)


# ---------- Choice admin ----------
@admin.register(Choice)
class ChoiceAdmin(admin.ModelAdmin):
    list_display = ("id", "__str__", "question", "position")
    list_filter = ("question__form",)
    search_fields = ("id", "label", "value")
    ordering = ("question", "position", "id")

    def has_module_permission(self, request):
        # Options are edited inline in the owning question card.
        return False


# ---------- Application admin ----------
@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("id", "form", "name", "email", "created_at")
    list_filter = ("form",)
    search_fields = ("id", "name", "email")
    ordering = ("-created_at",)


@admin.register(TaskType)
class TaskTypeAdmin(admin.ModelAdmin):
    form = TaskTypeAdminForm
    list_display = ("name", "slug", "position", "is_active", "is_revision_type")
    list_filter = ("is_active", "is_revision_type")
    search_fields = ("name", "slug")
    ordering = ("position", "name", "id")


@admin.register(ParticipantEmailStatus)
class ParticipantEmailStatusAdmin(admin.ModelAdmin):
    list_display = (
        "email",
        "participated",
        "contract_signed",
        "contract_signed_at",
        "contract_source",
        "updated_at",
    )
    list_filter = ("participated", "contract_signed", "contract_source")
    search_fields = ("email", "contract_signature_request_id")
    ordering = ("email",)


@admin.register(DropboxSignWebhookEvent)
class DropboxSignWebhookEventAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "event_type",
        "signature_request_id",
        "hash_verified",
        "processed",
        "marked_count",
        "created_at",
    )
    list_filter = ("event_type", "hash_verified", "processed")
    search_fields = ("signature_request_id", "signer_emails_text", "payload_digest")
    readonly_fields = (
        "event_type",
        "event_time",
        "event_hash",
        "signature_request_id",
        "signer_emails_text",
        "payload_json",
        "payload_digest",
        "hash_verified",
        "processed",
        "marked_count",
        "process_note",
        "created_at",
    )
    ordering = ("-created_at", "-id")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(GroupParticipantList)
class GroupParticipantListAdmin(admin.ModelAdmin):
    list_display = ("group", "updated_at", "created_at")
    search_fields = ("group__number",)
    ordering = ("-group__number",)


@admin.register(ParticipantSheetVersion)
class ParticipantSheetVersionAdmin(admin.ModelAdmin):
    list_display = ("group", "track", "row_count", "action", "saved_by", "created_at")
    list_filter = ("track", "action", "created_at")
    search_fields = ("group__number", "saved_by__email")
    ordering = ("-created_at", "-id")
    readonly_fields = ("created_at",)


class FixedCriterionTypeInlineFormSet(BaseInlineFormSet):
    criterion_type = GradingCriterion.TYPE_STRUCTURED

    def save_new(self, form, commit=True):
        obj = super().save_new(form, commit=False)
        obj.criterion_type = self.criterion_type
        if commit:
            obj.save()
            form.save_m2m()
        return obj

    def save_existing(self, form, instance, commit=True):
        instance.criterion_type = self.criterion_type
        return super().save_existing(form, instance, commit=commit)


class StructuredCriterionInlineFormSet(FixedCriterionTypeInlineFormSet):
    criterion_type = GradingCriterion.TYPE_STRUCTURED


class ParagraphCriterionInlineFormSet(FixedCriterionTypeInlineFormSet):
    criterion_type = GradingCriterion.TYPE_AI_TEXT


class StructuredGradingCriterionInline(admin.TabularInline):
    model = GradingCriterion
    formset = StructuredCriterionInlineFormSet
    extra = 0
    verbose_name = "Structured criterion"
    verbose_name_plural = "Structured criteria / numeric weights"
    fields = (
        "position",
        "active",
        "question_slug",
        "label",
        "weight",
        "negative_allowed",
    )

    def get_queryset(self, request):
        return super().get_queryset(request).filter(criterion_type=GradingCriterion.TYPE_STRUCTURED)


class ParagraphGradingCriterionInline(admin.TabularInline):
    model = GradingCriterion
    formset = ParagraphCriterionInlineFormSet
    extra = 0
    verbose_name = "Paragraph AI criterion"
    verbose_name_plural = "Paragraph AI criteria / prompts"
    fields = (
        "position",
        "active",
        "question_slug",
        "label",
        "weight",
        "negative_allowed",
        "prompt",
    )

    def get_queryset(self, request):
        return super().get_queryset(request).filter(criterion_type=GradingCriterion.TYPE_AI_TEXT)


class GradingResponseWeightInline(admin.TabularInline):
    model = GradingResponseWeight
    extra = 0
    verbose_name = "Dropdown response weight"
    verbose_name_plural = "Dropdown / checkbox response weights"
    fields = ("position", "active", "question", "choice", "weight")

    def get_formset(self, request, obj=None, **kwargs):
        request._grading_config_parent = obj
        return super().get_formset(request, obj, **kwargs)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        config = getattr(request, "_grading_config_parent", None)
        form = getattr(config, "form", None)
        if form and db_field.name == "question":
            kwargs["queryset"] = Question.objects.filter(
                form=form,
                field_type__in=[Question.CHOICE, Question.MULTI_CHOICE, Question.MULTIPLE_CHOICE_GRID],
            ).order_by("position", "id")
        elif form and db_field.name == "choice":
            kwargs["queryset"] = Choice.objects.filter(
                question__form=form,
                question__field_type__in=[Question.CHOICE, Question.MULTI_CHOICE, Question.MULTIPLE_CHOICE_GRID],
            ).select_related("question").order_by("question__position", "position", "id")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(ApplicationGradingConfig)
class ApplicationGradingConfigAdmin(admin.ModelAdmin):
    list_display = ("form", "model_name", "max_total_score", "updated_at")
    search_fields = ("form__slug", "form__name")
    autocomplete_fields = ("form",)
    inlines = (StructuredGradingCriterionInline, ParagraphGradingCriterionInline, GradingResponseWeightInline)
    fieldsets = (
        (None, {
            "fields": ("form", "model_name", "max_total_score", "rubric_note")
        }),
        ("Editor notes", {
            "fields": (),
            "description": (
                "Prompts are only available in Paragraph AI criteria. "
                "Dropdown and checkbox scoring is controlled in Dropdown / checkbox response weights, "
                "where each row points to one predefined choice."
            ),
        }),
    )


class PairingPriorityRuleInline(admin.TabularInline):
    model = PairingPriorityRule
    extra = 0
    fields = (
        "position",
        "active",
        "required",
        "label",
        "comparison_type",
        "emprendedora_question_slug",
        "mentora_question_slug",
        "weight",
        "output_key",
    )


class PairingAIComparisonInline(admin.TabularInline):
    model = PairingAIComparison
    extra = 0
    fields = (
        "position",
        "active",
        "label",
        "emprendedora_question_slug",
        "mentora_question_slug",
        "weight",
        "output_key",
        "prompt",
    )


@admin.register(PairingConfig)
class PairingConfigAdmin(admin.ModelAdmin):
    list_display = ("group", "top_k_for_ai", "availability_required", "model_name", "updated_at")
    search_fields = ("group__number", "group__custom_name")
    raw_id_fields = ("group",)
    inlines = (PairingPriorityRuleInline, PairingAIComparisonInline)
    fieldsets = (
        (None, {
            "fields": ("group", "model_name", "top_k_for_ai", "availability_required")
        }),
        ("AI prompt placeholders", {
            "fields": (),
            "description": (
                "For AI comparisons, prompts can use {{ label }}, {{ mentor_text }}, "
                "and {{ entrepreneur_text }}. Keep output format as lines beginning "
                "with Score: and Reasoning:."
            ),
        }),
    )
