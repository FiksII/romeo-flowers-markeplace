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
        "market/images/logo-emblem.webp",
        "storefront/images/design/hero.webp",
        "market/images/occasion-birthday.webp",
        "market/images/occasion-love.webp",
        "market/images/occasion-any-day.webp",
        "storefront/images/design/category-monobukety.webp",
        "storefront/images/design/category-kompozitsii.webp",
        "storefront/images/design/category-korziny.webp",
        "storefront/images/design/category-v-korobke.webp",
        "storefront/images/design/rose.webp",
    ),
)
def test_shared_design_assets_are_discoverable(asset_path):
    assert finders.find(asset_path)


def test_header_and_footer_show_the_round_emblem(client):
    html = client.get("/").content.decode()
    emblem = '<img class="brand-logo" src="/static/market/images/logo-emblem.webp"'
    assert html.count(emblem) == 2
    assert f'{emblem} width="64" height="64" alt="Цветы от Ромео">' in html
    assert f'{emblem} width="120" height="120" alt="Цветы от Ромео">' in html
    assert "storefront/images/design/logo.webp" not in html


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


def test_home_shows_the_category_chips_and_the_hero(client, listing):
    html = client.get("/").content.decode()
    chips = html.split('<nav class="category-chips"', 1)[1].split("</nav>", 1)[0]
    assert (
        '<a class="category-chip is-active" href="/#catalogue" data-live-link '
        'aria-current="true"><span class="category-name">Все</span><small>1</small></a>'
    ) in chips
    # The only bouquet is a monobouquet: the empty categories are shown, not linked.
    assert 'href="/?category=bouquet#catalogue"' in chips
    for value, label in (
        ("bouquet", "Монобукеты"),
        ("composition", "Композиции"),
        ("basket", "Корзины"),
        ("box", "В коробке"),
    ):
        assert f'<span class="category-name">{label}</span>' in chips
        if value != "bouquet":
            assert f'href="/?category={value}#catalogue"' not in chips
    assert chips.count('class="category-chip is-zero" aria-disabled="true"') == 3
    assert 'class="hero-slide is-active"' in html
    assert 'class="price-tile"' not in html


def test_place_and_time_bar_summarises_the_choice(client, city):
    html = client.get("/").content.decode()
    assert (
        "<strong>Куда доставить цветы?</strong>"
        "<span>Москва и область · любое время</span>"
    ) in html
    session = client.session
    session["receiving"] = {
        "city": city.pk,
        "method": "pickup",
        "when": "today",
        "hour": 14,
    }
    session.save()
    html = client.get("/").content.decode()
    assert (
        "<strong>Москва</strong><span>сегодня, 14:00–15:00 · самовывоз</span>" in html
    )
    assert "data-receiving-close" in html


def test_place_and_time_details_name_a_chosen_date():
    from datetime import date

    from market.context import FulfillmentContext
    from market.context_processors import receiving_details

    context = FulfillmentContext(when="date", day=date(2026, 10, 17), method="delivery")
    assert receiving_details(context) == "17 октября · доставка"
    assert receiving_details(FulfillmentContext(when="asap")) == "как можно скорее"


def test_home_carousel_starts_with_one_visible_slide_and_catalogue_links(client):
    import re

    html = client.get("/").content.decode()
    slides = re.findall(
        r"<article\b([^>]*data-carousel-slide[^>]*)>(.*?)</article>",
        html,
        re.DOTALL,
    )
    assert len(slides) == 4
    assert "hidden" not in slides[0][0]
    for attributes, body in slides[1:]:
        assert " hidden" in attributes and " inert" in attributes
        assert 'aria-hidden="true"' in attributes
        assert "#catalogue" in body
    assert [re.search(r"<h2>(.*?)</h2>", body)[1] for _, body in slides[1:]] == [
        "День рождения",
        "Для любимых",
        "Без повода",
    ]
    assert html.count("data-carousel-dot=") == 4
    assert 'aria-label="Слайд 1" aria-pressed="true"' in html
    assert html.count("<h1") == 1
    # The only h1 names the service; the slogan stays the large slide title.
    assert (
        '<h1 class="hero-kicker" id="home-heading">'
        "Доставка цветов по Москве и области</h1>"
    ) in slides[0][1]
    assert (
        '<h2>Для важных слов. <span class="nowrap">И просто так.</span></h2>'
        in slides[0][1]
    )


