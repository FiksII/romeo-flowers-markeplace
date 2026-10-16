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
    assert client.post(url(listing), {"sale_pickup_standard": "1"}).status_code == 403


def test_price_link_is_shown_to_superuser_only(client, admin_client, owner, listing):
    page = admin_client.get("/").content.decode()
    assert f"/operator/listings/{listing.pk}/price/" in page
    client.logout()
    client.force_login(owner)
    assert "/price/" not in client.get("/").content.decode()


def test_page_shows_shop_price_markup_price_and_romeo_part(admin_client, shop, listing):
    shop.markup_percent = Decimal(4)
    shop.save()
    page = admin_client.get(url(listing))
    row = page.context["form"].rows[0]
    assert (row["partner"], row["by_markup"], row["current"]) == (2500, 2600, None)
    assert "100 ₽" in page.content.decode()


def test_sale_price_changes_what_buyers_pay_but_not_what_the_shop_gets(
    admin_client, owner, shop, listing
):
    shop.markup_percent = Decimal(4)
    shop.save()
    old = place_market_order(
        owner,
        basket_for(owner, listing),
        {shop.pk: choice(shop)},
        ADDRESS,
        CONTACT,
        NOW,
    ).shop_orders.get()
    response = admin_client.post(
        url(listing), {"sale_pickup_standard": "10000", "next": "/"}
    )
    assert response.status_code == 302
    listing.refresh_from_db()
    assert listing.sale_prices == {"standard": {"pickup": "10000"}}
    assert listing.stockrecord.price == Decimal(2500)
    assert listing.price_for("pickup") == listing.price_for("delivery") == 10000
    part = place_market_order(
        owner,
        basket_for(owner, listing),
        {shop.pk: choice(shop)},
        ADDRESS,
        CONTACT,
        NOW,
    ).shop_orders.get()
    assert (part.goods_total, part.partner_total, part.commission_total) == (
        10000,
        2500,
        7500,
    )
    item = part.items.get()
    assert (item.unit_price, item.unit_payout, item.unit_commission) == (
        10000,
        2500,
        7500,
    )
    old.refresh_from_db()
    assert old.goods_total == 2600
    assert AuditEntry.objects.filter(action__contains="Цена продажи").exists()


def test_empty_field_returns_to_the_markup(admin_client, shop, listing):
    listing.sale_prices = {"standard": {"pickup": "9000"}}
    listing.save()
    admin_client.post(url(listing), {"sale_pickup_standard": ""})
    listing.refresh_from_db()
    assert listing.sale_prices == {}
    assert listing.price_for("pickup") == 2500


def test_invalid_sale_price_is_rejected(admin_client, listing):
    for value in ["0", "-5", "abc"]:
        response = admin_client.post(url(listing), {"sale_pickup_standard": value})
        assert response.status_code == 200
    listing.refresh_from_db()
    assert listing.sale_prices == {}


def test_own_delivery_shop_has_a_sale_price_per_way_and_size(
    admin_client, shop, listing
):
    shop.own_delivery = True
    shop.save()
    listing.delivery_price = Decimal(2900)
    listing.size_prices = {
        "standard": {},
        "m": {"pickup": "3000.00", "delivery": "3300.00"},
    }
    listing.save()
    fields = admin_client.get(url(listing)).context["form"].fields
    assert {
        "sale_pickup_standard",
        "sale_delivery_standard",
        "sale_pickup_m",
        "sale_delivery_m",
    } == set(fields)
    admin_client.post(
        url(listing), {"sale_delivery_standard": "5000", "sale_pickup_m": "4000"}
    )
    listing.refresh_from_db()
    assert listing.price_for("delivery", "standard") == 5000
    assert listing.price_for("pickup", "standard") == 2500
    assert listing.price_for("pickup", "m") == 4000
    assert listing.price_for("delivery", "m") == 3300


def test_seller_saving_the_bouquet_keeps_romeo_sale_price(client, owner, shop, listing):
    listing.sale_prices = {"standard": {"pickup": "9000"}}
    listing.save()
    client.force_login(owner)
    response = client.post(
        f"/partner/{shop.slug}/products/{listing.pk}/",
        {
            "title": "Нежность",
            "description": "Розы",
            "category": "bouquet",
            "sizes": ["standard"],
            "size_config": "1",
            "pickup_price": "2700",
            "stock": "5",
            "is_public": "on",
        },
    )
    assert response.status_code == 302
    listing.refresh_from_db()
    assert listing.pickup_price == 2700
    assert listing.sale_prices == {"standard": {"pickup": "9000"}}


def test_external_next_url_is_ignored(admin_client, listing):
    response = admin_client.post(
        url(listing), {"sale_pickup_standard": "2700", "next": "https://evil.example/"}
    )
    assert response["Location"] == f"/products/{listing.pk}/"


def test_product_page_never_shows_the_shop_price_to_buyers(client, shop, listing):
    shop.own_delivery = True
    shop.markup_percent = Decimal(4)
    shop.save()
    listing.delivery_price = Decimal(2900)
    listing.save()
    page = client.get(f"/products/{listing.pk}/").content.decode()
    assert "Самовывоз — 2 600 ₽" in page
    assert "2 500" not in page
