import html
import re
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.test_delivery_destination import pending_delivery
from tests.test_delivery_route_cards import admin_board

pytestmark = pytest.mark.django_db


def route_href(page):
    match = re.search(r'<a[^>]*href="([^"]+)"[^>]*>Построить маршрут</a>', page)
    assert match, "Кнопка маршрута должна быть доступна при известном адресе"
    return html.unescape(match[1])


def test_card_and_details_link_from_shop_to_recipient_address(
    client, stranger, shop, listing
):
    part = pending_delivery(stranger, shop, listing, own=False)
    part.address = "Москва, улица Получателя, дом 12 & корпус 3"
    part.save(update_fields=["address"])
    board = admin_board(client)
    detail = client.get(f"/partner/{shop.slug}/orders/{part.pk}/").content.decode()
    link = route_href(board)
    assert route_href(detail) == link
    url = urlsplit(link)
    assert (url.scheme, url.netloc, url.path) == ("https", "yandex.ru", "/maps/")
    params = parse_qs(url.query)
    assert set(params) == {"mode", "rtt", "rtext"}
    assert params["rtt"] == ["auto"] and params["mode"] == ["routes"]
    origin, destination = params["rtext"][0].split("~")
    assert tuple(map(Decimal, origin.split(","))) == (
        Decimal("55.75"),
        Decimal("37.61"),
    )
    assert destination == part.address
    assert part.contact_phone not in link
    assert 'target="_blank"' in board and 'rel="noopener noreferrer"' in board


def test_confirmed_point_coordinates_take_precedence(stranger, shop, listing):
    from market.templatetags.market_tags import yandex_delivery_route

    part = pending_delivery(stranger, shop, listing, own=False)
    part.address = "Москва, улица Получателя, дом 12"
    part.delivery_point = {
        "latitude": "55.76",
        "longitude": "37.64",
        "value": part.address,
        "city": "Москва",
        "region": "77",
    }
    params = parse_qs(urlsplit(yandex_delivery_route(part)).query)
    assert params["rtext"][0].split("~")[1] == "55.76,37.64"


@pytest.mark.parametrize(
    "point",
    [
        None,
        [],
        {"latitude": "NaN", "longitude": "37.64"},
        {"latitude": "95", "longitude": "37.64"},
    ],
)
def test_bad_stored_point_falls_back_to_known_address(stranger, shop, listing, point):
    from market.templatetags.market_tags import yandex_delivery_route

    part = pending_delivery(stranger, shop, listing, own=False)
    part.address = "Москва, улица Получателя, дом 12"
    part.delivery_point = point
    params = parse_qs(urlsplit(yandex_delivery_route(part)).query)
    assert params["rtext"][0].split("~")[1] == part.address


def test_unknown_destination_and_partner_card_have_no_route_link(
    client, owner, stranger, shop, listing
):
    part = pending_delivery(stranger, shop, listing, own=False)
    assert "Построить маршрут" not in admin_board(client)
    part.address = "Москва, улица Получателя, дом 12"
    part.save(update_fields=["address"])
    client.force_login(owner)
    for path in [
        f"/partner/{shop.slug}/orders/",
        f"/partner/{shop.slug}/orders/{part.pk}/",
    ]:
        assert "Построить маршрут" not in client.get(path).content.decode()


def test_live_revision_changes_when_shop_coordinates_change(
    client, stranger, shop, listing
):
    pending_delivery(stranger, shop, listing, own=False)
    admin_board(client)
    before = client.get("/operator/live/").json()["revision"]
    shop.latitude = Decimal("55.751")
    shop.save(update_fields=["latitude"])
    assert client.get("/operator/live/").json()["revision"] != before
