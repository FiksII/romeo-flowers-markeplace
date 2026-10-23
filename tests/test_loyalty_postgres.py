from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

import pytest
from django.db import connection

from market.loyalty import claim, progress
from market.models import LoyaltyReward
from market.orders import transition_shop_order
from market.payments import refund_online_payment
from tests.test_loyalty import CONTACT, NOW, choice, purchase
from tests.test_postgres_concurrency import require_postgres, thread_call

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.postgresql]


def test_duplicate_gift_claims_serialize_and_only_reserve_once(buyer, listing, shop):
    require_postgres()
    purchase(buyer, listing, 10)
    before = listing.stockrecord.num_allocated

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
        results = list(pool.map(lambda _: redeem(), range(2)))
    assert len(set(results)) == 1
    assert LoyaltyReward.objects.filter(user=buyer).count() == 1
    assert listing.stockrecord.num_allocated == before + 1
    assert progress(buyer)["available"] == 0


def test_purchase_completed_and_refunded_during_claim_cannot_change_snapshot(
    buyer, listing, shop, owner
):
    require_postgres()
    paid = purchase(buyer, listing, 10)
    stock = listing.stockrecord
    stock.price = 1000
    stock.save()
    entering = purchase(buyer, listing, 1, completed=False)
    entering.status = "ready"
    entering.save()
    order = entering.order
    order.date_placed = paid.order.date_placed - timedelta(days=1)
    order.save(update_fields=["date_placed"])
    stock.price = 2000
    stock.save()
    locked, completed, read_items, refunded = Event(), Event(), Event(), Event()

    def wrapper(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if (
            'FROM "market_shoporder"' in sql
            and "FOR UPDATE" in sql
            and not locked.is_set()
        ):
            locked.set()
            assert completed.wait(4)
        if (
            'FROM "market_shoporderitem"' in sql
            and "SUM(" in sql
            and not read_items.is_set()
        ):
            read_items.set()
            assert refunded.wait(4)
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

    def change_purchase():
        assert locked.wait(4)
        transition_shop_order(entering, owner, "completed")
        completed.set()
        assert read_items.wait(4)
        refund_online_payment(entering)
        refunded.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        gift = pool.submit(thread_call, redeem)
        change = pool.submit(thread_call, change_purchase)
        reward = gift.result(timeout=15)
        change.result(timeout=15)
    assert reward.limit == reward.paid_total / 10 == 2500
    assert not reward.purchases.filter(item__part=entering).exists()
