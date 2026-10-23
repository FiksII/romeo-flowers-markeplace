from decimal import Decimal, InvalidOperation

from django import template

from market.thumbnails import listing_photo as resized_listing_photo

register = template.Library()


@register.simple_tag
def order_next_step(part, user):
    from market.access import next_order_step

    return next_order_step(user, part)


@register.simple_tag
def listing_photo(listing, preset):
    """``{% listing_photo listing "card" as photo %}`` -> ``photo.src``, ``photo.srcset``."""
    return resized_listing_photo(listing, preset)


@register.filter
def bouquet_size(line):
    from market.bouquet_options import SIZE_OPTION

    return next(
        (
            attribute.value
            for attribute in line.attributes.all()
            if attribute.option_id and attribute.option.code == SIZE_OPTION
        ),
        "",
    )


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
def sub_money(value, other):
    return Decimal(value) - Decimal(other)


@register.simple_tag
def romeo_payment_instruction(part):
    if part.payment_status == "refunded":
        return "Оплата возвращена. Не доставляйте заказ без уточнения."
    if part.payment_status == "paid":
        paid = (
            "Оплачено онлайн" if part.payment_method == "online" else "Оплата получена"
        )
        return f"{paid}. Получать деньги с клиента не нужно."
    if part.payment_method == "online":
        return "Ожидается онлайн-оплата. Не доставляйте заказ до подтверждения оплаты."
    if part.method == "delivery" and part.delivery_owner == "platform":
        return f"Курьер Ромео: получить с клиента {rub(part.total)} при вручении."
    return "Оплату наличными получает магазин."


@register.filter
def abs_money(value):
    return abs(value)


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


@register.simple_tag
def payouts_to_confirm(shop):
    """Payouts Romeo has sent that the shop has not yet confirmed."""
    return list(shop.payouts.filter(status="sent"))


@register.simple_tag
def disputed_payouts():
    from market.models import Payout

    return list(Payout.objects.filter(status="disputed").select_related("shop"))


@register.simple_tag
def romeo_order_summary():
    import hashlib
    import json

    from django.db.models import Count, Max

    from market.models import ShopOrder
    from market.orders import ACTIVE_STATUSES, new_romeo_orders

    summary = new_romeo_orders().aggregate(count=Count("pk"), latest=Max("pk"))
    active = list(
        ShopOrder.objects.filter(
            method="delivery",
            delivery_owner="platform",
            status__in=ACTIVE_STATUSES,
        )
        .order_by("pk")
        .values_list(
            "pk",
            "status",
            "payment_method",
            "payment_status",
            "address",
            "address_details",
            "slot_start",
            "slot_end",
            "delivery_point",
            "delivery_contact",
            "instructions",
            "contact_name",
            "contact_phone",
            "customer_name",
            "customer_phone",
        )
    )
    summary["revision"] = hashlib.sha256(
        json.dumps(active, default=str, sort_keys=True).encode()
    ).hexdigest()
    return summary


@register.simple_tag
def new_orders_count(shop):
    from market.orders import new_orders

    return new_orders(shop).count()


@register.simple_tag
def latest_new_order(shop):
    from market.orders import new_orders

    return new_orders(shop).order_by("-pk").values_list("pk", flat=True).first() or 0


@register.filter
def phone(value):
    """+79991234567 → +7 (999) 123-45-67."""
    digits = "".join(character for character in str(value or "") if character.isdigit())
    if len(digits) != 11:
        return value
    return f"+{digits[0]} ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:]}"
