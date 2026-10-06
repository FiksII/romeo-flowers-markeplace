from datetime import date, datetime, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone
from oscar.core.loading import get_model

from market.availability import next_slot
from market.models import Membership, Shop, ShopOrder
from market.orders import place_market_order
from market.strategy import MarketplaceStrategy

pytestmark = pytest.mark.django_db


def seed(**options):
    call_command("seed_demo_history", stdout=StringIO(), **options)


def test_history_seed_refuses_non_demo_database():
    with override_settings(MARKET_DEMO=False), pytest.raises(CommandError):
        seed()
    assert not get_user_model().objects.exists()
    assert not Shop.objects.exists()


@override_settings(MARKET_DEMO=True)
def test_history_has_six_weeks_accounts_and_consistent_shop_finances():
    seed(weeks=6, orders_per_day=2, end_date="2026-10-05")
    Order = get_model("order", "Order")
    orders = Order.objects.filter(number__startswith="DEMO-")
    days = {
        timezone.localdate(value)
        for value in orders.values_list("date_placed", flat=True)
    }
    assert min(days) == date(2026, 8, 25)
    assert max(days) == date(2026, 10, 5)
    assert len(days) == 42
    assert orders.count() == 84
    assert get_user_model().objects.filter(username__startswith="buyer").count() == 20
    assert get_user_model().objects.get(username="operator").is_superuser
    for shop in Shop.objects.all():
        member = Membership.objects.get(shop=shop)
        assert member.user.check_password("Romeo-demo-2026")
        assert shop.legal_name and shop.inn and shop.bank_account and shop.bank_bik
    assert set(Shop.objects.values_list("settlement__region", flat=True)) == {
        "77",
        "50",
    }
    assert ShopOrder.objects.filter(status="completed", payment_status="paid").exists()
    assert ShopOrder.objects.filter(status="cancelled").exists()
    assert ShopOrder.objects.filter(payment_status="refunded").exists()
    assert ShopOrder.objects.filter(method="delivery").exists()
    assert ShopOrder.objects.filter(method="pickup").exists()
    assert apps.get_model("market", "DemoPayout").objects.exists()
    for order in orders:
        parts = list(order.shop_orders.all())
        assert order.total_incl_tax == sum(part.total for part in parts)
        assert len({part.address for part in parts if part.method == "delivery"}) <= 1
        for part in parts:
            assert part.goods_total == sum(
                line.line_price_incl_tax for line in part.lines
            )
            assert (
                part.partner_total + part.commission_total + part.delivery_total
                == part.total
            )
            assert part.partner_total == part.base_goods_total
            assert timezone.localdate(part.created_at) == timezone.localdate(
                order.date_placed
            )
    # Historical orders no longer reserve current catalogue stock.
    assert (
        not get_model("partner", "StockRecord")
        .objects.filter(num_allocated__gt=0)
        .exists()
    )


@override_settings(MARKET_DEMO=True)
def test_history_rerun_preserves_credentials_prices_and_existing_order(
    owner, shop, listing
):
    basket = get_model("basket", "Basket").objects.create(owner=owner)
    basket.strategy = MarketplaceStrategy()
    basket.add_product(listing.product)
    manual = place_market_order(
        owner,
        basket,
        {
            shop.pk: {
                "method": "pickup",
                "slot": next_slot(shop, "pickup", timezone.now()).value,
            }
        },
        None,
        {"name": "Покупатель", "phone": "+70000000000"},
    )
    manual_number = str(manual.number)
    seed(weeks=1, orders_per_day=2, end_date="2026-09-01")
    Order = get_model("order", "Order")
    DemoPayout = apps.get_model("market", "DemoPayout")
    counts = (
        Order.objects.count(),
        ShopOrder.objects.count(),
        DemoPayout.objects.count(),
    )
    buyer = get_user_model().objects.get(username="buyer")
    buyer.set_password("Changed-password-123")
    buyer.save()
    seeded_stock = get_model("partner", "StockRecord").objects.get(
        partner_sku="DEMO-01"
    )
    seeded_stock.price = Decimal(7654)
    seeded_stock.save()
    seed(weeks=1, orders_per_day=2, end_date="2026-09-01")
    assert counts == (
        Order.objects.count(),
        ShopOrder.objects.count(),
        DemoPayout.objects.count(),
    )
    buyer.refresh_from_db()
    seeded_stock.refresh_from_db()
    assert buyer.check_password("Changed-password-123")
    assert seeded_stock.price == Decimal(7654)
    assert listing.stockrecord.num_in_stock == 5
    assert listing.stockrecord.num_allocated == 1
    manual.refresh_from_db()
    assert manual.number == manual_number
    assert manual.total_incl_tax == 2500
    assert manual.shop_orders.get().payment_status == "pending"
    assert Shop.objects.get(pk=shop.pk).name == "Тестовые цветы"


