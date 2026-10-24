from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from django.db import connection

from market.loyalty import claim, progress
from market.models import CustomerPhone, Referral
from market.payments import record_online_payment, refund_online_payment
from market.referrals import bind, personal_link, qualify_part
from tests.test_loyalty import CONTACT, NOW, choice, purchase
from tests.test_postgres_concurrency import require_postgres, thread_call
from tests.test_referrals import invite

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.postgresql]


def test_two_purchase_confirmations_award_exactly_once(buyer, friend, listing):
    require_postgres()
    invite(buyer, friend)
    parts = [purchase(friend, listing, 1, paid=False) for _ in range(2)]
    for part in parts:
        part.payment_method = "online"
        part.save()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(
            pool.map(
                lambda part: thread_call(lambda: record_online_payment(part)), parts
            )
        )
    credit = Referral.objects.get(friend=friend)
    assert credit.first_purchase_id in [part.pk for part in parts]
    assert credit.bonus_value == 2500
    assert progress(buyer)["stamps"] == 1


def test_referral_award_during_claim_has_no_owner_user_deadlock(
    buyer, friend, listing, shop
):
    require_postgres()
    purchase(buyer, listing, 10)
    invite(buyer, friend)
    pending = purchase(friend, listing, 1, paid=False)
    pending.payment_method = "online"
    pending.save()
    parts_locked, awarded = Event(), Event()

    def wrapper(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if (
            'FROM "market_shoporder"' in sql
            and "FOR UPDATE" in sql
            and not parts_locked.is_set()
        ):
            parts_locked.set()
            assert awarded.wait(4)
        return result

    def redeem():
        with connection.execute_wrapper(wrapper):
            return claim(
                buyer,
                listing.pk,
                "standard",
                choice(shop),
                None,
                CONTACT,
                sequence=1,
                now=NOW,
            )

    def award():
        assert parts_locked.wait(4)
        record_online_payment(pending)
        awarded.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        gift = pool.submit(thread_call, redeem)
        payment = pool.submit(thread_call, award)
        reward = gift.result(timeout=15)
        payment.result(timeout=15)
    assert reward.purchases.get().units == 10
    assert not reward.referral_uses.exists()
    assert progress(buyer)["stamps"] == 1


def test_two_claims_consume_referral_once(buyer, friend, listing, shop):
    require_postgres()
    purchase(buyer, listing, 9)
    invite(buyer, friend)
    qualify_part(purchase(friend, listing, 1))

    def redeem():
        return thread_call(
            lambda: (
                claim(
                    buyer,
                    listing.pk,
                    "standard",
                    choice(shop),
                    None,
                    CONTACT,
                    sequence=1,
                    now=NOW,
                ).pk
            )
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: redeem(), range(2)))
    assert len(set(ids)) == 1
    assert Referral.objects.get(friend=friend).reward_id == ids[0]
    assert progress(buyer)["stamps"] == 0


def test_source_refund_and_claim_serialize_without_repricing(
    buyer, friend, listing, shop
):
    require_postgres()
    purchase(buyer, listing, 9)
    invite(buyer, friend)
    source = purchase(friend, listing, 1)
    source.payment_method = "online"
    source.save()
    qualify_part(source)
    locked, refund_started = Event(), Event()

    def wrapper(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if (
            'FROM "market_shoporder"' in sql
            and "FOR UPDATE" in sql
            and not locked.is_set()
        ):
            locked.set()
            assert refund_started.wait(4)
        return result

    def redeem():
        with connection.execute_wrapper(wrapper):
            return claim(
                buyer,
                listing.pk,
                "standard",
                choice(shop),
                None,
                CONTACT,
                sequence=1,
                now=NOW,
            )

    def refund():
        assert locked.wait(4)
        refund_started.set()
        refund_online_payment(source)

    with ThreadPoolExecutor(max_workers=2) as pool:
        gift = pool.submit(thread_call, redeem)
        returned = pool.submit(thread_call, refund)
        reward = gift.result(timeout=15)
        returned.result(timeout=15)
    assert reward.limit == 2500 and reward.referral_uses.count() == 1
    assert progress(buyer)["available"] == 0


def test_reciprocal_bindings_do_not_deadlock(buyer, friend):
    require_postgres()
    CustomerPhone.objects.create(user=buyer, phone="+79990000102", verified=True)
    buyer_code, friend_code = personal_link(buyer).code, personal_link(friend).code
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(thread_call, lambda: bind(buyer, friend_code))
        second = pool.submit(thread_call, lambda: bind(friend, buyer_code))
        assert first.result(timeout=15) is not None
        assert second.result(timeout=15) is not None


def test_later_friend_completion_and_gift_claim_do_not_deadlock(
    buyer, friend, listing, shop, owner
):
    from market.orders import transition_shop_order

    require_postgres()
    purchase(buyer, listing, 9)
    invite(buyer, friend)
    qualify_part(purchase(friend, listing, 1))
    later = purchase(friend, listing, 1, completed=False)
    later.status = "ready"
    later.save()
    credit_locked, stock_locked = Event(), Event()

    def claim_wrapper(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if 'FROM "market_referral"' in sql and "FOR UPDATE" in sql:
            credit_locked.set()
            assert stock_locked.wait(4)
        return result

    def completion_wrapper(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if 'FROM "partner_stockrecord"' in sql and "FOR UPDATE" in sql:
            stock_locked.set()
        return result

    def redeem():
        with connection.execute_wrapper(claim_wrapper):
            return claim(
                buyer,
                listing.pk,
                "standard",
                choice(shop),
                None,
                CONTACT,
                sequence=1,
                now=NOW,
            )

    def complete():
        assert credit_locked.wait(4)
        with connection.execute_wrapper(completion_wrapper):
            return transition_shop_order(later, owner, "completed")

    with ThreadPoolExecutor(max_workers=2) as pool:
        gift = pool.submit(thread_call, redeem)
        completion = pool.submit(thread_call, complete)
        reward = gift.result(timeout=15)
        assert completion.result(timeout=15).status == "completed"
    assert reward.referral_uses.count() == 1
    assert progress(buyer)["stamps"] == 0
