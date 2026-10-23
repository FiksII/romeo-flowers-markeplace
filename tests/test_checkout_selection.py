from html.parser import HTMLParser

import pytest
from django.urls import reverse
from django.utils import timezone
from oscar.core.loading import get_model

from market.addresses import encode_address
from market.availability import next_slot
from tests.test_orders import ADDRESS

pytestmark = pytest.mark.django_db


class SelectedSlots(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.slots = {}
        self.name = None
        self.method = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "select" and "data-slot" in attrs:
            self.name = attrs["name"]
            self.slots[self.name] = []
        elif self.name and tag == "optgroup":
            self.method = attrs["data-slot-method"]
        elif self.name and tag == "option" and "selected" in attrs:
            self.slots[self.name].append((self.method, attrs["value"]))

    def handle_endtag(self, tag):
        if tag == "select":
            self.name = None
        elif tag == "optgroup":
            self.method = None


def assert_selected_slots(response, expected):
    assert response.status_code == 200
    assert SelectedSlots(response.content.decode()).slots == expected


def contact_data():
    return {
        "customer_name": "Анна",
        "customer_phone": "+79997654321",
        "name": "Иван",
        "phone": "+79991234567",
        "delivery_contact": "no_call",
        "address": ADDRESS["value"],
        "address_token": encode_address(ADDRESS),
    }


@pytest.mark.parametrize("method", ["delivery", "pickup"])
@pytest.mark.parametrize("own_delivery", [False, True])
def test_checkout_preserves_interval_after_contact_error(
    client, buyer, shop, listing, settings, method, own_delivery
):
    settings.MARKET_ONLINE_PAYMENTS = True
    shop.own_delivery = own_delivery
    shop.save()
    client.force_login(buyer)
    client.post(f"/basket/add/{listing.pk}/")
    basket = client.get("/checkout/").wsgi_request.basket
    slot = next_slot(shop, method)
    data = {
        **contact_data(),
        "basket_id": basket.pk,
        "phone": "123",
        f"method_{shop.pk}": method,
        f"slot_{shop.pk}": slot.value,
    }
    response = client.post("/checkout/", data)
    assert "phone" in response.context["form"].errors
    assert_selected_slots(response, {f"slot_{shop.pk}": [(method, slot.value)]})
    data["phone"] = "+79991234567"
    assert client.post("/checkout/", data).status_code == 302
    part = get_model("order", "Order").objects.get(user=buyer).shop_orders.get()
    assert part.slot_start == slot.start and part.slot_end == slot.end


@pytest.mark.parametrize("token", ["", "forged"])
def test_checkout_preserves_delivery_interval_after_address_error(
    client, buyer, shop, listing, settings, token
):
    settings.MARKET_ONLINE_PAYMENTS = True
    client.force_login(buyer)
    client.post(f"/basket/add/{listing.pk}/")
    basket = client.get("/checkout/").wsgi_request.basket
    slot = next_slot(shop, "delivery")
    data = {
        **contact_data(),
        "basket_id": basket.pk,
        "address_token": token,
        f"method_{shop.pk}": "delivery",
        f"slot_{shop.pk}": slot.value,
    }
    for _ in range(2):
        response = client.post("/checkout/", data)
        assert response.context["form"].errors
        assert_selected_slots(
            response, {f"slot_{shop.pk}": [("delivery", slot.value)]}
        )
    assert not get_model("order", "Order").objects.filter(user=buyer).exists()
    data["address_token"] = encode_address(ADDRESS)
    assert client.post("/checkout/", data).status_code == 302
    part = get_model("order", "Order").objects.get(user=buyer).shop_orders.get()
    assert part.slot_start == slot.start and part.slot_end == slot.end


def test_mixed_checkout_restores_each_shops_interval(
    client, buyer, shop, listing, other_shop, settings
):
    from tests.test_loyalty import second_listing

    settings.MARKET_ONLINE_PAYMENTS = True
    second = second_listing(listing, other_shop)
    client.force_login(buyer)
    for item in [listing, second]:
        client.post(f"/basket/add/{item.pk}/")
    basket = client.get("/checkout/").wsgi_request.basket
    data = {**contact_data(), "basket_id": basket.pk, "phone": "123"}
    expected = {}
    for target, method in [(shop, "delivery"), (other_shop, "pickup")]:
        slot = next_slot(target, method)
        data[f"method_{target.pk}"] = method
        data[f"slot_{target.pk}"] = slot.value
        expected[f"slot_{target.pk}"] = [(method, slot.value)]
    assert_selected_slots(client.post("/checkout/", data), expected)


@pytest.mark.parametrize("method", ["delivery", "pickup"])
def test_gift_checkout_preserves_interval_after_validation_error(
    client, buyer, shop, listing, method
):
    from tests.test_loyalty import purchase

    purchase(buyer, listing, 10)
    client.force_login(buyer)
    slot = next_slot(shop, method, timezone.now())
    data = {
        **contact_data(),
        "sequence": "1",
        "size": "standard",
        "payment_method": "gift",
        "phone": "123",
        f"method_{shop.pk}": method,
        f"slot_{shop.pk}": slot.value,
    }
    response = client.post(reverse("market:loyalty-gift", args=[listing.pk]), data)
    assert "phone" in response.context["form"].errors
    assert_selected_slots(response, {f"slot_{shop.pk}": [(method, slot.value)]})
