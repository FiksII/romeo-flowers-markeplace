"""Paying orders online through T-Bank (SpeedPay buttons and the bank's payment form).

One Init covers every unpaid online part of an order. The bank reports the outcome by
notification; ``CONFIRMED`` marks the parts paid and credits the shops' balances. Money
that arrives for parts cancelled in the meantime is sent back to the customer.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from oscar.core.loading import get_model

from market import tbank
from market.models import OnlinePayment, ShopOrder
from market.payments import record_online_payment, refund_online_payment

logger = logging.getLogger(__name__)

REFUNDED = ("REFUNDED", "REVERSED")
# Statuses after which the bank sends nothing that changes the outcome.
FINAL = (
    *REFUNDED,
    "PARTIAL_REFUNDED",
    "PARTIAL_REVERSED",
    "REJECTED",
    "CANCELED",
    "DEADLINE_EXPIRED",
    "AUTH_FAIL",
)


def enabled():
    return bool(
        settings.MARKET_ONLINE_PAYMENTS
        and settings.TBANK_TERMINAL_KEY
        and settings.TBANK_PASSWORD
    )


def payable_parts(order):
    return (
        ShopOrder.objects.filter(
            order=order, payment_method="online", payment_status="pending"
        )
        .exclude(status__in=["cancelled", "completed"])
        .order_by("pk")
    )


def payment_deadline(order):
    """The reservation ends then; the bank's link expires at the same moment."""
    return order.date_placed + timedelta(minutes=settings.MARKET_RESERVATION_MINUTES)


def _receipt(order, parts):
    """54-FZ receipt for the terminal's online cash register, if one is connected."""
    if not settings.TBANK_TAXATION:
        return None
    items = []
    for part in parts:
        for item in part.items.all():
            items.append(
                {
                    "Name": item.title[:128],
                    "Price": tbank.kopecks(item.unit_price),
                    "Quantity": item.quantity,
                    "Amount": tbank.kopecks(item.line_total),
                    "Tax": settings.TBANK_VAT,
                    "PaymentMethod": "full_prepayment",
                    "PaymentObject": "commodity",
                }
            )
        if part.delivery_total:
            items.append(
                {
                    "Name": "Доставка",
                    "Price": tbank.kopecks(part.delivery_total),
                    "Quantity": 1,
                    "Amount": tbank.kopecks(part.delivery_total),
                    "Tax": settings.TBANK_VAT,
                    "PaymentMethod": "full_prepayment",
                    "PaymentObject": "service",
                }
            )
    receipt = {"Taxation": settings.TBANK_TAXATION, "Items": items}
    if order.email:
        receipt["Email"] = order.email
    else:
        receipt["Phone"] = parts[0].contact_phone
    return receipt


@transaction.atomic
def start_payment(order, notification_url, return_url, now):
    """Return a payment whose ``payment_url`` the customer can pay through."""
    if not enabled():
        raise ValidationError("Онлайн-оплата сейчас недоступна.")
    order = get_model("order", "Order").objects.select_for_update().get(pk=order.pk)
    parts = list(payable_parts(order).prefetch_related("items"))
    if not parts:
        raise ValidationError("Этот заказ не ждёт онлайн-оплаты.")
    deadline = payment_deadline(order)
    if deadline - now < timedelta(minutes=2):
        raise ValidationError("Время на оплату истекло, резерв букетов снят.")
    attempts = order.online_payments.select_for_update()
    if attempts.filter(status__in=OnlinePayment.IN_PROGRESS).exists():
        raise ValidationError("Оплата уже проводится. Обновите страницу через минуту.")
    amount = sum(part.total for part in parts)
    open_payment = (
        attempts.filter(status__in=OnlinePayment.OPEN, amount=amount)
        .exclude(payment_url="")
        .first()
    )
    if open_payment:
        return open_payment
    payment = OnlinePayment.objects.create(
        order=order,
        order_ref=f"{order.number}-{attempts.count() + 1}",
        amount=amount,
        expires_at=deadline,
    )
    params = {
        "Amount": tbank.kopecks(amount),
        "OrderId": payment.order_ref,
        "Description": f"Заказ № {order.number} — Цветы от Ромео"[:140],
        "PayType": "O",
        "Language": "ru",
        "NotificationURL": notification_url,
        "SuccessURL": return_url,
        "FailURL": return_url,
        "RedirectDueDate": deadline.replace(microsecond=0).isoformat(),
        # Required by T-Bank for the SpeedPay widget.
        "DATA": {"connection_type": "Widget"},
    }
    receipt = _receipt(order, parts)
    if receipt:
        params["Receipt"] = receipt
    result = tbank.call("Init", params)
    payment.payment_id = str(result.get("PaymentId", ""))
    payment.payment_url = result.get("PaymentURL", "")
    payment.status = result.get("Status") or "NEW"
    payment.save(update_fields=["payment_id", "payment_url", "status", "updated_at"])
    if not payment.payment_url:
        raise tbank.TbankError("Платёжный сервис не вернул ссылку на оплату.")
    return payment


