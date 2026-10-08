"""Catalogue filters on the home page: several flowers at once, counts, chips, links."""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.http import QueryDict
from oscar.core.loading import get_model

from market.catalogue import ListingFilter, flower_values, public_listings
from market.context import FulfillmentContext
from market.filters import (
    filter_chips,
    flower_label,
    price_label,
    price_range_links,
    toggle_flower_url,
)
from market.models import Flower, Listing
from market.templatetags.market_tags import ru_plural

pytestmark = pytest.mark.django_db
NOW = datetime(2026, 10, 6, 10, tzinfo=ZoneInfo("Europe/Moscow"))


def make_listing(shop, title, flowers=(), category="bouquet", price=2500):
    product = get_model("catalogue", "Product").objects.create(
        title=title,
        product_class=get_model("catalogue", "ProductClass").objects.first(),
        is_public=True,
    )
    get_model("partner", "StockRecord").objects.create(
        product=product,
        partner=shop.partner,
        partner_sku=f"sku-{title}",
        price=Decimal(price),
        price_currency="RUB",
        num_in_stock=5,
    )
    listing = Listing.objects.create(shop=shop, product=product, category=category)
    listing.flowers.set(Flower.objects.filter(name__in=flowers))
    return listing


@pytest.fixture
def bouquets(shop, listing):
    """«Нежность» (розы) plus a rose-and-peony and a peony-only bouquet."""
    listing.flowers.set(Flower.objects.filter(name="Роза"))
    both = make_listing(shop, "Пара", ("Роза", "Пион"), price=4000)
    peony = make_listing(shop, "Пионы", ("Пион",), category="composition", price=6000)
    return listing, both, peony


def pk(name):
    return Flower.objects.get(name=name).pk


def titles(items):
    return {item.product.title for item in items}


def test_every_chosen_flower_must_be_in_the_bouquet(bouquets):
    rose, peony = pk("Роза"), pk("Пион")
    ctx = FulfillmentContext()

    assert titles(public_listings(ctx, {"flower": [rose]}, NOW)) == {"Нежность", "Пара"}
    assert titles(public_listings(ctx, {"flower": [rose, peony]}, NOW)) == {"Пара"}
    assert titles(public_listings(ctx, {"flower": [peony]}, NOW)) == {"Пара", "Пионы"}


def test_repeated_query_flowers_are_all_applied(bouquets):
    query = QueryDict(f"flower={pk('Роза')}&flower={pk('Пион')}&flower={pk('Роза')}")

    assert flower_values(query) == [str(pk("Роза")), str(pk("Пион"))]
    assert titles(public_listings(FulfillmentContext(), query, NOW)) == {"Пара"}


def test_flower_filter_still_accepts_one_value_and_old_text_names(bouquets):
    ctx = FulfillmentContext()

    assert titles(public_listings(ctx, {"flower": "Розы"}, NOW)) == {"Нежность", "Пара"}
    assert titles(public_listings(ctx, {"flower": str(pk("Пион"))}, NOW)) == {
        "Пара",
        "Пионы",
    }
    assert not public_listings(ctx, {"flower": ["Орхидея"]}, NOW)


def test_filters_combine_category_flowers_and_price(bouquets):
    ctx = FulfillmentContext()
    peony = pk("Пион")

    assert titles(public_listings(ctx, {"category": "composition"}, NOW)) == {"Пионы"}
    assert titles(
        public_listings(ctx, {"flower": [peony], "min_price": "5000"}, NOW)
    ) == {"Пионы"}
    assert not public_listings(ctx, {"flower": [peony], "category": "basket"}, NOW)


def test_listing_filter_can_leave_one_facet_out_to_count_its_choices(bouquets):
    ctx = FulfillmentContext()
    from market.catalogue import catalogue_candidates

    candidates = catalogue_candidates(ctx, {}, NOW)
    spec = ListingFilter.parse({"flower": [pk("Роза")], "category": "composition"})

    assert not [c for c in candidates if spec.matches(c)]
    assert titles(c for c in candidates if spec.matches(c, skip={"category"})) == {
        "Нежность",
        "Пара",
    }
    assert (
        titles(c for c in candidates if spec.matches(c, extra_flower=str(pk("Пион"))))
        == set()
    )


