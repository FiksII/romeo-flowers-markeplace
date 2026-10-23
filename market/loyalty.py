"""One shared card across shops, using paid, completed bouquet units."""

from decimal import ROUND_DOWN, Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max, Sum
from oscar.core.loading import get_model

from market.bouquet_options import SIZE_OPTION, SIZES
from market.models import (
    Listing,
    LoyaltyPurchase,
    LoyaltyReward,
    ShopOrder,
    ShopOrderItem,
)
from market.strategy import GiftStrategy

BOUQUETS_PER_GIFT = 10
CENT = Decimal("0.01")


def unspent_purchases(user, *, part_ids=None):
    if not user.is_authenticated:
        return []
    items = (
        ShopOrderItem.objects.filter(
            part__order__user=user,
            part__status="completed",
            part__payment_status="paid",
        )
        .exclude(part__payment_method="gift")
        .annotate(spent=Sum("loyalty_uses__units"))
        .order_by("part__order__date_placed", "part__order_id", "pk")
    )
    if part_ids is not None:
        items = items.filter(part_id__in=part_ids)
    return [
        (item, item.quantity - (item.spent or 0))
        for item in items
        if item.quantity > (item.spent or 0)
    ]


def next_decade(purchases):
    need, paid, parts = BOUQUETS_PER_GIFT, Decimal(0), []
    for item, units in purchases:
        take = min(units, need)
        amount = item.unit_price * take
        parts.append((item, take, amount))
        paid += amount
        need -= take
        if not need:
            return parts, paid
    return None


def progress(user, *, purchases=None):
    purchases = unspent_purchases(user) if purchases is None else purchases
    count = sum(units for _, units in purchases)
    decade = next_decade(purchases)
    latest = (
        LoyaltyReward.objects.filter(user=user).aggregate(value=Max("sequence"))[
            "value"
        ]
        if user.is_authenticated
        else 0
    )
    return {
        "stamps": min(count, BOUQUETS_PER_GIFT),
        "available": count // BOUQUETS_PER_GIFT,
        "remaining": max(0, BOUQUETS_PER_GIFT - count),
        "sequence": (latest or 0) + 1,
        "limit": (decade[1] / BOUQUETS_PER_GIFT).quantize(CENT, rounding=ROUND_DOWN)
        if decade
        else None,
    }


@transaction.atomic
def claim(user, listing_id, size, choice, address, contact, *, sequence, now=None):
    from market.orders import place_market_order

    if not user.is_authenticated:
        raise PermissionDenied
    # Every claim and ordinary checkout by this customer share the same lock.
    get_user_model().objects.select_for_update().get(pk=user.pk)
    try:
        sequence = int(sequence)
    except (ValueError, TypeError):
        raise ValidationError("Обновите карту любителя цветов.") from None
    previous = LoyaltyReward.objects.filter(user=user, sequence=sequence).first()
    if previous and previous.order_id:
        return previous
    # Serialize with payment/refund changes before taking the purchase snapshot.
    locked_ids = list(
        ShopOrder.objects.select_for_update(of=("self",))
        .filter(
            order__user=user,
            status="completed",
            payment_status="paid",
        )
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    purchases = unspent_purchases(user, part_ids=locked_ids)
    state = progress(user, purchases=purchases)
    if sequence != state["sequence"] or not state["available"]:
        raise ValidationError("Для подарка нужны 10 оплаченных и полученных букетов.")
    listing = (
        Listing.objects.select_related("product", "shop").filter(pk=listing_id).first()
    )
    if (
        not listing
        or size not in listing.sizes
        or not listing.stockrecord
        or listing.stockrecord.price is None
    ):
        raise ValidationError("Выберите доступный букет и размер.")
    decade = next_decade(purchases)
    reward = LoyaltyReward.objects.create(
        user=user, sequence=sequence, limit=state["limit"], paid_total=decade[1]
    )
    LoyaltyPurchase.objects.bulk_create(
        [
            LoyaltyPurchase(reward=reward, item=item, units=units, paid=paid)
            for item, units, paid in decade[0]
        ]
    )
    # A separate basket prevents a gift from overwriting the customer's shopping.
    basket = get_model("basket", "Basket").objects.create(owner=user)
    basket.strategy = GiftStrategy()
    option, _ = get_model("catalogue", "Option").objects.get_or_create(
        code=SIZE_OPTION,
        defaults={"name": "Размер", "type": "text", "required": False},
    )
    with basket.strategy.for_size(size):
        basket.add_product(
            listing.product, options=[{"option": option, "value": dict(SIZES)[size]}]
        )
    reward.order = place_market_order(
        user,
        basket,
        {listing.shop_id: choice},
        address,
        contact,
        now,
        payment_method="gift",
        gift_reward=reward,
    )
    reward.save(update_fields=["order"])
    return reward
