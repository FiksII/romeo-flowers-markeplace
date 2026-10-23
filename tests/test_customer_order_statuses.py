import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from market.orders import transition_shop_order
from tests.test_fulfillment_statuses import paid_part

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "method,delivery_owner",
    [
        ("delivery", "platform"),
        ("delivery", "partner"),
        ("pickup", "platform"),
    ],
)
def test_customer_sees_every_transition_in_list_and_details(
    client, owner, buyer, stranger, shop, listing, method, delivery_owner
):
    # Customer is not a shop member. Fulfillment changes come from other users.
    part = paid_part(stranger, shop, listing, method, delivery_owner)
    operator = get_user_model().objects.create_superuser(
        "romeo", "op@example.com", "secret"
    )
    client.force_login(stranger)
    stages = [("preparing", "Собирается"), ("ready", "Готов")]
    if method == "delivery":
        stages.append(("courier", "Передан курьеру"))
    stages.append(("completed", "Выдан" if method == "pickup" else "Доставлен"))
    for status, label in stages:
        actor = (
            operator
            if status == "completed"
            and method == "delivery"
            and delivery_owner == "platform"
            else owner
        )
        transition_shop_order(part, actor, status)
        for path in [
            reverse("market:account"),
            reverse("market:order", args=[part.order.number]),
        ]:
            response = client.get(path)
            assert response.status_code == 200
            html = response.content.decode()
            assert f'class="status-chip">{label}</span>' in html
            assert (shop.name in html) is (method == "pickup")
    client.force_login(buyer)
    assert (
        client.get(reverse("market:order", args=[part.order.number])).status_code == 404
    )
    assert (
        str(part.order.number) not in client.get(reverse("market:account")).content.decode()
    )
