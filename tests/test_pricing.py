from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from market.catalogue import public_listings
from market.context import FulfillmentContext
from market.forms import ShopForm
from market.orders import place_market_order
from market.pricing import quote_product
from market.strategy import MarketplaceStrategy
from tests.test_orders import ADDRESS, CONTACT, NOW, basket_for, choice

pytestmark = pytest.mark.django_db


def own_delivery(shop, listing, price=2900):
    shop.own_delivery = True
    shop.save()
    listing.delivery_price = Decimal(price)
    listing.save()


def test_buyer_pays_the_price_the_shop_set(shop, listing):
    shop.commission_percent = Decimal(10)
    shop.save()
    info = MarketplaceStrategy().fetch_for_product(listing.product)
    # Nothing is added on top: the commission comes out of the shop's share.
    assert info.price.incl_tax == Decimal(2500)
    assert listing.pickup_price == Decimal(2500)
    assert listing.price == Decimal(2500)
    assert listing.price_for("pickup") == Decimal(2500)


def test_own_delivery_has_its_own_price(shop, listing):
    own_delivery(shop, listing)
    assert listing.price_for("delivery") == Decimal(2900)
    assert listing.price_for("pickup") == Decimal(2500)
    assert MarketplaceStrategy(methods={shop.pk: "delivery"}).fetch_for_product(
        listing.product
    ).price.incl_tax == Decimal(2900)


def test_delivery_price_is_ignored_without_own_delivery(shop, listing):
    listing.delivery_price = Decimal(2900)
    listing.save()
    assert listing.price_for("delivery") == Decimal(2500)


def test_own_delivery_without_delivery_price_falls_back_to_pickup_price(shop, listing):
    shop.own_delivery = True
    shop.save()
    assert listing.delivery_price is None
    assert listing.price_for("delivery") == Decimal(2500)


def test_catalogue_filters_by_the_price_of_the_chosen_way(shop, listing):
    own_delivery(shop, listing)
    assert not public_listings(FulfillmentContext(), {"max_price": "2700"}, NOW)
    pickup = public_listings(
        FulfillmentContext(method="pickup"), {"max_price": "2700"}, NOW
    )
    assert pickup[0].display_price == Decimal(2500)
    delivery = public_listings(FulfillmentContext(), {"min_price": "2800"}, NOW)
    assert delivery[0].display_price == Decimal(2900)


def test_commission_is_rounded_per_unit(shop):
    shop.commission_percent = Decimal(10)
    quote = quote_product(shop, Decimal("1000.05"))
    assert (quote.customer, quote.commission, quote.payout) == (
        Decimal("1000.05"),
        Decimal("100.01"),
        Decimal("900.04"),
    )


def test_platform_delivery_is_charged_on_top_and_commission_comes_from_goods(
    owner, shop, listing
):
    shop.commission_percent = Decimal(10)
    shop.save()
    order = place_market_order(
        owner,
        basket_for(owner, listing),
        {shop.pk: choice(shop, "delivery")},
        ADDRESS,
        CONTACT,
        NOW,
    )
    part = order.shop_orders.get()
    assert order.total_incl_tax == 2500 + 350
    assert part.goods_total == Decimal(2500)
    assert part.commission_total == Decimal(250)
    assert part.partner_total == part.base_goods_total == Decimal(2250)
    assert part.delivery_total == Decimal(350)
    assert part.delivery_owner == "platform"
    assert part.pickup_discount_total == 0
    # Later changes of prices, commission or fees never touch a placed order.
    shop.commission_percent = 35
    shop.delivery_fee = 900
    shop.save()
    stock = listing.stockrecord
    stock.price = 5000
    stock.save()
    part.refresh_from_db()
    assert (part.goods_total, part.partner_total, part.commission_total) == (
        Decimal(2500),
        Decimal(2250),
        Decimal(250),
    )


@pytest.mark.parametrize(
    "method, goods, delivery_owner",
    [("delivery", 2900, "partner"), ("pickup", 2500, "platform")],
)
def test_own_delivery_order_has_no_romeo_delivery_fee(
    owner, shop, listing, method, goods, delivery_owner
):
    own_delivery(shop, listing)
    shop.commission_percent = Decimal(10)
    shop.save()
    order = place_market_order(
        owner,
        basket_for(owner, listing),
        {shop.pk: choice(shop, method)},
        ADDRESS,
        CONTACT,
        NOW,
    )
    part = order.shop_orders.get()
    assert order.total_incl_tax == goods
    assert part.goods_total == goods
    assert part.delivery_total == 0
    assert part.delivery_owner == delivery_owner
    assert part.commission_total == Decimal(goods) / 10
    assert part.partner_total == goods - part.commission_total


def test_partner_cannot_set_platform_pricing(shop):
    form = ShopForm(instance=shop)
    assert "commission_percent" not in form.fields
    assert "delivery_fee" not in form.fields
    admin = ShopForm(instance=shop, operator=True)
    assert {"status", "commission_percent", "delivery_fee"} <= admin.fields.keys()


def test_minimum_order_uses_the_goods_price(owner, shop, listing):
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
