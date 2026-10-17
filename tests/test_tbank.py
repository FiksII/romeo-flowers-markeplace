import io
import json
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone
from oscar.core.loading import get_model

from market import online_payments, tbank
from market.models import BalanceEntry, Listing, OnlinePayment
from market.orders import cancel_shop_order, place_market_order
from tests.test_orders import ADDRESS, CONTACT, NOW, basket_for, choice

pytestmark = pytest.mark.django_db
URLS = {
    "notification_url": "https://romeo.test/n/",
    "return_url": "https://romeo.test/r/",
}


@pytest.fixture(autouse=True)
def terminal(settings):
    settings.TBANK_TERMINAL_KEY = "TestTerminal"
    settings.TBANK_PASSWORD = "secret"
    settings.TBANK_TAXATION = ""
    settings.MARKET_ONLINE_PAYMENTS = True


@pytest.fixture
def bank(monkeypatch):
    """A fake T-Bank: records calls and answers like the real API."""
    calls = []

    def call(method, params):
        calls.append((method, params))
        if method == "Init":
            return {
                "Success": True,
                "Status": "NEW",
                "PaymentId": str(1000 + len(calls)),
                "PaymentURL": f"https://securepay.tinkoff.ru/pay{len(calls)}",
            }
        return {"Success": True, "Status": bank.state, "OrderId": bank.order_ref}

    bank = type("Bank", (), {"calls": calls, "state": "NEW", "order_ref": ""})
    monkeypatch.setattr(online_payments.tbank, "call", call)
    return bank


def second_listing(other_shop, listing):
    product = get_model("catalogue", "Product").objects.create(
        title="Букет из Химок",
        product_class=listing.product.product_class,
        is_public=True,
    )
    get_model("partner", "StockRecord").objects.create(
        product=product,
        partner=other_shop.partner,
        partner_sku="b1",
        price=Decimal(1800),
        price_currency="RUB",
        num_in_stock=5,
    )
    return Listing.objects.create(product=product, shop=other_shop)


def online_order(owner, shop, listing, *more):
    basket = basket_for(owner, listing, *more)
    choices = {shop.pk: choice(shop)}
    for item in more:
        choices[item.shop_id] = choice(item.shop)
    return place_market_order(
        owner, basket, choices, ADDRESS, CONTACT, NOW, payment_method="online"
    )


def notification(payment, status, amount=None):
    data = {
        "TerminalKey": "TestTerminal",
        "OrderId": payment.order_ref,
        "Success": True,
        "Status": status,
        "PaymentId": payment.payment_id,
        "ErrorCode": "0",
        "Amount": str(tbank.kopecks(payment.amount) if amount is None else amount),
        "Pan": "430000******0777",
        "Data": {"connection_type": "Widget"},
    }
    data["Token"] = tbank.make_token(data)
    return data


def test_token_matches_the_documentation_example():
    params = {
        "TerminalKey": "MerchantTerminalKey",
        "Amount": 19200,
        "OrderId": "00000",
        "Description": "Подарочная карта на 1000 рублей",
        "DATA": {"Phone": "+71234567890"},
        "Receipt": {"Items": []},
    }
    assert (
        tbank.make_token(params, password="11111111111111")
        == "72dd466f8ace0a37a1f740ce5fb78101712bc0665d91a8108c7c8a0ccd426db2"
    )


def test_call_signs_the_request_without_sending_the_password(monkeypatch):
    sent = {}

    def urlopen(request, timeout):
        sent["url"] = request.full_url
        sent["body"] = json.loads(request.data)
        return io.BytesIO(json.dumps({"Success": True, "Status": "NEW"}).encode())

    monkeypatch.setattr(tbank, "urlopen", urlopen)
    tbank.call("GetState", {"PaymentId": "42"})
    assert sent["url"] == "https://securepay.tinkoff.ru/v2/GetState"
    assert sent["body"]["TerminalKey"] == "TestTerminal"
    assert "Password" not in sent["body"]
    assert sent["body"]["Token"] == tbank.make_token(
        {"TerminalKey": "TestTerminal", "PaymentId": "42"}
    )


