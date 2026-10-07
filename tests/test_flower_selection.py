import pytest
from django.core.exceptions import ValidationError

from market.forms import ProductForm, save_listing
from market.models import Listing

pytestmark = pytest.mark.django_db


def test_partner_login_opens_shop_and_customer_keeps_orders(
    client, owner, shop, stranger
):
    response = client.post(
        "/login/", {"username": owner.username, "password": "long-secret-pass"}
    )
    assert response.url == f"/partner/{shop.slug}/"
    client.logout()
    response = client.post(
        "/login/", {"username": stranger.username, "password": "long-secret-pass"}
    )
    assert response.url == "/account/orders/"


def test_explicit_login_destination_is_preserved(client, owner, shop):
    response = client.post(
        "/login/",
        {
            "username": owner.username,
            "password": "long-secret-pass",
            "next": "/checkout/",
        },
    )
    assert response.url == "/checkout/"


def test_fixed_flowers_reject_unknown_values(shop):
    form = ProductForm(shop=shop)
    field = form.fields["flowers"]
    assert field.queryset.count() == 53
    with pytest.raises(ValidationError):
        field.clean(["999999"])


def test_save_multiple_flowers_filter_and_partner_popularity(
    client, owner, shop, listing, other_shop
):
    from market.catalogue import public_listings
    from market.context import FulfillmentContext
    from market.models import Flower
    from tests.test_catalogue import NOW

    rose = Flower.objects.get(name="Роза")
    peony = Flower.objects.get(name="Пион")
    client.force_login(owner)
    response = client.post(
        f"/partner/{shop.slug}/products/{listing.pk}/",
        {
            "title": "Смешанный букет",
            "description": "",
            "category": "composition",
            "flowers": [rose.pk, peony.pk],
            "pickup_price": "2500",
            "stock": "5",
            "is_public": "on",
        },
    )
    assert response.status_code == 302
    assert set(listing.flowers.all()) == {rose, peony}
    assert public_listings(FulfillmentContext(), {"flower": "Розы"}, NOW) == [listing]
    assert public_listings(FulfillmentContext(), {"flower": str(peony.pk)}, NOW) == [
        listing
    ]
    listing.flowers.set([peony])
    for index in range(3):
        save_listing(
            other_shop,
            {
                "title": f"Розы другого магазина {index}",
                "description": "",
                "category": "bouquet",
                "flowers": [rose],
                "pickup_price": "2500",
                "stock": 5,
                "is_public": True,
            },
        )
    form = ProductForm(shop=shop)
    assert next(iter(form.fields["flowers"].queryset)) == peony
    assert next(iter(ProductForm(shop=other_shop).fields["flowers"].queryset)) == rose
    assert peony.name in client.get(f"/products/{listing.pk}/").content.decode()
    client.post(
        f"/partner/{shop.slug}/products/{listing.pk}/",
        {
            "title": "Без цветов",
            "description": "",
            "category": "box",
            "pickup_price": "2500",
            "stock": "5",
            "is_public": "on",
        },
    )
    assert not listing.flowers.exists()


def test_four_categories_and_no_partner_promotion(client):
    assert [label for _, label in Listing._meta.get_field("category").choices] == [
        "Монобукеты",
        "Композиции",
        "Корзины",
        "В коробке",
    ]
    html = client.get("/").content.decode()
    assert "Стать партнёром" not in html
    assert ">Партнёрам<" not in html


def test_flower_picker_renders_search_colours_and_saved_selection(
    client, owner, shop, listing
):
    from market.models import Flower

    red = Flower.objects.get(name="Роза красная")
    white = Flower.objects.get(name="Роза белая")
    cornflower = Flower.objects.get(name="Василёк")
    assert len({red.tag_palette, white.tag_palette, cornflower.tag_palette}) == 3
    listing.flowers.set([red, white])
    client.force_login(owner)
    html = client.get(f"/partner/{shop.slug}/products/{listing.pk}/").content.decode()
    assert "data-flower-search" in html
    assert "data-flower-selected" in html
    assert "flower-palette-red" in html
    assert "flower-palette-ivory" in html
    assert html.count('name="flowers"') == 53
    assert html.count("checked") == 3  # two flowers and public-product checkbox
    assert (
        "flower-palette-red" in client.get(f"/products/{listing.pk}/").content.decode()
    )
