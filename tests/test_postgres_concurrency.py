from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections
from oscar.core.loading import get_model

from market.forms import save_listing
from market.orders import cancel_shop_order, place_market_order
from tests.test_orders import CONTACT, NOW, basket_for, choice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.postgresql]


def require_postgres():
    if connection.vendor != "postgresql":
        pytest.skip("Run with romeo_market.postgres_test_settings for row-lock checks")


def thread_call(action):
    close_old_connections()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET lock_timeout = '4s'")
            cursor.execute("SET deadlock_timeout = '150ms'")
        return action()
    finally:
        connections.close_all()


def test_cancel_and_checkout_no_deadlock(owner, shop, listing):
    require_postgres()
    order = place_market_order(
        owner, basket_for(owner, listing), {shop.pk: choice(shop)}, None, CONTACT, NOW
    )
    part = order.shop_orders.get()
    new_basket = basket_for(owner, listing)
    stock_held, cancellation_selected = Event(), Event()

    def checkout_wrapper(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if 'FROM "partner_stockrecord"' in sql and "FOR UPDATE" in sql:
            stock_held.set()
            assert cancellation_selected.wait(2)
        return result

    def cancel_wrapper(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if 'FROM "market_shoporder"' in sql and "FOR UPDATE" in sql:
            cancellation_selected.set()
        return result

    def checkout_action():
        with connection.execute_wrapper(checkout_wrapper):
            return place_market_order(
                owner, new_basket, {shop.pk: choice(shop)}, None, CONTACT, NOW
            ).pk

    def cancel_action():
        assert stock_held.wait(2)
        with connection.execute_wrapper(cancel_wrapper):
            return cancel_shop_order(part, owner).status

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(thread_call, checkout_action)
        second = pool.submit(thread_call, cancel_action)
        assert first.result(timeout=10)
        assert second.result(timeout=10) == "cancelled"
    assert listing.stockrecord.num_allocated == 1


def test_concurrent_last_item_cannot_oversell(owner, shop, listing):
    require_postgres()
    stock = listing.stockrecord
    stock.num_in_stock = 1
    stock.save(update_fields=["num_in_stock"])
    baskets = [basket_for(owner, listing), basket_for(owner, listing)]

    def submit(basket):
        try:
            return place_market_order(
                owner, basket, {shop.pk: choice(shop)}, None, CONTACT, NOW
            ).pk
        except ValidationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda basket: thread_call(lambda: submit(basket)), baskets)
        )
    assert sum(result is not None for result in results) == 1
    assert listing.stockrecord.num_allocated == 1
    assert get_model("order", "Order").objects.count() == 1


def test_concurrent_duplicate_checkout_has_one_order(owner, shop, listing):
    require_postgres()
    basket = basket_for(owner, listing)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: thread_call(
                    lambda: (
                        place_market_order(
                            owner, basket, {shop.pk: choice(shop)}, None, CONTACT, NOW
                        ).pk
                    )
                ),
                range(2),
            )
        )
    assert len(set(results)) == 1
    assert get_model("order", "Order").objects.count() == 1
    assert listing.stockrecord.num_allocated == 1


def test_product_edit_and_checkout_no_deadlock(owner, shop, listing):
    require_postgres()
    basket = basket_for(owner, listing)
    stock_held, seller_waiting = Event(), Event()

    def checkout_wrapper(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if 'FROM "partner_stockrecord"' in sql and "FOR UPDATE" in sql:
            stock_held.set()
            assert seller_waiting.wait(2)
        return result

    def seller_wrapper(execute, sql, params, many, context):
        if 'FROM "partner_stockrecord"' in sql and "FOR UPDATE" in sql:
            seller_waiting.set()
        return execute(sql, params, many, context)

    def checkout_action():
        with connection.execute_wrapper(checkout_wrapper):
            return place_market_order(
                owner, basket, {shop.pk: choice(shop)}, None, CONTACT, NOW
            ).pk

    def seller_action():
        assert stock_held.wait(2)
        with connection.execute_wrapper(seller_wrapper):
            return save_listing(
                shop,
                {
                    "title": "Новая цена",
                    "description": "Розы",
                    "is_public": True,
                    "pickup_price": 3300,
                    "stock": 5,
                    "category": "bouquet",
                    "flower_kind": "Розы",
                },
                listing,
            ).pk

    with ThreadPoolExecutor(max_workers=2) as pool:
        checkout = pool.submit(thread_call, checkout_action)
        edit = pool.submit(thread_call, seller_action)
        assert checkout.result(timeout=10)
        assert edit.result(timeout=10)
    assert listing.stockrecord.num_allocated == 1
