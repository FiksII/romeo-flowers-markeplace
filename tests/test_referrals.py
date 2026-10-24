from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from market.models import CustomerPhone, Referral
from market.referrals import SESSION_KEY, bind, personal_link, qualify_part
from tests.test_loyalty import NOW, purchase

pytestmark = pytest.mark.django_db


@pytest.fixture
def friend(stranger):
    CustomerPhone.objects.create(user=stranger, phone="+79990000101", verified=True)
    return stranger


def invite(buyer, friend):
    return bind(friend, personal_link(buyer).code, now=NOW - timedelta(hours=1))


def test_personal_link_is_stable_and_opaque(buyer):
    link = personal_link(buyer)
    assert personal_link(buyer).pk == link.pk
    assert len(link.code.hex) == 32
    assert link.user == buyer


def test_binding_requires_verified_phone_and_rejects_self(buyer, stranger):
    code = personal_link(buyer).code
    assert bind(stranger, code) is None
    CustomerPhone.objects.create(user=buyer, phone="+79990000102", verified=True)
    assert bind(buyer, code) is None
    assert not Referral.objects.exists()


def test_binding_first_owner_wins_and_invalid_code_does_not_poison(buyer, friend):
    assert bind(friend, uuid4()) is None
    first = invite(buyer, friend)
    other = get_user_model().objects.create_user("another-inviter")
    assert bind(friend, personal_link(other).code).pk == first.pk
    assert Referral.objects.get(friend=friend).referrer == buyer


def test_first_award_is_one_stamp_with_average_bouquet_value(buyer, friend, listing):
    referral = invite(buyer, friend)
    part = purchase(friend, listing, 3)
    result = qualify_part(part)
    assert result.pk == referral.pk
    referral.refresh_from_db()
    assert referral.first_purchase == part
    assert referral.bonus_value == Decimal("2500.00")
    assert referral.awarded_at is not None
    assert qualify_part(part).pk == referral.pk
    second = purchase(friend, listing, 2)
    qualify_part(second)
    referral.refresh_from_db()
    assert referral.first_purchase == part
    assert Referral.objects.filter(bonus_value__isnull=False).count() == 1


@pytest.mark.parametrize(
    "paid,completed", [(False, False), (True, False), (False, True)]
)
def test_pending_payment_or_receiving_never_awards(
    buyer, friend, listing, paid, completed
):
    referral = invite(buyer, friend)
    part = purchase(friend, listing, 1, paid=paid, completed=completed)
    assert qualify_part(part) is None
    referral.refresh_from_db()
    assert referral.first_purchase is None


@pytest.mark.parametrize("payment_status", ["paid", "refunded"])
def test_previous_successful_buyer_cannot_bind(buyer, friend, listing, payment_status):
    part = purchase(friend, listing, 1)
    part.payment_status = payment_status
    part.save()
    assert bind(friend, personal_link(buyer).code) is None


def test_preexisting_pending_order_disqualifies_future_awards(buyer, friend, listing):
    old = purchase(friend, listing, 1, paid=False, completed=False)
    referral = bind(
        friend,
        personal_link(buyer).code,
        now=old.order.date_placed + timedelta(minutes=1),
    )
    old.status, old.payment_status = "completed", "paid"
    old.save()
    qualify_part(old)
    referral.refresh_from_db()
    assert referral.first_purchase == old
    assert referral.bonus_value is None
    later = purchase(friend, listing, 1)
    qualify_part(later)
    referral.refresh_from_db()
    assert referral.bonus_value is None


def test_online_confirmation_after_receiving_awards_automatically(
    buyer, friend, listing
):
    from market.payments import record_online_payment

    referral = invite(buyer, friend)
    part = purchase(friend, listing, 1, paid=False)
    part.payment_method = "online"
    part.save()
    record_online_payment(part)
    referral.refresh_from_db()
    assert referral.bonus_value == 2500


@pytest.mark.parametrize("payment_method", ["cash", "online"])
def test_receiving_awards_automatically_for_cash_and_paid_online(
    buyer, friend, listing, shop, owner, payment_method
):
    from market.orders import transition_shop_order

    referral = invite(buyer, friend)
    part = purchase(
        friend, listing, 1, paid=payment_method == "online", completed=False
    )
    part.payment_method, part.status = payment_method, "ready"
    part.save()
    transition_shop_order(part, owner, "completed")
    referral.refresh_from_db()
    assert referral.bonus_value == 2500


def test_refund_never_moves_bonus_to_second_purchase(buyer, friend, listing):
    from market.payments import refund_online_payment

    referral = invite(buyer, friend)
    part = purchase(friend, listing, 1)
    part.payment_method = "online"
    part.save()
    qualify_part(part)
    refund_online_payment(part)
    qualify_part(purchase(friend, listing, 1))
    referral.refresh_from_db()
    assert referral.first_purchase == part


def test_database_rejects_self_and_second_inviter(buyer, friend):
    invite(buyer, friend)
    with pytest.raises(IntegrityError), transaction.atomic():
        Referral.objects.create(friend=friend, referrer=buyer)
    with pytest.raises(IntegrityError), transaction.atomic():
        Referral.objects.create(friend=buyer, referrer=buyer)


def test_database_rejects_awarded_credit_without_value(buyer, friend, listing):
    referral = invite(buyer, friend)
    part = purchase(friend, listing, 1)
    with pytest.raises(IntegrityError), transaction.atomic():
        Referral.objects.filter(pk=referral.pk).update(
            first_purchase=part, awarded_at=timezone.now()
        )


def test_guest_link_survives_login_and_binds_first_owner(client, buyer, friend):
    code = personal_link(buyer).code
    url = reverse("market:referral", args=[code])
    assert client.get(url).url == reverse("market:home")
    assert client.session[SESSION_KEY]["code"] == str(code)
    other = get_user_model().objects.create_user("second-inviter")
    client.get(reverse("market:referral", args=[personal_link(other).code]))
    assert client.session[SESSION_KEY]["code"] == str(code)
    client.force_login(friend)
    client.get(reverse("market:account"))
    assert Referral.objects.get(friend=friend).referrer == buyer
    assert SESSION_KEY not in client.session


def test_expired_link_does_not_bind_and_valid_new_link_can_replace(
    client, buyer, friend
):
    code = personal_link(buyer).code
    session = client.session
    session[SESSION_KEY] = {
        "code": str(code),
        "captured_at": (timezone.now() - timedelta(days=31)).timestamp(),
    }
    session.save()
    client.force_login(friend)
    client.get(reverse("market:account"))
    assert not Referral.objects.filter(friend=friend).exists()
    client.get(reverse("market:referral", args=[code]))
    assert Referral.objects.get(friend=friend).referrer == buyer


def test_invalid_and_self_links_preserve_valid_pending_attribution(
    client, buyer, friend
):
    code = personal_link(buyer).code
    client.get(reverse("market:referral", args=[code]))
    client.get(reverse("market:referral", args=[uuid4()]))
    assert client.session[SESSION_KEY]["code"] == str(code)
    client.force_login(friend)
    client.get(reverse("market:referral", args=[personal_link(friend).code]))
    assert Referral.objects.get(friend=friend).referrer == buyer
