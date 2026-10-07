from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


@dataclass(frozen=True)
class ProductQuote:
    """One bouquet: what the buyer pays, the platform commission, what the shop gets."""

    customer: Decimal
    commission: Decimal
    payout: Decimal


def customer_price(shop, pickup_price, delivery_price, method="delivery"):
    """The shop sets the prices; the platform adds nothing on top.

    A shop with its own delivery may set a separate price with delivery. Otherwise (and
    for pickup) the buyer pays the pickup price, and Romeo's delivery is charged on top."""
    price = pickup_price
    # Until the buyer picks a way of receiving ("any"), the catalogue shows the delivery price.
    if method != "pickup" and shop.own_delivery and delivery_price is not None:
        price = delivery_price
    return Decimal(price).quantize(CENT, rounding=ROUND_HALF_UP)


def quote_product(shop, pickup_price, delivery_price=None, method="delivery"):
    customer = customer_price(shop, pickup_price, delivery_price, method)
    commission = (customer * shop.commission_percent / 100).quantize(
        CENT, rounding=ROUND_HALF_UP
    )
    return ProductQuote(customer, commission, customer - commission)
