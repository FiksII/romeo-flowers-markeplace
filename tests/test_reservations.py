from datetime import timedelta

import pytest
from django.utils import timezone

from market.orders import expire_unpaid_orders, place_market_order
from tests.test_orders import CONTACT, NOW, basket_for, choice

pytestmark = pytest.mark.django_db


def test_expiry_releases_stock_once_and_keeps_paid_parts(owner, shop, listing):
    order = place_market_order(
        owner, basket_for(owner, listing), {shop.pk: choice(shop)}, None, CONTACT, NOW
    )
    order.date_placed = timezone.now() - timedelta(minutes=31)
    order.save(update_fields=["date_placed"])
    assert expire_unpaid_orders() == 1
    assert expire_unpaid_orders() == 0
    assert listing.stockrecord.num_allocated == 0
    assert order.shop_orders.get().status == "cancelled"
    paid = place_market_order(
        owner, basket_for(owner, listing), {shop.pk: choice(shop)}, None, CONTACT, NOW
    )
    paid.date_placed = order.date_placed
    paid.save(update_fields=["date_placed"])
    paid.shop_orders.update(payment_status="paid")
    assert expire_unpaid_orders() == 0
    assert listing.stockrecord.num_allocated == 1
