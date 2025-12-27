from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse

from .models import FormDefinition, Question, Choice, Application


# ---------- FormDefinition admin (with Preview button) ----------

@admin.register(FormDefinition)
class FormDefinitionAdmin(admin.ModelAdmin):
    """
    Admin for the four form definitions: E_A1, E_A2, M_A1, M_A2.
    Shows a Preview button that opens the public form in a new tab.
    """
    list_display = ("__str__", "slug", "preview_link")
    search_fields = ("slug", "name")
    readonly_fields = ("preview_link",)  # also show in the detail page

    def preview_link(self, obj):
        """
        Map each slug to the correct public/preview URL.
        """
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

        return format_html(
            '<a href="{}" target="_blank" '
            'style="padding:4px 10px; '
            'background:#163108; color:white; '
            'border-radius:6px; text-decoration:none; '
            'font-size:12px; font-weight:600;">'
            'Preview</a>',
            url,
        )

    preview_link.short_description = "Preview"


# ---------- Very simple admin for the other models ----------

@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("id", "__str__")
    search_fields = ("id",)


@admin.register(Choice)
class ChoiceAdmin(admin.ModelAdmin):
    list_display = ("id", "__str__")
    search_fields = ("id",)


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("id", "__str__")
    search_fields = ("id",)
