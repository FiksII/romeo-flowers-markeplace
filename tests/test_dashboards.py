import pytest
from django.core.management import call_command
from django.test import override_settings

from market.models import Listing, Shop

pytestmark = pytest.mark.django_db


def test_seller_urls_are_scoped(client, owner, shop, other_shop, listing):
    client.force_login(owner)
    assert client.get(f"/partner/{shop.slug}/").status_code == 200
    assert client.get(f"/partner/{other_shop.slug}/").status_code == 404
    assert (
        client.post(
            f"/partner/{other_shop.slug}/settings/", {"name": "Hacked"}
        ).status_code
        == 404
    )
    assert client.get("/operator/").status_code == 403
    assert client.get("/checkout/preview/").status_code == 404


def test_public_shop_never_discloses_bank_data(client, shop):
    shop.bank_account = "12345678901234567890"
    shop.save()
    response = client.get(f"/shops/{shop.slug}/")
    assert response.status_code == 200
    assert shop.bank_account not in response.content.decode()


def test_product_creation_owned_and_cannot_set_shop(client, owner, shop, other_shop):
    client.force_login(owner)
    response = client.post(
        f"/partner/{shop.slug}/products/new/",
        {
            "title": "Розовый букет",
            "description": "Розы",
            "category": "bouquet",
            "flower_kind": "Розы",
            "price": "3300",
            "stock": "9",
            "is_public": "on",
            "shop": other_shop.pk,
        },
    )
    assert response.status_code == 302
    listing = Listing.objects.get(product__title="Розовый букет")
    assert listing.shop == shop
    assert listing.stockrecord.partner_id == shop.partner_id
    assert listing.price == 3300


def test_seller_cannot_approve_own_shop(client, owner, shop):
    from market.forms import ShopForm

    form = ShopForm(instance=shop)
    assert "status" not in form.fields
    assert "commission_percent" not in form.fields


@override_settings(MARKET_DEMO=True)
def test_demo_seed_idempotent():
    call_command("seed_marketplace")
    shops, products = Shop.objects.count(), Listing.objects.count()
    assert shops >= 3 and products >= 12
    call_command("seed_marketplace")
    assert Shop.objects.count() == shops
    assert Listing.objects.count() == products
    assert set(Shop.objects.values_list("settlement__region", flat=True)) <= {
        "77",
        "50",
    }


def test_customer_signup_and_login(client):
    response = client.post(
        "/signup/",
        {
            "username": "buyer",
            "email": "buyer@example.com",
            "password1": "Some-Long-Secret-732",
            "password2": "Some-Long-Secret-732",
        },
    )
    assert response.status_code == 302
    assert client.get("/account/orders/").status_code == 200
