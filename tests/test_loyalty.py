from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from oscar.core.loading import get_model

from market.availability import next_slot
from market.models import Listing
from market.orders import place_market_order

pytestmark = pytest.mark.django_db
NOW = datetime(2026, 10, 23, 10, tzinfo=ZoneInfo("Europe/Moscow"))
CONTACT = {"name": "Иван", "phone": "+79991234567"}
ADDRESS = {
    "region": "77",
    "city": "Москва",
    "value": "Москва, Тестовая улица, д. 1",
    "latitude": "55.75",
    "longitude": "37.61",
}


def choice(shop, method="pickup"):
    return {"method": method, "slot": next_slot(shop, method, NOW).value}


def purchase(buyer, listing, quantity, *, paid=True, completed=True):
    stock = listing.stockrecord
    stock.num_in_stock = 100
    stock.save()
    basket = get_model("basket", "Basket").objects.create(owner=buyer)
    from market.strategy import MarketplaceStrategy

    basket.strategy = MarketplaceStrategy()
    basket.add_product(listing.product, quantity=quantity)
    order = place_market_order(
        buyer, basket, {listing.shop_id: choice(listing.shop)}, None, CONTACT, NOW
    )
    part = order.shop_orders.get()
    part.status = "completed" if completed else "accepted"
    part.payment_status = "paid" if paid else "pending"
    part.save()
    return part


def second_listing(listing, other_shop):
    product = get_model("catalogue", "Product").objects.create(
        title="Второй букет", product_class=listing.product.product_class
    )
    get_model("partner", "StockRecord").objects.create(
        product=product,
        partner=other_shop.partner,
        partner_sku="loyal-second",
        price=1500,
        price_currency="RUB",
        num_in_stock=100,
    )
    return Listing.objects.create(product=product, shop=other_shop)


def test_counts_bouquets_across_shops_and_paid_completed_parts(
    buyer, listing, other_shop
):
    from market.loyalty import progress

    second = second_listing(listing, other_shop)
    purchase(buyer, listing, 4)
    purchase(buyer, second, 6)
    purchase(buyer, listing, 2, paid=False)
    purchase(buyer, second, 3, completed=False)
    state = progress(buyer)
    assert (state["stamps"], state["available"], state["remaining"]) == (10, 1, 0)
    assert state["limit"] == Decimal("1900.00")


def test_gift_is_free_idempotent_and_does_not_earn_stamps(buyer, listing, shop):
    from market.loyalty import claim, progress
    from market.models import LoyaltyReward
    from market.online_payments import payable_parts
    from market.orders import expire_unpaid_orders, new_orders, transition_shop_order

    purchase(buyer, listing, 10)
    before = listing.stockrecord.num_allocated
    reward = claim(
        buyer, listing.pk, "standard", choice(shop), None, CONTACT, sequence=1, now=NOW
    )
    again = claim(
        buyer, listing.pk, "standard", choice(shop), None, CONTACT, sequence=1, now=NOW
    )
    assert again.pk == reward.pk
    assert LoyaltyReward.objects.count() == 1
    order = reward.order
    part = order.shop_orders.get()
    assert (
        order.total_incl_tax
        == part.total
        == part.partner_total
        == part.commission_total
        == 0
    )
    assert part.payment_method == "gift" and part.payment_status == "free"
    assert part.items.get().unit_price == part.items.get().unit_payout == 0
    assert part.lines.get().line_price_incl_tax == 0
    assert part.can_be_worked and part in new_orders(shop)
    assert not payable_parts(order).exists()
    assert expire_unpaid_orders(NOW) == 0
    assert listing.stockrecord.num_allocated == before + 1
    assert progress(buyer)["stamps"] == 0
    actor = shop.memberships.get().user
    for status in ["preparing", "ready", "completed"]:
        part = transition_shop_order(part, actor, status)
    assert not shop.balance_entries.exists()
    assert progress(buyer)["available"] == 0


