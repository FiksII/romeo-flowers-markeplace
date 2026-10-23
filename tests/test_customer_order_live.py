import pytest
from django.urls import reverse

from market.orders import transition_shop_order
from tests.test_fulfillment_statuses import paid_part

pytestmark = pytest.mark.django_db


def test_live_only_contains_customer_parts_and_current_labels(
    client, owner, stranger, shop, listing
):
    own = paid_part(stranger, shop, listing, method="pickup")
    foreign = paid_part(owner, shop, listing)
    client.force_login(stranger)
    url = reverse("market:customer-orders-live")
    for status, label in [
        ("preparing", "Собирается"),
        ("ready", "Готов"),
        ("completed", "Выдан"),
    ]:
        transition_shop_order(own, owner, status)
        response = client.get(url)
        assert response.status_code == 200
        assert "no-store" in response["Cache-Control"]
        assert "private" in response["Cache-Control"]
        assert response.json() == {
            "parts": [
                {"id": own.pk, "status": status, "label": label, "can_cancel": False}
            ]
        }
        assert str(foreign.pk) not in {
            str(row["id"]) for row in response.json()["parts"]
        }
    assert client.post(url).status_code == 405
    client.logout()
    assert client.get(url).status_code == 302


def test_live_delivery_labels_and_cancellation(client, owner, shop, listing):
    part = paid_part(owner, shop, listing)
    client.force_login(owner)
    url = reverse("market:customer-orders-live")
    for status, label in [
        ("ready", "Готов"),
        ("courier", "Передан курьеру"),
        ("completed", "Доставлен"),
        ("cancelled", "Отменён"),
    ]:
        part.status = status
        part.save(update_fields=["status"])
        data = client.get(url).json()["parts"][0]
        assert data["status"] == status and data["label"] == label
        assert data["can_cancel"] is False
    part.status = "accepted"
    part.payment_status = "pending"
    part.save(update_fields=["status", "payment_status"])
    assert client.get(url).json()["parts"][0]["can_cancel"] is True


def test_both_customer_views_include_live_hooks(client, owner, shop, listing):
    part = paid_part(owner, shop, listing)
    client.force_login(owner)
    for url in [
        reverse("market:account"),
        reverse("market:order", args=[part.order.number]),
    ]:
        html = client.get(url).content.decode()
        assert (
            f'data-customer-orders-live="{reverse("market:customer-orders-live")}"'
            in html
        )
        assert f'data-customer-order-status="{part.pk}"' in html
        assert "customer-orders.js" in html
