from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from market.availability import next_slot, shop_can_receive
from market.context import FulfillmentContext
from market.models import DateException, WeeklyHours

pytestmark = pytest.mark.django_db
MOSCOW = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 10, 6, 10, 0, tzinfo=MOSCOW)


def test_any_context_keeps_open_shop(shop):
    assert shop_can_receive(shop, FulfillmentContext(), NOW)


def test_city_filters_delivery_and_pickup(shop, oblast):
    assert not shop_can_receive(
        shop, FulfillmentContext(city_id=oblast.pk, method="delivery"), NOW
    )
    shop.delivery_settlements.add(oblast)
    assert shop_can_receive(
        shop, FulfillmentContext(city_id=oblast.pk, method="delivery"), NOW
    )
    assert not shop_can_receive(
        shop, FulfillmentContext(city_id=oblast.pk, method="pickup"), NOW
    )


def test_exact_address_radius_including_boundary(shop):
    context = FulfillmentContext(
        method="delivery",
        address={
            "latitude": "55.750000",
            "longitude": "37.610000",
            "region": "77",
            "value": "Москва",
        },
    )
    assert shop_can_receive(shop, context, NOW)
    context.address["latitude"] = "56.750000"
    assert not shop_can_receive(shop, context, NOW)
    context.address["region"] = "16"
    assert not shop_can_receive(shop, context, NOW)


def test_closed_shop_has_next_day_slot_and_today_filter_excludes(shop):
    shop.hours.all().delete()
    for weekday in range(7):
        for method in ["work", "delivery", "pickup"]:
            WeeklyHours.objects.create(
                shop=shop,
                weekday=weekday,
                method=method,
                start_minute=9 * 60,
                end_minute=20 * 60,
            )
    evening = datetime(2026, 10, 6, 21, tzinfo=MOSCOW)
    assert next_slot(shop, "delivery", evening).start == datetime(
        2026, 10, 7, 10, tzinfo=MOSCOW
    )
    assert shop_can_receive(shop, FulfillmentContext(), evening)
    assert not shop_can_receive(
        shop, FulfillmentContext(method="delivery", when="today"), evening
    )


def test_preparation_does_not_run_while_shop_closed(shop):
    shop.hours.filter(method="work").delete()
    for weekday in range(7):
        WeeklyHours.objects.create(
            shop=shop,
            weekday=weekday,
            method="work",
            start_minute=9 * 60,
            end_minute=20 * 60,
        )
    evening = datetime(2026, 10, 6, 19, 30, tzinfo=MOSCOW)
    assert next_slot(shop, "pickup", evening).start == datetime(
        2026, 10, 7, 10, tzinfo=MOSCOW
    )


def test_overnight_and_closed_date_override(shop):
    shop.hours.filter(method="pickup").delete()
    for weekday in range(7):
        WeeklyHours.objects.create(
            shop=shop,
            weekday=weekday,
            method="pickup",
            start_minute=22 * 60,
            end_minute=2 * 60,
        )
    evening = datetime(2026, 10, 6, 23, tzinfo=MOSCOW)
    assert next_slot(shop, "pickup", evening).start == datetime(
        2026, 10, 7, 0, tzinfo=MOSCOW
    )
    DateException.objects.create(
        shop=shop, day=date(2026, 10, 7), method="pickup", closed=True
    )
    assert next_slot(shop, "pickup", evening).start == datetime(
        2026, 10, 8, 22, tzinfo=MOSCOW
    )


def test_delivery_hours_distinct_from_store_hours(shop):
    shop.hours.filter(method="delivery").delete()
    for weekday in range(7):
        WeeklyHours.objects.create(
            shop=shop,
            weekday=weekday,
            method="delivery",
            start_minute=12 * 60,
            end_minute=15 * 60,
        )
    assert next_slot(shop, "delivery", NOW).start.hour == 12
    assert next_slot(shop, "pickup", NOW).start.hour == 11


def test_today_is_shop_timezone(shop):
    utc_now = datetime(2026, 10, 6, 22, tzinfo=UTC)
    slot = next_slot(shop, "pickup", utc_now)
    assert slot.start.date() == date(2026, 10, 7)
    assert shop_can_receive(
        shop, FulfillmentContext(when="today", method="pickup"), utc_now
    )


def test_unavailable_product_shop_does_not_publish(shop):
    shop.status = "suspended"
    shop.save()
    assert not shop_can_receive(shop, FulfillmentContext(), NOW)


def test_invalid_schedule_rejected(shop):
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        WeeklyHours(
            shop=shop, weekday=8, method="pickup", start_minute=500, end_minute=500
        ).full_clean()
