# applications/admin.py

from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from .models import FormDefinition, Question, Choice, Application


# =========================
# Inlines
# =========================

class ChoiceInline(admin.TabularInline):
    """
    Lets you edit Choices directly inside the Question edit page.
    This is what you need to edit multiple-choice options.
    """
    model = Choice
    extra = 2
    fields = ("position", "label", "value")
    ordering = ("position", "id")


class FormQuestionInline(admin.TabularInline):
    """
    Lets you manage which Questions belong to a FormDefinition.
    This assumes FormDefinition has a ManyToMany to Question:
        FormDefinition.questions = models.ManyToManyField(Question, ...)
    Django automatically creates a through table, so we can inline it.

    If your model is instead a ForeignKey from Question -> FormDefinition,
    tell me and I’ll adjust, but your code (fd.questions.get(...)) strongly
    suggests ManyToMany.
    """
    model = FormDefinition.questions.through  # auto-created through model
    extra = 1
    autocomplete_fields = ("question",)

    # show a nicer label
    verbose_name = "Question in this form"
    verbose_name_plural = "Questions in this form"


# =========================
# FormDefinition admin
# =========================

@admin.register(FormDefinition)
class FormDefinitionAdmin(admin.ModelAdmin):
    """
    Admin for the form definitions (E_A1, E_A2, M_A1, M_A2, G6_... clones, etc.)
    Includes:
      - Preview button
      - Inline list of questions that belong to the form
    """
    list_display = ("__str__", "slug", "preview_link")
    search_fields = ("slug", "name")
    readonly_fields = ("preview_link",)
    inlines = (FormQuestionInline,)

    def preview_link(self, obj):
        slug = getattr(obj, "slug", None)
        if not slug:
            return "-"

        # Your existing preview routing:
        if slug == "E_A1":
            url_name = "apply_emprendedora_first"
        elif slug == "E_A2":
            url_name = "preview_emprendedora_second"
        elif slug == "M_A1":
            url_name = "apply_mentora_first"
        elif slug == "M_A2":
            url_name = "preview_mentora_second"
        else:
            # For group forms (G6_E_A1, etc), use apply_by_slug
            # if you want preview for all:
            try:
                url = reverse("apply_by_slug", kwargs={"form_slug": slug})
                return format_html(
                    '<a href="{}" target="_blank" class="button">Preview</a>',
                    url,
                )
            except Exception:
                return "-"

        try:
            url = reverse(url_name)
        except Exception:
            return "-"

        return format_html(
            '<a href="{}" target="_blank" class="button">Preview</a>',
            url,
        )

    preview_link.short_description = "Preview"


# =========================
# Question admin
# =========================

@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    """
    This is the main upgrade:
    - Edit question text/type/etc
    - AND edit choices inline on the same page (ChoiceInline)
    """
    list_display = ("id", "slug", "field_type", "required", "active", "position")
    list_filter = ("active", "field_type", "required")
    search_fields = ("id", "slug", "text")
    ordering = ("position", "id")

    fields = (
        "slug",
        "text",
        "help_text",
        "field_type",
        "required",
        "active",
        "position",
    )

    inlines = (ChoiceInline,)


# =========================
# Choice admin (optional to keep)
# =========================

@admin.register(Choice)
class ChoiceAdmin(admin.ModelAdmin):
    list_display = ("id", "question", "label", "value", "position")
    search_fields = ("id", "label", "value", "question__slug")
    list_filter = ("question",)
    ordering = ("question", "position", "id")


# =========================
# Application admin
# =========================

@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("id", "form", "name", "email", "created_at")
    list_filter = ("form",)
    search_fields = ("id", "name", "email")
    ordering = ("-created_at",)
