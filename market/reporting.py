from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from market.models import Payout


def dashboard_report(parts, requested_days=None, now=None, value="goods_total"):
    """Report only the supplied, already-authorized shop orders.

    ``value`` is the amount the sales chart adds up: buyer prices for Romeo, the shop's
    own amount (``partner_total``) in a shop cabinet."""
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

    # Money Romeo actually sent to these shops (a withdrawn payout does not count).
    payouts = Payout.objects.filter(
        shop_id__in=parts.order_by().values("shop_id"),
        status__in=["sent", "confirmed", "disputed"],
        created_at__gte=start,
        created_at__lt=end,
    )
    romeo_deliveries = period.filter(method="delivery", delivery_owner="platform")
    stats = {
        "orders": period.count(),
        "created_total": total(live, "goods_total"),
        "paid_total": total(paid, "goods_total"),
        "paid_base_total": total(paid, "base_goods_total"),
        "delivery_revenue": total(
            paid.filter(method="delivery", delivery_owner="platform"), "delivery_total"
        ),
        # A courier expense survives cancellation, refund and missing buyer payment.
        "delivery_cost": total(romeo_deliveries, "actual_delivery_cost"),
        "missing_delivery_costs": romeo_deliveries.exclude(status="cancelled").filter(
            actual_delivery_cost__isnull=True
        ).count(),
        "expected_commission": total(live, "commission_total"),
        "commission_total": total(paid, "commission_total"),
        "refunded_total": total(period.filter(payment_status="refunded"), "goods_total")
        + total(period.filter(payment_status="refunded"), "delivery_total"),
        "refunded_partner_total": total(
            period.filter(payment_status="refunded"), "partner_total"
        ),
        "created_partner_total": total(live, "partner_total"),
        "paid_partner_total": total(paid, "partner_total"),
        "payout_total": total(payouts, "amount"),
    }
    stats["delivery_margin"] = stats["delivery_revenue"] - stats["delivery_cost"]
    stats["platform_gross_total"] = stats["commission_total"] + stats["delivery_revenue"]
    stats["platform_total"] = stats["platform_gross_total"] - stats["delivery_cost"]
    by_day = dict(
        paid.order_by()
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(value=Sum(value))
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
        "trend_total": sum((point["value"] for point in trend), Decimal(0)),
        "stats": stats,
        "trend": trend,
        "days": days,
        "period_start": start_day,
        "period_end": now.date(),
        "period_options": [7, 30, 42, 90],
        "period_parts": period,
    }
