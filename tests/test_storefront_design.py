"""Storefront shell shared with the shop: shared theme, header, rails, product cards."""

import pytest
from django.contrib.staticfiles import finders

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "asset_path",
    (
        "storefront/css/theme.css",
        "storefront/css/romeo.css",
        "storefront/css/fonts.css",
        "storefront/fonts/onest-cyrillic.woff2",
        "storefront/fonts/cormorant-garamond-cyrillic.woff2",
        "storefront/images/design/logo.webp",
        "storefront/images/design/hero.webp",
        "storefront/images/design/category-monobukety.webp",
        "storefront/images/design/category-kompozitsii.webp",
        "storefront/images/design/category-korziny.webp",
        "storefront/images/design/category-v-korobke.webp",
        "storefront/images/design/rose.webp",
    ),
)
def test_shared_design_assets_are_discoverable(asset_path):
    assert finders.find(asset_path)


def test_shared_theme_loads_after_the_base_theme(client):
    html = client.get("/").content.decode()
    assert html.index("storefront/css/theme.css") < html.index(
        "storefront/css/romeo.css"
    )
    assert html.index("storefront/css/romeo.css") < html.index("market/market.css")


def test_header_has_place_search_basket_and_account(client):
    html = client.get("/").content.decode()
    assert 'class="service-bar"' in html
    assert 'class="city"' in html
    assert '<form class="site-search" action="/catalogue/"' in html
    assert 'aria-label="Корзина"' in html
    assert 'aria-label="Войти"' in html
    assert 'class="mobile-bottom-nav"' in html


def test_home_shows_the_four_category_circles_and_the_hero(client, listing):
    html = client.get("/").content.decode()
    for value, label in (
        ("bouquet", "Монобукеты"),
        ("composition", "Композиции"),
        ("basket", "Корзины"),
        ("box", "В коробке"),
    ):
        assert f'href="/catalogue/?category={value}"' in html
        assert f'<span class="category-name">{label}</span>' in html
    assert 'class="hero-slide is-active"' in html
    assert html.count('class="price-tile"') == 3


def test_category_circle_is_active_in_the_catalogue(client, listing):
    html = client.get("/catalogue/?category=basket").content.decode()
    assert (
        'class="category-circle is-active" href="/catalogue/?category=basket"' in html
    )


def test_product_card_uses_the_shared_card_markup(client, listing):
    html = client.get("/").content.decode()
    assert '<article class="product-card">' in html
    assert 'class="basket-add-form"' in html
    assert "market-product" not in html


def test_catalogue_filters_are_collapsible_and_open_by_default(client, listing):
    html = client.get("/catalogue/").content.decode()
    assert '<details class="filter-details" open data-filter-details>' in html
    assert 'id="filter-q" type="text"' in html


def test_receiving_panel_has_an_anchor_for_the_header_place_chip(client):
    html = client.get("/catalogue/").content.decode()
    assert 'href="/catalogue/#receiving"' in html
    assert '<details class="receiving-panel" id="receiving"' in html
