from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from oscar.core.loading import get_model

from market.models import ShopOrder

pytestmark = pytest.mark.django_db


def make_part(shop, number, *, age=0, goods=2500, payment="paid", status="completed"):
    created = timezone.now() - timedelta(days=age)
    order = get_model("order", "Order").objects.create(
        number=number,
        currency="RUB",
        total_incl_tax=Decimal(goods) + 350,
        total_excl_tax=Decimal(goods) + 350,
        shipping_incl_tax=350,
        shipping_excl_tax=350,
        date_placed=created,
    )
    part = ShopOrder.objects.create(
        order=order,
        shop=shop,
        method="delivery",
        slot_start=created,
        slot_end=created + timedelta(hours=1),
        address="Москва",
        contact_name="Покупатель",
        contact_phone="+70000000000",
        goods_total=goods,
        delivery_total=350,
        commission_percent=10,
        commission_total=Decimal(goods) / 10,
        partner_total=Decimal(goods) * Decimal("0.9") + 350,
        payment_status=payment,
        status=status,
    )
    ShopOrder.objects.filter(pk=part.pk).update(created_at=created)
    return part


def test_order_json_requires_login_own_shop_or_operator(
    client, owner, shop, other_shop
):
    url = f"/partner/{shop.slug}/orders/data/"
    assert client.get(url).status_code == 302
    client.force_login(owner)
    assert client.get(url).status_code == 200
    assert client.get(f"/partner/{other_shop.slug}/orders/data/").status_code == 404
    assert client.get("/operator/orders/data/").status_code == 403
    assert client.post(url).status_code == 405


def test_partner_json_searches_entire_period_without_foreign_orders(
    client, owner, shop, other_shop
):
    for index in range(25):
        make_part(shop, f"order-{index:02}", age=index % 5)
    make_part(shop, "needle-outside-first-page", age=5)
    make_part(shop, "needle-outside-period", age=15)
    make_part(other_shop, "needle-foreign")
    client.force_login(owner)
    url = f"/partner/{shop.slug}/orders/data/"
    page = client.get(url, {"days": "7", "draw": "3", "length": "20"}).json()
    assert page["draw"] == 3
    assert page["recordsTotal"] == 26
    assert page["recordsFiltered"] == 26
    assert len(page["data"]) == 20
    assert "needle-outside-first-page" not in str(page["data"])
    result = client.get(url, {"days": "7", "search[value]": "needle"}).json()
    assert result["recordsTotal"] == 26
    assert result["recordsFiltered"] == 1
    assert "needle-outside-first-page" in result["data"][0][0]
    assert "foreign" not in str(result)


def test_operator_json_includes_shops_and_searches_localized_status(
    client, shop, other_shop
):
    make_part(shop, "received")
    make_part(other_shop, "pending", payment="pending", status="accepted")
    make_part(other_shop, "preparing", status="preparing")
    operator = get_user_model().objects.create_superuser(
        "table-operator", "operator@example.invalid", "secret"
    )
    client.force_login(operator)
    url = "/operator/orders/data/"
    result = client.get(url, {"search[value]": "ОЖИДАЕТ ОПЛАТЫ"}).json()
    assert result["recordsTotal"] == 3
    assert result["recordsFiltered"] == 1
    assert result["data"][0][2] == other_shop.name
    assert result["data"][0][4] == "Ожидает оплаты"
    result = client.get(url, {"search[value]": "СОБИРАЕТСЯ"}).json()
    assert result["recordsFiltered"] == 1
    assert "preparing" in result["data"][0][0]
    shop_result = client.get(url, {"search[value]": "ЦВЕТЫ ХИМКИ"}).json()
    assert shop_result["recordsFiltered"] == 2
    assert all(row[2] == "Цветы Химки" for row in shop_result["data"])


def test_json_sorts_money_numerically_and_orders_by_number(client, owner, shop):
    make_part(shop, "C", goods=1000)
    make_part(shop, "A", goods=90)
    make_part(shop, "B", goods=9)
    client.force_login(owner)
    url = f"/partner/{shop.slug}/orders/data/"
    amounts = client.get(url, {"order[0][column]": "3", "order[0][dir]": "asc"}).json()[
        "data"
    ]
    assert [row[3] for row in amounts] == ["359 ₽", "440 ₽", "1\u00a0350 ₽"]
    assert "B" in amounts[0][0] and "A" in amounts[1][0] and "C" in amounts[2][0]
    numbers = client.get(
        url, {"order[0][column]": "0", "order[0][dir]": "desc"}
    ).json()["data"]
    assert [row[0].split("№ ")[1].split(" /")[0] for row in numbers] == ["C", "B", "A"]


