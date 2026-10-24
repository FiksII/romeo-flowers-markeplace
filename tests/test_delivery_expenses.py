from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from market.models import AuditEntry, BalanceEntry, ShopOrder
from market.payments import record_online_payment, shop_balance
from market.reporting import dashboard_report
from tests.test_payments import order_part
from tests.test_reporting import NOW, part_for

pytestmark = pytest.mark.django_db


@pytest.fixture
def boss():
    return get_user_model().objects.create_superuser(
        "cost-boss", "cost@test.ru", "pass"
    )


def test_admin_saves_corrects_zeroes_and_clears_cost_without_changing_sale(
    client, boss, stranger, shop, listing
):
    part = order_part(stranger, shop, listing, "delivery", "online")
    part = record_online_payment(part)
    original = (
        part.total,
        part.partner_total,
        shop_balance(shop),
        BalanceEntry.objects.count(),
    )
    url = f"/partner/{shop.slug}/orders/{part.pk}/"
    client.force_login(boss)
    for raw, expected in [
        ("700.25", Decimal("700.25")),
        ("800", Decimal(800)),
        ("0", Decimal(0)),
        ("", None),
    ]:
        response = client.post(
            url, {"action": "delivery-cost", "actual_delivery_cost": raw}
        )
        assert response.status_code == 302
        part.refresh_from_db()
        assert part.actual_delivery_cost == expected
        assert (
            part.total,
            part.partner_total,
            shop_balance(shop),
            BalanceEntry.objects.count(),
        ) == original
        assert part.status == "accepted"
    assert (
        AuditEntry.objects.filter(
            actor=boss, action__contains="Расходы на доставку"
        ).count()
        == 4
    )


@pytest.mark.parametrize(
    "raw", ["-1", "1.001", "10000000000", "NaN", "Infinity", "abc"]
)
def test_invalid_cost_preserves_value_and_shows_error(client, boss, shop, raw):
    part = part_for(shop, "invalid-cost", NOW)
    client.force_login(boss)
    url = f"/partner/{shop.slug}/orders/{part.pk}/"
    assert (
        client.post(
            url, {"action": "delivery-cost", "actual_delivery_cost": "123.45"}
        ).status_code
        == 302
    )
    response = client.post(
        url, {"action": "delivery-cost", "actual_delivery_cost": raw}
    )
    assert response.status_code == 200
    assert response.context["delivery_cost_form"].errors
    part.refresh_from_db()
    assert part.actual_delivery_cost == Decimal("123.45")


def test_partner_cannot_edit_or_see_internal_delivery_cost(client, boss, owner, shop):
    part = part_for(shop, "hidden-cost", NOW)
    url = f"/partner/{shop.slug}/orders/{part.pk}/"
    client.force_login(boss)
    client.post(url, {"action": "delivery-cost", "actual_delivery_cost": "987.65"})
    client.force_login(owner)
    assert (
        client.post(
            url, {"action": "delivery-cost", "actual_delivery_cost": "1"}
        ).status_code
        == 403
    )
    html = client.get(url).content.decode()
    assert "actual_delivery_cost" not in html
    assert "Фактические расходы" not in html
    part.refresh_from_db()
    assert part.actual_delivery_cost == Decimal("987.65")
    assert part.status == "completed"


@pytest.mark.parametrize(
    "method,delivery_owner", [("pickup", "platform"), ("delivery", "partner")]
)
def test_non_romeo_delivery_rejects_cost(client, boss, shop, method, delivery_owner):
    part = part_for(shop, "wrong-cost-kind", NOW)
    part.method, part.delivery_owner = method, delivery_owner
    part.save(update_fields=["method", "delivery_owner"])
    client.force_login(boss)
    url = f"/partner/{shop.slug}/orders/{part.pk}/"
    assert (
        client.post(
            url, {"action": "delivery-cost", "actual_delivery_cost": "700"}
        ).status_code
        == 404
    )
    assert "actual_delivery_cost" not in client.get(url).content.decode()


