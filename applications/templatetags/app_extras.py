import html
import re

from django import template
from django.template.defaultfilters import linebreaks, urlize
from django.utils.html import strip_tags
from django.utils.safestring import mark_safe

register = template.Library()

LEGACY_ANCHOR_RE = re.compile(
    r"<a\b[^>]*\bhref=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<label>.*?)</a>",
    flags=re.IGNORECASE | re.DOTALL,
)

@register.filter
def split(value, sep=","):
    if not value:
        return []
    return [v.strip() for v in str(value).split(sep) if v.strip()]


@register.filter(needs_autoescape=True)
def format_help_text(value, autoescape=True):
    """Render editor help text as paragraphs and safe automatic links."""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")

    # Preserve the two legacy help texts that used hand-written anchor tags,
    # while keeping all new editor input plain text only.
    def anchor_to_plain(match):
        label = strip_tags(html.unescape(match.group("label"))).strip()
        href = html.unescape(match.group("href")).strip()
        return "\n".join(part for part in (label, href) if part)

    text = LEGACY_ANCHOR_RE.sub(anchor_to_plain, text)
    rendered = linebreaks(urlize(text, autoescape=autoescape), autoescape=False)
    rendered = rendered.replace(
        ' rel="nofollow"',
        ' target="_blank" rel="nofollow noopener noreferrer"',
    )
    return mark_safe(rendered)
