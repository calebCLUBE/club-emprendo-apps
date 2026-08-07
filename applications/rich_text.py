import html
import re

import bleach
from bleach.css_sanitizer import CSSSanitizer
from bs4 import BeautifulSoup
from django.utils.html import strip_tags


RICH_TEXT_MARKER = 'data-ce-rich-text="1"'
RICH_TEXT_RE = re.compile(
    r'^\s*<div\s+data-ce-rich-text=["\']1["\']\s*>(?P<body>.*)</div>\s*$',
    re.IGNORECASE | re.DOTALL,
)

_CSS_SANITIZER = CSSSanitizer(
    allowed_css_properties=[
        "display",
        "float",
        "font-size",
        "font-weight",
        "height",
        "line-height",
        "margin-bottom",
        "margin-left",
        "margin-right",
        "max-width",
        "text-align",
        "width",
    ],
)
_ALLOWED_TAGS = [
    "a", "b", "br", "div", "em", "font", "i", "img", "li", "ol", "p", "span",
    "strong", "u", "ul",
]


def _allow_image_attribute(tag, name, value):
    if name in {"alt", "style", "title"}:
        return True
    if name == "data-ce-align":
        return str(value or "") in {"left", "center", "right"}
    if name == "data-ce-width":
        try:
            return 10 <= int(value) <= 160
        except (TypeError, ValueError):
            return False
    if name == "data-ce-oversize":
        return str(value or "") == "1"
    if name == "data-ce-free-resize":
        return str(value or "") == "1"
    if name != "src":
        return False
    return bool(re.match(
        r"^data:image/(?:png|jpe?g|webp|gif);base64,[a-zA-Z0-9+/=\s]+$",
        str(value or ""),
        flags=re.IGNORECASE,
    ))


_ALLOWED_ATTRIBUTES = {
    "a": ["href", "title", "target", "rel"],
    "font": ["size"],
    "div": ["style"],
    "p": ["style"],
    "span": ["style"],
    "img": _allow_image_attribute,
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
        protocols=["http", "https", "mailto", "data"],
        css_sanitizer=_CSS_SANITIZER,
        strip=True,
    )
    return bleach.linkify(cleaned, skip_tags=["a"], parse_email=True)


def split_rich_text_images(value) -> tuple[str, str]:
    """Return rich text with its inline images removed, plus those images."""
    rendered = render_rich_text(value)
    if not rendered:
        return str(value or ""), ""

    soup = BeautifulSoup(rendered, "html.parser")
    images = soup.find_all("img")
    if not images:
        return str(value or ""), ""

    extracted = "".join(str(image.extract()) for image in images)
    marker = '<div data-ce-rich-text="1">{}</div>'
    return marker.format(str(soup)), marker.format(extracted)


def rich_text_to_plain(value) -> str:
    rendered = render_rich_text(value)
    if not rendered:
        return str(value or "")
    with_breaks = re.sub(r"<br\s*/?>", "\n", rendered, flags=re.IGNORECASE)
    with_breaks = re.sub(r"</(?:p|div|li)>", "\n", with_breaks, flags=re.IGNORECASE)
    return html.unescape(strip_tags(with_breaks)).strip()
