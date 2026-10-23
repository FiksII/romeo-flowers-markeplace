import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError

from market.orders import transition_shop_order
from tests.test_payments import order_part

pytestmark = pytest.mark.django_db


@pytest.fixture
def operator():
    return get_user_model().objects.create_superuser(
        "operator", "op@example.com", "secret"
    )


def paid_part(owner, shop, listing, method="delivery", delivery_owner="platform"):
    part = order_part(owner, shop, listing, method=method, payment="online")
    part.payment_status = "paid"
    part.delivery_owner = delivery_owner
    part.save(update_fields=["payment_status", "delivery_owner"])
    return part


@pytest.mark.parametrize(
    "method,delivery_owner,label",
    [
        ("delivery", "platform", "Доставлен"),
        ("delivery", "partner", "Доставлен"),
        ("pickup", "platform", "Выдан"),
    ],
)
def test_complete_chain(owner, operator, shop, listing, method, delivery_owner, label):
    part = paid_part(owner, shop, listing, method, delivery_owner)
    for status, display in [("preparing", "Собирается"), ("ready", "Готов")]:
        part = transition_shop_order(part, owner, status)
        assert part.get_status_display() == display
    if method == "delivery":
        part = transition_shop_order(part, owner, "courier")
        assert part.get_status_display() == "Передан курьеру"
    actor = operator if method == "delivery" and delivery_owner == "platform" else owner
    part = transition_shop_order(part, actor, "completed")
    assert part.get_status_display() == label
    part.order.refresh_from_db()
    assert part.order.status == "Completed"
    stock = listing.stockrecord
    stock.refresh_from_db()
    assert stock.num_allocated == 0 and stock.num_in_stock == 4


def test_delivery_cannot_skip_ready(owner, shop, listing):
    part = paid_part(owner, shop, listing)
    part = transition_shop_order(part, owner, "preparing")
    with pytest.raises(ValidationError):
        transition_shop_order(part, owner, "courier")


def test_shop_cannot_complete_romeo_delivery_even_by_post(client, owner, shop, listing):
    part = paid_part(owner, shop, listing)
    part.status = "courier"  # Existing orders already handed to a courier remain valid.
    part.save(update_fields=["status"])
    with pytest.raises(PermissionDenied):
        transition_shop_order(part, owner, "completed")
    client.force_login(owner)
    # Reverse keeps this regression tied to the real write endpoint.
    from django.urls import reverse

    url = reverse("market:partner-order", args=[shop.slug, part.pk])
    assert client.post(url, {"status": "completed"}).status_code == 403
    part.refresh_from_db()
    assert part.status == "courier"
    stock = listing.stockrecord
    stock.refresh_from_db()
    assert stock.num_allocated == 1 and stock.num_in_stock == 5


def test_role_aware_actions_in_cards_and_detail(client, owner, operator, shop, listing):
    from django.urls import reverse

    part = paid_part(owner, shop, listing)
    detail = reverse("market:partner-order", args=[shop.slug, part.pk])
    part.status = "preparing"
    part.save(update_fields=["status"])
    client.force_login(owner)
    for url in [detail, reverse("market:partner-orders", args=[shop.slug])]:
        html = client.get(url).content.decode()
        assert 'value="ready"' in html
        assert 'value="courier"' not in html
    part.status = "ready"
    part.save(update_fields=["status"])
    assert 'value="courier"' in client.get(detail).content.decode()
    part.status = "courier"
    part.save(update_fields=["status"])
    for url in [detail, reverse("market:partner-orders", args=[shop.slug])]:
        assert 'value="completed"' not in client.get(url).content.decode()
    client.force_login(operator)
    for url in [detail, reverse("market:operator-orders")]:
        html = client.get(url).content.decode()
        assert 'value="completed"' in html and "Доставлен" in html


def test_unpaid_online_cannot_be_prepared(owner, shop, listing):
    part = order_part(owner, shop, listing, method="delivery", payment="online")
    with pytest.raises(ValidationError):
        transition_shop_order(part, owner, "preparing")


def test_pickup_cannot_be_given_to_courier_or_complete_twice(owner, shop, listing):
    part = order_part(owner, shop, listing, payment="cash")
    for status in ["preparing", "ready"]:
        part = transition_shop_order(part, owner, status)
    assert part.next_step == ("completed", "Выдан, наличные приняты")
    with pytest.raises(ValidationError):
        transition_shop_order(part, owner, "courier")
    part = transition_shop_order(part, owner, "completed")
    assert part.payment_status == "paid" and part.get_status_display() == "Выдан"
    with pytest.raises(ValidationError):
        transition_shop_order(part, owner, "completed")
    stock = listing.stockrecord
    stock.refresh_from_db()
    assert stock.num_in_stock == 4 and stock.num_allocated == 0


def test_other_shop_cannot_change_order(stranger, owner, shop, listing):
    part = paid_part(owner, shop, listing)
    with pytest.raises(PermissionDenied):
        transition_shop_order(part, stranger, "preparing")
