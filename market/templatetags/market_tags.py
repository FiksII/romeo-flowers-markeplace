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
