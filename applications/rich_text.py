import html
import re

import bleach
from bleach.css_sanitizer import CSSSanitizer
from django.utils.html import strip_tags


RICH_TEXT_MARKER = 'data-ce-rich-text="1"'
RICH_TEXT_RE = re.compile(
    r'^\s*<div\s+data-ce-rich-text=["\']1["\']\s*>(?P<body>.*)</div>\s*$',
    re.IGNORECASE | re.DOTALL,
)

_CSS_SANITIZER = CSSSanitizer(
    allowed_css_properties=["font-size", "line-height", "font-weight", "text-align"],
)
_ALLOWED_TAGS = [
    "a", "b", "br", "div", "em", "font", "i", "li", "ol", "p", "span",
    "strong", "u", "ul",
]
_ALLOWED_ATTRIBUTES = {
    "a": ["href", "title", "target", "rel"],
    "font": ["size"],
    "div": ["style"],
    "p": ["style"],
    "span": ["style"],
}
_FONT_SIZE_MAP = {
    "1": "0.75em", "2": "0.875em", "3": "1em", "4": "1.25em",
    "5": "1.5em", "6": "2em", "7": "3em",
}


def is_rich_text(value) -> bool:
    return bool(RICH_TEXT_RE.match(str(value or "")))


def render_rich_text(value) -> str:
    """Return sanitized rich HTML, or an empty string for non-rich values."""
    match = RICH_TEXT_RE.match(str(value or ""))
    if not match:
        return ""
    body = re.sub(
        r'<font\s+size=["\']?([1-7])["\']?\s*>',
        lambda item: f'<span style="font-size: {_FONT_SIZE_MAP[item.group(1)]}">',
        match.group("body"),
        flags=re.IGNORECASE,
    )
    body = re.sub(r"</font\s*>", "</span>", body, flags=re.IGNORECASE)
    cleaned = bleach.clean(
        body,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        protocols=["http", "https", "mailto"],
        css_sanitizer=_CSS_SANITIZER,
        strip=True,
    )
    return bleach.linkify(cleaned, skip_tags=["a"], parse_email=True)


def rich_text_to_plain(value) -> str:
    rendered = render_rich_text(value)
    if not rendered:
        return str(value or "")
    with_breaks = re.sub(r"<br\s*/?>", "\n", rendered, flags=re.IGNORECASE)
    with_breaks = re.sub(r"</(?:p|div|li)>", "\n", with_breaks, flags=re.IGNORECASE)
    return html.unescape(strip_tags(with_breaks)).strip()
