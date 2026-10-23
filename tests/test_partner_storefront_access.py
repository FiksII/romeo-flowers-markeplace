import pytest
from oscar.core.loading import get_model

from market.models import Membership

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("path", [
    "/", "/catalogue/", "/products/1/", "/shops/test-shop/", "/basket/",
    "/checkout/", "/account/orders/", "/account/orders/live/",
    "/account/orders/old/", "/account/orders/old/pay/status/",
])
def test_partner_customer_pages_redirect_to_own_dashboard(client, owner, shop, path):
    client.force_login(owner)
    response = client.get(path)
    assert response.status_code == 302
    assert response.url == "/partner/test-shop/"


@pytest.mark.parametrize("path", [
    "/basket/add/1/", "/basket/update/1/", "/checkout/", "/receiving/",
    "/account/orders/old/pay/", "/account/cancel/1/",
])
def test_partner_purchase_posts_do_not_create_orders_or_basket_lines(
    client, owner, shop, path,
):
    client.force_login(owner)
    response = client.post(path, {"quantity": 1})
    assert response.status_code == 302
    assert response.url == "/partner/test-shop/"
    assert not get_model("order", "Order").objects.exists()
    assert not get_model("basket", "Line").objects.exists()


def test_partner_with_multiple_shops_goes_to_shop_selection(
    client, owner, shop, other_shop,
):
    Membership.objects.create(user=owner, shop=other_shop)
    client.force_login(owner)
    response = client.get("/")
    assert response.status_code == 302
    assert response.url == "/partner/"
    assert client.get(response.url).status_code == 200


@pytest.mark.parametrize("status", ["draft", "review", "suspended"])
def test_partner_access_does_not_depend_on_shop_publication(
    client, owner, shop, status,
):
    shop.status = status
    shop.save()
    client.force_login(owner)
    assert client.get("/checkout/").url == "/partner/test-shop/"


def test_partner_navigation_only_links_to_management(client, owner, shop):
    client.force_login(owner)
    response = client.get("/partner/test-shop/")
    assert response.status_code == 200
    html = response.content.decode()
    for link in ('href="/basket/"', 'href="/account/orders/"', 'href="/"',
                 'href="/shops/test-shop/"',
                 'class="site-search"', 'href="/#receiving"', 'href="/?category='):
        assert link not in html
    assert 'href="/partner/test-shop/"' in html
    assert 'action="/logout/"' in html
    assert client.get("/addresses/?q=Москва").status_code == 200


def test_existing_customer_basket_cannot_be_purchased_after_becoming_partner(
    client, buyer, shop, listing,
):
    client.force_login(buyer)
    client.post(f"/basket/add/{listing.pk}/")
    basket = client.get("/basket/").wsgi_request.basket
    assert basket.num_items == 1
    Membership.objects.create(user=buyer, shop=shop)
    response = client.post("/checkout/", {"basket_id": basket.pk})
    assert response.status_code == 302
    assert response.url == "/partner/test-shop/"
    basket.refresh_from_db()
    assert basket.status == "Open"
    assert basket.all_lines().get().quantity == 1
    assert not get_model("order", "Order").objects.exists()
    listing.stockrecord.refresh_from_db()
    assert listing.stockrecord.num_allocated is None


def test_partner_cannot_add_a_real_product(client, owner, listing):
    client.force_login(owner)
    response = client.post(
        f"/basket/add/{listing.pk}/", {"quantity": 1},
        HTTP_ACCEPT="application/json",
    )
    assert response.status_code == 302
    assert response.url == "/partner/test-shop/"
    assert not get_model("basket", "Line").objects.exists()


@pytest.mark.parametrize("role", ["anonymous", "customer", "operator"])
def test_other_roles_keep_customer_storefront(client, stranger, shop, role):
    if role == "operator":
        stranger.is_superuser = True
        stranger.is_staff = True
        stranger.save()
    if role != "anonymous":
        client.force_login(stranger)
    response = client.get("/")
    assert response.status_code == 200
    assert 'href="/basket/"' in response.content.decode()
