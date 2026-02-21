# applications/admin.py
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
import re
from django import forms
import json
from django.http import HttpResponseRedirect

from .models import FormDefinition, Question, Choice, Application, Section


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


# =========================
# CUSTOM ADMIN FORM FOR QUESTION
# =========================
class QuestionAdminForm(forms.ModelForm):
    class Media:
        js = ("applications/js/admin_show_if_value.js",)

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
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Parse existing help_text into admin-friendly pieces
        pre_text, pre_hr, rest = _split_help_text(getattr(self.instance, "help_text", "") or "")
        self.fields["pre_text"].initial = pre_text
        self.fields["pre_hr"].initial = pre_hr
        self.fields["help_text_clean"].initial = rest

        form_obj = getattr(self.instance, "form", None)
        qs = Section.objects.none()
        show_if_qs = Question.objects.none()
        if form_obj:
            qs = form_obj.sections.all()
            show_if_qs = (
                form_obj.questions
                .exclude(id=getattr(self.instance, "id", None))
                .prefetch_related("choices")
            )
        self.fields["section"].queryset = qs
        self.fields["section"].initial = getattr(self.instance, "section_id", None)

        # ----- Multi controlling questions (OR) -----
        questions_json = json.dumps(
            [
                {
                    "id": q.id,
                    "text": q.text or q.slug,
                    "field_type": q.field_type,
                    "choices": [
                        {"value": c.value, "label": c.label or c.value}
                        for c in q.choices.all()
                    ] if q.field_type in (Question.CHOICE, Question.MULTI_CHOICE) else
                    [{"value": "yes", "label": "Sí / Yes"}, {"value": "no", "label": "No"}] if q.field_type == Question.BOOLEAN else [],
                }
                for q in show_if_qs
            ]
        )

        self.fields["show_if_conditions"] = forms.JSONField(
            required=False,
            widget=ShowIfConditionsWidget(questions_json=questions_json),
            label="Show if conditions (ANY match)",
            help_text="Add one or more controlling questions + expected values. This question shows if ANY condition matches.",
        )

        # initial conditions from stored JSON or legacy single
        if not self.data:
            conds = list(getattr(self.instance, "show_if_conditions", []) or [])
            if not conds and getattr(self.instance, "show_if_question_id", None) and self.instance.show_if_value:
                conds.append({"question_id": self.instance.show_if_question_id, "value": self.instance.show_if_value})
            self.fields["show_if_conditions"].initial = conds

        # ----- Legacy single fields (visible for clarity, mirrored to first condition) -----
        self.fields["show_if_question"].queryset = show_if_qs
        self.fields["show_if_question"].label = "Controlling question (first condition)"

        # Build value choices map
        choice_map: dict[str, list[tuple[str, str]]] = {}
        for q in show_if_qs:
            opts: list[tuple[str, str]] = []
            if q.field_type == Question.BOOLEAN:
                opts = [("yes", "Sí / Yes"), ("no", "No")]
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

        opts = [("", "— Selecciona valor —")]
        opts += choice_map.get(sel_qid, [])
        if current_val and current_val not in [v for v, _ in opts]:
            opts.append((current_val, f"{current_val} (actual)"))

        self.fields["show_if_value"] = forms.ChoiceField(
            required=False,
            label="Triggering answer (first condition)",
            choices=opts,
        )

    def save(self, commit=True):
        obj = super().save(commit=False)

        pre_text = self.cleaned_data.get("pre_text", "")
        pre_hr = bool(self.cleaned_data.get("pre_hr"))
        rest = self.cleaned_data.get("help_text_clean", "")
        section = self.cleaned_data.get("section")

        obj.help_text = _pack_help_text(pre_text, pre_hr, rest)
        obj.section = section

        # Sync multi-conditions back to model + legacy fields (first condition)
        conds = self.cleaned_data.get("show_if_conditions") or []
        obj.show_if_conditions = conds
        obj.show_if_question = None
        obj.show_if_value = ""
        if conds:
            try:
                obj.show_if_question_id = int(conds[0].get("question_id") or 0) or None
                obj.show_if_value = conds[0].get("value", "")
            except Exception:
                pass

        if commit:
            obj.save()
            self.save_m2m()
        return obj


# ---------- Inlines so you can edit questions + choices inside a form ----------
class ChoiceInline(admin.TabularInline):
    model = Choice
    extra = 0
    fields = ("position", "label", "value")
    ordering = ("position", "id")