def test_call_raises_on_refusal(monkeypatch):
    monkeypatch.setattr(
        tbank,
        "urlopen",
        lambda request, timeout: io.BytesIO(
            json.dumps(
                {"Success": False, "ErrorCode": "9", "Message": "Ошибка"}
            ).encode()
        ),
    )
    with pytest.raises(tbank.TbankError, match="Ошибка"):
        tbank.call("Init", {})


def test_init_sends_the_order_amount_and_widget_flag(bank, owner, shop, listing):
    order = online_order(owner, shop, listing)
    payment = online_payments.start_payment(order, now=timezone.now(), **URLS)
    method, params = bank.calls[0]
    assert method == "Init"
    assert params["Amount"] == 250000
    assert params["OrderId"] == f"{order.number}-1"
    assert params["DATA"] == {"connection_type": "Widget"}
    assert params["NotificationURL"] == URLS["notification_url"]
    assert "Receipt" not in params
    assert payment.payment_url == "https://securepay.tinkoff.ru/pay1"
    # Pressing another SpeedPay button reuses the same open payment.
    assert online_payments.start_payment(order, now=timezone.now(), **URLS) == payment
    assert len(bank.calls) == 1


def test_receipt_is_sent_when_a_cash_register_is_connected(
    bank, settings, owner, shop, listing
):
    settings.TBANK_TAXATION = "usn_income"
    order = online_order(owner, shop, listing)
    online_payments.start_payment(order, now=timezone.now(), **URLS)
    receipt = bank.calls[0][1]["Receipt"]
    assert receipt["Taxation"] == "usn_income"
    assert receipt["Email"] == owner.email
    assert receipt["Items"] == [
        {
            "Name": "Нежность",
            "Price": 250000,
            "Quantity": 1,
            "Amount": 250000,
            "Tax": "none",
            "PaymentMethod": "full_prepayment",
            "PaymentObject": "commodity",
        }
    ]


def test_cash_orders_and_disabled_terminal_cannot_start(
    bank, settings, owner, shop, listing
):
    cash = place_market_order(
        owner,
        basket_for(owner, listing),
        {shop.pk: choice(shop)},
        ADDRESS,
        CONTACT,
        NOW,
        payment_method="cash",
    )
    with pytest.raises(ValidationError):
        online_payments.start_payment(cash, now=timezone.now(), **URLS)
    settings.TBANK_PASSWORD = ""
    with pytest.raises(ValidationError):
        online_payments.start_payment(
            online_order(owner, shop, listing), now=timezone.now(), **URLS
        )
    assert bank.calls == []


def test_expired_reservation_cannot_be_paid(bank, owner, shop, listing):
    order = online_order(owner, shop, listing)
    later = online_payments.payment_deadline(order)
    with pytest.raises(ValidationError, match="истекло"):
        online_payments.start_payment(order, now=later, **URLS)


