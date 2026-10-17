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


def test_live_endpoint_reports_new_orders_and_payouts(
    client, owner, stranger, shop, other_shop, listing
):
    from django.contrib.auth import get_user_model

    from market.payments import create_payout, record_online_payment

    url = f"/partner/{shop.slug}/live/"
    client.force_login(owner)
    assert client.get(url).json() == {"new_orders": 0, "latest_order": 0, "payouts": 0}
    part = order_part(stranger, shop, listing)
    paid = order_part(stranger, shop, listing, payment="online")
    record_online_payment(paid)
    boss = get_user_model().objects.create_superuser("boss", "b@example.com", "pass")
    create_payout(shop, paid.partner_total, boss)
    response = client.get(url)
    assert response["Cache-Control"] == "no-store"
    assert response.json() == {
        "new_orders": 2,
        "latest_order": max(part.pk, paid.pk),
        "payouts": 1,
    }
    assert client.get(f"/partner/{other_shop.slug}/live/").status_code == 404
    client.logout()
    assert client.get(url).status_code == 302


def test_cabinet_pages_carry_the_polling_hooks(client, owner, stranger, shop, listing):
    part = order_part(stranger, shop, listing)
    client.force_login(owner)
    html = client.get(f"/partner/{shop.slug}/orders/").content.decode()
    assert f'data-partner-live="/partner/{shop.slug}/live/"' in html
    assert f'data-latest-order="{part.pk}"' in html
    assert 'data-new-orders="1"' in html
    assert "data-order-board" in html
    settings_page = client.get(f"/partner/{shop.slug}/settings/").content.decode()
    assert "data-browser-notify" in settings_page


def test_shop_cabinet_never_shows_romeo_income(client, owner, stranger, shop, listing):
    from decimal import Decimal

    from market.payments import record_online_payment

    shop.markup_percent = Decimal(20)
    shop.save()
    listing.sale_prices = {"standard": {"pickup": "9000"}}
    listing.save()
    online = order_part(stranger, shop, listing, payment="online")
    record_online_payment(online)
    client.force_login(owner)
    pages = [
        client.get(f"/partner/{shop.slug}/").content.decode(),
        client.get(f"/partner/{shop.slug}/orders/").content.decode(),
        client.get(f"/partner/{shop.slug}/orders/{online.pk}/").content.decode(),
        client.get(f"/partner/{shop.slug}/balance/").content.decode(),
        str(client.get(f"/partner/{shop.slug}/orders/data/").json()),
    ]
    for page in pages:
        assert "9 000" not in page  # buyer price of an order Romeo collected
        assert "6 500" not in page  # Romeo's part
        assert "Доля Ромео" not in page and "наценк" not in page.lower()
    assert "2 500" in pages[2]  # the shop's own amount


def test_cash_card_shows_only_the_amount_to_collect(
    client, owner, stranger, shop, listing
):
    from decimal import Decimal

    shop.markup_percent = Decimal(20)
    shop.save()
    part = order_part(stranger, shop, listing)  # cash, pickup: the shop collects
    client.force_login(owner)
    for path in [
        f"/partner/{shop.slug}/orders/",
        f"/partner/{shop.slug}/orders/{part.pk}/",
    ]:
        html = client.get(path).content.decode()
        assert "Получить с покупателя" in html
        assert "Вам 2\u00a0500" not in html and "Вам за заказ" not in html


def test_cabinet_pages_have_no_demo_notices(client, owner, shop, settings):
    settings.MARKET_DEMO = True
    client.force_login(owner)
    for path in [
        f"/partner/{shop.slug}/",
        f"/partner/{shop.slug}/orders/",
        f"/partner/{shop.slug}/payout/",
        "/",
    ]:
        html = client.get(path).content.decode()
        assert "Демонстрац" not in html and "Демо-" not in html and "Демо:" not in html
