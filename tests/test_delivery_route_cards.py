import pytest
from django.contrib.auth import get_user_model

from tests.test_delivery_destination import pending_delivery

pytestmark = pytest.mark.django_db


def admin_board(client):
    boss = get_user_model().objects.create_superuser(
        "route-admin", "route@test.ru", "pass"
    )
    client.force_login(boss)
    html = client.get("/operator/orders/").content.decode()
    return html.split('id="romeo-deliveries"', 1)[1].split("</section>", 1)[0]


def test_admin_card_shows_route_once_with_recipient_payment_and_actions(
    client, stranger, shop, listing
):
    part = pending_delivery(stranger, shop, listing, own=False)
    part.address = "Москва, ул. Получателя, д. 12"
    part.address_details = "Подъезд 2, квартира 8"
    part.payment_method, part.payment_status = "online", "paid"
    part.save()
    html = admin_board(client)
    assert 'aria-label="Маршрут доставки"' in html
    assert "Откуда" in html and "Куда" in html
    assert html.count(shop.address) == 1
    assert html.count(part.address) == 1
    assert html.count(part.address_details) == 1
    assert html.index(shop.address) < html.index(part.address)
    assert part.contact_name in html and f'href="tel:{part.contact_phone}"' in html
    assert html.count("Оплачено онлайн. Получать деньги с клиента не нужно") == 1
    assert "Получить с клиента" not in html
    assert 'name="status"' in html and "Установить конечную точку" in html
    assert "#delivery-cost" in html


def test_unknown_destination_is_explicit_and_does_not_repeat_origin_as_destination(
    client, stranger, shop, listing
):
    part = pending_delivery(stranger, shop, listing, own=False)
    assert not part.address
    html = admin_board(client)
    assert 'aria-label="Маршрут доставки"' in html
    assert html.count(shop.address) == 1
    assert "Адрес ещё не уточнён" in html
    assert "Курьер Ромео: получить с клиента" in html
    assert "Установить конечную точку" in html


def test_admin_details_include_both_route_ends_without_repeating_destination(
    client, stranger, shop, listing
):
    part = pending_delivery(stranger, shop, listing, own=False)
    part.address = "Москва, ул. Получателя, д. 12"
    part.save(update_fields=["address"])
    admin_board(client)
    html = client.get(f"/partner/{shop.slug}/orders/{part.pk}/").content.decode()
    assert 'aria-label="Маршрут доставки"' in html
    assert html.count(shop.address) == 1 and html.count(part.address) == 1


def test_partner_cards_keep_their_existing_order_information(
    client, owner, stranger, shop, listing
):
    part = pending_delivery(stranger, shop, listing, own=False)
    client.force_login(owner)
    html = client.get(f"/partner/{shop.slug}/orders/").content.decode()
    assert 'aria-label="Маршрут доставки"' not in html
    assert part.contact_name in html
    assert "actual_delivery_cost" not in html


def test_operator_live_revision_tracks_changed_pickup_address(
    client, stranger, shop, listing
):
    pending_delivery(stranger, shop, listing, own=False)
    admin_board(client)
    before = client.get("/operator/live/").json()["revision"]
    shop.address = "Москва, новый адрес магазина, 25"
    shop.save(update_fields=["address"])
    after = client.get("/operator/live/").json()["revision"]
    assert before != after


def test_no_call_instruction_stays_visible_in_admin_card_and_details(
    client, stranger, shop, listing
):
    part = pending_delivery(stranger, shop, listing, own=False)
    part.address = "Москва, ул. Получателя, д. 12"
    part.delivery_contact = "no_call"
    part.save(update_fields=["address", "delivery_contact"])
    board = admin_board(client)
    details = client.get(f"/partner/{shop.slug}/orders/{part.pk}/").content.decode()
    for html in (board, details):
        assert "Доставить без звонка получателю" in html
