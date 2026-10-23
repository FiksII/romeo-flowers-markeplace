from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from market.availability import next_slot
from market.models import BalanceEntry, Payout
from market.orders import cancel_shop_order, place_market_order, transition_shop_order
from market.payments import (
    cancel_payout,
    confirm_payout,
    create_payout,
    dispute_payout,
    record_online_payment,
    refund_online_payment,
    shop_balance,
)
from tests.test_orders import ADDRESS, CONTACT, NOW, basket_for, choice

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_user():
    return get_user_model().objects.create_superuser(
        "boss", "boss@example.com", "long-secret-pass"
    )


def order_part(owner, shop, listing, method="pickup", payment="cash"):
    shop.markup_percent = Decimal(20)
    shop.save()
    basket = basket_for(owner, listing)
    order = place_market_order(
        owner,
        basket,
        {shop.pk: choice(shop, method)},
        ADDRESS,
        CONTACT,
        NOW,
        payment_method="online" if method == "delivery" else payment,
    )
    part = order.shop_orders.get()
    if method == "delivery" and payment == "cash":
        # Historical cash deliveries must remain operable. New checkout forbids
        # them, so construct the old persisted state explicitly for those tests.
        from market import messengers

        part.payment_method = "cash"
        part.save(update_fields=["payment_method"])
        order.status = "Processing"
        order.save(update_fields=["status"])
        messengers.notify_new_part(part)
    return part


def deliver(part, actor):
    for target in ["preparing", "ready"] + (
        ["courier"] if part.method == "delivery" else []
    ):
        transition_shop_order(part, actor, target)
    return transition_shop_order(part, actor, "completed")


def test_item_keeps_the_price_it_was_bought_at(owner, shop, listing):
    part = order_part(owner, shop, listing)
    stock = listing.stockrecord
    stock.price = Decimal(3500)
    stock.save()
    listing.product.title = "Новое название"
    listing.product.save()
    item = part.items.get()
    assert (item.title, item.quantity) == ("Нежность", 1)
    assert item.unit_price == Decimal(3000)
    assert item.unit_commission == Decimal(500)
    assert item.unit_payout == Decimal(2500)
    assert item.commission_percent == Decimal(20)
    listing.delete()
    item.refresh_from_db()
    assert item.listing is None
    assert item.unit_price == Decimal(3000)


def test_online_payment_credits_the_shop_once(owner, shop, listing):
    part = order_part(owner, shop, listing, "delivery", payment="online")
    assert shop_balance(shop) == 0
    record_online_payment(part)
    record_online_payment(part)
    part.refresh_from_db()
    assert part.payment_status == "paid"
    assert shop_balance(shop) == Decimal(2500)
    assert BalanceEntry.objects.get().kind == "platform_collected"


def test_refund_takes_the_share_back(owner, shop, listing):
    part = order_part(owner, shop, listing, payment="online")
    record_online_payment(part)
    refund_online_payment(part)
    with pytest.raises(ValidationError):
        refund_online_payment(part)
    assert shop_balance(shop) == 0


def test_cash_in_the_shop_makes_the_shop_owe_romeo_its_part(owner, shop, listing):
    part = order_part(owner, shop, listing, "pickup")
    assert part.payment_status == "pending"
    assert part.order.status == "Processing"
    deliver(part, owner)
    part.refresh_from_db()
    assert part.payment_status == "paid"
    assert shop_balance(shop) == Decimal(-500)
    assert BalanceEntry.objects.get().kind == "shop_collected"


def test_cash_taken_by_romeo_courier_credits_the_shop(owner, admin_user, shop, listing):
    part = order_part(owner, shop, listing, "delivery")
    assert part.platform_collects
    deliver(part, admin_user)
    assert shop_balance(shop) == Decimal(2500)


def test_cash_order_can_be_worked_without_prepayment_and_cancelled(
    owner, shop, listing
):
    part = order_part(owner, shop, listing)
    transition_shop_order(part, owner, "preparing")
    cancel_shop_order(part, owner)
    part.refresh_from_db()
    assert part.status == "cancelled"
    assert not BalanceEntry.objects.exists()


def test_customer_cannot_cancel_cash_order_after_it_is_ready(
    owner, stranger, shop, listing
):
    part = order_part(stranger, shop, listing)
    cancel_shop_order(part, stranger)
    part = order_part(stranger, shop, listing)
    transition_shop_order(part, owner, "preparing")
    transition_shop_order(part, owner, "ready")
    part.refresh_from_db()
    with pytest.raises(ValidationError):
        cancel_shop_order(part, stranger)