def test_home_counts_what_each_choice_would_show(client, bouquets):
    response = client.get("/", {"flower": [pk("Роза")]})

    options = {o["name"]: o for o in response.context["flower_options"]}
    assert response.context["total_count"] == 2
    assert options["Роза"]["selected"] and options["Роза"]["count"] == 2
    assert options["Пион"]["count"] == 1 and not options["Пион"]["zero"]
    assert options["Орхидея"]["zero"]
    categories = {o["value"]: o["count"] for o in response.context["category_options"]}
    assert categories == {"bouquet": 2, "composition": 0, "basket": 0, "box": 0}
    assert response.context["category_total"] == 2


def test_home_with_two_rail_flowers_avoids_duplicate_chips(client, bouquets):
    response = client.get("/", {"flower": [pk("Роза"), pk("Пион")]})
    html = response.content.decode()

    assert response.context["total_count"] == 1
    assert response.context["filter_chips"] == []
    assert response.context["flower_pill"] == "Роза, Пион"
    assert "Показать 1 букет<" in html
    assert 'class="reset"' in html
    for name in ("Роза", "Пион"):
        assert f'aria-label="Убрать фильтр: {name}"' not in html


def test_category_has_one_control_and_is_preserved_by_the_filter_form(client, bouquets):
    response = client.get("/", {"category": "composition"})
    html = response.content.decode()
    form = html.split('id="filter-form"', 1)[1].split("</form>", 1)[0]
    pills = form.split('data-live-region="pills"', 1)[1]

    assert 'data-pop="category"' not in html
    assert response.context["filter_chips"] == []
    assert '<input type="hidden" name="category" value="composition">' in pills
    submitted = {"category": "composition", "min_price": "5000"}
    filtered = client.get("/", submitted)
    assert titles(filtered.context["page"]) == {"Пионы"}
    selected = next(o for o in filtered.context["category_options"] if o["selected"])
    assert selected["url"] == "/?min_price=5000#catalogue"
    cleared = client.get(selected["url"])
    assert 'name="category"' not in cleared.content.decode()


def test_flower_outside_the_rail_keeps_its_removable_chip(client, bouquets):
    response = client.get("/", {"flower": [pk("Роза"), pk("Василёк")]})

    assert [c["label"] for c in response.context["filter_chips"]] == ["Василёк"]
    assert response.context["filter_chips"][0]["url"] == (
        f"/?flower={pk('Роза')}#catalogue"
    )


def test_home_explains_an_empty_result_for_several_flowers(client, bouquets):
    response = client.get("/", {"flower": [pk("Пион"), pk("Орхидея")]})

    assert response.context["total_count"] == 0
    assert (
        "Ни в одном букете нет сразу всех выбранных цветов" in response.content.decode()
    )


def test_flower_checkboxes_are_a_plain_form_field(client, bouquets):
    html = client.get(
        "/", {"flower": [pk("Пион")], "sort": "price-desc"}
    ).content.decode()

    assert f'<input type="checkbox" name="flower" value="{pk("Пион")}" checked>' in html
    assert 'name="sort" value="price-desc"' in html
    assert (
        '<form class="filter-bar" id="filter-form" method="get" action="/#catalogue"'
        in html
    )


def test_filter_bar_has_no_shop_or_sort_controls(client, bouquets):
    html = client.get("/").content.decode()

    assert 'data-pop="category"' not in html and 'data-pop="flower"' in html
    assert 'data-pop="price"' in html
    assert 'data-pop="shop"' not in html and 'data-pop="sort"' not in html
    assert "Сначала новые" not in html


