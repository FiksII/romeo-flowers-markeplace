from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
RUBLE = Decimal(1)


@dataclass(frozen=True)
class ProductQuote:
    """One bouquet: what the buyer pays, what Romeo keeps, what the shop gets."""

    customer: Decimal
    commission: Decimal  # Romeo's part: the markup, or the difference to its own price
    payout: Decimal


def partner_price(shop, pickup_price, delivery_price, method="delivery"):
    """What the shop gets for one bouquet: the price it set for this way of receiving.

    A shop with its own delivery may set a separate price with delivery. Otherwise (and
    for pickup) it gets the base price; Romeo's delivery is included in it."""
    price = pickup_price
    # Until the buyer picks a way of receiving ("any"), the catalogue shows the delivery price.
    if method != "pickup" and shop.own_delivery and delivery_price is not None:
        price = delivery_price
    return Decimal(price).quantize(CENT, rounding=ROUND_HALF_UP)


def quote_product(
    shop, pickup_price, delivery_price=None, method="delivery", sale_price=None
):
    """Romeo sells at its own price when the administrator set one, otherwise at the
    shop's price plus the shop's markup, rounded to whole roubles."""
    payout = partner_price(shop, pickup_price, delivery_price, method)
    if sale_price is not None:
        customer = Decimal(sale_price).quantize(CENT, rounding=ROUND_HALF_UP)
    elif shop.markup_percent:
        customer = (payout * (100 + shop.markup_percent) / 100).quantize(
            RUBLE, rounding=ROUND_HALF_UP
        )
    else:
        customer = payout
    return ProductQuote(customer, customer - payout, payout)


def customer_price(
    shop, pickup_price, delivery_price, method="delivery", sale_price=None
):
    return quote_product(
        shop, pickup_price, delivery_price, method, sale_price
    ).customer
