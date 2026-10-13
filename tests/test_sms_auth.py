import io
import json
import re
import time
from urllib.parse import parse_qs

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from oscar.core.loading import get_model

from market.availability import next_slot

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_sms_limits():
    cache.clear()


@pytest.fixture
def sms_gateway(monkeypatch, settings):
    settings.SMSPILOT_API_KEY = "test-private-key"
    messages = []

    def transport(request, timeout):
        payload = parse_qs(request.data.decode())
        messages.append(payload)
        return io.BytesIO(
            json.dumps({"send": [{"server_id": "123", "status": "0"}]}).encode()
        )

    monkeypatch.setattr("market.sms.urlopen", transport)
    return messages


def received_code(messages):
    return re.search(r"\b\d{6}\b", messages[-1]["send"][0]).group()


def test_signup_only_asks_for_phone(client):
    response = client.get("/signup/")
    assert response.status_code == 200
    assert set(response.context["form"].fields) == {"phone"}


def test_login_offers_sms_and_legacy_password(client):
    response = client.get("/login/")
    assert response.status_code == 200
    assert set(response.context["form"].fields) == {"phone"}
    assert "/login/password/" in response.content.decode()


def test_first_checkout_requires_email(client, owner, listing):
    owner.email = ""
    owner.save()
    client.force_login(owner)
    client.post(f"/basket/add/{listing.pk}/")
    response = client.post("/checkout/", {"name": "Иван", "phone": "+79991234567"})
    assert response.status_code == 200
    assert "email" in response.context["form"].errors


def test_returning_buyer_is_not_asked_for_email(client, owner, listing):
    client.force_login(owner)
    client.post(f"/basket/add/{listing.pk}/")
    response = client.get("/checkout/")
    assert "email" not in response.context["form"].fields


def test_registration_cannot_bypass_sms_with_old_password_fields(client):
    response = client.post(
        "/signup/",
        {
            "username": "bypass",
            "email": "buyer@example.com",
            "password1": "Some-Long-Secret-732",
            "password2": "Some-Long-Secret-732",
        },
    )
    assert response.status_code == 200
    assert not get_user_model().objects.filter(username="bypass").exists()


def test_sms_registration_then_login_uses_same_account(client, sms_gateway):
    response = client.post("/signup/", {"phone": "8 (999) 123-45-67"})
    assert response.status_code == 302
    assert "_auth_user_id" not in client.session
    assert sms_gateway[-1]["to"] == ["79991234567"]
    from market.models import CustomerPhone

    record = CustomerPhone.objects.get(phone="+79991234567")
    assert not record.verified
    assert not record.user.has_usable_password()
    assert record.user.email == ""
    code = received_code(sms_gateway)
    assert code not in client.get(response.url).content.decode()
    verified = client.post(response.url, {"code": code})
    assert verified.status_code == 302
    assert client.session["_auth_user_id"] == str(record.user_id)
    record.refresh_from_db()
    assert record.verified
    client.post("/logout/")
    cache.clear()  # Represent a later login, after the send cooldown.
    login = client.post("/login/", {"phone": "9991234567"})
    assert login.status_code == 302
    assert "_auth_user_id" not in client.session
    client.post(login.url, {"code": received_code(sms_gateway)})
    assert client.session["_auth_user_id"] == str(record.user_id)
    assert CustomerPhone.objects.count() == 1


def test_bad_code_does_not_authenticate_and_exhausts_attempts(client, sms_gateway):
    response = client.post("/signup/", {"phone": "+79991234567"})
    code = received_code(sms_gateway)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(3):
        client.post(response.url, {"code": wrong})
    client.post(response.url, {"code": code})
    assert "_auth_user_id" not in client.session


def test_expired_code_cannot_authenticate(client, sms_gateway, monkeypatch):
    response = client.post("/signup/", {"phone": "+79991234567"})
    code = received_code(sms_gateway)
    later = time.time() + 301
    monkeypatch.setattr(
        "allauth.account.internal.flows.code_verification.time.time", lambda: later
    )
    client.post(response.url, {"code": code})
    assert "_auth_user_id" not in client.session