class ShowIfConditionsWidget(forms.Widget):
    template_name = "admin/widgets/show_if_conditions.html"

    def __init__(self, *args, **kwargs):
        self.questions_json = kwargs.pop("questions_json", "[]")
        super().__init__(*args, **kwargs)

    def get_context(self, name, value, attrs):
        ctx = super().get_context(name, value, attrs)
        ctx["widget"]["questions_json"] = self.questions_json
        if isinstance(value, str):
            ctx["widget"]["value_json"] = value
        else:
            ctx["widget"]["value_json"] = json.dumps(value or [])
        return ctx


class SectionAdminForm(forms.ModelForm):
    class Meta:
        model = Section
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        form_obj = getattr(self.instance, "form", None)
        if form_obj:
            qs = form_obj.questions.all()
        else:
            qs = Question.objects.none()

        self.fields["show_if_question"].queryset = qs
        # remove secondary condition fields from the form (we only want one)
        self.fields.pop("show_if_question_2", None)
        self.fields.pop("show_if_value_2", None)

        def _choices_for(q: Question | None):
            if not q:
                return [("", "— Selecciona valor —")]
            if q.field_type == Question.BOOLEAN:
                return [("", "— Selecciona valor —"), ("yes", "Sí"), ("no", "No")]
            if q.field_type in (Question.CHOICE, Question.MULTI_CHOICE):
                opts = [("", "— Selecciona valor —")]
                opts += [(c.value, c.label or c.value) for c in q.choices.all()]
                return opts
            return [("", "— Selecciona valor —")]

        q1 = None
        if self.data.get("show_if_question"):
            try:
                q1 = qs.get(id=self.data.get("show_if_question"))
            except Exception:
                pass
        elif getattr(self.instance, "show_if_question_id", None):
            q1 = self.instance.show_if_question

        self.fields["show_if_value"].widget = forms.Select(choices=_choices_for(q1))

    def save(self, commit=True):
        obj = super().save(commit=False)
        # keep JSON field in sync for compatibility (optional)
        conds = []
        if obj.show_if_question_id and obj.show_if_value:
            conds.append({"question_id": obj.show_if_question_id, "value": obj.show_if_value})
        # clear secondary fields
        obj.show_if_question_2 = None
        obj.show_if_value_2 = ""
        obj.show_if_conditions = conds

        if commit:
            obj.save()
            self.save_m2m()
        return obj


class SectionInline(admin.TabularInline):
    model = Section
    extra = 0
    form = SectionAdminForm
    fields = (
        "position",
        "title",
        "description",
        "show_if_question",
        "show_if_value",
    )
    ordering = ("position", "id")


class QuestionInline(admin.StackedInline):
    model = Question
    form = QuestionAdminForm  # ✅ IMPORTANT: make inline use the custom form
    extra = 0
    show_change_link = True
    ordering = ("position", "id")

    # ✅ Show the new admin-friendly fields instead of raw help_text
    fields = (
        "position",
        "active",
        "required",
        "slug",
        "text",
        "section",
        "show_if_question",
        "show_if_value",
        "confirm_value",
        "pre_hr",
        "pre_text",
        "help_text_clean",
        "field_type",
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
    readonly_fields = ("preview_link", "survey_public_link", "survey_data_link")
    fields = (
        "slug",
        "name",
        "description",
        "is_master",
        "group",
        "is_public",
        "accepting_responses",
        "default_section_title",
        "preview_link",
        "survey_public_link",
        "survey_data_link",
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
    inlines = [SectionInline, QuestionInline]
    def submission_count(self, obj):
        return obj.applications.count()
    submission_count.short_description = "Submissions"

    def preview_link(self, obj):
        slug = getattr(obj, "slug", None)
        if not slug:
            return "-"

        if slug == "E_A1":
            url_name = "apply_emprendedora_first"
        elif slug == "E_A2":
            url_name = "preview_emprendedora_second"
        elif slug == "M_A1":
            url_name = "apply_mentora_first"
        elif slug == "M_A2":
            url_name = "preview_mentora_second"
        else:
            return "-"

        try:
            url = reverse(url_name)
        except Exception:
            return "-"

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
    inlines = [ChoiceInline]

    # ✅ Show the friendly fields here too
    fields = (
        "form",
        "position",
        "active",
        "required",
        "slug",
        "text",
        "section",
        "show_if_conditions",
        "confirm_value",
        "pre_hr",
        "pre_text",
        "help_text_clean",
        "field_type",
    )

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


# ---------- Application admin ----------
@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("id", "form", "name", "email", "created_at")
    list_filter = ("form",)
    search_fields = ("id", "name", "email")
    ordering = ("-created_at",)
