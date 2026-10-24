from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from oscar.apps.order.utils import OrderCreator
from oscar.core.loading import get_model
from oscar.core.prices import Price

from market import messengers
from market.access import (
    may_complete_order,
    may_set_delivery_destination,
    shops_for_user,
)
from market.addresses import validate_address
from market.availability import available_slots, location_matches
from market.bouquet_options import line_size
from market.context import FulfillmentContext
from market.models import (
    AuditEntry,
    Listing,
    LoyaltyReward,
    OnlinePayment,
    Shop,
    ShopOrder,
    ShopOrderItem,
)
from market.payments import record_cash_receipt
from market.pricing import ProductQuote, quote_product
from market.strategy import GiftStrategy, MarketplaceStrategy

CENT = Decimal("0.01")


@transaction.atomic
def set_delivery_cost(part, actor, amount):
    """Record Romeo's own expense without touching sale amounts or shop balances."""
    if not actor.is_superuser:
        raise PermissionDenied
    part = ShopOrder.objects.select_for_update().get(pk=part.pk)
    if not part.is_romeo_delivery:
        raise ValidationError("Расходы можно указать только для доставки Ромео.")
    amount = ShopOrder._meta.get_field("actual_delivery_cost").clean(amount, part)
    previous = part.actual_delivery_cost
    if previous != amount:
        part.actual_delivery_cost = amount
        part.save(update_fields=["actual_delivery_cost"])
        AuditEntry.objects.create(
            actor=actor,
            shop_id=part.shop_id,
            action=f"Расходы на доставку части заказа #{part.pk}: "
            f"{previous if previous is not None else 'не указаны'} → "
            f"{amount if amount is not None else 'не указаны'} ₽",
        )
    return part


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
def place_market_order(
    user,
    basket,
    choices,
    address,
    contact,
    now=None,
    payment_method="online",
    *,
    gift_reward=None,
):
    now = now or timezone.now()
    gift = payment_method == "gift"
    if payment_method not in {"cash", "online", "gift"} or gift != (
        gift_reward is not None
    ):
        raise ValidationError("Выберите способ оплаты.")
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
    customer = get_user_model().objects.select_for_update().get(pk=user.pk)
    if gift:
        gift_reward = LoyaltyReward.objects.select_for_update().get(
            pk=gift_reward.pk, user=user
        )
        if (
            gift_reward.order_id
            or gift_reward.purchases.aggregate(units=Sum("units"))["units"] != 10
        ):
            raise ValidationError("Обновите карту любителя цветов.")
    email = customer.email.strip() or contact.get("email", "").strip()
    if not email:
        raise ValidationError("Укажите ваш email для информации о заказе.")
    validate_email(email)
    if basket.status != Basket.OPEN:
        raise ValidationError("Корзина уже оформлена или недоступна.")
    basket.strategy = MarketplaceStrategy()
    if basket.vouchers.exists():
        raise ValidationError("Промокоды пока не поддерживаются. Обновите корзину.")
    lines = list(basket.all_lines())
    if not lines:
        raise ValidationError("Добавьте товары в корзину.")
    if gift and (len(lines) != 1 or lines[0].quantity != 1):
        raise ValidationError("В подарок можно выбрать один букет.")
    if not contact.get("name", "").strip() or not contact.get("phone", "").strip():
        raise ValidationError("Укажите имя и телефон получателя.")
    delivery_contact = contact.get("delivery_contact") or "no_call"
    if delivery_contact not in {"call", "no_call"}:
        raise ValidationError("Выберите, нужно ли звонить получателю.")
    stock_ids = sorted({line.stockrecord_id for line in lines})
    stocks = {
        row.pk: row
        for row in Stock.objects.select_for_update()
        .filter(pk__in=stock_ids)
        .order_by("pk")
    }
    # Sellers edit a listing only while holding its stock row, which is locked above, so
    # these rows cannot change under this checkout.
    listing_rows = {
        row.product_id: row
        for row in Listing.objects.select_related("shop__settlement").filter(
            product_id__in=[line.product_id for line in lines]
        )
    }
    groups = defaultdict(list)
    quantities = defaultdict(int)
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
        listing.prices_for_size(line_size(line))
        quantities[stock.pk] += line.quantity
        if stock.price is None or not (
            listing.always_in_stock or stock.net_stock_level >= quantities[stock.pk]
        ):
            raise ValidationError(
                f"Недостаточно цветов для «{line.product.title}». Обновите корзину."
            )
        line.stockrecord = stock
        groups[listing.shop_id].append((line, stock, listing))
    shops = {
        row.pk: row
        for row in Shop.objects.select_for_update(of=("self",))
        .select_related("settlement")
        .filter(pk__in=sorted(groups))
        .order_by("pk")
    }
    if set(choices) != set(groups):
        raise ValidationError("Выберите получение для каждой части заказа.")
    basket.strategy = (GiftStrategy if gift else MarketplaceStrategy)(
        methods={shop_id: choice.get("method") for shop_id, choice in choices.items()},
        shops=shops,
    )
    prepared = []
    shipping_total = Decimal(0)
    goods_total = Decimal(0)
    has_delivery = False
    for shop_id, items in groups.items():
        shop, choice = shops[shop_id], choices[shop_id]
        method = choice.get("method")
        if shop.status != "active" or shop.settlement.region not in {"77", "50"}:
            raise ValidationError("Эта часть заказа временно недоступна.")
        if method not in {"delivery", "pickup"} or not getattr(
            shop, f"{method}_enabled"
        ):
            raise ValidationError("Этот способ получения недоступен.")
        if method == "delivery" and payment_method not in {"online", "gift"}:
            raise ValidationError("Доставка оплачивается только онлайн.")
        clarify = method == "delivery" and delivery_contact == "call"
        defer_slot = clarify and shop.own_delivery
        slot = None
        try:
            if defer_slot:
                # Only a shop's own delivery can agree its interval after ordering.
                selected = None
            else:
                selected = datetime.fromisoformat(choice.get("slot", ""))
                if selected.tzinfo is None:
                    raise ValueError
        except (ValueError, TypeError):
            raise ValidationError("Выберите доступный интервал получения.")
        # No display limit: every slot in the selected day is checked on the server.
        from zoneinfo import ZoneInfo

        target_day = (
            selected.astimezone(ZoneInfo(shop.timezone)).date() if selected else None
        )
        slot = next(
            (
                item
                for item in available_slots(shop, method, now, target_day, limit=48)
                if item.start == selected
            ),
            None,
        )
        if not defer_slot and not slot:
            raise ValidationError("Интервал получения изменился. Выберите другой.")
        if method == "delivery" and not clarify:
            if address is None:
                raise ValidationError("Выберите точный адрес доставки из подсказок.")
            validate_address(address)
            if not location_matches(shop, method, FulfillmentContext(address=address)):
                raise ValidationError(
                    "Эти товары недоступны для доставки по выбранному адресу."
                )
            has_delivery = True
        goods, payout, commission = Decimal(0), Decimal(0), Decimal(0)
        snapshot = []
        for line, stock, listing in items:
            size = line_size(line)
            pickup, delivery = listing.prices_for_size(size)
            quote = quote_product(
                shop,
                stock.price if size == "standard" else pickup,
                delivery,
                method,
                listing.sale_price_for(size, method),
            )
            if gift:
                if quote.customer > gift_reward.limit:
                    raise ValidationError(
                        "Стоимость букета превышает ваш лимит подарка. Выберите другой букет или размер."
                    )
                quote = ProductQuote(Decimal(0), Decimal(0), Decimal(0))
            line._info = basket.strategy.fetch_for_line(line, stockrecord=stock)
            snapshot.append((line, listing, size, quote))
            goods += quote.customer * line.quantity
            payout += quote.payout * line.quantity
            commission += quote.commission * line.quantity
        if not gift and goods < shop.minimum_order:
            raise ValidationError(
                f"Минимальная сумма товаров в этой части заказа: {shop.minimum_order} ₽."
            )
        # A shop with its own delivery brings the bouquet itself and has it in the price.
        own = method == "delivery" and shop.own_delivery
        fee = Decimal(0)
        prepared.append(
            (
                shop,
                method,
                slot,
                goods,
                payout,
                fee,
                commission,
                "partner" if own else "platform",
                snapshot,
            )
        )
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
    if not customer.email.strip():
        customer.email = email
        customer.save(update_fields=["email"])
        user.email = email
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
        guest_email=email,
        total=total,
        shipping_method=SimpleNamespace(
            name="Доставка Ромео и самовывоз", code="market"
        ),
        shipping_charge=charge,
        shipping_address=shipping_address,
        status="AwaitingPayment" if payment_method == "online" else "Processing",
    )
    for shop, method, slot, goods, payout, fee, commission, owner, snapshot in prepared:
        part = ShopOrder.objects.create(
            order=order,
            shop=shop,
            method=method,
            slot_start=slot.start if slot else None,
            slot_end=slot.end if slot else None,
            address=(address["value"] if address else "")
            if method == "delivery"
            else f"{shop.settlement.name}, {shop.address}",
            contact_name=contact["name"][:100],
            contact_phone=contact["phone"][:25],
            customer_name=(contact.get("customer_name") or customer.get_full_name())[
                :150
            ],
            customer_phone=(
                contact.get("customer_phone")
                or getattr(getattr(customer, "customer_phone", None), "phone", "")
            )[:25],
            delivery_contact=delivery_contact if method == "delivery" else "no_call",
            address_details=contact.get("address_details", "")[:300]
            if method == "delivery" and delivery_contact == "no_call"
            else "",
            instructions=contact.get("instructions", "")[:1000],
            goods_total=goods,
            base_goods_total=payout,
            delivery_total=fee,
            delivery_owner=owner,
            commission_percent=shop.markup_percent,
            commission_total=commission,
            partner_total=payout,
            payment_method=payment_method,
            payment_status="free" if gift else "pending",
        )
        # The prices are copied, so later edits of the listing never change this order.
        ShopOrderItem.objects.bulk_create(
            ShopOrderItem(
                part=part,
                listing=listing,
                title=line.product.title[:255],
                size=size if size != "standard" else "",
                quantity=line.quantity,
                unit_price=quote.customer,
                unit_commission=quote.commission,
                unit_payout=quote.payout,
                commission_percent=shop.markup_percent,
            )
            for line, listing, size, quote in snapshot
        )
        if payment_method in {"cash", "gift"}:
            # Online parts are announced once the bank confirms the payment.
            messengers.notify_new_part(part)
    basket.submit()
    return order