def test_gift_delivery_and_shop_delivery_are_free_without_bank(
    buyer, listing, shop, settings
):
    from market.loyalty import claim
    from market.orders import new_romeo_orders

    settings.MARKET_ONLINE_PAYMENTS = False
    purchase(buyer, listing, 20)
    first = claim(
        buyer,
        listing.pk,
        "standard",
        choice(shop, "delivery"),
        ADDRESS,
        CONTACT,
        sequence=1,
        now=NOW,
    )
    part = first.order.shop_orders.get()
    assert part.total == part.delivery_total == 0
    assert part in new_romeo_orders()
    shop.own_delivery = True
    shop.save()
    second = claim(
        buyer,
        listing.pk,
        "standard",
        choice(shop, "delivery"),
        ADDRESS,
        CONTACT,
        sequence=2,
        now=NOW,
    )
    assert second.order.shop_orders.get().delivery_owner == "partner"
    assert second.order.total_incl_tax == 0


def test_limit_and_fulfillment_rechecked_and_failed_claim_keeps_entitlement(
    buyer, listing, shop
):
    from market.loyalty import claim, progress
    from market.models import LoyaltyReward

    purchase(buyer, listing, 10)
    stock = listing.stockrecord
    stock.price = 2600
    stock.save()
    with pytest.raises(ValidationError, match="лимит"):
        claim(
            buyer,
            listing.pk,
            "standard",
            choice(shop),
            None,
            CONTACT,
            sequence=1,
            now=NOW,
        )
    assert not LoyaltyReward.objects.exists()
    assert progress(buyer)["available"] == 1
    stock.price = 2500
    stock.num_in_stock = stock.num_allocated
    stock.save()
    with pytest.raises(ValidationError):
        claim(
            buyer,
            listing.pk,
            "standard",
            choice(shop),
            None,
            CONTACT,
            sequence=1,
            now=NOW,
        )
    stock.num_in_stock = 100
    stock.save()
    for size, delivery, addr in [("invalid", False, None), ("standard", True, None)]:
        with pytest.raises(ValidationError):
            claim(
                buyer,
                listing.pk,
                size,
                choice(shop, "delivery" if delivery else "pickup"),
                addr,
                CONTACT,
                sequence=1,
                now=NOW,
            )


def test_consumed_purchases_do_not_shift_after_refund_or_gift_cancellation(
    buyer, listing, shop
):
    from market.loyalty import claim, progress
    from market.orders import cancel_shop_order

    old = purchase(buyer, listing, 10)
    gift = claim(
        buyer, listing.pk, "standard", choice(shop), None, CONTACT, sequence=1, now=NOW
    )
    cancel_shop_order(gift.order.shop_orders.get(), buyer)
    old.payment_status = "refunded"
    old.save()
    purchase(buyer, listing, 9)
    assert progress(buyer)["stamps"] == 9
    assert progress(buyer)["available"] == 0
    purchase(buyer, listing, 1)
    assert progress(buyer)["available"] == 1


def test_card_visible_with_ten_flowers_and_gift_rules(client, buyer, listing):
    client.force_login(buyer)
    page = client.get(reverse("market:account"))
    assert page.status_code == 200
    assert "Карта любителя цветов" in page.content.decode()
    assert page.content.decode().count('class="loyalty-stamp') == 10
    page = client.get(reverse("market:loyalty"))
    assert page.status_code == 200
    assert "из всех магазинов" in page.content.decode()
    assert "Доставка подарка бесплатная" in page.content.decode()


