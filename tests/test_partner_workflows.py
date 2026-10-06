from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from market.addresses import encode_address
from market.forms import ShopForm
from market.models import Shop
from market.orders import place_market_order, transition_shop_order
from tests.test_orders import ADDRESS, CONTACT, NOW, basket_for, choice

pytestmark = pytest.mark.django_db


def shop_data(shop):
    return {
        "name": shop.name,
        "description": "Обновлённое описание",
        "settlement": shop.settlement_id,
        "address": shop.address,
        "phone": "",
        "delivery_enabled": True,
        "pickup_enabled": True,
        "delivery_settlements": [shop.settlement_id],
        "radius_km": 15,
        "delivery_fee": 350,
        "minimum_order": 0,
        "prep_minutes": 60,
    }


def test_partner_profile_cannot_overwrite_new_moderation(shop):
    form = ShopForm(shop_data(shop), instance=shop)
    assert form.is_valid(), form.errors
    Shop.objects.filter(pk=shop.pk).update(
        status="suspended", markup_percent=Decimal(17)
    )
    form.save()
    shop.refresh_from_db()
    assert shop.status == "suspended"
    assert shop.markup_percent == Decimal(17)


def test_unpaid_fulfillment_and_paid_completion(owner, shop, listing):
    order = place_market_order(
        owner, basket_for(owner, listing), {shop.pk: choice(shop)}, None, CONTACT, NOW
    )
    part = order.shop_orders.get()
    with pytest.raises(ValidationError):
        transition_shop_order(part, owner, "preparing")
    part.payment_status = "paid"
    part.save(update_fields=["payment_status"])
    for status in ["preparing", "ready", "completed"]:
        part = transition_shop_order(part, owner, status)
    stock = listing.stockrecord
    assert stock.num_in_stock == 4 and stock.num_allocated == 0
    order.refresh_from_db()
    assert order.status == "Completed"


def test_schedule_settings_and_operator_scope(
    client, owner, stranger, shop, other_shop, listing
):
    client.force_login(owner)
    assert (
        client.post(
            f"/partner/{shop.slug}/hours/",
            {"weekday": "0", "method": "work", "start": "22:00", "end": "06:00"},
        ).status_code
        == 302
    )
    assert shop.hours.get(weekday=0, method="work").start_minute == 1320
    assert (
        client.post(
            f"/partner/{other_shop.slug}/hours/",
            {"weekday": "0", "method": "work", "start": "00:00", "end": "24:00"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/partner/{shop.slug}/hours/",
            {
                "form_kind": "exception",
                "day": "2026-12-31",
                "method": "delivery",
                "closed": "on",
                "start": "09:00",
                "end": "20:00",
            },
        ).status_code
        == 302
    )
    assert shop.date_exceptions.get().closed
    owner.is_superuser = True
    owner.save()
    response = client.get("/operator/")
    assert response.status_code == 200
    assert {row.pk for row in response.context["shops"]} == {shop.pk, other_shop.pk}


def test_new_shop_requires_signed_local_address_and_approval(client, owner, city):
    client.force_login(owner)
    data = {
        "name": "Новая мастерская",
        "settlement": city.pk,
        "address": ADDRESS["value"],
        "address_token": encode_address(ADDRESS),
        "delivery_enabled": "on",
        "pickup_enabled": "on",
        "radius_km": "10",
        "delivery_fee": "200",
        "minimum_order": "1500",
        "prep_minutes": "30",
        "status": "active",
        "commission_percent": "0",
    }
    response = client.post("/partner/new/", data)
    assert response.status_code == 302
    shop = Shop.objects.get(name="Новая мастерская")
    assert shop.status == "review"
    assert shop.memberships.get().user_id == owner.pk
    assert client.get(f"/shops/{shop.slug}/").status_code == 404
