from decimal import Decimal

import pytest
from oscar.apps.partner.strategy import Default
from oscar.core.loading import get_model

from market.orders import place_market_order
from tests.test_orders import CONTACT, NOW, basket_for, choice

pytestmark = pytest.mark.django_db


def test_locked_price_is_used_for_every_order_total(monkeypatch, owner, shop, listing):
    Basket = get_model("basket", "Basket")
    original = Basket.all_lines
    updated = False

    def intervening_price_change(basket):
        nonlocal updated
        result = original(basket)
        if not updated and basket.pk:
            for line in result:
                # A cached display price existed before acquiring the stock lock.
                line._info = Default().fetch_for_product(line.product)
            get_model("partner", "StockRecord").objects.filter(
                pk=listing.stockrecord.pk
            ).update(price=Decimal(3300))
            updated = True
        return result

    basket = basket_for(owner, listing)
    monkeypatch.setattr(Basket, "all_lines", intervening_price_change)
    order = place_market_order(
        owner, basket, {shop.pk: choice(shop)}, None, CONTACT, NOW
    )
    assert order.total_incl_tax == Decimal(3300)
    assert order.shop_orders.get().goods_total == Decimal(3300)
    assert order.lines.get().line_price_incl_tax == Decimal(3300)


def test_listing_partner_selected_and_offers_disabled(client, listing, other_shop):
    stock = listing.stockrecord
    stock.delete()
    get_model("partner", "StockRecord").objects.create(
        product=listing.product,
        partner=other_shop.partner,
        partner_sku="foreign-first",
        price_currency="RUB",
        price=Decimal(1),
        num_in_stock=5,
    )
    own = get_model("partner", "StockRecord").objects.create(
        product=listing.product,
        partner=listing.shop.partner,
        partner_sku="own-second",
        price_currency="RUB",
        price=Decimal(2500),
        num_in_stock=5,
    )
    client.post(f"/basket/add/{listing.pk}/")
    basket = client.get("/basket/").wsgi_request.basket
    assert basket.all_lines().get().stockrecord_id == own.pk
