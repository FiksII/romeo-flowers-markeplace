from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from oscar.core.loading import get_model

from market.models import Listing, Membership

pytestmark = pytest.mark.django_db
URL = "/partner/shops/storefront/"


def add_listing(shop, title="Второй букет", **values):
    product_class, _ = get_model("catalogue", "ProductClass").objects.get_or_create(
        name="Витрина", defaults={"track_stock": True}
    )
    product = get_model("catalogue", "Product").objects.create(
        title=title, product_class=product_class, is_public=values.pop("public", True)
    )
    get_model("partner", "StockRecord").objects.create(
        product=product, partner=shop.partner, partner_sku=f"preview-{product.pk}",
        price=values.pop("price", Decimal(3000)), price_currency="RUB",
        num_in_stock=values.pop("stock", 3), num_allocated=values.pop("allocated", 0),
    )
    return Listing.objects.create(shop=shop, product=product, **values)


def shown_ids(response):
    return {row.pk for row in response.context["page"]}


def test_owner_sees_bouquets_from_both_assigned_shops(client, owner, listing, other_shop):
    Membership.objects.create(user=owner, shop=other_shop)
    second = add_listing(other_shop)
    client.force_login(owner)
    response = client.get(URL)
    assert response.status_code == 200
    assert shown_ids(response) == {listing.pk, second.pk}
    html = response.content.decode()
    for shop in (listing.shop, other_shop):
        assert shop.name in html and shop.address in html
    assert f'href="/partner/{listing.shop.slug}/products/{listing.pk}/"' in html
    assert 'action="/basket/' not in html
    assert 'href="/products/' not in html
    assert 'href="/shops/' not in html


def test_membership_does_not_spread_through_other_staff(client, owner, listing, other_shop):
    Membership.objects.create(user=other_shop.memberships.first().user, shop=listing.shop)
    foreign = add_listing(other_shop)
    client.force_login(owner)
    response = client.get(URL)
    assert response.status_code == 200
    assert shown_ids(response) == {listing.pk}
    assert foreign.product.title not in response.content.decode()
    assert other_shop.slug not in response.content.decode()


def test_filter_selects_one_owned_shop(client, owner, listing, other_shop):
    Membership.objects.create(user=owner, shop=other_shop)
    second = add_listing(other_shop)
    client.force_login(owner)
    response = client.get(URL, {"shop": other_shop.slug})
    assert response.status_code == 200
    assert shown_ids(response) == {second.pk}
    assert response.context["selected_shop"] == other_shop


@pytest.mark.parametrize("slug", ["khimki-shop", "unknown-shop"])
def test_foreign_or_unknown_filter_is_rejected(client, owner, listing, other_shop, slug):
    add_listing(other_shop)
    client.force_login(owner)
    assert client.get(URL, {"shop": slug}).status_code == 404


def test_storefront_requires_login_and_shop_access(client, buyer):
    response = client.get(URL)
    assert response.status_code == 302 and "next=/partner/shops/storefront/" in response.url
    client.force_login(buyer)
    assert client.get(URL).status_code == 403


@pytest.mark.parametrize("excluded", ["hidden", "sold-out", "reserved", "no-price"])
def test_storefront_omits_unsellable_bouquets(client, owner, listing, excluded):
    options = {
        "hidden": {"public": False}, "sold-out": {"stock": 0},
        "reserved": {"stock": 2, "allocated": 2}, "no-price": {"price": None},
    }
    add_listing(listing.shop, title="Не для продажи", **options[excluded])
    client.force_login(owner)
    response = client.get(URL)
    assert response.status_code == 200
    assert shown_ids(response) == {listing.pk}


@pytest.mark.parametrize("status", ["draft", "review", "suspended"])
def test_storefront_omits_inactive_shops(client, owner, listing, other_shop, status):
    Membership.objects.create(user=owner, shop=other_shop)
    add_listing(other_shop)
    other_shop.status = status
    other_shop.save()
    client.force_login(owner)
    response = client.get(URL)
    assert response.status_code == 200
    assert shown_ids(response) == {listing.pk}


