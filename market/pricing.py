from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


@dataclass(frozen=True)
class ProductQuote:
    base: Decimal
    markup: Decimal
    discount: Decimal
    customer: Decimal


def quote_product(shop, base_price, method="delivery"):
    base = Decimal(base_price).quantize(CENT, rounding=ROUND_HALF_UP)
    markup = (base * shop.markup_percent / 100).quantize(CENT, rounding=ROUND_HALF_UP)
    discount = (
        (markup * shop.pickup_discount_percent / 100).quantize(
            CENT, rounding=ROUND_HALF_UP
        )
        if method == "pickup"
        else Decimal(0)
    )
    discount = min(markup, discount)
    return ProductQuote(base, markup, discount, base + markup - discount)