def test_cash_orders_do_not_expire(owner, shop, listing):
    from market.orders import expire_unpaid_orders

    order_part(owner, shop, listing)
    assert expire_unpaid_orders(NOW.replace(year=2030)) == 0


def paid_up(owner, shop, listing):
    part = order_part(owner, shop, listing, payment="online")
    record_online_payment(part)


def test_payout_lowers_the_balance_and_needs_the_shop_to_confirm(
    owner, stranger, shop, listing, admin_user
):
    paid_up(owner, shop, listing)
    payout = create_payout(shop, Decimal(1500), admin_user, "Неделя 41")
    assert shop_balance(shop) == Decimal(1000)
    assert payout.status == "sent"
    with pytest.raises(PermissionDenied):
        confirm_payout(payout, stranger)
    with pytest.raises(PermissionDenied):
        confirm_payout(payout, admin_user)
    confirm_payout(payout, owner)
    payout.refresh_from_db()
    assert (payout.status, payout.responded_by) == ("confirmed", owner)
    with pytest.raises(ValidationError):
        dispute_payout(payout, owner)
    assert shop_balance(shop) == Decimal(1000)


def test_payout_is_limited_to_what_romeo_owes(owner, shop, listing, admin_user):
    paid_up(owner, shop, listing)
    with pytest.raises(ValidationError):
        create_payout(shop, Decimal("2500.01"), admin_user)
    with pytest.raises(ValidationError):
        create_payout(shop, Decimal(0), admin_user)
    with pytest.raises(PermissionDenied):
        create_payout(shop, Decimal(10), owner)


def test_disputed_payout_can_be_cancelled_and_restores_the_debt(
    owner, shop, listing, admin_user
):
    paid_up(owner, shop, listing)
    payout = create_payout(shop, Decimal(2000), admin_user)
    dispute_payout(payout, owner)
    cancel_payout(payout, admin_user)
    payout.refresh_from_db()
    assert payout.status == "cancelled"
    assert shop_balance(shop) == Decimal(2500)
    with pytest.raises(ValidationError):
        cancel_payout(payout, admin_user)
    assert Payout.objects.count() == 1


def test_balance_entries_are_never_edited(owner, shop, listing):
    paid_up(owner, shop, listing)
    entry = BalanceEntry.objects.get()
    entry.amount = 1
    with pytest.raises(ValidationError):
        entry.save()


def test_pickup_checkout_creates_cash_order_and_rejects_online_until_connected(
    client, owner, shop, listing
):
    client.force_login(owner)
    client.post(f"/basket/add/{listing.pk}/")
    basket = client.get("/checkout/").wsgi_request.basket
    data = {
        "basket_id": basket.pk,
        "customer_name": "Анна",
        "customer_phone": "+79997654321",
        "name": "Иван",
        "phone": "+79991234567",
        "delivery_contact": "call",
        f"method_{shop.pk}": "pickup",
        f"slot_{shop.pk}": next_slot(shop, "pickup", timezone.now()).value,
    }
    response = client.post("/checkout/", {**data, "payment_method": "online"})
    assert response.status_code == 200
    response = client.post("/checkout/", {**data, "payment_method": "cash"})
    assert response.status_code == 302
    assert owner.orders.get().shop_orders.get().payment_method == "cash"


def test_balance_pages_and_payout_flow_over_http(
    client, owner, stranger, shop, listing, admin_user
):
    paid_up(owner, shop, listing)
    client.force_login(admin_user)
    page = client.get("/operator/balances/")
    assert page.status_code == 200
    assert "Ромео должен магазинам" in page.content.decode()
    response = client.post(
        f"/operator/balances/{shop.slug}/pay/",
        {f"shop{shop.pk}-amount": "1000", f"shop{shop.pk}-note": "Тест"},
    )
    assert response.status_code == 302
    payout = Payout.objects.get()
    client.force_login(owner)
    page = client.get(f"/partner/{shop.slug}/balance/")
    assert "Деньги получены" in page.content.decode()
    assert (
        "Подтвердите получение" in client.get(f"/partner/{shop.slug}/").content.decode()
    )
    client.force_login(stranger)
    assert (
        client.post(f"/partner/{shop.slug}/payouts/{payout.pk}/confirm/").status_code
        == 404
    )
    client.force_login(owner)
    client.post(f"/partner/{shop.slug}/payouts/{payout.pk}/confirm/")
    payout.refresh_from_db()
    assert payout.status == "confirmed"
    client.force_login(stranger)
    assert client.get("/operator/balances/").status_code == 403
