from decimal import Decimal

import pytest
from django.utils import timezone
from oscar.core.loading import get_model

from market.addresses import encode_address
from market.availability import next_slot
from tests.test_orders import ADDRESS

pytestmark = pytest.mark.django_db


def test_checkout_options_ignore_stale_catalogue_time(client, owner, shop, listing):
    client.force_login(owner)
    client.post(f"/basket/add/{listing.pk}/")
    session = client.session
    session["receiving"] = {"when": "date", "day": "2020-01-01", "hour": 3}
    session.save()
    group = client.get("/checkout/").context["groups"][0]
    assert group["slot_options"]["delivery"]
    assert group["slot_options"]["pickup"]


def test_checkout_can_clarify_delivery_with_recipient(client, owner, shop, listing):
    client.force_login(owner)
    client.post(f"/basket/add/{listing.pk}/")
    basket = client.get("/checkout/").wsgi_request.basket
    response = client.post(
        "/checkout/",
        {
            "basket_id": basket.pk,
            "name": "Иван",
            "phone": "+79991234567",
            "delivery_contact": "call",
            "instructions": "Поздравить с днём рождения",
            f"method_{shop.pk}": "delivery",
        },
    )
    assert response.status_code == 302
    order = get_model("order", "Order").objects.get(user=owner)
    part = order.shop_orders.get()
    assert order.total_incl_tax == Decimal(2500)
    assert order.shipping_incl_tax == 0
    assert part.delivery_contact == "call"
    assert part.slot_start is None
    assert part.address == ""
    assert part.instructions == "Поздравить с днём рождения"


def test_checkout_no_call_accepts_address_in_checkout(client, owner, shop, listing):
    client.force_login(owner)
    client.post(f"/basket/add/{listing.pk}/")
    basket = client.get("/checkout/").wsgi_request.basket
    data = {
        "basket_id": basket.pk,
        "name": "Иван",
        "phone": "+79991234567",
        "delivery_contact": "no_call",
        "address": ADDRESS["value"],
        "address_token": encode_address(ADDRESS),
        "address_details": "Кв. 5, домофон 5",
        f"method_{shop.pk}": "delivery",
        f"slot_{shop.pk}": next_slot(shop, "delivery", timezone.now()).value,
    }
    response = client.post("/checkout/", data)
    assert response.status_code == 302
    part = get_model("order", "Order").objects.get(user=owner).shop_orders.get()
    assert part.address == ADDRESS["value"]
    assert part.delivery_contact == "no_call"
    assert part.address_details == "Кв. 5, домофон 5"
    assert part.slot_start is not None


@pytest.mark.parametrize("missing", ["address_token", "slot"])
def test_checkout_no_call_requires_address_and_slot(
    client, owner, shop, listing, missing
):
    client.force_login(owner)
    client.post(f"/basket/add/{listing.pk}/")
    basket = client.get("/checkout/").wsgi_request.basket
    data = {
        "basket_id": basket.pk,
        "name": "Иван",
        "phone": "+79991234567",
        "delivery_contact": "no_call",
        "address": ADDRESS["value"],
        "address_token": encode_address(ADDRESS),
        f"method_{shop.pk}": "delivery",
        f"slot_{shop.pk}": next_slot(shop, "delivery", timezone.now()).value,
    }
    data.pop("address_token" if missing == "address_token" else f"slot_{shop.pk}")
    response = client.post("/checkout/", data)
    assert response.status_code == 200
    assert not get_model("order", "Order").objects.filter(user=owner).exists()


def test_context_optional_and_signed_address(client, city, shop, listing):
    assert (
        client.post(
            "/receiving/", {"method": "any", "when": "any", "next": "/"}
        ).status_code
        == 302
    )
    assert client.get("/").context["total_count"] == 1
    assert (
        client.post(
            "/receiving/",
            {
                "method": "delivery",
                "when": "any",
                "address_token": encode_address(ADDRESS),
            },
        ).status_code
        == 302
    )
    assert client.get("/").context["receiving"].address["value"] == ADDRESS["value"]
    client.post(
        "/receiving/", {"method": "delivery", "when": "any", "address_token": "forged"}
    )
    assert client.get("/").context["receiving"].address["value"] == ADDRESS["value"]
    client.post("/receiving/", {"clear": "1", "next": "https://evil.invalid/"})
    assert not client.session.get("receiving")


def test_buyer_checkout_http_and_duplicate_post(client, owner, shop, listing):
    client.force_login(owner)
    assert client.post(f"/basket/add/{listing.pk}/").status_code == 302
    response = client.get("/checkout/")
    assert response.status_code == 200
    basket = response.wsgi_request.basket
    data = {
        "basket_id": basket.pk,
        "name": "Иван",
        "phone": "+79991234567",
        f"method_{shop.pk}": "pickup",
        f"slot_{shop.pk}": next_slot(shop, "pickup", timezone.now()).value,
    }
    placed = client.post("/checkout/", data)
    assert placed.status_code == 302
    order = get_model("order", "Order").objects.get(user=owner)
    assert order.total_incl_tax == Decimal(2500)
    assert client.get(placed.url).status_code == 200
    assert client.post("/checkout/", data).url == placed.url
    assert get_model("order", "Order").objects.count() == 1


def test_home_listing_and_all_portal_forms_render(client, owner, shop, listing):
    for url in [
        "/",
        f"/products/{listing.pk}/",
        f"/shops/{shop.slug}/",
        "/basket/",
        "/login/",
        "/signup/",
        "/partner/",
    ]:
        assert client.get(url).status_code == 200
    client.force_login(owner)
    for url in [
        f"/partner/{shop.slug}/",
        f"/partner/{shop.slug}/products/",
        f"/partner/{shop.slug}/info/",
        f"/partner/{shop.slug}/payout/",
        f"/partner/{shop.slug}/products/{listing.pk}/",
        f"/partner/{shop.slug}/products/new/",
        "/partner/new/",
    ]:
        assert client.get(url).status_code == 200
