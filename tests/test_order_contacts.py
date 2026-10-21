import pytest
from django.utils import timezone

from market.availability import next_slot
from market.forms import CheckoutContactForm
from market.models import CustomerPhone
from tests.test_payments import order_part

pytestmark = pytest.mark.django_db


def test_checkout_requires_customer_contacts_separately_from_recipient():
    form = CheckoutContactForm({"name": "Получатель", "phone": "+79991234567"})
    assert not form.is_valid()
    assert "customer_name" in form.errors
    assert "customer_phone" in form.errors


def test_checkout_prefills_customer_contacts_from_account(client, owner, listing):
    owner.first_name = "Анна"
    owner.save()
    CustomerPhone.objects.create(user=owner, phone="+79997654321", verified=True)
    client.force_login(owner)
    client.post(f"/basket/add/{listing.pk}/")
    form = client.get("/checkout/").context["form"]
    assert form["customer_name"].value() == "Анна"
    assert form["customer_phone"].value() == "+79997654321"


def test_partner_sees_customer_snapshot_and_separate_recipient(
    client, owner, stranger, shop, listing
):
    client.force_login(stranger)
    client.post(f"/basket/add/{listing.pk}/")
    basket = client.get("/checkout/").wsgi_request.basket
    response = client.post(
        "/checkout/",
        {
            "basket_id": basket.pk,
            "customer_name": "Анна Заказчик",
            "customer_phone": "8 (999) 765-43-21",
            "name": "Иван Получатель",
            "phone": "+79991234567",
            f"method_{shop.pk}": "pickup",
            f"slot_{shop.pk}": next_slot(shop, "pickup", timezone.now()).value,
        },
    )
    assert response.status_code == 302
    part = stranger.orders.get().shop_orders.get()
    stranger.first_name = "Изменённое имя"
    stranger.save()
    client.force_login(owner)
    for path in (
        f"/partner/{shop.slug}/orders/",
        f"/partner/{shop.slug}/orders/{part.pk}/",
    ):
        page = client.get(path)
        assert page.status_code == 200
        html = page.content.decode()
        assert "Заказчик" in html and "Получатель" in html
        assert "Анна Заказчик" in html and "Иван Получатель" in html
        assert 'href="tel:+79997654321"' in html
        assert 'href="tel:+79991234567"' in html
        assert "Изменённое имя" not in html


def test_legacy_order_uses_customer_account_without_recipient_substitution(
    client, owner, stranger, shop, listing
):
    part = order_part(stranger, shop, listing)
    stranger.first_name = "Мария"
    stranger.save()
    CustomerPhone.objects.create(user=stranger, phone="+79997654321", verified=True)
    client.force_login(owner)
    path = f"/partner/{shop.slug}/orders/{part.pk}/"
    html = client.get(path).content.decode()
    assert "Мария" in html and 'href="tel:+79997654321"' in html
    CustomerPhone.objects.filter(user=stranger).delete()
    stranger.first_name = ""
    stranger.save()
    html = client.get(path).content.decode()
    assert "Имя не указано" in html and "Телефон не указан" in html
    assert 'href="tel:"' not in html
