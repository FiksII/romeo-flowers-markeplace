from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from market.models import AuditEntry
from market.orders import place_market_order
from tests.test_orders import ADDRESS, CONTACT, NOW, basket_for, choice

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(client):
    boss = get_user_model().objects.create_superuser(
        "boss", "boss@example.com", "long-secret-pass"
    )
    client.force_login(boss)
    return client


def url(listing):
    return f"/operator/listings/{listing.pk}/price/"


def test_only_superuser_can_open_the_price_page(client, owner, listing):
    assert client.get(url(listing)).status_code == 302
    client.force_login(owner)
    assert client.get(url(listing)).status_code == 403
    assert client.post(url(listing), {"pickup_price": "1"}).status_code == 403


def test_price_link_is_shown_to_superuser_only(client, admin_client, owner, listing):
    page = admin_client.get("/").content.decode()
    assert f"/operator/listings/{listing.pk}/price/" in page
    client.logout()
    client.force_login(owner)
    assert "/price/" not in client.get("/").content.decode()


def test_superuser_changes_price_and_old_orders_keep_theirs(
    admin_client, owner, shop, listing
):
    basket = basket_for(owner, listing)
    order = place_market_order(
        owner, basket, {shop.pk: choice(shop)}, ADDRESS, CONTACT, NOW
    )
    response = admin_client.post(
        url(listing), {"pickup_price": "3500", "next": "/products/1/"}
    )
    assert response.status_code == 302
    assert listing.stockrecord.price == Decimal(3500)
    assert listing.price_for("pickup") == Decimal(3500)
    part = order.shop_orders.get()
    assert part.items.get().unit_price == Decimal(2500)
    assert part.goods_total == Decimal(2500)
    assert AuditEntry.objects.filter(action__contains="Цена букета").exists()


def test_invalid_price_is_rejected(admin_client, listing):
    for value in ["", "0", "-5", "abc"]:
        assert (
            admin_client.post(url(listing), {"pickup_price": value}).status_code == 200
        )
    assert listing.stockrecord.price == Decimal(2500)


def test_own_delivery_price_is_optional_and_sizes_get_their_own_prices(
    admin_client, shop, listing
):
    shop.own_delivery = True
    shop.save()
    listing.size_prices = {
        "standard": {},
        "m": {"pickup": "3000.00", "delivery": "3300.00"},
    }
    listing.save()
    page = admin_client.get(url(listing))
    assert {
        "pickup_price",
        "delivery_price",
        "pickup_price_m",
        "delivery_price_m",
    } <= set(page.context["form"].fields)
    response = admin_client.post(
        url(listing),
        {
            "pickup_price": "2600",
            "delivery_price": "",
            "pickup_price_m": "3100",
            "delivery_price_m": "3400",
        },
    )
    assert response.status_code == 302
    listing.refresh_from_db()
    assert listing.delivery_price is None
    assert listing.size_prices["m"] == {"pickup": "3100", "delivery": "3400"}
    assert listing.stockrecord.price == Decimal(2600)


def test_external_next_url_is_ignored(admin_client, listing):
    response = admin_client.post(
        url(listing), {"pickup_price": "2700", "next": "https://evil.example/"}
    )
    assert response["Location"] == f"/products/{listing.pk}/"
