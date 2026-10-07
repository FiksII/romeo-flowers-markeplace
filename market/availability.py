from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from market.zones import point_in_zone


@dataclass(frozen=True)
class Slot:
    start: datetime
    end: datetime

    @property
    def label(self):
        return f"{self.start:%d.%m} · {self.start:%H:%M}–{self.end:%H:%M}"

    @property
    def value(self):
        return self.start.isoformat()


def _rules(shop):
    return (
        {(row.weekday, row.method): row for row in shop.hours.all()},
        {(row.day, row.method): row for row in shop.date_exceptions.all()},
    )


def _source_window(shop, day, method, rules):
    weekly, exceptions = rules
    row = exceptions.get((day, method)) or weekly.get((day.weekday(), method))
    if row is None or getattr(row, "closed", False):
        return None
    midnight = datetime.combine(day, time.min, ZoneInfo(shop.timezone))
    start = midnight + timedelta(minutes=row.start_minute)
    end = midnight + timedelta(minutes=row.end_minute)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def _daily_windows(shop, day, method, rules):
    midnight = datetime.combine(day, time.min, ZoneInfo(shop.timezone))
    tomorrow = midnight + timedelta(days=1)
    windows = []
    sources = [day]
    if (day, method) not in rules[1]:
        sources.insert(0, day - timedelta(days=1))
    for source_day in sources:
        window = _source_window(shop, source_day, method, rules)
        if window:
            start, end = max(window[0], midnight), min(window[1], tomorrow)
            if start < end:
                windows.append((start, end))
    merged = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _ready_at(shop, now, rules):
    now = now.astimezone(ZoneInfo(shop.timezone))
    remaining = timedelta(minutes=shop.prep_minutes)
    for offset in range(32):
        day = now.date() + timedelta(days=offset)
        for start, end in _daily_windows(shop, day, "work", rules):
            start = max(start, now)
            if start >= end:
                continue
            if end - start >= remaining:
                return start + remaining
            remaining -= end - start
    return None


def available_slots(shop, method, now=None, day=None, hour=None, limit=96):
    if method not in {"delivery", "pickup"} or not getattr(shop, f"{method}_enabled"):
        return []
    now = (now or timezone.now()).astimezone(ZoneInfo(shop.timezone))
    rules = _rules(shop)
    ready = _ready_at(shop, now, rules)
    if ready is None or (
        day and not now.date() <= day <= now.date() + timedelta(days=30)
    ):
        return []
    days = [day] if day else [now.date() + timedelta(days=i) for i in range(31)]
    slots = []
    for target in days:
        for start, end in _daily_windows(shop, target, method, rules):
            start = max(start, ready)
            rounded = start.replace(minute=0, second=0, microsecond=0)
            if rounded < start:
                rounded += timedelta(hours=1)
            while rounded + timedelta(hours=1) <= end:
                if hour is None or rounded.hour == hour:
                    slots.append(Slot(rounded, rounded + timedelta(hours=1)))
                    if len(slots) >= limit:
                        return slots
                rounded += timedelta(hours=1)
    return slots


def next_slot(shop, method, now=None, day=None, hour=None):
    slots = available_slots(shop, method, now, day, hour, limit=1)
    return slots[0] if slots else None


def context_day(context, shop, now):
    today = now.astimezone(ZoneInfo(shop.timezone)).date()
    if context.when == "today":
        return today
    if context.when == "tomorrow":
        return today + timedelta(days=1)
    return context.day if context.when == "date" else None


def location_matches(shop, method, context):
    if context.address:
        if context.address.get("region") not in {"77", "50"}:
            return False
        if method == "delivery":
            return point_in_zone(
                context.address["latitude"],
                context.address["longitude"],
                shop.delivery_zone,
            )
        return (
            shop.settlement.name.casefold()
            == context.address.get("city", "").casefold()
        )
    if context.city_id:
        if method == "pickup":
            return shop.settlement_id == context.city_id
        return any(
            city.pk == context.city_id for city in shop.delivery_settlements.all()
        )
    return True


def receiving_options(shop, context, now=None):
    now = now or timezone.now()
    if shop.status != "active" or shop.settlement.region not in {"77", "50"}:
        return {}
    methods = (
        [context.method]
        if context.method in {"delivery", "pickup"}
        else ["delivery", "pickup"]
    )
    options = {}
    for method in methods:
        if getattr(shop, f"{method}_enabled") and location_matches(
            shop, method, context
        ):
            slot = next_slot(
                shop, method, now, context_day(context, shop, now), context.hour
            )
            if slot:
                options[method] = slot
    return options


def shop_can_receive(shop, context, now=None):
    return bool(receiving_options(shop, context, now))
