import pytest
from django.contrib.auth import get_user_model

from market.models import CustomerPhone

pytestmark = pytest.mark.django_db


def test_password_form_uses_phone_or_email_label(client):
    form = client.get("/login/password/").context["form"]
    assert form.fields["username"].label == "Телефон или email"


def test_email_password_login_is_case_insensitive(client, owner, shop):
    response = client.post(
        "/login/password/",
        {"username": "SELLER@example.com", "password": "long-secret-pass"},
    )
    assert response.status_code == 302
    assert response.url == f"/partner/{shop.slug}/"
    assert client.session["_auth_user_id"] == str(owner.pk)


@pytest.mark.parametrize("phone", ["+79991234567", "8 (999) 123-45-67", "9991234567"])
def test_verified_phone_password_login(client, owner, phone):
    CustomerPhone.objects.create(user=owner, phone="+79991234567", verified=True)
    response = client.post(
        "/login/password/",
        {"username": phone, "password": "long-secret-pass", "next": "/checkout/"},
    )
    assert response.url == "/checkout/"
    assert client.session["_auth_user_id"] == str(owner.pk)


def test_unverified_phone_cannot_be_used_for_password_login(client, owner):
    CustomerPhone.objects.create(user=owner, phone="+79991234567", verified=False)
    response = client.post(
        "/login/password/", {"username": "+79991234567", "password": "long-secret-pass"}
    )
    assert response.status_code == 200
    assert "_auth_user_id" not in client.session


def test_duplicate_email_does_not_choose_an_account(client, owner):
    get_user_model().objects.create_user(
        "duplicate", "SELLER@example.com", "different-password"
    )
    response = client.post(
        "/login/password/", {"username": owner.email, "password": "long-secret-pass"}
    )
    assert response.status_code == 200
    assert "_auth_user_id" not in client.session


@pytest.mark.parametrize(
    "identifier,password",
    [
        ("seller@example.com", "wrong"),
        ("missing@example.com", "long-secret-pass"),
        ("seller", "long-secret-pass"),
    ],
)
def test_invalid_credentials_are_rejected(client, owner, identifier, password):
    response = client.post(
        "/login/password/", {"username": identifier, "password": password}
    )
    assert response.status_code == 200
    assert "_auth_user_id" not in client.session


def test_inactive_account_cannot_login_by_email(client, owner):
    owner.is_active = False
    owner.save()
    response = client.post(
        "/login/password/", {"username": owner.email, "password": "long-secret-pass"}
    )
    assert response.status_code == 200
    assert "_auth_user_id" not in client.session
