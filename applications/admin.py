# applications/admin.py
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from .models import FormDefinition, Question, Choice, Application


# ---------- Inlines so you can edit questions + choices inside a form ----------
class ChoiceInline(admin.TabularInline):
    model = Choice
    extra = 0
    fields = ("position", "label", "value")
    ordering = ("position", "id")


class QuestionInline(admin.StackedInline):
    model = Question
    extra = 0
    show_change_link = True
    fields = (
        "position",
        "active",
        "required",
        "slug",
        "text",
        "help_text",
        "field_type",
    )
    ordering = ("position", "id")


# ---------- FormDefinition admin ----------
@admin.register(FormDefinition)
class FormDefinitionAdmin(admin.ModelAdmin):
    """
    Admin for form definitions (E_A1, E_A2, M_A1, M_A2, plus surveys like PRIMER_E, etc.)

    Adds:
      - Preview button (existing)
      - Survey (public) button: /survey/<slug>/
      - Survey data button: Admin Application changelist filtered by this form
      - Inline edit of questions (and choices from QuestionAdmin)
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

        # If you only want this for surveys, keep this check:
        # surveys commonly like PRIMER_E, PRIMER_M, FINAL_E, FINAL_M
        if obj.slug.upper() not in {"PRIMER_E", "PRIMER_M", "FINAL_E", "FINAL_M"}:
            # If you'd rather allow ANY slug to open /survey/<slug>/, just delete this block.
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
        # Filter by form id
        url = f"{app_list_url}?form__id__exact={obj.pk}"
        return format_html('<a href="{}" class="button">Survey data</a>', url)

    survey_data_link.short_description = "Survey data"


# ---------- Question admin ----------
@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("id", "__str__", "form", "field_type", "active", "required", "position")
    list_filter = ("active", "required", "field_type", "form")
    search_fields = ("id", "slug", "text", "help_text")
    ordering = ("form", "position", "id")
    inlines = [ChoiceInline]


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
