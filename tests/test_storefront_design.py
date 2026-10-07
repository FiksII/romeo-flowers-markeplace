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
    assert '<form class="site-search" action="/#catalogue"' in html
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
        assert f'href="/?category={value}#catalogue"' in html
        assert f'<span class="category-name">{label}</span>' in html
    assert 'class="hero-slide is-active"' in html
    assert html.count('class="price-tile"') == 3


def test_category_circle_is_active_and_clears_the_filter_on_the_home_page(
    client, listing
):
    response = client.get("/", {"category": "basket"})
    html = response.content.decode()
    assert 'class="category-circle is-active" href="/#catalogue"' in html
    assert response.context["total_count"] == 0


def test_product_card_uses_the_shared_card_markup(client, listing):
    html = client.get("/").content.decode()
    assert '<article class="product-card">' in html
    assert 'class="basket-add-form"' in html
    assert "market-product" not in html


def test_home_lists_the_catalogue_with_the_filter_bar(client, listing):
    response = client.get("/")
    html = response.content.decode()
    assert response.context["total_count"] == 1
    assert 'id="catalogue"' in html and "data-catalogue-live" in html
    assert listing.product.title in html
    assert "data-filter-form" in html
    assert 'id="filter-q" type="search" name="q"' in html
    assert "filter-panel" not in html and "catalogue-layout" not in html


def test_home_filters_narrow_the_listing(client, listing):
    assert client.get("/", {"max_price": "1000"}).context["total_count"] == 0
    assert client.get("/", {"min_price": "2000"}).context["total_count"] == 1
    assert 'value="розы"' in client.get("/", {"q": "розы"}).content.decode()


def test_home_pagination_keeps_the_filters_and_the_listing_anchor(
    client, listing, monkeypatch
):
    from django.core.paginator import Paginator

    from market import storefront_views

    monkeypatch.setattr(
        storefront_views,
        "Paginator",
        lambda items, per_page: Paginator(items * 2, 1),
    )

    html = client.get("/", {"min_price": "2000"}).content.decode()

    assert "min_price=2000&page=2#catalogue" in html


def test_old_catalogue_url_redirects_to_the_home_listing(client):
    response = client.get("/catalogue/")
    assert response.status_code == 301
    assert response.url == "/#catalogue"

    response = client.get("/catalogue/?q=%D1%80%D0%BE%D0%B7%D1%8B&page=2")
    assert response.status_code == 301
    assert response.url == "/?q=%D1%80%D0%BE%D0%B7%D1%8B&page=2#catalogue"


def test_pages_no_longer_link_to_the_removed_catalogue_page(client, listing):
    for path in ("/", f"/products/{listing.pk}/", f"/shops/{listing.shop.slug}/"):
        html = client.get(path).content.decode()
        assert 'href="/catalogue/' not in html
        assert 'action="/catalogue/' not in html


def test_receiving_panel_has_an_anchor_for_the_header_place_chip(client):
    html = client.get("/").content.decode()
    assert 'href="/#receiving"' in html
    assert '<details class="receiving-panel" id="receiving"' in html
