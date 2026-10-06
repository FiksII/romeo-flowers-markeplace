from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.test import override_settings
from oscar.core.loading import get_model

from market.models import DemoPayout, ShopOrder
from market.reporting import dashboard_report

pytestmark = pytest.mark.django_db
NOW = datetime(2026, 10, 6, 16, tzinfo=ZoneInfo("Europe/Moscow"))


def part_for(shop, number, created, *, payment="paid", status="completed"):
    order = get_model("order", "Order").objects.create(
        number=number,
        currency="RUB",
        total_incl_tax=2850,
        total_excl_tax=2850,
        shipping_incl_tax=350,
        shipping_excl_tax=350,
        date_placed=created,
    )
    part = ShopOrder.objects.create(
        order=order,
        shop=shop,
        method="delivery",
        slot_start=created,
        slot_end=created,
        address="Москва",
        contact_name="Демо",
        contact_phone="+70000000000",
        goods_total=2500,
        base_goods_total=2250,
        delivery_total=350,
        commission_percent=10,
        commission_total=250,
        partner_total=2250,
        payment_status=payment,
        status=status,
    )
    ShopOrder.objects.filter(pk=part.pk).update(created_at=created)
    return part


@override_settings(MARKET_DEMO=True)
def test_report_period_refunds_pending_payments_and_shop_scope(shop, other_shop):
    recent = datetime(2026, 10, 5, 12, tzinfo=NOW.tzinfo)
    older = datetime(2026, 9, 20, 12, tzinfo=NOW.tzinfo)
    paid = part_for(shop, "report-paid", recent)
    part_for(shop, "report-pending", recent, payment="pending", status="accepted")
    part_for(shop, "report-refunded", recent, payment="refunded", status="cancelled")
    old_paid = part_for(shop, "report-old", older)
    foreign = part_for(other_shop, "report-foreign", recent)
    DemoPayout.objects.create(part=paid, amount=2250, paid_at=recent)
    DemoPayout.objects.create(part=old_paid, amount=2250, paid_at=older)
    DemoPayout.objects.create(part=foreign, amount=2250, paid_at=recent)
    report = dashboard_report(shop.shop_orders.all(), "7", NOW)
    assert report["stats"] == {
        "orders": 3,
        "created_total": Decimal(5000),
        "paid_total": Decimal(2500),
        "paid_base_total": Decimal(2250),
        "delivery_revenue": Decimal(350),
        "platform_total": Decimal(600),
        "expected_commission": Decimal(500),
        "commission_total": Decimal(250),
        "refunded_total": Decimal(2850),
        "payout_total": Decimal(2250),
        "payout_pending": Decimal(0),
    }
    assert sum(point["value"] for point in report["trend"]) == Decimal(2500)
    assert list(report["payouts"].values_list("part_id", flat=True)) == [paid.pk]
    assert dashboard_report(shop.shop_orders.all(), "invalid", NOW)["days"] == 42
    # No fixture transfer is reported as a real payment in production mode.
    with override_settings(MARKET_DEMO=False):
        assert (
            dashboard_report(shop.shop_orders.all(), "7", NOW)["stats"]["payout_total"]
            == 0
        )


@override_settings(MARKET_DEMO=True)
def test_report_includes_local_midnight_and_zero_days(shop):
    boundary = datetime(2026, 9, 30, 0, tzinfo=NOW.tzinfo)
    part_for(shop, "report-boundary", boundary)
    report = dashboard_report(shop.shop_orders.all(), "7", NOW)
    assert len(report["trend"]) == 7
    assert report["trend"][0]["value"] == Decimal(2500)
    assert report["trend"][1]["value"] == 0
    assert report["stats"]["payout_pending"] == Decimal(2250)


def test_chart_sums_same_day_orders_even_when_order_table_is_sorted(shop):
    morning = datetime(2026, 10, 5, 9, tzinfo=NOW.tzinfo)
    afternoon = datetime(2026, 10, 5, 14, tzinfo=NOW.tzinfo)
    part_for(shop, "report-morning", morning)
    part_for(shop, "report-afternoon", afternoon)
    report = dashboard_report(shop.shop_orders.order_by("-created_at"), "7", NOW)
    assert report["stats"]["paid_total"] == Decimal(5000)
    assert sum(point["value"] for point in report["trend"]) == Decimal(5000)