def _return_money(payment, amount):
    def send():
        try:
            tbank.call(
                "Cancel",
                {"PaymentId": payment.payment_id, "Amount": tbank.kopecks(amount)},
            )
        except tbank.TbankError:
            logger.error(
                "Return of %s RUB for payment %s failed; return it in the T-Bank "
                "personal account.",
                amount,
                payment.order_ref,
            )

    transaction.on_commit(send)


def _settle(payment):
    order = (
        get_model("order", "Order").objects.select_for_update().get(pk=payment.order_id)
    )
    paid = 0
    parts = (
        ShopOrder.objects.select_for_update(of=("self",))
        .filter(order=order, payment_method="online", payment_status="pending")
        .order_by("pk")
    )
    for part in parts:
        if part.status == "cancelled" or paid + part.total > payment.amount:
            continue
        record_online_payment(part)
        paid += part.total
    returned = payment.amount - paid
    if returned > 0:
        # The customer cancelled a part (or its reservation ran out) while paying.
        payment.returned_amount = returned
        payment.save(update_fields=["returned_amount", "updated_at"])
        _return_money(payment, returned)
    if order.status == "AwaitingPayment" and not payable_parts(order).exists():
        live = order.shop_orders.exclude(status="cancelled").exists()
        order.status = "Processing" if live else "Cancelled"
        order.save(update_fields=["status"])


def _refund(payment):
    for part in ShopOrder.objects.filter(
        order_id=payment.order_id, payment_method="online", payment_status="paid"
    ).order_by("pk"):
        refund_online_payment(part)


@transaction.atomic
def apply_status(payment, status):
    """Record the bank's status of ``payment`` and act on it once."""
    payment = OnlinePayment.objects.select_for_update().get(pk=payment.pk)
    current = payment.status
    if not status or status == current or current in FINAL:
        return payment
    if current == OnlinePayment.PAID and status not in FINAL:
        # Notifications may arrive out of order; a paid payment never goes back.
        return payment
    payment.status = status
    payment.save(update_fields=["status", "updated_at"])
    if status == OnlinePayment.PAID:
        _settle(payment)
    elif status in REFUNDED and current == OnlinePayment.PAID:
        _refund(payment)
    return payment


def sync_status(payment):
    """Ask the bank for the payment's status (the notification may still be on its way)."""
    if not payment.payment_id or payment.status in FINAL:
        return payment
    try:
        result = tbank.call("GetState", {"PaymentId": payment.payment_id})
    except tbank.TbankError:
        return payment
    if str(result.get("OrderId", payment.order_ref)) != payment.order_ref:
        return payment
    return apply_status(payment, result.get("Status"))


def handle_notification(data):
    """Process a notification; False means it is not from the bank."""
    if not tbank.notification_is_genuine(data):
        return False
    payment = OnlinePayment.objects.filter(order_ref=str(data.get("OrderId"))).first()
    if payment is None:
        logger.warning("T-Bank notification for unknown order %s", data.get("OrderId"))
        return True
    if payment.payment_id and str(data.get("PaymentId")) != payment.payment_id:
        logger.warning(
            "T-Bank notification for another payment of %s", payment.order_ref
        )
        return True
    status = data.get("Status")
    if status == OnlinePayment.PAID and str(data.get("Amount")) != str(
        tbank.kopecks(payment.amount)
    ):
        logger.error("T-Bank confirmed a different amount for %s", payment.order_ref)
        return True
    apply_status(payment, status)
    return True