def test_storefront_ignores_customer_city_and_time(client, owner, listing, other_shop):
    Membership.objects.create(user=owner, shop=other_shop)
    second = add_listing(other_shop)
    session = client.session
    session["receiving"] = {"city": listing.shop.settlement_id, "method": "delivery", "when": "today"}
    session.save()
    client.force_login(owner)
    response = client.get(URL)
    assert response.status_code == 200
    assert shown_ids(response) == {listing.pk, second.pk}


def test_revoked_shop_disappears_on_next_request(client, owner, listing, other_shop):
    membership = Membership.objects.create(user=owner, shop=other_shop)
    second = add_listing(other_shop)
    client.force_login(owner)
    first = client.get(URL)
    assert first.status_code == 200
    assert shown_ids(first) == {listing.pk, second.pk}
    membership.delete()
    response = client.get(URL)
    assert shown_ids(response) == {listing.pk}
    assert client.get(URL, {"shop": other_shop.slug}).status_code == 404


def test_size_prices_use_existing_sale_prices_and_markup(client, owner, listing):
    shop = listing.shop
    shop.own_delivery = True
    shop.markup_percent = 10
    shop.save()
    listing.delivery_price = Decimal(3000)
    listing.size_prices = {"standard": {}, "l": {"pickup": "4000", "delivery": "4500"}}
    listing.sale_prices = {"standard": {"pickup": "2700", "delivery": "3300"}}
    listing.save()
    client.force_login(owner)
    response = client.get(URL)
    assert response.status_code == 200
    html = response.content.decode().replace("\u00a0", " ")
    for price in ("2 700 ₽", "3 300 ₽", "4 400 ₽", "4 950 ₽"):
        assert price in html
    assert "Самовывоз" in html and "Доставка" in html


def test_storefront_paginates_and_keeps_shop_filter(client, owner, shop, other_shop):
    Membership.objects.create(user=owner, shop=other_shop)
    for i in range(25):
        add_listing(shop, title=f"Букет {i}")
    foreign = add_listing(other_shop)
    client.force_login(owner)
    response = client.get(URL, {"shop": shop.slug})
    assert response.status_code == 200
    assert len(response.context["page"]) == 24
    assert response.context["page"].paginator.count == 25
    assert "shop=test-shop&amp;page=2" in response.content.decode()
    last = client.get(URL, {"shop": shop.slug, "page": 2})
    assert len(last.context["page"]) == 1
    assert foreign.pk not in shown_ids(last)


def test_empty_and_unpublished_shop_have_clear_empty_state(client, owner, shop):
    client.force_login(owner)
    response = client.get(URL)
    assert response.status_code == 200
    assert not shown_ids(response)
    assert "В продаже пока нет букетов" in response.content.decode()
    assert client.post(URL).status_code == 405


def test_navigation_reaches_common_storefront(client, owner, shop, other_shop):
    Membership.objects.create(user=owner, shop=other_shop)
    client.force_login(owner)
    for url in ("/partner/", f"/partner/{shop.slug}/"):
        response = client.get(url)
        assert response.status_code == 200
        assert 'href="/partner/shops/storefront/"' in response.content.decode()


def test_operator_retains_full_shop_access(client, listing, other_shop):
    user = get_user_model().objects.create_superuser("preview-admin", "admin@example.com", "secret")
    second = add_listing(other_shop)
    client.force_login(user)
    response = client.get(URL)
    assert response.status_code == 200
    assert shown_ids(response) == {listing.pk, second.pk}


def test_shop_named_storefront_keeps_its_dashboard(client, owner, shop):
    shop.slug = "storefront"
    shop.save(update_fields=["slug"])
    client.force_login(owner)
    response = client.get("/partner/storefront/")
    assert response.status_code == 200
    assert response.resolver_match.url_name == "partner-shop"
    assert response.context["shop"] == shop
