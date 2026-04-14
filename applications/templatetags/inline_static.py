from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from django import template
from django.conf import settings
from django.contrib.staticfiles import finders
from django.utils.safestring import mark_safe

register = template.Library()


@lru_cache(maxsize=64)
def _read_static_text(path: str) -> str:
    resolved = finders.find(path)
    if isinstance(resolved, (list, tuple)):
        resolved = resolved[0] if resolved else None
    if not resolved:
        resolved = str(Path(settings.BASE_DIR) / "static" / path)
    try:
        return Path(resolved).read_text(encoding="utf-8")
    except Exception:
        return ""


@register.simple_tag
def inline_static(path: str):
    return mark_safe(_read_static_text(path))

