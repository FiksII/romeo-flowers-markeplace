from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from market.catalogue import public_listings
from market.context import FulfillmentContext
from market.forms import ShopForm
from market.orders import place_market_order
from market.strategy import MarketplaceStrategy
from tests.test_orders import ADDRESS, CONTACT, NOW, basket_for, choice

pytestmark = pytest.mark.django_db


def configure(shop):
    shop.markup_percent = Decimal(10)
    shop.pickup_discount_percent = Decimal(50)
    shop.save()


def test_customer_sees_markup_and_filter_uses_customer_price(shop, listing):
    configure(shop)
    info = MarketplaceStrategy().fetch_for_product(listing.product)
    assert info.price.incl_tax == Decimal(2750)
    assert listing.stockrecord.price == Decimal(2500)
    assert listing.price == Decimal(2750)
    assert listing.pickup_price == Decimal(2625)
    assert not public_listings(FulfillmentContext(), {"max_price": "2700"}, NOW)
    pickup = public_listings(
        FulfillmentContext(method="pickup"), {"max_price": "2700"}, NOW
    )
    assert pickup[0].display_price == Decimal(2625)


@pytest.mark.parametrize(
    "method, goods, delivery, markup, discount",
    [
        ("delivery", 2750, 350, 250, 0),
        ("pickup", 2625, 0, 125, 125),
    ],
)
def test_order_keeps_store_price_and_platform_delivery_separate(
    owner, shop, listing, method, goods, delivery, markup, discount
):
    configure(shop)
    order = place_market_order(
        owner,
        basket_for(owner, listing),
        {shop.pk: choice(shop, method)},
        ADDRESS,
        CONTACT,
        NOW,
    )
    part = order.shop_orders.get()
    assert order.total_incl_tax == goods + delivery
    assert order.lines.get().line_price_incl_tax == goods
    assert part.base_goods_total == Decimal(2500)
    assert part.partner_total == Decimal(2500)
    assert part.commission_total == markup
    assert part.delivery_total == delivery
    assert part.pickup_discount_total == discount
    assert part.delivery_owner == "platform"
    shop.markup_percent = 35
    shop.delivery_fee = 900
    shop.save()
    stock = listing.stockrecord
    stock.price = 5000
    stock.save()
    part.refresh_from_db()
    assert part.partner_total == 2500
    assert part.commission_total == markup


def test_discount_cannot_eat_store_price_and_rounds_each_unit(shop, listing):
    shop.markup_percent = Decimal(10)
    shop.pickup_discount_percent = Decimal(100)
    shop.save()
    stock = listing.stockrecord
    stock.price = Decimal("1000.05")
    stock.save()
    assert listing.pickup_price == Decimal("1000.05")
    shop.pickup_discount_percent = Decimal(50)
    shop.save()
    assert listing.price == Decimal("1100.06")
    assert listing.pickup_price == Decimal("1050.05")


def test_partner_cannot_set_platform_pricing(shop):
    form = ShopForm(instance=shop)
    assert "markup_percent" not in form.fields
    assert "pickup_discount_percent" not in form.fields
    assert "delivery_fee" not in form.fields
    admin = ShopForm(instance=shop, operator=True)
    assert {
        "markup_percent",
        "pickup_discount_percent",
        "delivery_fee",
    } <= admin.fields.keys()


def test_minimum_order_uses_store_base_price(owner, shop, listing):
    configure(shop)
    shop.minimum_order = 2600
    shop.save()
    with pytest.raises(ValidationError):
        place_market_order(
            owner,
            basket_for(owner, listing),
            {shop.pk: choice(shop, "delivery")},
            ADDRESS,
            CONTACT,
            NOW,
        )