def test_json_bounds_invalid_parameters_and_escapes_stored_html(
    client, owner, shop, other_shop
):
    for index in range(105):
        make_part(shop, f"safe-{index:03}")
    dangerous = make_part(shop, '<img src=x onerror="alert(1)">')
    other_shop.name = '<svg onload="alert(2)">'
    other_shop.save(update_fields=["name"])
    make_part(other_shop, "foreign")
    client.force_login(owner)
    url = f"/partner/{shop.slug}/orders/data/"
    result = client.get(
        url,
        {
            "draw": "<script>",
            "start": "-1",
            "length": "999999999",
            "order[0][column]": "order__user__password",
            "order[0][dir]": "SQL",
            "days": "bad",
        },
    ).json()
    assert result["draw"] == 0
    assert result["recordsTotal"] == 106
    assert len(result["data"]) == 100
    assert "&lt;img" in result["data"][0][0]
    assert "<img" not in result["data"][0][0]
    assert f"/orders/{dangerous.pk}/" in result["data"][0][0]
    negative_length = client.get(url, {"length": "-1"}).json()
    assert len(negative_length["data"]) == 20
    assert client.get(url, {"start": "999999999999999999"}).json()["data"] == []
    operator = get_user_model().objects.create_superuser(
        "escaping-operator", "operator@example.invalid", "secret"
    )
    client.force_login(operator)
    row = client.get("/operator/orders/data/", {"search[value]": "foreign"}).json()[
        "data"
    ][0]
    assert row[2] == "&lt;svg onload=&quot;alert(2)&quot;&gt;"


def test_json_paginates_stably_and_ignores_out_of_range_sort_column(
    client, owner, shop
):
    first = make_part(shop, "first", status="accepted")
    second = make_part(shop, "second", status="completed")
    newest = make_part(shop, "newest", status="preparing")
    created = timezone.now()
    ShopOrder.objects.filter(pk__in=[first.pk, second.pk, newest.pk]).update(
        created_at=created
    )
    client.force_login(owner)
    url = f"/partner/{shop.slug}/orders/data/"
    query = {
        "start": "1",
        "length": "1",
        "order[0][column]": "999",
        "order[0][dir]": "desc",
    }
    result = client.get(url, query).json()
    assert result["recordsFiltered"] == 3
    assert len(result["data"]) == 1
    assert "second" in result["data"][0][0]


def test_json_search_matches_displayed_money_receiving_method_and_date(
    client, owner, shop
):
    recent = make_part(shop, "recent", goods=2150)
    make_part(shop, "old", age=2, goods=1000)
    client.force_login(owner)
    url = f"/partner/{shop.slug}/orders/data/"
    amount = client.get(url, {"search[value]": "2\u00a0500 ₽"}).json()
    assert amount["recordsFiltered"] == 1
    assert "recent" in amount["data"][0][0]
    delivery = client.get(url, {"search[value]": "ДОСТАВКА"}).json()
    assert delivery["recordsFiltered"] == 2
    date_result = client.get(
        url,
        {"search[value]": timezone.localtime(recent.slot_start).strftime("%d.%m.%Y")},
    ).json()
    assert date_result["recordsFiltered"] == 1


def test_dashboard_supplies_scoped_data_url_and_safe_chart_data(client, owner, shop):
    import json
    from html.parser import HTMLParser

    class ReportParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.url = None
            self.script_data = ""
            self.in_chart_data = False

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "table" and attrs.get("data-orders-url"):
                self.url = attrs["data-orders-url"]
            if tag == "script" and attrs.get("id") == "sales-trend-data":
                self.in_chart_data = True

        def handle_endtag(self, tag):
            if tag == "script":
                self.in_chart_data = False

        def handle_data(self, data):
            if self.in_chart_data:
                self.script_data += data

    make_part(shop, "report-order")
    client.force_login(owner)
    response = client.get(f"/partner/{shop.slug}/", {"days": "7"})
    parser = ReportParser()
    parser.feed(response.content.decode())
    assert parser.url == f"/partner/{shop.slug}/orders/data/"
    assert client.get(parser.url, {"days": "7"}).json()["recordsTotal"] == 1
    points = json.loads(parser.script_data)
    assert len(points) == 7
    assert sum(Decimal(point["value"]) for point in points) == 2500
