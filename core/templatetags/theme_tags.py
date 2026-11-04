from django import template

from core.themes import resolve_theme

register = template.Library()


@register.simple_tag
def store_theme(store):
    """{% store_theme current_store as theme %} → dict с css, layout, mode, font_url..."""
    return resolve_theme(store)
