from django import template

register = template.Library()

@register.filter
def split(value, sep=","):
    if not value:
        return []
    return [v.strip() for v in str(value).split(sep) if v.strip()]
