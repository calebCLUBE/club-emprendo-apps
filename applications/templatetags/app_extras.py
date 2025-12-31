# applications/templatetags/app_extras.py
from django import template

register = template.Library()

@register.filter
def get_item(d, key):
    """
    Usage in templates:
      {{ my_dict|get_item:some_key }}
    """
    if d is None:
        return None
    return d.get(key)
