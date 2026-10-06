from collections import defaultdict
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from types import SimpleNamespace

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from oscar.apps.order.utils import OrderCreator
from oscar.core.loading import get_model
from oscar.core.prices import Price

from market.access import shops_for_user
from market.addresses import validate_address
from market.availability import available_slots, location_matches
from market.context import FulfillmentContext
from market.models import Listing, Shop, ShopOrder
from market.strategy import MarketplaceStrategy

CENT = Decimal("0.01")


def basket_groups(basket):
    groups = {}
    for line in basket.all_lines():
        try:
            listing = Listing.objects.select_related("shop__settlement").get(
                product_id=line.product_id
            )
        except Listing.DoesNotExist:
            continue
        group = groups.setdefault(
            listing.shop_id, {"shop": listing.shop, "lines": [], "total": Decimal(0)}
        )
        group["lines"].append({"line": line, "listing": listing})
        group["total"] += line.line_price_incl_tax
    return list(groups.values())


@transaction.atomic
def place_market_order(user, basket, choices, address, contact, now=None):
    now = now or timezone.now()
    Basket, Stock = get_model("basket", "Basket"), get_model("partner", "StockRecord")
    if not user.is_authenticated or not basket.pk:
        raise PermissionDenied
    basket = Basket.objects.select_for_update().get(pk=basket.pk)
    if basket.owner_id != user.pk:
        raise PermissionDenied
    Order = get_model("order", "Order")
    previous = Order.objects.filter(basket=basket, user=user).first()
    if previous:
        return previous
    if basket.status != Basket.OPEN:
        raise ValidationError("Корзина уже оформлена или недоступна.")
    basket.strategy = MarketplaceStrategy()
    if basket.vouchers.exists():
        raise ValidationError("Промокоды пока не поддерживаются. Обновите корзину.")
    lines = list(basket.all_lines())
    if not lines:
        raise ValidationError("Добавьте товары в корзину.")
    if not contact.get("name", "").strip() or not contact.get("phone", "").strip():
        raise ValidationError("Укажите имя и телефон получателя.")
    stock_ids = sorted({line.stockrecord_id for line in lines})
    stocks = {
        row.pk: row
        for row in Stock.objects.select_for_update()
        .filter(pk__in=stock_ids)
        .order_by("pk")
    }
    listing_rows = {
        row.product_id: row
        for row in Listing.objects.select_related("shop__settlement").filter(
            product_id__in=[line.product_id for line in lines]
        )
    }
    groups = defaultdict(list)
    for line in lines:
        listing, stock = (
            listing_rows.get(line.product_id),
            stocks.get(line.stockrecord_id),
        )
        if (
            not listing
            or not stock
            or listing.shop.partner_id != stock.partner_id
            or not line.product.is_public
        ):
            raise ValidationError(
                "Одно из предложений больше недоступно. Обновите корзину."
            )
        if stock.price is None or stock.net_stock_level < line.quantity:
            raise ValidationError(
                f"Недостаточно цветов для «{line.product.title}». Обновите корзину."
            )
        line.stockrecord = stock
        line._info = basket.strategy.fetch_for_product(line.product, stockrecord=stock)
        groups[listing.shop_id].append((line, stock))
    shops = {
        row.pk: row
        for row in Shop.objects.select_for_update(of=("self",))
        .select_related("settlement")
        .filter(pk__in=sorted(groups))
        .order_by("pk")
    }
    if set(choices) != set(groups):
        raise ValidationError("Выберите получение для каждого магазина.")
    prepared = []
    shipping_total = Decimal(0)
    goods_total = Decimal(0)
    has_delivery = False
    for shop_id, items in groups.items():
        shop, choice = shops[shop_id], choices[shop_id]
        method = choice.get("method")
        if shop.status != "active" or shop.settlement.region not in {"77", "50"}:
            raise ValidationError(f"Магазин «{shop.name}» временно недоступен.")
        if method not in {"delivery", "pickup"} or not getattr(
            shop, f"{method}_enabled"
        ):
            raise ValidationError("Этот способ получения недоступен.")
        try:
            selected = datetime.fromisoformat(choice.get("slot", ""))
            if selected.tzinfo is None:
                raise ValueError
        except (ValueError, TypeError):
            raise ValidationError("Выберите доступный интервал получения.")
        # No display limit: every slot in the selected day is checked on the server.
        from zoneinfo import ZoneInfo

        target_day = selected.astimezone(ZoneInfo(shop.timezone)).date()
        slot = next(
            (
                item
                for item in available_slots(shop, method, now, target_day, limit=48)
                if item.start == selected
            ),
            None,
        )
        if not slot:
            raise ValidationError(
                f"Интервал магазина «{shop.name}» изменился. Выберите другой."
            )
        if method == "delivery":
            if address is None:
                raise ValidationError("Выберите точный адрес доставки из подсказок.")
            validate_address(address)
            if not location_matches(shop, method, FulfillmentContext(address=address)):
                raise ValidationError(
                    f"Магазин «{shop.name}» не доставляет по этому адресу."
                )
            has_delivery = True
        goods = sum(
            (stock.price * line.quantity for line, stock in items), Decimal(0)
        ).quantize(CENT)
        if goods < shop.minimum_order:
            raise ValidationError(
                f"Минимальная сумма товаров в «{shop.name}»: {shop.minimum_order} ₽."
            )
        fee = shop.delivery_fee if method == "delivery" else Decimal(0)
        commission = (goods * shop.commission_percent / 100).quantize(
            CENT, rounding=ROUND_HALF_UP
        )
        prepared.append((shop, method, slot, goods, fee, commission))
        goods_total += goods
        shipping_total += fee
    shipping_address = None
    if has_delivery:
        Country = get_model("address", "Country")
        country, _ = Country.objects.get_or_create(
            iso_3166_1_a2="RU",
            defaults={
                "name": "Россия",
                "printable_name": "Россия",
                "is_shipping_country": True,
            },
        )
        shipping_address = get_model("order", "ShippingAddress").objects.create(
            first_name=contact["name"][:100],
            line1=address["value"][:255],
            line4=address["city"][:255],
            country=country,
            phone_number=contact["phone"],
        )
    basket.freeze()
    charge = Price(currency="RUB", excl_tax=shipping_total, incl_tax=shipping_total)
    total = Price(
        currency="RUB",
        excl_tax=goods_total + shipping_total,
        incl_tax=goods_total + shipping_total,
    )
    order = OrderCreator().place_order(
        basket=basket,
        user=user,
        total=total,
        shipping_method=SimpleNamespace(name="Получение от магазинов", code="market"),
        shipping_charge=charge,
        shipping_address=shipping_address,
        status="AwaitingPayment",
    )
    for shop, method, slot, goods, fee, commission in prepared:
        ShopOrder.objects.create(
            order=order,
            shop=shop,
            method=method,
            slot_start=slot.start,
            slot_end=slot.end,
            address=address["value"]
            if method == "delivery"
            else f"{shop.settlement.name}, {shop.address}",
            contact_name=contact["name"][:100],
            contact_phone=contact["phone"][:25],
            instructions=contact.get("instructions", "")[:1000],
            goods_total=goods,
            delivery_total=fee,
            commission_percent=shop.commission_percent,
            commission_total=commission,
            partner_total=goods + fee - commission,
        )
    basket.submit()
    return order