def test_report_deducts_all_incurred_costs_and_flags_unknowns(
    client, boss, shop, other_shop
):
    from market.orders import set_delivery_cost

    paid = part_for(shop, "cost-paid", NOW)
    cancelled = part_for(
        shop, "cost-cancelled", NOW, payment="refunded", status="cancelled"
    )
    pending = part_for(shop, "cost-pending", NOW, payment="pending", status="accepted")
    unknown = part_for(shop, "cost-unknown", NOW)
    zero = part_for(shop, "cost-zero", NOW)
    foreign = part_for(other_shop, "cost-foreign", NOW)
    for part, amount in [
        (paid, "700"),
        (cancelled, "100"),
        (pending, "50"),
        (zero, "0"),
        (foreign, "9999"),
    ]:
        set_delivery_cost(part, boss, Decimal(amount))
    report = dashboard_report(shop.shop_orders.all(), "7", NOW)
    stats = report["stats"]
    assert stats["delivery_revenue"] == Decimal(1050)
    assert stats["delivery_cost"] == Decimal(850)
    assert stats["delivery_margin"] == Decimal(200)
    assert stats["platform_gross_total"] == Decimal(1800)
    assert stats["platform_total"] == Decimal(950)
    assert stats["missing_delivery_costs"] == 1
    assert report["trend_total"] == Decimal(7500)
    paid.refresh_from_db()
    cancelled.refresh_from_db()
    assert paid.platform_net_total == Decimal(-100)
    assert cancelled.platform_net_total == Decimal(-100)
    assert unknown.actual_delivery_cost is None


def test_cost_is_admin_only_audited_and_live_revision_changes(
    client, boss, owner, stranger, shop, listing
):
    from market.orders import set_delivery_cost

    part = order_part(stranger, shop, listing, "delivery")
    with pytest.raises(PermissionDenied):
        set_delivery_cost(part, owner, Decimal(700))
    client.force_login(boss)
    revision = client.get("/operator/live/").json()["revision"]
    set_delivery_cost(part, boss, Decimal(700))
    assert client.get("/operator/live/").json()["revision"] != revision
    assert AuditEntry.objects.filter(
        actor=boss, shop=shop, action__contains="700"
    ).exists()


@pytest.mark.parametrize(
    "amount",
    [
        Decimal(-1),
        Decimal("0.001"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal(10000000000),
    ],
)
def test_service_rejects_invalid_cost(boss, shop, amount):
    from market.orders import set_delivery_cost

    part = part_for(shop, "invalid-service", NOW)
    with pytest.raises(ValidationError):
        set_delivery_cost(part, boss, amount)


def test_database_prevents_negative_cost_and_cost_on_pickup(shop):
    part = part_for(shop, "db-cost", NOW)
    for values in [
        {"actual_delivery_cost": -1},
        {"method": "pickup", "actual_delivery_cost": 20},
    ]:
        with pytest.raises(IntegrityError), transaction.atomic():
            ShopOrder.objects.filter(pk=part.pk).update(**values)


def test_admin_can_record_gift_delivery_expense(client, boss, shop):
    part = part_for(shop, "gift-cost", NOW, payment="free")
    part.payment_method = "gift"
    part.goods_total = part.delivery_total = part.commission_total = (
        part.partner_total
    ) = 0
    part.save()
    client.force_login(boss)
    response = client.post(
        f"/partner/{shop.slug}/orders/{part.pk}/",
        {"action": "delivery-cost", "actual_delivery_cost": "500"},
    )
    assert response.status_code == 302
    part.refresh_from_db()
    assert part.actual_delivery_cost == Decimal(500)
    assert part.platform_net_total == Decimal(-500)
    assert not BalanceEntry.objects.filter(part=part).exists()


def test_history_displays_cost_only_to_admin(client, boss, owner, shop):
    from django.utils import timezone

    from market.orders import set_delivery_cost

    part = part_for(shop, "cost-history", timezone.now())
    set_delivery_cost(part, boss, Decimal("765.43"))
    client.force_login(boss)
    admin_data = client.get("/operator/orders/data/", {"history": "1"}).json()
    assert "765" in admin_data["data"][0][3]
    assert "Расходы на доставку" in admin_data["data"][0][3]
    admin_html = client.get("/operator/orders/").content.decode()
    assert "Расходы на доставку" in admin_html
    client.force_login(owner)
    partner_data = client.get(
        f"/partner/{shop.slug}/orders/data/", {"history": "1"}
    ).json()
    assert "765" not in partner_data["data"][0][3]


def test_report_excludes_old_expenses_and_non_romeo_deliveries(boss, shop):
    from datetime import timedelta

    from market.orders import set_delivery_cost

    old = part_for(shop, "cost-old", NOW - timedelta(days=10))
    set_delivery_cost(old, boss, Decimal(900))
    for method, delivery_owner in [("pickup", "platform"), ("delivery", "partner")]:
        part = part_for(shop, method, NOW)
        part.method, part.delivery_owner = method, delivery_owner
        part.save(update_fields=["method", "delivery_owner"])
    stats = dashboard_report(shop.shop_orders.all(), "7", NOW)["stats"]
    assert stats["delivery_revenue"] == 0
    assert stats["delivery_cost"] == 0
    assert stats["missing_delivery_costs"] == 0
    assert stats["platform_total"] == 500
