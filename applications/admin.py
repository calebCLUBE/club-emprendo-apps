# applications/admin.py
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
import re
from django import forms

from .models import FormDefinition, Question, Choice, Application


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

    def save(self, commit=True):
        obj = super().save(commit=False)

        pre_text = self.cleaned_data.get("pre_text", "")
        pre_hr = bool(self.cleaned_data.get("pre_hr"))
        rest = self.cleaned_data.get("help_text_clean", "")

        obj.help_text = _pack_help_text(pre_text, pre_hr, rest)

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
    list_display = ("__str__", "slug", "preview_link", "survey_public_link", "survey_data_link")
    search_fields = ("slug", "name")
    readonly_fields = ("preview_link", "survey_public_link", "survey_data_link")
    inlines = [QuestionInline]

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
        "pre_hr",
        "pre_text",
        "help_text_clean",
        "field_type",
    )


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
