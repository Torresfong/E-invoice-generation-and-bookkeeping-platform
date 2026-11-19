from django import template

from core import quality

register = template.Library()


@register.filter
def reason_text(code):
    """`{{ document.bounce_reason|reason_text }}`: the stored code's message, in the page's own language."""
    return quality.reason_text(code) if code else ""