def test_filters_work_without_javascript(client, bouquets):
    html = client.get("/").content.decode()

    assert "no-live-only" not in html
    assert (
        html.count('type="submit"') >= 3
    )  # one per menu, so every menu applies by itself
    assert "<details" in html and 'name="filter-pop"' in html


def test_flower_rail_toggles_the_flower_in_the_filter(client, bouquets):
    rose, peony = pk("Роза"), pk("Пион")

    plain = {c["pk"]: c for c in client.get("/").context["flower_rail"]}
    chosen = {
        c["pk"]: c for c in client.get("/", {"flower": [rose]}).context["flower_rail"]
    }

    assert plain[rose]["url"] == f"/?flower={rose}#catalogue"
    assert chosen[rose]["active"] and chosen[rose]["url"] == "/#catalogue"
    assert chosen[peony]["url"] == f"/?flower={rose}&flower={peony}#catalogue"


def test_toggle_flower_url_keeps_other_filters_and_drops_the_page():
    params = QueryDict("q=%D1%80%D0%BE%D0%B7%D1%8B&flower=1&page=3&category=box")

    added = toggle_flower_url(params, 2)
    removed = toggle_flower_url(params, 1)

    assert (
        added == "/?q=%D1%80%D0%BE%D0%B7%D1%8B&category=box&flower=1&flower=2#catalogue"
    )
    assert removed == "/?q=%D1%80%D0%BE%D0%B7%D1%8B&category=box#catalogue"


def test_chips_drop_exactly_one_filter_each(bouquets, shop):
    params = QueryDict(
        f"q=Нежность&category=bouquet&flower={pk('Роза')}&flower={pk('Пион')}"
        "&min_price=2000&max_price=5000"
    )
    from market.models import Listing as ListingModel

    chips = filter_chips(
        params,
        ListingModel._meta.get_field("category").choices,
        list(Flower.objects.all()),
        [shop],
    )

    assert [c["label"] for c in chips] == [
        "«Нежность»",
        "Монобукеты",
        "Роза",
        "Пион",
        "2 000 ₽ – 5 000 ₽",
    ]
    rose_chip = next(c for c in chips if c["label"] == "Роза")
    assert f"flower={pk('Пион')}" in rose_chip["url"]
    assert f"flower={pk('Роза')}" not in rose_chip["url"]
    assert "min_price" in rose_chip["url"] and "q=" in rose_chip["url"]
    price_chip = chips[-1]
    assert "min_price" not in price_chip["url"] and "max_price" not in price_chip["url"]


def test_button_labels():
    names = {"1": "Роза", "2": "Пион", "3": "Ромашка"}

    assert flower_label(QueryDict(""), names) == "Цветы"
    assert flower_label(QueryDict("flower=1"), names) == "Роза"
    assert flower_label(QueryDict("flower=1&flower=2"), names) == "Роза, Пион"
    assert flower_label(QueryDict("flower=1&flower=2&flower=3"), names) == "Роза +2"
    assert price_label(QueryDict("")) == ""
    assert price_label(QueryDict("max_price=4500")) == "до 4 500 ₽"
    assert price_label(QueryDict("min_price=2000")) == "от 2 000 ₽"
    assert price_label(QueryDict("min_price=oops&max_price=-5")) == ""


def test_quick_price_ranges_keep_other_filters_and_mark_the_active_one():
    links = price_range_links(QueryDict("category=box&min_price=3000&max_price=5000"))

    assert [link["active"] for link in links] == [False, True, False]
    assert links[0]["url"] == "/?category=box&max_price=3000#catalogue"
    assert links[2]["url"] == "/?category=box&min_price=5000#catalogue"


@pytest.mark.parametrize(
    ("number", "word"),
    [
        (0, "букетов"),
        (1, "букет"),
        (2, "букета"),
        (5, "букетов"),
        (11, "букетов"),
        (12, "букетов"),
        (21, "букет"),
        (24, "букета"),
        (101, "букет"),
        (111, "букетов"),
    ],
)
def test_russian_plural(number, word):
    assert ru_plural(number, "букет,букета,букетов") == word