def test_sms_failure_rolls_back_new_account(client, sms_gateway, monkeypatch):
    def rejected(request, timeout):
        return io.BytesIO(b'{"error":{"code":"111"}}')

    monkeypatch.setattr("market.sms.urlopen", rejected)
    response = client.post("/signup/", {"phone": "+79991234567"})
    assert response.status_code == 200
    assert response.context["form"].non_field_errors()
    assert not get_user_model().objects.exists()
    assert "_auth_user_id" not in client.session


def test_repeated_sms_request_is_limited(client, sms_gateway):
    client.post("/signup/", {"phone": "+79991234567"})
    response = client.post("/login/", {"phone": "+79991234567"})
    assert response.status_code in {200, 302, 429}
    assert len(sms_gateway) == 1


def test_first_order_saves_email_and_later_checkout_does_not_ask(
    client, owner, shop, listing
):
    owner.email = ""
    owner.save()
    client.force_login(owner)
    client.post(f"/basket/add/{listing.pk}/")
    basket = client.get("/checkout/").wsgi_request.basket
    response = client.post(
        "/checkout/",
        {
            "basket_id": basket.pk,
            "email": "buyer@example.com",
            "name": "Получатель",
            "phone": "+79991234567",
            f"method_{shop.pk}": "pickup",
            f"slot_{shop.pk}": next_slot(shop, "pickup", timezone.now()).value,
        },
    )
    assert response.status_code == 302
    owner.refresh_from_db()
    assert owner.email == "buyer@example.com"
    assert (
        get_model("order", "Order").objects.get(user=owner).guest_email
        == "buyer@example.com"
    )
    assert "email" not in client.get("/checkout/").context["form"].fields


def test_invalid_email_blocks_order(client, owner, listing):
    owner.email = ""
    owner.save()
    client.force_login(owner)
    client.post(f"/basket/add/{listing.pk}/")
    response = client.post(
        "/checkout/", {"email": "invalid", "name": "Иван", "phone": "+79991234567"}
    )
    assert "email" in response.context["form"].errors
    assert not get_model("order", "Order").objects.exists()


def test_restart_can_change_phone_and_invalidates_old_code(client, sms_gateway):
    response = client.post("/signup/", {"phone": "+79991234567"})
    code = received_code(sms_gateway)
    assert client.post("/login/restart/").url == "/login/"
    assert client.get("/login/").status_code == 200
    client.post(response.url, {"code": code})
    assert "_auth_user_id" not in client.session


def test_resend_provider_failure_returns_recoverable_page(
    client, sms_gateway, monkeypatch
):
    response = client.post("/signup/", {"phone": "+79991234567"})
    cache.clear()

    def failed(request, timeout):
        raise TimeoutError("private-key-must-not-leak")

    monkeypatch.setattr("market.sms.urlopen", failed)
    response = client.post(response.url, {"action": "resend"})
    assert response.status_code == 503
    assert "private-key-must-not-leak" not in response.content.decode()
    assert "_auth_user_id" not in client.session


def test_sms_next_redirect_returns_to_checkout(client, sms_gateway):
    response = client.post("/signup/?next=/checkout/", {"phone": "+79991234567"})
    response = client.post(response.url, {"code": received_code(sms_gateway)})
    assert response.url == "/checkout/"


def test_sms_next_redirect_rejects_external_site(client, sms_gateway):
    response = client.post(
        "/signup/?next=https://evil.example/", {"phone": "+79991234567"}
    )
    response = client.post(response.url, {"code": received_code(sms_gateway)})
    assert response.url == "/account/orders/"


def test_hourly_phone_limit_survives_cooldown_and_different_ips(
    client, sms_gateway, monkeypatch
):
    now = time.time()
    monkeypatch.setattr("allauth.core.internal.ratelimit.time.time", lambda: now)
    for attempt in range(6):
        client.post("/login/restart/")
        client.post(
            "/login/", {"phone": "+79991234567"}, REMOTE_ADDR=f"192.0.2.{attempt + 1}"
        )
        now += 61
    assert len(sms_gateway) == 5