def _cancel_locked(part):
    if part.status == "cancelled":
        return part
    if part.status == "completed" or (
        part.payment_status != "pending" and not part.is_gift
    ):
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
    is_shop = shops_for_user(actor).filter(pk=part.shop_id).exists()
    if not is_shop and part.status not in {"accepted", "preparing"}:
        raise ValidationError(
            "Заказ уже готов или передан курьеру. Для отмены обратитесь в поддержку."
        )
    return _cancel_locked(part)


def expire_unpaid_orders(now=None, limit=100):
    now = now or timezone.now()
    cutoff = now - timedelta(minutes=settings.MARKET_RESERVATION_MINUTES)
    ids = list(
        ShopOrder.objects.filter(
            payment_method="online",
            payment_status="pending",
            order__date_placed__lte=cutoff,
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
            if order.online_payments.filter(
                status__in=OnlinePayment.IN_PROGRESS,
                updated_at__gt=now - timedelta(minutes=10),
            ).exists():
                # The bank is taking the money right now; keep the bouquets for it.
                continue
            parts = (
                ShopOrder.objects.select_for_update(of=("self",))
                .select_related("order", "shop")
                .filter(order=order, payment_method="online", payment_status="pending")
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
    if not part.can_be_worked:
        raise ValidationError("Исполнение доступно после подтверждения оплаты.")
    allowed = {
        "accepted": {"preparing"},
        "preparing": {"ready"},
        "ready": {"completed"} if part.method == "pickup" else {"courier"},
        "courier": {"completed"} if part.method == "delivery" else set(),
    }
    if target not in allowed.get(part.status, set()):
        raise ValidationError("Этот переход статуса недоступен.")
    if target == "completed":
        if not may_complete_order(actor, part):
            raise PermissionDenied
        Stock = get_model("partner", "StockRecord")
        for line in part.lines.order_by("stockrecord_id"):
            Stock.objects.select_for_update().get(
                pk=line.stockrecord_id
            ).consume_allocation(line.quantity)
    part.status = target
    part.save(update_fields=["status"])
    if target == "completed" and part.payment_method == "cash":
        # The cash is handed over together with the bouquet.
        part = record_cash_receipt(part)
    order.status = (
        "Completed"
        if not order.shop_orders.exclude(status__in=["completed", "cancelled"]).exists()
        else "Processing"
    )
    order.save(update_fields=["status"])
    return part


@transaction.atomic
def set_delivery_destination(
    part, actor, address, details, slot_start=None, slot_end=None
):
    get_model("order", "Order").objects.select_for_update().get(pk=part.order_id)
    part = (
        ShopOrder.objects.select_for_update(of=("self",))
        .select_related("shop")
        .get(pk=part.pk)
    )
    if not may_set_delivery_destination(actor, part):
        raise PermissionDenied
    if not part.can_set_destination:
        raise ValidationError("Для этого заказа нельзя изменить конечную точку.")
    validate_address(address)
    if not location_matches(part.shop, "delivery", FulfillmentContext(address=address)):
        raise ValidationError("Адрес находится вне зоны доставки магазина.")
    if not part.slot_start or not part.slot_end:
        if (
            not slot_start
            or not slot_end
            or slot_start <= timezone.now()
            or slot_end <= slot_start
        ):
            raise ValidationError("Укажите согласованный интервал доставки в будущем.")
        part.slot_start, part.slot_end = slot_start, slot_end
    part.delivery_point = {
        key: str(address[key])
        for key in ("value", "city", "region", "latitude", "longitude")
    }
    part.address = address["value"]
    part.address_details = details
    part.save(
        update_fields=[
            "address",
            "address_details",
            "delivery_point",
            "slot_start",
            "slot_end",
        ]
    )
    AuditEntry.objects.create(
        shop=part.shop,
        actor=actor,
        action=f"Конечная точка части заказа #{part.pk} сохранена",
    )
    return part


ACTIVE_STATUSES = ("accepted", "preparing", "ready", "courier")
HISTORY_STATUSES = ("completed", "cancelled")


def new_romeo_orders():
    from django.db.models import Q

    return ShopOrder.objects.filter(
        status="accepted", method="delivery", delivery_owner="platform"
    ).filter(
        Q(payment_method="cash")
        | Q(payment_status="paid")
        | Q(payment_method="gift", payment_status="free")
    )


def new_orders(shop):
    """Parts the shop has not started yet and can start right now."""
    from django.db.models import Q

    return shop.shop_orders.filter(status="accepted").filter(
        Q(payment_method="cash")
        | Q(payment_status="paid")
        | Q(payment_method="gift", payment_status="free")
    )
