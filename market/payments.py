"""Money between Romeo and the shops.

Every shop has a balance made of ``BalanceEntry`` rows: positive means Romeo owes the
shop, negative means the shop owes Romeo. Who holds the customer's money decides the sign:

* paid online, or cash taken by Romeo's courier: Romeo holds the money and owes the shop
  its share (``partner_total``);
* cash taken in the shop (pickup or the shop's own courier): the shop holds the money and
  owes Romeo its part (sale price minus the shop's price).

Entries are written once per order part, so repeating a call never counts a sale twice.
"""

from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from market import messengers
from market.models import BalanceEntry, Membership, Payout, Shop, ShopOrder


def shop_balance(shop):
    return shop.balance_entries.aggregate(value=Sum("amount"))["value"] or Decimal(0)


def balances_by_shop():
    """{shop_id: balance} for every shop that has any movement."""
    return dict(
        BalanceEntry.objects.order_by()
        .values("shop_id")
        .annotate(value=Sum("amount"))
        .values_list("shop_id", "value")
    )


def balance_totals(balances):
    """What Romeo owes shops, and what shops owe Romeo (both as positive numbers)."""
    values = list(balances.values())
    return (
        sum((v for v in values if v > 0), Decimal(0)),
        -sum((v for v in values if v < 0), Decimal(0)),
    )


def _post(part, kind, amount):
    if amount:
        BalanceEntry.objects.get_or_create(
            part=part,
            kind=kind,
            defaults={"shop_id": part.shop_id, "amount": amount},
        )


def _lock_part(part):
    return ShopOrder.objects.select_for_update(of=("self",)).get(pk=part.pk)


@transaction.atomic
def record_online_payment(part):
    """The payment provider confirmed the customer's online payment."""
    part = _lock_part(part)
    if part.payment_method != "online":
        raise ValidationError("Эта часть заказа оплачивается при получении.")
    if part.payment_status == "paid":
        return part
    if part.payment_status != "pending" or part.status == "cancelled":
        raise ValidationError("Эту часть заказа нельзя оплатить.")
    part.payment_status = "paid"
    part.save(update_fields=["payment_status"])
    _post(part, "platform_collected", part.balance_effect)
    messengers.notify_new_part(part)
    return part


@transaction.atomic
def record_cash_receipt(part):
    """The customer handed over the cash and received the bouquet."""
    part = _lock_part(part)
    if part.payment_method != "cash":
        raise ValidationError("Эта часть заказа оплачена онлайн.")
    if part.payment_status == "paid":
        return part
    if part.payment_status != "pending" or part.status == "cancelled":
        raise ValidationError("Наличные по этой части заказа принять нельзя.")
    part.payment_status = "paid"
    part.save(update_fields=["payment_status"])
    kind = "platform_collected" if part.platform_collects else "shop_collected"
    _post(part, kind, part.balance_effect)
    return part


@transaction.atomic
def refund_online_payment(part):
    """Return an online payment to the customer and take the share back from the shop."""
    part = _lock_part(part)
    if part.payment_method != "online" or part.payment_status != "paid":
        raise ValidationError("Вернуть можно только оплаченный онлайн заказ.")
    part.payment_status = "refunded"
    part.save(update_fields=["payment_status"])
    _post(part, "refund", -part.partner_total)
    return part


def _is_member(user, shop):
    return Membership.objects.filter(user=user, shop=shop).exists()


@transaction.atomic
def create_payout(shop, amount, actor, note=""):
    """Romeo has sent ``amount`` to the shop; the balance drops right away."""
    if not actor.is_superuser:
        raise PermissionDenied
    shop = Shop.objects.select_for_update().get(pk=shop.pk)
    if amount is None or amount <= 0 or amount != amount.quantize(Decimal("0.01")):
        raise ValidationError("Укажите сумму выплаты в рублях и копейках.")
    balance = shop_balance(shop)
    if amount > balance:
        raise ValidationError(
            f"Нельзя выплатить больше, чем Ромео должен магазину: {balance} ₽."
        )
    payout = Payout.objects.create(
        shop=shop, amount=amount, note=note[:255], created_by=actor
    )
    BalanceEntry.objects.create(shop=shop, payout=payout, kind="payout", amount=-amount)
    return payout


def _respond(payout, actor, status, allowed):
    payout = Payout.objects.select_for_update().select_related("shop").get(pk=payout.pk)
    if payout.status not in allowed:
        raise ValidationError("Статус этой выплаты уже нельзя изменить.")
    payout.status = status
    payout.responded_by = actor
    payout.responded_at = timezone.now()
    payout.save(update_fields=["status", "responded_by", "responded_at"])
    return payout


@transaction.atomic
def confirm_payout(payout, actor):
    """The shop confirms the money arrived."""
    payout = Payout.objects.select_related("shop").get(pk=payout.pk)
    if not _is_member(actor, payout.shop):
        raise PermissionDenied
    return _respond(payout, actor, "confirmed", {"sent", "disputed"})


@transaction.atomic
def dispute_payout(payout, actor):
    """The shop says the money has not arrived."""
    payout = Payout.objects.select_related("shop").get(pk=payout.pk)
    if not _is_member(actor, payout.shop):
        raise PermissionDenied
    return _respond(payout, actor, "disputed", {"sent"})


@transaction.atomic
def cancel_payout(payout, actor):
    """Romeo withdraws a payout that never reached the shop; the debt returns."""
    if not actor.is_superuser:
        raise PermissionDenied
    payout = _respond(payout, actor, "cancelled", {"sent", "disputed"})
    BalanceEntry.objects.create(
        shop=payout.shop,
        payout=payout,
        kind="payout_cancelled",
        amount=payout.amount,
    )
    return payout


def awaiting_confirmation(shop):
    return shop.payouts.filter(status__in=["sent", "disputed"])
