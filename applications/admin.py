# applications/admin.py

from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from .models import FormDefinition, Question, Choice, Application


# -----------------------------
# Inlines (edit questions/choices inside a form)
# -----------------------------

class ChoiceInline(admin.TabularInline):
    model = Choice
    extra = 0
    fields = ("label", "value", "position")
    ordering = ("position", "id")


class QuestionInline(admin.StackedInline):
    model = Question
    extra = 0
    fields = (
        "text",
        "help_text",
        "field_type",
        "required",
        "position",
        "slug",
        "active",
    )
    ordering = ("position", "id")
    show_change_link = True


# ---------- FormDefinition admin (with Preview button + Question editing) ----------

@admin.register(FormDefinition)
class FormDefinitionAdmin(admin.ModelAdmin):
    """
    Admin for forms (E_A1, E_A2, M_A1, M_A2 and group clones).
    Adds inline editing so you can add/edit/remove Questions (and then edit Choices).
    """
    list_display = ("__str__", "slug", "preview_link")
    search_fields = ("slug", "name")
    readonly_fields = ("preview_link",)
    inlines = [QuestionInline]  # ✅ add/edit/remove questions directly on the form page

    def preview_link(self, obj):
        slug = getattr(obj, "slug", None)
        if not slug:
            return "-"

        # Master slugs route to named views
        if slug == "E_A1":
            url_name = "apply_emprendedora_first"
            try:
                url = reverse(url_name)
                return format_html('<a href="{}" target="_blank" class="button">Preview</a>', url)
            except Exception:
                return "-"

        if slug == "E_A2":
            url_name = "preview_emprendedora_second"
            try:
                url = reverse(url_name)
                return format_html('<a href="{}" target="_blank" class="button">Preview</a>', url)
            except Exception:
                return "-"

        if slug == "M_A1":
            url_name = "apply_mentora_first"
            try:
                url = reverse(url_name)
                return format_html('<a href="{}" target="_blank" class="button">Preview</a>', url)
            except Exception:
                return "-"

        if slug == "M_A2":
            url_name = "preview_mentora_second"
            try:
                url = reverse(url_name)
                return format_html('<a href="{}" target="_blank" class="button">Preview</a>', url)
            except Exception:
                return "-"

        # Group clones (G#_E_A1, G#_M_A2, etc.) route through apply_by_slug
        try:
            url = reverse("apply_by_slug", kwargs={"form_slug": slug})
            return format_html('<a href="{}" target="_blank" class="button">Preview</a>', url)
        except Exception:
            return "-"

    preview_link.short_description = "Preview"


# ---------- Question / Choice / Application admins ----------

@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("id", "form", "slug", "position", "active")
    list_filter = ("active", "form")
    search_fields = ("id", "slug", "text", "form__slug")
    ordering = ("form__slug", "position", "id")
    inlines = [ChoiceInline]  # ✅ add/edit/remove choices on the question page


@admin.register(Choice)
class ChoiceAdmin(admin.ModelAdmin):
    list_display = ("id", "question", "label", "value", "position")
    search_fields = ("id", "label", "value", "question__slug", "question__form__slug")
    ordering = ("question__form__slug", "question__slug", "position", "id")


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("id", "form", "name", "email", "created_at")
    list_filter = ("form",)
    search_fields = ("id", "name", "email", "form__slug")
    ordering = ("-created_at",)
