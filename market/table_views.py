from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.db.models import (
    Case,
    CharField,
    DecimalField,
    ExpressionWrapper,
    F,
    Q,
    Value,
    When,
)
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape, format_html
from django.views.decorators.http import require_GET

from market.access import get_shop_for_user
from market.models import ShopOrder
from market.templatetags.market_tags import rub


def _bounded_integer(value, default, maximum, minimum=0):
    try:
        number = int(value)
    except (ValueError, TypeError):
        return default
    return min(number, maximum) if number >= minimum else default


def _labels(field):
    return Case(
        *[
            When(**{field: code}, then=Value(label))
            for code, label in ShopOrder._meta.get_field(field).choices
        ],
        default=F(field),
        output_field=CharField(),
    )


def _search(parts, term, operator):
    if not term:
        return parts
    query = Q(order__number__icontains=term)
    normalized = term.casefold()
    if operator:
        query |= Q(shop__name__icontains=term)
        # SQLite's LIKE folds ASCII only; retain Cyrillic search in the local demo.
        if connection.vendor == "sqlite" and not term.isascii():
            matches = [
                shop_id
                for shop_id, name in parts.order_by()
                .values_list("shop_id", "shop__name")
                .distinct()
                if normalized in name.casefold()
            ]
            query |= Q(shop_id__in=matches)
    for field in ("status", "payment_status", "method"):
        matches = [
            code
            for code, label in ShopOrder._meta.get_field(field).choices
            if normalized in label.casefold() or normalized in code.casefold()
        ]
        if matches:
            query |= Q(**{f"{field}__in": matches})
    if term.isascii() and term.isdecimal() and len(term) <= 18:
        query |= Q(pk=int(term))
    try:
        amount = Decimal(
            term.replace("₽", "")
            .replace(" ", "")
            .replace("\u00a0", "")
            .replace(",", ".")
        )
        if amount.is_finite() and abs(amount) < Decimal(100000000000000):
            query |= Q(row_total=amount)
    except InvalidOperation:
        pass
    for pattern in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            day = (
                datetime.strptime(term, pattern)
                .replace(tzinfo=timezone.get_current_timezone())
                .date()
            )
        except ValueError:
            continue
        query |= Q(created_at__date=day) | Q(slot_start__date=day)
        break
    return parts.filter(query)


def _order_response(request, parts, *, operator=False):
    now = timezone.localtime()
    requested_days = request.GET.get("days")
    days = int(requested_days) if requested_days in {"7", "30", "42", "90"} else 42
    start = datetime.combine(
        now.date() - timedelta(days=days - 1), time.min, now.tzinfo
    )
    end = datetime.combine(now.date() + timedelta(days=1), time.min, now.tzinfo)
    parts = parts.filter(created_at__gte=start, created_at__lt=end).annotate(
        row_total=ExpressionWrapper(
            F("goods_total") + F("delivery_total"),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
        payment_label=_labels("payment_status"),
        status_label=_labels("status"),
    )
    count = parts.count()
    term = request.GET.get("search[value]", "").strip()[:200]
    filtered = _search(parts, term, operator)
    fields = (
        "order__number",
        "created_at",
        "shop__name" if operator else "slot_start",
        "row_total",
        "payment_label",
        "status_label",
    )
    ordering = []
    for index in range(len(fields)):
        try:
            column = int(request.GET.get(f"order[{index}][column]", ""))
        except ValueError:
            continue
        direction = request.GET.get(f"order[{index}][dir]")
        if not 0 <= column < len(fields) or direction not in {"asc", "desc"}:
            continue
        field = fields[column]
        if field not in {item.removeprefix("-") for item in ordering}:
            ordering.append(("-" if direction == "desc" else "") + field)
    ordering = ordering or ["-created_at"]
    start = _bounded_integer(request.GET.get("start"), 0, 1_000_000)
    length = _bounded_integer(request.GET.get("length"), 20, 100, minimum=1)
    rows = []
    for part in filtered.select_related("order", "shop").order_by(*ordering, "-pk")[
        start : start + length
    ]:
        url = reverse("market:partner-order", args=[part.shop.slug, part.pk])
        receiving = (
            escape(part.shop.name)
            if operator
            else format_html(
                "{}<br>{}",
                part.get_method_display(),
                timezone.localtime(part.slot_start).strftime("%d.%m %H:%M"),
            )
        )
        rows.append(
            [
                format_html(
                    '<a class="text-link" href="{}">№ {} / {}</a>',
                    url,
                    part.order.number,
                    part.pk,
                ),
                timezone.localtime(part.created_at).strftime("%d.%m.%Y"),
                receiving,
                rub(part.total),
                escape(part.get_payment_status_display()),
                escape(part.get_status_display()),
            ]
        )
    return JsonResponse(
        {
            "draw": _bounded_integer(request.GET.get("draw"), 0, 2_147_483_647),
            "recordsTotal": count,
            "recordsFiltered": filtered.count() if term else count,
            "data": rows,
        },
        json_dumps_params={"ensure_ascii": False},
    )


@login_required
@require_GET
def partner_orders(request, slug):
    shop = get_shop_for_user(request.user, slug)
    return _order_response(request, shop.shop_orders.all())


@login_required
@require_GET
def operator_orders(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    return _order_response(request, ShopOrder.objects.all(), operator=True)
