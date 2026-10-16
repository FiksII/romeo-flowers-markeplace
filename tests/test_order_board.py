import pytest

from market.orders import new_orders
from tests.test_payments import order_part
from tests.test_table_views import make_part

pytestmark = pytest.mark.django_db


def test_tab_shows_red_count_of_new_orders(client, owner, stranger, shop, listing):
    client.force_login(owner)
    assert "nav-badge" not in client.get(f"/partner/{shop.slug}/").content.decode()
    order_part(stranger, shop, listing)
    page = client.get(f"/partner/{shop.slug}/").content.decode()
    assert 'aria-label="Новых заказов: 1">1</span>' in page


def test_unpaid_online_order_is_not_counted_as_new(stranger, shop, listing):
    order_part(stranger, shop, listing, payment="online")
    assert new_orders(shop).count() == 0


def test_orders_page_splits_new_work_and_history(
    client, owner, stranger, shop, listing
):
    fresh = order_part(stranger, shop, listing)
    working = order_part(stranger, shop, listing, "delivery")
    client.force_login(owner)
    client.post(
        f"/partner/{shop.slug}/orders/{working.pk}/",
        {"status": "preparing", "next": "orders"},
    )
    done = make_part(shop, "history-order")
    response = client.get(f"/partner/{shop.slug}/orders/")
    assert [p.pk for p in response.context["new_parts"]] == [fresh.pk]
    assert [p.pk for p in response.context["work_parts"]] == [working.pk]
    assert [p.pk for p in response.context["parts"]] == [done.pk]
    html = response.content.decode()
    assert "Начать сборку" in html and "Передан курьеру" in html
    assert "Самовывоз" in html and "Доставка Ромео" in html
    assert 'data-history="1"' in html


def test_card_button_moves_order_and_returns_to_the_list(
    client, owner, stranger, shop, listing
):
    part = order_part(stranger, shop, listing)
    client.force_login(owner)
    response = client.post(
        f"/partner/{shop.slug}/orders/{part.pk}/",
        {"status": "preparing", "next": "orders"},
    )
    assert response["Location"] == f"/partner/{shop.slug}/orders/"
    part.refresh_from_db()
    assert part.status == "preparing"
    assert new_orders(shop).count() == 0


def test_history_data_excludes_active_orders(client, owner, stranger, shop, listing):
    order_part(stranger, shop, listing)
    make_part(shop, "history-only")
    client.force_login(owner)
    url = f"/partner/{shop.slug}/orders/data/"
    assert client.get(url).json()["recordsTotal"] == 2
    data = client.get(url, {"history": "1"}).json()
    assert data["recordsTotal"] == 1
    assert "history-only" in data["data"][0][0]
