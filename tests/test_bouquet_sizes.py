from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from oscar.core.loading import get_model

from market.forms import ProductForm, save_listing
from market.orders import place_market_order
from tests.test_orders import CONTACT, NOW, choice

pytestmark = pytest.mark.django_db


def data(**changes):
    return {
        "title": "Букет",
        "description": "Розы",
        "category": "composition",
        "pickup_price": "2500",
        "stock": "5",
        "is_public": "on",
        "size_config": "1",
        "sizes": ["standard", "s", "m", "l"],
        "pickup_price_s": "1500",
        "pickup_price_m": "3000",
        "pickup_price_l": "4500",
        "tags": ["birthday", "woman"],
        **changes,
    }


@pytest.mark.parametrize("category", ["bouquet", "composition"])
def test_seller_saves_tags_and_all_sizes(shop, category):
    form = ProductForm(data(category=category), shop=shop)
    assert form.is_valid(), form.errors
    listing = save_listing(shop, form.cleaned_data)
    assert listing.tags == ["birthday", "woman"]
    assert listing.price_for("pickup", "standard") == Decimal(2500)
    assert listing.price_for("pickup", "s") == Decimal(1500)
    assert listing.price_for("pickup", "m") == Decimal(3000)
    assert listing.price_for("pickup", "l") == Decimal(4500)
    assert listing.price_for("pickup") == Decimal(1500)


@pytest.mark.parametrize(
    "changes,field",
    [
        ({"tags": ["unknown"]}, "tags"),
        ({"sizes": []}, "sizes"),
        ({"sizes": ["xl"]}, "sizes"),
        ({"pickup_price_m": ""}, "pickup_price_m"),
        ({"pickup_price_s": "0"}, "pickup_price_s"),
    ],
)
def test_invalid_options_rejected(shop, changes, field):
    form = ProductForm(data(**changes), shop=shop)
    assert not form.is_valid()
    assert field in form.errors


def configure(listing, **changes):
    form = ProductForm(data(**changes), shop=listing.shop)
    assert form.is_valid(), form.errors
    return save_listing(listing.shop, form.cleaned_data, listing)


def test_one_size_adds_without_selector(client, listing):
    configure(listing, sizes=["m"], pickup_price="", pickup_price_m="3200")
    page = client.get(f"/products/{listing.pk}/")
    assert page.status_code == 200
    assert b"data-size-picker" not in page.content
    client.post(f"/basket/add/{listing.pk}/")
    basket = client.get("/basket/").wsgi_request.basket
    assert basket.total_incl_tax == Decimal(3200)
    assert basket.all_lines().get().attributes.get().value == "M"


@pytest.mark.parametrize('same_price', [False, True])
def test_customer_price_prefix_depends_on_distinct_prices(client, listing, same_price):
    configure(listing, sizes=['s', 'm'], pickup_price_s='1500',
              pickup_price_m='1500' if same_price else '3000')
    page = client.get(f'/products/{listing.pk}/')
    price = page.content.decode().split('class="detail-price money">')[1].split('</strong>')[0]
    assert price.startswith('от ') is not same_price
    assert '1\xa0500' in price
    html = client.get('/').content.decode()
    card_price = html.split('class="product-price"><b>')[1].split('</b>')[0]
    assert card_price.startswith('от ') is not same_price
    # A price from an arbitrary first size must not replace the minimum on load.
    assert ' checked' not in page.content.decode().split('data-size-picker>')[1].split('</fieldset>')[0]


def test_sizes_have_separate_lines_and_locked_order_prices(
    client, buyer, shop, listing
):
    configure(listing)
    client.force_login(buyer)
    for size in ["s", "l"]:
        client.post(f"/basket/add/{listing.pk}/", {"size": size})
    response = client.get("/basket/")
    basket = response.wsgi_request.basket
    assert basket.num_lines == 2
    assert basket.total_incl_tax == Decimal(6000)
    order = place_market_order(
        buyer, basket, {shop.pk: choice(shop)}, None, CONTACT, NOW
    )
    assert order.total_incl_tax == Decimal(6000)
    assert order.shop_orders.get().goods_total == Decimal(6000)
    assert sorted(order.lines.values_list("unit_price_incl_tax", flat=True)) == [
        1500,
        4500,
    ]
    assert {a.value for line in order.lines.all() for a in line.attributes.all()} == {
        "S",
        "L",
    }


@pytest.mark.parametrize("size", [None, "xl", "standard"])
def test_missing_or_disabled_size_cannot_be_added(client, listing, size):
    configure(listing, sizes=["s", "m"])
    response = client.post(
        f"/basket/add/{listing.pk}/", {} if size is None else {"size": size}
    )
    assert response.status_code == 302
    assert client.get("/basket/").wsgi_request.basket.num_lines == 0


def test_removed_size_rejected_at_checkout(client, buyer, shop, listing):
    configure(listing)
    client.force_login(buyer)
    client.post(f"/basket/add/{listing.pk}/", {"size": "s"})
    basket = client.get("/basket/").wsgi_request.basket
    configure(listing, sizes=["standard"])
    for path in ["/basket/", "/checkout/"]:
        response = client.get(path)
        assert response.status_code == 200
        assert response.context["groups"][0]["available"] is False
    with pytest.raises(ValidationError):
        place_market_order(buyer, basket, {shop.pk: choice(shop)}, None, CONTACT, NOW)


def test_total_stock_shared_by_sizes_at_update_and_checkout(
    client, buyer, shop, listing
):
    configure(listing)
    client.force_login(buyer)
    for size in ["s", "m"]:
        client.post(f"/basket/add/{listing.pk}/", {"size": size, "quantity": "2"})
    basket = client.get("/basket/").wsgi_request.basket
    assert basket.num_lines == 2
    line = basket.all_lines().first()
    client.post(f"/basket/update/{line.pk}/", {"quantity": "4"})
    line.refresh_from_db()
    assert line.quantity == 2
    get_model("basket", "Line").objects.filter(pk=line.pk).update(quantity=4)
    with pytest.raises(ValidationError):
        place_market_order(buyer, basket, {shop.pk: choice(shop)}, None, CONTACT, NOW)


def test_size_specific_own_delivery_price(client, listing, shop):
    shop.own_delivery = True
    shop.save()
    configure(
        listing,
        delivery_price="2800",
        delivery_price_s="1800",
        delivery_price_m="3500",
        delivery_price_l="5000",
    )
    client.post(f"/basket/add/{listing.pk}/", {"size": "l"})
    assert client.get("/basket/").wsgi_request.basket.total_incl_tax == Decimal(5000)
