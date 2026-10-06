from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from market.models import DemoPayout


def dashboard_report(parts, requested_days=None, now=None):
    """Report only the supplied, already-authorized shop orders."""
    days = int(requested_days) if str(requested_days) in {"7", "30", "42", "90"} else 42
    now = timezone.localtime(now or timezone.now())
    start_day = now.date() - timedelta(days=days - 1)
    start = datetime.combine(start_day, time.min, now.tzinfo)
    end = datetime.combine(now.date() + timedelta(days=1), time.min, now.tzinfo)
    period = parts.filter(created_at__gte=start, created_at__lt=end)
    live = period.exclude(status="cancelled")
    paid = live.filter(payment_status="paid")

    def total(query, field):
        return query.aggregate(value=Sum(field))["value"] or Decimal(0)

    payouts = DemoPayout.objects.none()
    settled = parts.filter(status="completed", payment_status="paid")
    if settings.MARKET_DEMO:
        payouts = DemoPayout.objects.filter(part__in=settled).select_related(
            "part__order", "part__shop"
        )
    stats = {
        "orders": period.count(),
        "created_total": total(live, "goods_total"),
        "paid_total": total(paid, "goods_total"),
        "paid_base_total": total(paid, "base_goods_total"),
        "delivery_revenue": total(
            paid.filter(delivery_owner="platform"), "delivery_total"
        ),
        "expected_commission": total(live, "commission_total"),
        "commission_total": total(paid, "commission_total"),
        "refunded_total": total(period.filter(payment_status="refunded"), "goods_total")
        + total(period.filter(payment_status="refunded"), "delivery_total"),
        "payout_total": total(
            payouts.filter(paid_at__gte=start, paid_at__lt=end), "amount"
        ),
        "payout_pending": max(
            Decimal(0), total(settled, "partner_total") - total(payouts, "amount")
        ),
    }
    stats["platform_total"] = stats["commission_total"] + stats["delivery_revenue"]
    by_day = dict(
        paid.order_by()
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(value=Sum("goods_total"))
        .values_list("day", "value")
    )
    ceiling = max(by_day.values(), default=Decimal(0)) or Decimal(1)
    trend = []
    for offset in range(days):
        day = start_day + timedelta(days=offset)
        value = by_day.get(day, Decimal(0))
        trend.append(
            {
                "day": day,
                "value": value,
                "height": max(2 if value else 0, round(value / ceiling * 100)),
            }
        )
    return {
        "stats": stats,
        "trend": trend,
        "days": days,
        "period_start": start_day,
        "period_end": now.date(),
        "period_options": [7, 30, 42, 90],
        "payouts": payouts.filter(paid_at__gte=start, paid_at__lt=end)[:12],
        "period_parts": period,
    }
