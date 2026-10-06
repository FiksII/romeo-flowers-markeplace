from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from oscar.apps.partner.strategy import Default
from oscar.core.loading import get_model

from market.availability import next_slot
from market.models import Listing
from market.orders import cancel_shop_order, place_market_order

pytestmark = pytest.mark.django_db
NOW = datetime(2026, 10, 6, 10, tzinfo=ZoneInfo("Europe/Moscow"))
ADDRESS = {
    "region": "77",
    "city": "Москва",
    "value": "Москва, Тестовая улица, д. 1",
    "latitude": "55.75",
    "longitude": "37.61",
}
CONTACT = {"name": "Иван", "phone": "+79991234567"}


def basket_for(user, *items):
    basket = get_model("basket", "Basket").objects.create(owner=user)
    basket.strategy = Default()
    for item in items:
        basket.add_product(item.product)
    return basket


def choice(shop, method="pickup"):
    return {"method": method, "slot": next_slot(shop, method, NOW).value}


def test_mixed_order_and_idempotent_submission(owner, shop, other_shop, listing):
    product = get_model("catalogue", "Product").objects.create(
        title="Букет из Химок", product_class=listing.product.product_class
    )
    get_model("partner", "StockRecord").objects.create(
        product=product,
        partner=other_shop.partner,
        partner_sku="b1",
        price=Decimal(1800),
        price_currency="RUB",
        num_in_stock=5,
    )
    second = Listing.objects.create(product=product, shop=other_shop)
    shop.commission_percent = Decimal(10)
    shop.save()
    basket = basket_for(owner, listing, second)
    choices = {shop.pk: choice(shop, "delivery"), other_shop.pk: choice(other_shop)}
    order = place_market_order(owner, basket, choices, ADDRESS, CONTACT, NOW)
    assert order.total_incl_tax == Decimal(4650)
    assert order.shop_orders.count() == 2
    first = order.shop_orders.get(shop=shop)
    assert first.commission_total == Decimal(250)
    assert first.partner_total == Decimal(2600)
    assert first.address == ADDRESS["value"]
    assert (
        order.shop_orders.get(shop=other_shop).address
        == f"{other_shop.settlement.name}, {other_shop.address}"
    )
    assert first.payment_status == "pending"
    assert (
        place_market_order(owner, basket, choices, ADDRESS, CONTACT, NOW).pk == order.pk
    )
    stock = listing.stockrecord
    assert stock.num_allocated == 1
    stock.price = 9900
    stock.save()
    first.refresh_from_db()
    assert first.goods_total == Decimal(2500)
    assert first.lines.get().line_price_incl_tax == Decimal(2500)


def test_delivery_requires_verified_address(owner, shop, listing):
    basket = basket_for(owner, listing)
    with pytest.raises(ValidationError):
        place_market_order(
            owner, basket, {shop.pk: choice(shop, "delivery")}, None, CONTACT, NOW
        )
    assert not get_model("order", "Order").objects.exists()


def test_invalid_slot_and_stock_are_rechecked(owner, shop, listing):
    basket = basket_for(owner, listing)
    with pytest.raises(ValidationError):
        place_market_order(
            owner,
            basket,
            {shop.pk: {"method": "pickup", "slot": "2020-01-01T10:00:00+03:00"}},
            None,
            CONTACT,
            NOW,
        )
    stock = listing.stockrecord
    stock.num_in_stock = 0
    stock.save()
    with pytest.raises(ValidationError):
        place_market_order(owner, basket, {shop.pk: choice(shop)}, None, CONTACT, NOW)


def test_suspended_shop_rejected(owner, shop, listing):
    basket = basket_for(owner, listing)
    shop.status = "suspended"
    shop.save()
    with pytest.raises(ValidationError):
        place_market_order(owner, basket, {shop.pk: choice(shop)}, None, CONTACT, NOW)


def test_basket_ownership_enforced(owner, stranger, shop, listing):
    basket = basket_for(owner, listing)
    with pytest.raises(PermissionDenied):
        place_market_order(
            stranger, basket, {shop.pk: choice(shop)}, None, CONTACT, NOW
        )


def test_cancellation_releases_only_own_stock(owner, stranger, shop, listing):
    basket = basket_for(owner, listing)
    order = place_market_order(
        owner, basket, {shop.pk: choice(shop)}, None, CONTACT, NOW
    )
    part = order.shop_orders.get()
    with pytest.raises(PermissionDenied):
        cancel_shop_order(part, stranger)
    cancel_shop_order(part, owner)
    assert listing.stockrecord.num_allocated == 0
    cancel_shop_order(part, owner)
    assert listing.stockrecord.num_allocated == 0
