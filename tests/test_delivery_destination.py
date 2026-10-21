from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from market.addresses import encode_address
from market.models import AuditEntry
from tests.test_orders import ADDRESS
from tests.test_payments import order_part

pytestmark = pytest.mark.django_db


def destination_url(part):
    return f"/partner/{part.shop.slug}/orders/{part.pk}/destination/"


def pending_delivery(buyer, shop, listing, *, own=True):
    shop.own_delivery = own
    shop.save()
    part = order_part(buyer, shop, listing, "delivery")
    part.address = ""
    part.delivery_contact = "call"
    if own:
        part.slot_start = part.slot_end = None
    part.save()
    return part


def destination_data():
    start = (timezone.localtime() + timedelta(days=1)).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    return {
        "address": ADDRESS["value"],
        "address_token": encode_address(ADDRESS),
        "address_details": "Кв. 7, подъезд 2, домофон 7",
        "slot_start": start.strftime("%Y-%m-%dT%H:%M"),
        "slot_end": (start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
    }


def test_shop_saves_agreed_destination_and_buyer_sees_it(
    client, owner, stranger, shop, listing
):
    part = pending_delivery(stranger, shop, listing)
    client.force_login(owner)
    response = client.post(destination_url(part), destination_data())
    assert response.status_code == 302
    part.refresh_from_db()
    assert part.address == ADDRESS["value"]
    assert part.address_details == "Кв. 7, подъезд 2, домофон 7"
    assert part.delivery_point["latitude"] == "55.75"
    assert part.slot_end - part.slot_start == timedelta(hours=1)
    assert timezone.localtime(part.slot_start).hour == 12
    assert AuditEntry.objects.filter(shop=shop, actor=owner).count() == 1
    html = client.get(f"/partner/{shop.slug}/orders/").content.decode()
    assert "Изменить конечную точку" in html
    assert "Позвонить получателю и уточнить" not in html
    client.force_login(stranger)
    html = client.get(f"/account/orders/{part.order.number}/").content.decode()
    assert ADDRESS["value"] in html and "Кв. 7" in html
    assert "Позвонить получателю и уточнить" not in html


def test_superadmin_sets_romeo_destination_preserving_booked_interval(
    client, stranger, shop, listing
):
    part = pending_delivery(stranger, shop, listing, own=False)
    booked = part.slot_start, part.slot_end
    admin = get_user_model().objects.create_superuser("boss", "boss@test.ru", "secret")
    client.force_login(admin)
    assert client.post(destination_url(part), destination_data()).status_code == 302
    part.refresh_from_db()
    assert part.address == ADDRESS["value"]
    assert (part.slot_start, part.slot_end) == booked
    html = client.get("/operator/orders/").content.decode()
    assert "Доставка Ромео" in html
    assert "Изменить конечную точку" in html
    data = client.get("/operator/orders/data/").json()
    assert "Доставка Ромео" in str(data["data"])


def test_shop_cannot_change_romeo_destination(client, owner, stranger, shop, listing):
    part = pending_delivery(stranger, shop, listing, own=False)
    client.force_login(owner)
    for response in [
        client.get(destination_url(part)),
        client.post(destination_url(part), destination_data()),
    ]:
        assert response.status_code == 403
    html = client.get(f"/partner/{shop.slug}/orders/").content.decode()
    assert destination_url(part) not in html
    part.refresh_from_db()
    assert part.address == ""


def test_other_shop_and_buyer_cannot_change_destination(
    client, owner, stranger, other_shop, shop, listing
):
    part = pending_delivery(stranger, shop, listing)
    client.force_login(stranger)
    assert client.post(destination_url(part), destination_data()).status_code == 404
    part.refresh_from_db()
    assert part.address == ""


@pytest.mark.parametrize("status", ["completed", "cancelled"])
def test_terminal_orders_cannot_change_destination(
    client, owner, stranger, shop, listing, status
):
    part = pending_delivery(stranger, shop, listing)
    part.status = status
    part.save()
    client.force_login(owner)
    assert client.post(destination_url(part), destination_data()).status_code == 404
    part.refresh_from_db()
    assert part.address == ""


def test_pickup_has_no_destination_editor(client, owner, stranger, shop, listing):
    part = order_part(stranger, shop, listing)
    client.force_login(owner)
    assert client.get(destination_url(part)).status_code == 404
    html = client.get(f"/partner/{shop.slug}/orders/").content.decode()
    assert destination_url(part) not in html


@pytest.mark.parametrize(
    "change",
    ["forged", "mismatch", "outside", "missing_time", "reversed", "past"],
)
def test_invalid_destination_does_not_partially_save(
    client, owner, stranger, shop, listing, change
):
    part = pending_delivery(stranger, shop, listing)
    data = destination_data()
    if change == "forged":
        data["address_token"] = "forged"
    elif change == "mismatch":
        data["address"] = "Другой дом"
    elif change == "outside":
        address = {**ADDRESS, "latitude": "56.8", "longitude": "38.9"}
        data["address_token"] = encode_address(address)
    elif change == "missing_time":
        del data["slot_end"]
    elif change == "reversed":
        data["slot_end"] = data["slot_start"]
    elif change == "past":
        data["slot_start"] = "2020-01-01T12:00"
        data["slot_end"] = "2020-01-01T13:00"
    client.force_login(owner)
    response = client.post(destination_url(part), data)
    assert response.status_code == 200
    assert response.context["form"].errors
    part.refresh_from_db()
    assert part.address == ""
    assert part.slot_start is None
    assert not AuditEntry.objects.filter(shop=shop).exists()


def test_confirmed_point_can_be_edited_without_selecting_unchanged_house_again(
    client, owner, stranger, shop, listing
):
    part = pending_delivery(stranger, shop, listing)
    client.force_login(owner)
    assert client.post(destination_url(part), destination_data()).status_code == 302
    page = client.get(destination_url(part))
    form = page.context["form"]
    data = {
        "address": form["address"].value(),
        "address_token": form["address_token"].value(),
        "address_details": "Квартира 9",
    }
    assert client.post(destination_url(part), data).status_code == 302
    part.refresh_from_db()
    assert part.address_details == "Квартира 9"
    assert part.slot_start is not None


def test_unresolved_delivery_card_links_to_editor_and_recipient(
    client, owner, stranger, shop, listing
):
    part = pending_delivery(stranger, shop, listing)
    client.force_login(owner)
    for path in [
        f"/partner/{shop.slug}/orders/",
        f"/partner/{shop.slug}/orders/{part.pk}/",
    ]:
        html = client.get(path).content.decode()
        assert destination_url(part) in html
        assert f'href="tel:{part.contact_phone}"' in html
        assert "Установить конечную точку" in html
