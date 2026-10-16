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


def test_buyer_pays_the_shop_price_plus_the_markup(shop, listing):
    shop.markup_percent = Decimal(4)
    shop.save()
    info = MarketplaceStrategy().fetch_for_product(listing.product)
    # The shop's price stays its own; Romeo's markup is added on top.
    assert info.price.incl_tax == Decimal(2600)
    assert listing.pickup_price == Decimal(2500)
    assert listing.price == Decimal(2600)
    assert listing.price_for("pickup") == Decimal(2600)


def test_new_shops_get_four_percent_markup():
    from market.models import Shop

    assert Shop._meta.get_field("markup_percent").default == 4


def test_administrator_sale_price_replaces_the_markup(shop, listing):
    shop.markup_percent = Decimal(4)
    shop.save()
    listing.sale_prices = {"standard": {"pickup": "10000"}}
    listing.save()
    assert listing.price_for("pickup") == listing.price_for("delivery") == 10000
    quote = quote_product(shop, Decimal(3000), sale_price=Decimal(10000))
    assert (quote.customer, quote.commission, quote.payout) == (10000, 7000, 3000)


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


def test_markup_price_is_rounded_to_whole_roubles(shop):
    shop.markup_percent = Decimal(4)
    quote = quote_product(shop, Decimal("1010.05"))
    assert (quote.customer, quote.commission, quote.payout) == (
        Decimal(1050),
        Decimal("39.95"),
        Decimal("1010.05"),
    )


def test_platform_delivery_is_included_and_the_shop_gets_its_price(
    owner, shop, listing
):
    shop.markup_percent = Decimal(10)
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
    assert order.total_incl_tax == 2750
    assert part.goods_total == Decimal(2750)
    assert part.commission_total == Decimal(250)
    assert part.partner_total == part.base_goods_total == Decimal(2500)
    assert part.delivery_total == Decimal(0)
    assert part.delivery_owner == "platform"
    assert part.pickup_discount_total == 0
    # Later changes of prices, markup or fees never touch a placed order.
    shop.markup_percent = 35
    shop.delivery_fee = 900
    shop.save()
    stock = listing.stockrecord
    stock.price = 5000
    stock.save()
    part.refresh_from_db()
    assert (part.goods_total, part.partner_total, part.commission_total) == (
        Decimal(2750),
        Decimal(2500),
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
    shop.markup_percent = Decimal(10)
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
    customer = Decimal(goods) * Decimal("1.1")
    assert order.total_incl_tax == customer
    assert part.goods_total == customer
    assert part.delivery_total == 0
    assert part.delivery_owner == delivery_owner
    assert part.partner_total == goods
    assert part.commission_total == customer - goods


def test_partner_cannot_set_platform_pricing(shop):
    form = ShopForm(instance=shop)
    assert "markup_percent" not in form.fields
    assert "delivery_fee" not in form.fields
    admin = ShopForm(instance=shop, operator=True)
    assert {"status", "markup_percent", "delivery_fee"} <= admin.fields.keys()


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
