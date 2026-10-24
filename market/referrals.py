"""First-touch invitations and transactional first-purchase flower credits."""

from decimal import ROUND_DOWN, Decimal
from math import isfinite
from uuid import UUID

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from market.models import CustomerPhone, Membership, Referral, ReferralLink, ShopOrder

SESSION_KEY = "market_referral"
MAX_AGE = 30 * 24 * 60 * 60


def personal_link(user):
    if not user.is_authenticated:
        raise PermissionDenied
    return ReferralLink.objects.get_or_create(user=user)[0]


def share_url(user):
    return settings.SITE_URL.rstrip("/") + reverse(
        "market:referral", args=[personal_link(user).code]
    )


def _link(code):
    try:
        code = UUID(str(code))
    except (ValueError, TypeError, AttributeError):
        return None
    return ReferralLink.objects.filter(code=code).first()


def _successful_parts(user_id):
    return ShopOrder.objects.filter(
        order__user_id=user_id,
        status="completed",
        payment_status__in=["paid", "refunded"],
        items__quantity__gt=0,
    ).exclude(payment_method="gift")


@transaction.atomic
def bind(user, code, *, now=None):
    link = _link(code)
    if (
        not user.is_authenticated
        or not link
        or link.user_id == user.pk
        or user.is_staff
        or user.is_superuser
        or Membership.objects.filter(user=user).exists()
        or not CustomerPhone.objects.filter(user=user, verified=True).exists()
    ):
        return None
    # Checkout takes this same lock before placing an order: every order eligible
    # for this invitation is necessarily created after this transaction commits.
    # Include the referrer in stable order because inserting the relationship
    # also takes FK key-share locks. Reciprocal invitations must not deadlock.
    list(
        get_user_model()
        .objects.select_for_update()
        .filter(pk__in=[user.pk, link.user_id])
        .order_by("pk")
    )
    existing = Referral.objects.filter(friend=user).first()
    if existing:
        return existing
    if _successful_parts(user.pk).exists():
        return None
    return Referral.objects.create(
        friend=user, referrer_id=link.user_id, created_at=now or timezone.now()
    )


@transaction.atomic
def qualify_part(part):
    # Callers already hold the part lock. Reacquire it here for direct/retry calls.
    # Never lock the referrer's User here: gift claims lock that User before parts.
    part = (
        ShopOrder.objects.select_for_update(of=("self",))
        .select_related("order")
        .get(pk=part.pk)
    )
    if (
        part.status != "completed"
        or part.payment_status != "paid"
        or part.payment_method == "gift"
    ):
        return None
    invitation = (
        Referral.objects.select_for_update(of=("self",))
        .filter(friend_id=part.order.user_id, first_purchase__isnull=True)
        .first()
    )
    if not invitation:
        # A determined outcome is immutable. Do not lock it behind stock held
        # by a later order: gift checkout locks the same credit before stock.
        return Referral.objects.filter(friend_id=part.order.user_id).first()
    invitation.first_purchase = part
    previous = _successful_parts(part.order.user_id).exclude(pk=part.pk).exists()
    totals = part.items.aggregate(units=Sum("quantity"))
    units = totals["units"] or 0
    if part.order.date_placed >= invitation.created_at and not previous and units:
        paid = sum(
            (item.unit_price * item.quantity for item in part.items.all()), Decimal(0)
        )
        value = (paid / units).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        if value > 0:
            invitation.bonus_value = value
            invitation.awarded_at = timezone.now()
    # Only these columns change. Referrer FK and consumed reward stay untouched.
    invitation.save(update_fields=["first_purchase", "bonus_value", "awarded_at"])
    return invitation


def _valid_candidate(candidate):
    try:
        age = timezone.now().timestamp() - float(candidate["captured_at"])
        if isfinite(age) and 0 <= age <= MAX_AGE and _link(candidate["code"]):
            return {"code": candidate["code"], "captured_at": candidate["captured_at"]}
    except (TypeError, KeyError, ValueError, OverflowError):
        pass
    return None


def _pending(request):
    raw = request.session.get(SESSION_KEY)
    candidates = [raw, raw.get("fallback") if isinstance(raw, dict) else None]
    valid = [
        value for candidate in candidates if (value := _valid_candidate(candidate))
    ]
    if not valid:
        request.session.pop(SESSION_KEY, None)
        return None
    pending = valid[0]
    if len(valid) > 1 and valid[1]["code"] != pending["code"]:
        pending["fallback"] = valid[1]
    if pending != raw:
        request.session[SESSION_KEY] = pending
    return pending


def remember(request, code):
    link = _link(code)
    if not link or (request.user.is_authenticated and link.user_id == request.user.pk):
        return
    pending = _pending(request)
    candidate = {"code": str(link.code), "captured_at": timezone.now().timestamp()}
    if not pending:
        pending = candidate
    elif pending["code"] != candidate["code"] and "fallback" not in pending:
        # Before login, the first code may belong to this visitor. Two distinct
        # codes suffice: an account has exactly one personal code, so at most
        # one candidate can be self. Never replace the first eligible owner.
        pending["fallback"] = candidate
    if pending != request.session.get(SESSION_KEY):
        request.session[SESSION_KEY] = pending
        request.session.set_expiry(max(MAX_AGE, request.session.get_expiry_age()))
    bind_pending(request)


def bind_pending(request):
    if SESSION_KEY not in request.session:
        return
    pending = _pending(request)
    if not pending or not request.user.is_authenticated:
        return
    if not CustomerPhone.objects.filter(user=request.user, verified=True).exists():
        return
    candidate = pending
    if _link(candidate["code"]).user_id == request.user.pk:
        candidate = pending.get("fallback")
    if candidate:
        bind(request.user, candidate["code"])
    request.session.pop(SESSION_KEY, None)