def _cancel_locked(part):
    if part.status == "cancelled":
        return part
    if part.status == "completed" or part.payment_status != "pending":
        raise ValidationError(
            "Для оплаченного или полученного заказа требуется оформить возврат через платёжный сервис."
        )
    stocks = get_model("partner", "StockRecord")
    for line in part.lines.order_by("stockrecord_id"):
        stock = stocks.objects.select_for_update().get(pk=line.stockrecord_id)
        stock.cancel_allocation(line.quantity)
    part.status = "cancelled"
    part.save(update_fields=["status"])
    if not part.order.shop_orders.exclude(status="cancelled").exists():
        part.order.status = "Cancelled"
        part.order.save(update_fields=["status"])
    return part


@transaction.atomic
def cancel_shop_order(part, actor):
    get_model("order", "Order").objects.select_for_update().get(pk=part.order_id)
    part = (
        ShopOrder.objects.select_for_update(of=("self",))
        .select_related("order", "shop")
        .get(pk=part.pk)
    )
    if (
        not shops_for_user(actor).filter(pk=part.shop_id).exists()
        and part.order.user_id != actor.pk
    ):
        raise PermissionDenied
    return _cancel_locked(part)


def expire_unpaid_orders(now=None, limit=100):
    cutoff = (now or timezone.now()) - timedelta(
        minutes=settings.MARKET_RESERVATION_MINUTES
    )
    ids = list(
        ShopOrder.objects.filter(
            payment_status="pending", order__date_placed__lte=cutoff
        )
        .exclude(status__in=["cancelled", "completed"])
        .order_by("order_id")
        .values_list("order_id", flat=True)
        .distinct()[:limit]
    )
    count = 0
    for order_id in ids:
        with transaction.atomic():
            order = (
                get_model("order", "Order").objects.select_for_update().get(pk=order_id)
            )
            if order.date_placed > cutoff:
                continue
            parts = (
                ShopOrder.objects.select_for_update(of=("self",))
                .select_related("order", "shop")
                .filter(order=order, payment_status="pending")
                .exclude(status__in=["cancelled", "completed"])
                .order_by("pk")
            )
            for part in parts:
                _cancel_locked(part)
                count += 1
    return count


@transaction.atomic
def transition_shop_order(part, actor, target):
    order = (
        get_model("order", "Order").objects.select_for_update().get(pk=part.order_id)
    )
    part = (
        ShopOrder.objects.select_for_update(of=("self",))
        .select_related("shop")
        .get(pk=part.pk)
    )
    if not shops_for_user(actor).filter(pk=part.shop_id).exists():
        raise PermissionDenied
    if target == "cancelled":
        return cancel_shop_order(part, actor)
    if part.payment_status != "paid":
        raise ValidationError("Исполнение доступно после подтверждения оплаты.")
    allowed = {
        "accepted": {"preparing"},
        "preparing": {"ready" if part.method == "pickup" else "courier"},
        "ready": {"completed"} if part.method == "pickup" else set(),
        "courier": {"completed"} if part.method == "delivery" else set(),
    }
    if target not in allowed.get(part.status, set()):
        raise ValidationError("Этот переход статуса недоступен.")
    if target == "completed":
        Stock = get_model("partner", "StockRecord")
        for line in part.lines.order_by("stockrecord_id"):
            Stock.objects.select_for_update().get(
                pk=line.stockrecord_id
            ).consume_allocation(line.quantity)
    part.status = target
    part.save(update_fields=["status"])
    order.status = (
        "Completed"
        if not order.shop_orders.exclude(status__in=["completed", "cancelled"]).exists()
        else "Processing"
    )
    order.save(update_fields=["status"])
    return part
