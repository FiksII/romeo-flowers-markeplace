from decimal import Decimal

import pytest
from django.utils import timezone
from oscar.core.loading import get_model

from market.addresses import encode_address
from market.availability import next_slot
from tests.test_orders import ADDRESS

pytestmark = pytest.mark.django_db


def test_context_optional_and_signed_address(client, city, shop, listing):
    assert (
        client.post(
            "/receiving/", {"method": "any", "when": "any", "next": "/catalogue/"}
        ).status_code
        == 302
    )
    assert client.get("/catalogue/").context["total_count"] == 1
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
    assert (
        client.get("/catalogue/").context["receiving"].address["value"]
        == ADDRESS["value"]
    )
    client.post(
        "/receiving/", {"method": "delivery", "when": "any", "address_token": "forged"}
    )
    assert (
        client.get("/catalogue/").context["receiving"].address["value"]
        == ADDRESS["value"]
    )
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


def test_catalogue_and_all_portal_forms_render(client, owner, shop, listing):
    for url in [
        "/",
        "/catalogue/",
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
        f"/partner/{shop.slug}/hours/",
        f"/partner/{shop.slug}/settings/",
        f"/partner/{shop.slug}/products/{listing.pk}/",
        "/partner/new/",
    ]:
        assert client.get(url).status_code == 200