@override_settings(MARKET_DEMO=True)
def test_current_day_has_actionable_orders_and_seeded_dashboards(client):
    seed(weeks=2, orders_per_day=8)
    assert not ShopOrder.objects.filter(
        status="completed", slot_end__gt=timezone.now()
    ).exists()
    assert not ShopOrder.objects.filter(
        payment_status="refunded", slot_end__gt=timezone.now()
    ).exists()
    buyer = get_user_model().objects.get(username="buyer")
    client.force_login(buyer)
    assert client.get("/account/orders/").context["orders"]
    partner = get_user_model().objects.get(username="partner")
    client.force_login(partner)
    response = client.get("/partner/petal-studio/")
    assert response.status_code == 200
    assert response.context["stats"]["paid_total"] > 0
    assert response.context["stats"]["payout_total"] > 0
    assert len(response.context["trend"]) == 42
    assert ShopOrder.objects.filter(
        shop__slug="petal-studio", status="preparing", payment_status="paid"
    ).exists()
    assert (
        ShopOrder.objects.filter(payment_status="pending")
        .exclude(status="cancelled")
        .exists()
    )
    client.force_login(get_user_model().objects.get(username="operator"))
    overview = client.get("/operator/")
    assert (
        overview.context["stats"]["paid_total"]
        >= response.context["stats"]["paid_total"]
    )
    assert "Демо-выплаты" in overview.content.decode()
    # Simulated transfers must never appear as actual payouts outside demo mode.
    with override_settings(MARKET_DEMO=False):
        client.force_login(partner)
        assert (
            client.get("/partner/petal-studio/").context["stats"]["payout_total"] == 0
        )


@override_settings(MARKET_DEMO=True)
def test_rerun_settles_due_demo_payouts_without_recreating_orders():
    first = datetime(2026, 9, 30, 16, tzinfo=ZoneInfo("Europe/Moscow"))
    with patch(
        "market.management.commands.seed_demo_history.timezone.now", return_value=first
    ):
        seed(weeks=1, orders_per_day=4, end_date="2026-09-29")
    part = ShopOrder.objects.filter(
        created_at__date__gte=date(2026, 9, 27),
        status="completed",
        payment_status="paid",
        demo_payout__isnull=True,
    ).first()
    assert part is not None
    DemoPayout = apps.get_model("market", "DemoPayout")
    assert not DemoPayout.objects.filter(part=part).exists()
    before = get_model("order", "Order").objects.count()
    later = datetime(2026, 10, 6, 16, tzinfo=first.tzinfo)
    with patch(
        "market.management.commands.seed_demo_history.timezone.now", return_value=later
    ):
        seed(weeks=1, orders_per_day=4, end_date="2026-09-29")
    assert get_model("order", "Order").objects.count() == before
    assert timezone.localdate(DemoPayout.objects.get(part=part).paid_at) == date(
        2026, 10, 5
    )


@pytest.mark.parametrize(
    "options",
    [
        {"weeks": 0},
        {"weeks": 53},
        {"orders_per_day": 0},
        {"orders_per_day": 21},
        {"end_date": "wrong"},
        {"end_date": (timezone.localdate() + timedelta(days=1)).isoformat()},
    ],
)
@override_settings(MARKET_DEMO=True)
def test_invalid_history_options_do_not_write(options):
    with pytest.raises(CommandError):
        seed(**options)
    assert not Shop.objects.exists()
