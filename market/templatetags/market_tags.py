from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def unlocalize_money(value):
    return str(value)


@register.filter
def rub(value):
    try:
        number = Decimal(value)
        text = f"{number:,.0f}" if number == number.to_integral() else f"{number:,.2f}"
        return text.replace(",", "\u00a0").replace(".", ",") + " ₽"
    except (InvalidOperation, TypeError, ValueError):
        return "—"


@register.filter
def clock_minute(value):
    return f"{int(value) // 60:02}:{int(value) % 60:02}"


@register.filter
def weekday(value):
    return ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][int(value)]


@register.filter
def ru_plural(value, forms):
    """Russian plural: ``{{ n|ru_plural:"букет,букета,букетов" }}`` -> the right form."""
    one, few, many = forms.split(",")
    number = abs(int(value))
    if 10 < number % 100 < 15:
        return many
    return {1: one, 2: few, 3: few, 4: few}.get(number % 10, many)