def test_card_keeps_the_receiving_time_on_one_line(client, listing):
    response = client.get("/")
    label = response.context["page"][0].nearest_slot.label
    html = response.content.decode()
    assert f'Получение <span class="slot-time">{label}</span>' in html


def test_category_chip_is_active_and_clears_the_filter_on_the_home_page(
    client, listing
):
    response = client.get("/", {"category": "basket"})
    html = response.content.decode()
    # A chosen category stays a link even when it is empty, so it can be cleared.
    assert 'class="category-chip is-active" href="/#catalogue"' in html
    assert '<a class="category-chip" href="/#catalogue"' in html
    assert response.context["total_count"] == 0


def test_category_tiles_keep_the_other_catalogue_filters(client, listing):
    from html import unescape
    from urllib.parse import parse_qs, urlsplit

    from market.models import Flower

    rose = str(Flower.objects.get(name="Роза").pk)
    peony = str(Flower.objects.get(name="Пион").pk)
    params = {
        "category": "bouquet",
        "flower": [rose, peony],
        "min_price": "2000",
        "q": "Нежность",
        "sort": "price-desc",
        "page": "3",
    }
    options = client.get("/", params).context["category_options"]
    for tile, category in zip(options, (None, "composition", "basket", "box")):
        query = parse_qs(urlsplit(unescape(tile["url"])).query)
        expected = {
            "flower": [rose, peony],
            "min_price": ["2000"],
            "q": ["Нежность"],
            "sort": ["price-desc"],
        }
        if category:
            expected["category"] = [category]
        assert query == expected


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


def test_benefits_sit_under_the_banner(client, listing):
    html = client.get("/").content.decode()
    order = [
        html.index(marker)
        for marker in (
            'class="shell hero-wrap"',
            'class="shell home-benefits"',
            'id="receiving"',
            'id="catalogue"',
        )
    ]
    assert order == sorted(order)
    assert "<strong>Цветы от Ромео</strong>" in html
    assert 'id="partners"' not in html


def test_partner_shops_show_shops_with_available_bouquets(client, listing):
    session = client.session
    session['receiving'] = {'method': 'pickup'}
    session.save()
    html = client.get("/").content.decode()
    block = html.split('id="partners"', 1)[1].split("</section>", 1)[0]
    assert '<a href="/shops/test-shop/">Тестовые цветы</a>' in block
    assert "Москва · доставка · самовывоз" in block
    assert "1 букет в магазине →" in block
    assert f'<a href="/products/{listing.pk}/"><img' in block


def test_partner_shops_ignore_the_catalogue_search(client, listing):
    session = client.session
    session['receiving'] = {'method': 'pickup'}
    session.save()
    response = client.get("/", {"q": "нет такого букета"})
    assert response.context["total_count"] == 0
    shops = [item["shop"].slug for item in response.context["partner_shops"]]
    assert shops == ["test-shop"]


def test_footer_has_documents_and_hides_contacts_that_are_not_set(client, settings):
    settings.SUPPORT_PHONE = ""
    settings.SUPPORT_EMAIL = ""
    footer = client.get("/").content.decode().split('<footer class="site-footer">')[1]
    for path, title in (
        ("/privacy/", "Политика конфиденциальности"),
        ("/offer/", "Публичная оферта"),
        ("/requisites/", "Реквизиты"),
    ):
        assert f'<a href="{path}">{title}</a>' in footer
    assert "Контакты" not in footer and "tel:" not in footer
    assert "social-links" not in footer


def test_footer_shows_the_contacts_from_the_settings(client, settings):
    settings.SUPPORT_PHONE = "+7 (999) 123-45-67"
    settings.SUPPORT_EMAIL = "hello@example.com"
    settings.TELEGRAM_URL = "https://t.me/example"
    footer = client.get("/").content.decode().split('<footer class="site-footer">')[1]
    assert '<a href="tel:+79991234567">' in footer and "+7 (999) 123-45-67" in footer
    assert '<a href="mailto:hello@example.com">' in footer
    assert '<a href="https://t.me/example" aria-label="Telegram"' in footer
    assert 'aria-label="Instagram"' not in footer


@pytest.mark.parametrize(
    "path, title",
    (("/privacy/", "Политика конфиденциальности"),),
)
def test_legal_pages_are_placeholders_kept_out_of_search(client, path, title):
    response = client.get(path)
    assert response.status_code == 200
    assert response["X-Robots-Tag"] == "noindex"
    html = response.content.decode()
    assert f"<h1>{title}</h1>" in html and "Документ готовится" in html
    assert client.head(path).status_code == 200