def test_gift_payment_instructions_never_request_cash_or_claim_online_payment(
    buyer, listing, shop, client
):
    from market.loyalty import claim
    from market.messengers import order_text
    from market.templatetags.market_tags import romeo_payment_instruction

    purchase(buyer, listing, 10)
    reward = claim(
        buyer, listing.pk, "standard", choice(shop), None, CONTACT, sequence=1, now=NOW
    )
    part = reward.order.shop_orders.get()
    for text in [
        order_text(part),
        order_text(part, for_admin=True),
        romeo_payment_instruction(part),
    ]:
        assert "Подарок" in text
        assert "Получать деньги с клиента не нужно" in text
        assert "Оплачено онлайн" not in text
        assert "Оплата получена" not in text
    client.force_login(shop.memberships.get().user)
    page = client.get(reverse("market:partner-order", args=[shop.slug, part.pk]))
    html = page.content.decode()
    assert "за счёт магазина" in html
    assert "Получить с покупателя" not in html


@pytest.mark.parametrize("method", ["pickup", "delivery"])
def test_gift_http_checkout_preserves_ordinary_basket_and_replays(
    client, buyer, listing, shop, settings, method
):
    from django.utils import timezone

    from market.models import LoyaltyReward

    settings.MARKET_ONLINE_PAYMENTS = False
    purchase(buyer, listing, 10)
    client.force_login(buyer)
    client.post(reverse("market:basket-add", args=[listing.pk]))
    ordinary = client.get(reverse("market:basket")).wsgi_request.basket
    url = reverse("market:loyalty-gift", args=[listing.pk])
    page = client.get(url)
    assert page.status_code == 200
    assert page.context["is_gift"]
    assert "data-gift-checkout" in page.content.decode()
    assert 'value="cash"' not in page.content.decode()
    data = {
        "sequence": "1",
        "size": "standard",
        "payment_method": "gift",
        "customer_name": "Анна",
        "customer_phone": "+79997654321",
        **CONTACT,
        "delivery_contact": "call",
        f"method_{shop.pk}": method,
        f"slot_{shop.pk}": next_slot(shop, method, timezone.now()).value,
    }
    result = client.post(url, data)
    assert result.status_code == 302
    reward = LoyaltyReward.objects.get(user=buyer)
    assert result.url == reverse("market:order", args=[reward.order.number])
    repeat = client.post(url, data)
    assert repeat.url == result.url
    assert LoyaltyReward.objects.count() == 1
    assert client.get(reverse("market:basket")).wsgi_request.basket.pk == ordinary.pk
    ordinary.refresh_from_db()
    assert ordinary.status == "Open" and ordinary.lines.get().quantity == 1
    html = client.get(result.url).content.decode()
    assert "платить не нужно" in html
    assert "не был оплачен онлайн" not in html
    assert not reward.order.online_payments.exists()
    assert "Подарок" in client.get(reverse("market:account")).content.decode()


def test_gift_routes_require_customer_access(client, owner, listing):
    url = reverse("market:loyalty-gift", args=[listing.pk])
    assert client.get(url).status_code == 302
    client.force_login(owner)
    assert client.get(url).url == reverse(
        "market:partner-shop", args=[listing.shop.slug]
    )


def test_gift_cannot_be_requested_through_normal_checkout(buyer, listing, shop):
    from market.strategy import MarketplaceStrategy

    basket = get_model("basket", "Basket").objects.create(owner=buyer)
    basket.strategy = MarketplaceStrategy()
    basket.add_product(listing.product)
    with pytest.raises(ValidationError):
        place_market_order(
            buyer,
            basket,
            {shop.pk: choice(shop)},
            None,
            CONTACT,
            NOW,
            payment_method="gift",
        )


def test_partner_can_still_cancel_ready_unpaid_order(
    client, buyer, owner, listing, shop
):
    part = purchase(buyer, listing, 1, paid=False, completed=False)
    part.status = "ready"
    part.save()
    client.force_login(owner)
    url = reverse("market:partner-order", args=[shop.slug, part.pk])
    html = client.get(url).content.decode()
    assert '<option value="cancelled">' in html
    assert client.post(url, {"status": "cancelled"}).status_code == 302
    part.refresh_from_db()
    assert part.status == "cancelled"