def test_confirmed_notification_pays_every_part(
    bank, owner, shop, other_shop, listing, client
):
    other = second_listing(other_shop, listing)
    order = online_order(owner, shop, listing, other)
    payment = online_payments.start_payment(order, now=timezone.now(), **URLS)
    assert payment.amount == Decimal(4300)
    for status in ["AUTHORIZED", "CONFIRMED", "CONFIRMED"]:
        response = client.post(
            "/payments/tbank/notification/",
            notification(payment, status),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.content == b"OK"
    assert set(order.shop_orders.values_list("payment_status", flat=True)) == {"paid"}
    order.refresh_from_db()
    assert order.status == "Processing"
    assert BalanceEntry.objects.filter(kind="platform_collected").count() == 2
    # A late AUTHORIZED does not move a paid payment back.
    client.post(
        "/payments/tbank/notification/",
        notification(payment, "AUTHORIZED"),
        content_type="application/json",
    )
    payment.refresh_from_db()
    assert payment.status == "CONFIRMED"


def test_forged_or_wrong_amount_notifications_are_ignored(
    bank, owner, shop, listing, client
):
    order = online_order(owner, shop, listing)
    payment = online_payments.start_payment(order, now=timezone.now(), **URLS)
    forged = notification(payment, "CONFIRMED")
    forged["Token"] = "0" * 64
    response = client.post(
        "/payments/tbank/notification/", forged, content_type="application/json"
    )
    assert response.status_code == 403
    client.post(
        "/payments/tbank/notification/",
        notification(payment, "CONFIRMED", amount=100),
        content_type="application/json",
    )
    assert order.shop_orders.get().payment_status == "pending"


def test_money_for_a_cancelled_part_is_returned(
    bank, owner, shop, other_shop, listing, django_capture_on_commit_callbacks
):
    other = second_listing(other_shop, listing)
    order = online_order(owner, shop, listing, other)
    payment = online_payments.start_payment(order, now=timezone.now(), **URLS)
    cancel_shop_order(order.shop_orders.get(shop=other_shop), owner)
    with django_capture_on_commit_callbacks(execute=True):
        online_payments.handle_notification(notification(payment, "CONFIRMED"))
    payment.refresh_from_db()
    assert payment.returned_amount == Decimal(1800)
    assert order.shop_orders.get(shop=shop).payment_status == "paid"
    assert ("Cancel", {"PaymentId": payment.payment_id, "Amount": 180000}) in bank.calls


def test_bank_refund_takes_the_share_back(bank, owner, shop, listing):
    order = online_order(owner, shop, listing)
    payment = online_payments.start_payment(order, now=timezone.now(), **URLS)
    online_payments.handle_notification(notification(payment, "CONFIRMED"))
    online_payments.handle_notification(notification(payment, "REFUNDED"))
    part = order.shop_orders.get()
    assert part.payment_status == "refunded"
    assert BalanceEntry.objects.filter(part=part, kind="refund").exists()


def test_status_check_applies_the_bank_state(bank, owner, shop, listing):
    order = online_order(owner, shop, listing)
    payment = online_payments.start_payment(order, now=timezone.now(), **URLS)
    bank.state, bank.order_ref = "CONFIRMED", payment.order_ref
    assert online_payments.sync_status(payment).status == "CONFIRMED"
    assert order.shop_orders.get().payment_status == "paid"


def test_reservation_waits_for_a_payment_in_progress(bank, owner, shop, listing):
    from datetime import timedelta

    from market.orders import expire_unpaid_orders

    order = online_order(owner, shop, listing)
    payment = online_payments.start_payment(order, now=timezone.now(), **URLS)
    later = timezone.now() + timedelta(minutes=31)
    OnlinePayment.objects.filter(pk=payment.pk).update(
        status="AUTHORIZING", updated_at=later - timedelta(minutes=1)
    )
    assert expire_unpaid_orders(later) == 0
    assert expire_unpaid_orders(later + timedelta(minutes=15)) == 1


def test_pay_endpoint_returns_the_payment_url(
    bank, owner, stranger, shop, listing, client
):
    order = online_order(owner, shop, listing)
    client.force_login(stranger)
    assert client.post(f"/account/orders/{order.number}/pay/").status_code == 404
    client.force_login(owner)
    page = client.get(f"/account/orders/{order.number}/")
    assert b"data-tbank-payment" in page.content
    assert b'data-terminal-key="TestTerminal"' in page.content
    response = client.post(f"/account/orders/{order.number}/pay/")
    assert response.json() == {"paymentUrl": "https://securepay.tinkoff.ru/pay1"}
    status = client.post(f"/account/orders/{order.number}/pay/status/").json()
    assert status == {"status": "NEW", "waiting": True}


def test_pay_endpoint_reports_errors(bank, owner, shop, listing, client):
    order = online_order(owner, shop, listing)
    cancel_shop_order(order.shop_orders.get(), owner)
    client.force_login(owner)
    response = client.post(f"/account/orders/{order.number}/pay/")
    assert response.status_code == 409
    assert "error" in response.json()
    assert (
        b"data-tbank-payment"
        not in client.get(f"/account/orders/{order.number}/").content
    )
