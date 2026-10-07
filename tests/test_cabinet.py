import json
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from oscar.core.loading import get_model

from market.availability import location_matches
from market.catalogue import public_listings
from market.context import FulfillmentContext
from market.forms import ProductForm, WeeklyHoursForm, save_listing
from market.models import Listing, Shop, WeeklyHours
from market.orders import place_market_order, transition_shop_order
from market.zones import circle_zone, point_in_zone, validate_zone
from tests.test_orders import ADDRESS, CONTACT, NOW, basket_for, choice

pytestmark = pytest.mark.django_db


def product_data(**values):
    return {
        "title": "Новый букет",
        "description": "",
        "category": "bouquet",
        "pickup_price": Decimal(2000),
        "stock": 4,
        "always_in_stock": False,
        "is_public": True,
        **values,
    }


def make_listing(shop, **values):
    return save_listing(shop, product_data(**values))


def info_post(shop, **extra):
    return {
        "name": shop.name,
        "description": "",
        "settlement": shop.settlement_id,
        "address": shop.address,
        "phone": "",
        "pickup_enabled": "on",
        "delivery_enabled": "on",
        "prep_minutes": "60",
        "pickup_instructions": "",
        **extra,
    }


# --- pages and access -----------------------------------------------------------------


def test_cabinet_has_four_sections(client, owner, shop, listing):
    client.force_login(owner)
    dashboard = client.get(f"/partner/{shop.slug}/").content.decode()
    for label in [
        "Дашборд",
        "Букеты",
        "Информация о магазине",
        "Реквизиты и зона доставки",
    ]:
        assert label in dashboard
    for path in ["products", "info", "payout"]:
        assert f"/partner/{shop.slug}/{path}/" in dashboard
        assert client.get(f"/partner/{shop.slug}/{path}/").status_code == 200
    # The bouquets moved out of the dashboard.
    assert "Ваши товары" not in dashboard


def test_sections_are_scoped_to_the_sellers_own_shop(
    client, owner, stranger, shop, other_shop
):
    for path in ["products", "info", "payout", "products/new"]:
        assert client.get(f"/partner/{shop.slug}/{path}/").status_code == 302
    client.force_login(owner)
    for path in ["products", "info", "payout", "products/new"]:
        assert client.get(f"/partner/{other_shop.slug}/{path}/").status_code == 404
    assert (
        client.post(f"/partner/{other_shop.slug}/payout/", {"inn": "1"}).status_code
        == 404
    )


def test_partner_settings_url_leads_to_the_info_page(client, owner, shop):
    client.force_login(owner)
    response = client.get(f"/partner/{shop.slug}/settings/")
    assert response.status_code == 302
    assert response.url == f"/partner/{shop.slug}/info/"


# --- bouquets -------------------------------------------------------------------------


def test_bouquet_list_shows_sold_out_unlimited_and_hidden(client, owner, shop):
    fresh = make_listing(shop, title="В продаже", stock=3)
    sold = make_listing(shop, title="Распродан", stock=0)
    always = make_listing(shop, title="Всегда", always_in_stock=True, stock=None)
    hidden = make_listing(shop, title="Скрыт", is_public=False)
    client.force_login(owner)
    page = client.get(f"/partner/{shop.slug}/products/")
    assert {row.pk for row in page.context["listings"]} == {
        fresh.pk,
        sold.pk,
        always.pk,
        hidden.pk,
    }
    html = page.content.decode()
    assert "Всегда в наличии" in html and "Распродан" in html
    summary = page.context["bouquets"]
    assert (summary["total"], summary["on_sale"], summary["sold_out"]) == (4, 2, 1)
    assert summary["hidden"] == 1

    def shown(show):
        response = client.get(f"/partner/{shop.slug}/products/?show={show}")
        return {row.product.title for row in response.context["listings"]}

    assert shown("sold-out") == {"Распродан"}
    assert shown("hidden") == {"Скрыт"}
    assert shown("on-sale") == {"В продаже", "Всегда"}
    assert len(shown("junk")) == 4


def test_sold_out_bouquet_leaves_the_catalogue_until_restocked(shop):
    sold = make_listing(shop, stock=0)
    assert not public_listings(FulfillmentContext(), {}, NOW)
    save_listing(shop, product_data(stock=2), sold)
    assert public_listings(FulfillmentContext(), {}, NOW) == [sold]


def test_delivery_price_column_only_for_own_delivery_shops(client, owner, shop):
    make_listing(shop)
    client.force_login(owner)
    page = client.get(f"/partner/{shop.slug}/products/").content.decode()
    assert "Цена с доставкой" not in page
    shop.own_delivery = True
    shop.save()
    page = client.get(f"/partner/{shop.slug}/products/").content.decode()
    assert "Цена с доставкой" in page and "Укажите цену" in page


# --- the product form ------------------------------------------------------------------


def form_data(**values):
    return {
        "title": "Букет",
        "category": "bouquet",
        "pickup_price": "2000",
        "is_public": "on",
        **values,
    }


def test_product_form_needs_a_quantity_or_always_in_stock(shop):
    assert not ProductForm(form_data(), shop=shop).is_valid()
    assert "stock" in ProductForm(form_data(), shop=shop).errors
    assert ProductForm(form_data(stock="0"), shop=shop).is_valid()
    assert ProductForm(form_data(always_in_stock="on"), shop=shop).is_valid()
    assert not ProductForm(form_data(stock="-1"), shop=shop).is_valid()


def test_delivery_price_is_asked_only_when_the_shop_delivers_itself(shop):
    assert "delivery_price" not in ProductForm(shop=shop).fields
    shop.own_delivery = True
    form = ProductForm(form_data(stock="1"), shop=shop)
    assert "delivery_price" in form.fields and not form.is_valid()
    assert ProductForm(
        form_data(stock="1", delivery_price="2400"), shop=shop
    ).is_valid()


def test_edit_form_is_prefilled_with_both_prices_and_stock_mode(client, owner, shop):
    shop.own_delivery = True
    shop.save()
    listing = make_listing(shop, always_in_stock=True, stock=None, delivery_price=2500)
    client.force_login(owner)
    form = client.get(f"/partner/{shop.slug}/products/{listing.pk}/").context["form"]
    assert form.initial["pickup_price"] == Decimal(2000)
    assert form.initial["delivery_price"] == Decimal(2500)
    assert form.initial["always_in_stock"] is True


def test_saving_both_prices_through_the_cabinet(client, owner, shop):
    shop.own_delivery = True
    shop.save()
    client.force_login(owner)
    response = client.post(
        f"/partner/{shop.slug}/products/new/",
        form_data(stock="3", delivery_price="2400"),
    )
    assert response.status_code == 302
    listing = Listing.objects.get(product__title="Букет")
    assert (listing.pickup_price, listing.delivery_price) == (
        Decimal(2000),
        Decimal(2400),
    )
    assert listing.price_for("delivery") == Decimal(2400)
    assert listing.price_for("pickup") == Decimal(2000)


# --- «Всегда в наличии» -----------------------------------------------------------------


def test_always_in_stock_uses_oscars_untracked_class(shop):
    listing = make_listing(shop, always_in_stock=True, stock=None)
    assert listing.always_in_stock
    assert listing.product.get_product_class().track_stock is False
    assert listing.stockrecord.num_in_stock is None
    assert listing.stock_left is None and not listing.sold_out
    assert listing.can_supply(10_000)
    assert public_listings(FulfillmentContext(), {}, NOW) == [listing]


def test_always_in_stock_order_never_reserves_or_consumes_stock(owner, shop):
    listing = make_listing(shop, always_in_stock=True, stock=None)
    order = place_market_order(
        owner, basket_for(owner, listing), {shop.pk: choice(shop)}, None, CONTACT, NOW
    )
    stock = listing.stockrecord
    stock.refresh_from_db()
    assert not stock.num_allocated and stock.num_in_stock is None
    part = order.shop_orders.get()
    part.payment_status = "paid"
    part.save(update_fields=["payment_status"])
    for status in ["preparing", "ready", "completed"]:
        part = transition_shop_order(part, owner, status)
    stock.refresh_from_db()
    assert not stock.num_allocated and stock.num_in_stock is None
    # And it can be ordered again: nothing ran out.
    place_market_order(
        owner, basket_for(owner, listing), {shop.pk: choice(shop)}, None, CONTACT, NOW
    )


def test_cancelling_an_always_in_stock_order_is_harmless(owner, shop):
    from market.orders import cancel_shop_order

    listing = make_listing(shop, always_in_stock=True, stock=None)
    order = place_market_order(
        owner, basket_for(owner, listing), {shop.pk: choice(shop)}, None, CONTACT, NOW
    )
    cancel_shop_order(order.shop_orders.get(), owner)
    stock = listing.stockrecord
    stock.refresh_from_db()
    assert not stock.num_allocated


def test_stock_mode_can_change_only_without_open_orders(owner, shop):
    listing = make_listing(shop, stock=5)
    order = place_market_order(
        owner, basket_for(owner, listing), {shop.pk: choice(shop)}, None, CONTACT, NOW
    )
    with pytest.raises(ValidationError):
        save_listing(shop, product_data(always_in_stock=True, stock=None), listing)
    part = order.shop_orders.get()
    from market.orders import cancel_shop_order

    cancel_shop_order(part, owner)
    listing = Listing.objects.get(pk=listing.pk)
    save_listing(shop, product_data(always_in_stock=True, stock=None), listing)
    listing = Listing.objects.get(pk=listing.pk)
    assert listing.always_in_stock and listing.stockrecord.num_in_stock is None
    # And back to counted stock.
    save_listing(shop, product_data(stock=7), listing)
    listing = Listing.objects.get(pk=listing.pk)
    assert not listing.always_in_stock
    assert listing.stockrecord.num_in_stock == 7
    assert listing.stock_left == 7


def test_counted_stock_cannot_drop_below_reserved(owner, shop):
    listing = make_listing(shop, stock=5)
    place_market_order(
        owner, basket_for(owner, listing), {shop.pk: choice(shop)}, None, CONTACT, NOW
    )
    with pytest.raises(ValidationError):
        save_listing(shop, product_data(stock=0), listing)
    save_listing(shop, product_data(stock=1), listing)


def test_basket_accepts_any_quantity_of_an_always_in_stock_bouquet(client, owner, shop):
    always = make_listing(shop, title="Всегда", always_in_stock=True, stock=None)
    counted = make_listing(shop, title="Три штуки", stock=3)
    client.force_login(owner)
    client.post(f"/basket/add/{always.pk}/", {"quantity": "50"})
    client.post(f"/basket/add/{counted.pk}/", {"quantity": "50"})
    basket = get_model("basket", "Basket").objects.get(owner=owner)
    quantities = {line.product.title: line.quantity for line in basket.all_lines()}
    assert quantities == {"Всегда": 50}
    client.post(f"/basket/add/{counted.pk}/", {"quantity": "3"})
    line = basket.all_lines().get(product=counted.product)
    assert (
        client.post(f"/basket/update/{line.pk}/", {"quantity": "4"}).status_code == 302
    )
    line.refresh_from_db()
    assert line.quantity == 3


# --- information, schedule ------------------------------------------------------------


def test_own_delivery_needs_delivery_switched_on(client, owner, shop):
    client.force_login(owner)
    response = client.post(
        f"/partner/{shop.slug}/info/",
        info_post(shop, own_delivery="on", delivery_enabled=""),
    )
    assert response.status_code == 200
    shop.refresh_from_db()
    assert not shop.own_delivery
    response = client.post(
        f"/partner/{shop.slug}/info/", info_post(shop, own_delivery="on")
    )
    assert response.status_code == 302
    shop.refresh_from_db()
    assert shop.own_delivery


def test_info_page_does_not_touch_moderation_or_requisites(client, owner, shop):
    shop.inn = "7707083893"
    shop.save()
    client.force_login(owner)
    Shop.objects.filter(pk=shop.pk).update(status="suspended", commission_percent=17)
    assert (
        client.post(
            f"/partner/{shop.slug}/info/", info_post(shop, phone="+7 495 000-00-00")
        ).status_code
        == 302
    )
    shop.refresh_from_db()
    assert shop.phone == "+7 495 000-00-00"
    assert (shop.status, shop.commission_percent, shop.inn) == (
        "suspended",
        Decimal(17),
        "7707083893",
    )


def test_weekly_grid_is_prefilled_from_the_saved_schedule(shop):
    shop.hours.all().delete()
    WeeklyHours.objects.create(
        shop=shop, weekday=2, method="delivery", start_minute=540, end_minute=1440
    )
    form = WeeklyHoursForm(shop=shop)
    assert form["delivery_2_start"].value() == "09:00"
    assert form["delivery_2_end"].value() == "24:00"
    assert form["work_2_start"].value() == ""
    assert form.rows[0]["label"] == "Понедельник"
    assert len(form.rows) == 7 and len(form.rows[0]["cells"]) == 3


def test_weekly_grid_saves_replaces_and_clears_days(shop):
    form = WeeklyHoursForm(
        {
            "work_0_start": "09:00",
            "work_0_end": "21:00",
            "delivery_0_start": "00:00",
            "delivery_0_end": "24:00",
            "pickup_6_start": "22:00",
            "pickup_6_end": "02:00",
        },
        shop=shop,
    )
    assert form.is_valid(), form.errors
    form.save()
    rows = {(row.weekday, row.method): row for row in shop.hours.all()}
    assert set(rows) == {(0, "work"), (0, "delivery"), (6, "pickup")}
    assert (rows[0, "work"].start_minute, rows[0, "work"].end_minute) == (540, 1260)
    assert rows[0, "delivery"].end_minute == 1440
    assert rows[6, "pickup"].end_minute == 120
    # Saving the same grid again changes nothing; clearing a pair closes the day.
    again = WeeklyHoursForm({"work_0_start": "09:00", "work_0_end": "21:00"}, shop=shop)
    assert again.is_valid()
    again.save()
    assert shop.hours.count() == 1


@pytest.mark.parametrize(
    "values",
    [
        {"work_0_start": "09:00"},
        {"work_0_end": "21:00"},
        {"work_0_start": "9-00", "work_0_end": "21:00"},
        {"work_0_start": "25:00", "work_0_end": "21:00"},
        {"work_0_start": "24:00", "work_0_end": "24:00"},
        {"work_0_start": "10:00", "work_0_end": "10:00"},
        {"work_0_start": "10:60", "work_0_end": "12:00"},
    ],
)
def test_weekly_grid_rejects_bad_or_half_filled_times(shop, values):
    form = WeeklyHoursForm(values, shop=shop)
    assert not form.is_valid()
    assert shop.hours.count() == 21


def test_invalid_grid_changes_nothing_on_the_info_page(client, owner, shop):
    client.force_login(owner)
    response = client.post(
        f"/partner/{shop.slug}/info/",
        info_post(shop, name="Другое имя", work_0_start="09:00"),
    )
    assert response.status_code == 200
    shop.refresh_from_db()
    assert shop.name != "Другое имя"
    assert shop.hours.count() == 21


def test_exception_form_rejects_a_bad_interval(client, owner, shop):
    client.force_login(owner)
    response = client.post(
        f"/partner/{shop.slug}/info/",
        {
            "form_kind": "exception",
            "day": "2026-12-31",
            "method": "work",
            "start": "bad",
            "end": "20:00",
        },
    )
    assert response.status_code == 200
    assert not shop.date_exceptions.exists()


def test_removing_an_exception_returns_to_the_info_page(client, owner, shop):
    client.force_login(owner)
    client.post(
        f"/partner/{shop.slug}/info/",
        {
            "form_kind": "exception",
            "day": "2026-12-31",
            "method": "work",
            "closed": "on",
            "start": "09:00",
            "end": "20:00",
        },
    )
    row = shop.date_exceptions.get()
    response = client.post(f"/partner/{shop.slug}/exceptions/{row.pk}/remove/")
    assert response.status_code == 302
    assert response.url == f"/partner/{shop.slug}/info/"
    assert not shop.date_exceptions.exists()


# --- requisites and delivery zone -----------------------------------------------------

BOX = [[55.70, 37.55], [55.70, 37.70], [55.80, 37.70], [55.80, 37.55]]


def payout_post(shop, **extra):
    return {
        "legal_name": "ООО «Цветы»",
        "inn": "7707083893",
        "bank_account": "40702810900000000001",
        "bank_bik": "044525225",
        "delivery_zone": json.dumps(BOX),
        "delivery_settlements": [shop.settlement_id],
        "minimum_order": "1500",
        **extra,
    }


def test_requisites_and_zone_are_saved_together(client, owner, shop):
    client.force_login(owner)
    response = client.post(f"/partner/{shop.slug}/payout/", payout_post(shop))
    assert response.status_code == 302
    shop.refresh_from_db()
    assert shop.legal_name == "ООО «Цветы»" and shop.bank_bik == "044525225"
    assert shop.delivery_zone == BOX
    assert shop.minimum_order == 1500


@pytest.mark.parametrize(
    "field, value",
    [("inn", "123"), ("bank_account", "123"), ("bank_bik", "12345678a")],
)
def test_requisites_check_digits(client, owner, shop, field, value):
    client.force_login(owner)
    response = client.post(
        f"/partner/{shop.slug}/payout/", payout_post(shop, **{field: value})
    )
    assert response.status_code == 200
    shop.refresh_from_db()
    assert shop.legal_name == ""


@pytest.mark.parametrize(
    "zone",
    [
        [[55.7, 37.6], [55.8, 37.7]],
        [[55.70, 37.55], [55.80, 37.70], [55.80, 37.55], [55.70, 37.70]],
        [[10.0, 10.0], [10.0, 11.0], [11.0, 11.0]],
        [[55.7, 37.6], [55.7, 37.7], [55.7, 37.8]],
        "not a zone",
        [["a", "b"], [1, 2], [3, 4]],
    ],
)
def test_invalid_zones_are_rejected(client, owner, shop, zone):
    old = list(shop.delivery_zone)
    client.force_login(owner)
    response = client.post(
        f"/partner/{shop.slug}/payout/",
        payout_post(shop, delivery_zone=json.dumps(zone)),
    )
    assert response.status_code == 200
    shop.refresh_from_db()
    assert shop.delivery_zone == old


def test_zone_can_be_cleared(client, owner, shop):
    client.force_login(owner)
    response = client.post(
        f"/partner/{shop.slug}/payout/", payout_post(shop, delivery_zone="")
    )
    assert response.status_code == 302
    shop.refresh_from_db()
    assert shop.delivery_zone == []


def test_zone_geometry():
    assert point_in_zone(55.75, 37.6, BOX)
    assert not point_in_zone(55.9, 37.6, BOX)
    assert not point_in_zone(55.75, 37.8, BOX)
    assert point_in_zone(55.70, 37.6, BOX)  # on the border
    assert not point_in_zone(55.75, 37.6, [])
    concave = [[55.0, 37.0], [55.0, 38.0], [55.5, 37.5], [56.0, 38.0], [56.0, 37.0]]
    assert point_in_zone(55.9, 37.2, concave)
    assert point_in_zone(55.2, 37.5, concave)
    assert not point_in_zone(55.5, 37.9, concave)  # inside the notch
    validate_zone(concave)
    with pytest.raises(ValidationError):
        validate_zone(BOX[:2])


def test_circle_zone_is_a_valid_polygon_around_the_point():
    zone = circle_zone(55.75, 37.61, 15)
    validate_zone(zone)
    assert point_in_zone(55.75, 37.61, zone)
    assert point_in_zone(55.75 + 14 / 111.32, 37.61, zone)
    assert not point_in_zone(55.75 + 17 / 111.32, 37.61, zone)


def test_delivery_follows_the_drawn_zone(shop):
    shop.delivery_zone = BOX
    shop.save()
    inside = FulfillmentContext(
        address={**ADDRESS, "latitude": "55.75", "longitude": "37.60"}
    )
    outside = FulfillmentContext(
        address={**ADDRESS, "latitude": "55.90", "longitude": "37.60"}
    )
    assert location_matches(shop, "delivery", inside)
    assert not location_matches(shop, "delivery", outside)
    # Pickup depends on the city, not on the zone.
    assert location_matches(shop, "pickup", outside)


def test_no_zone_means_no_delivery_to_an_exact_address(shop):
    shop.delivery_zone = []
    shop.save()
    assert not location_matches(shop, "delivery", FulfillmentContext(address=ADDRESS))
    # A buyer who only picked the city still sees the shop in its delivery cities.
    assert location_matches(
        shop, "delivery", FulfillmentContext(city_id=shop.settlement_id)
    )


def test_checkout_refuses_an_address_outside_the_zone(owner, shop, listing):
    shop.delivery_zone = BOX
    shop.save()
    with pytest.raises(ValidationError):
        place_market_order(
            owner,
            basket_for(owner, listing),
            {shop.pk: choice(shop, "delivery")},
            {**ADDRESS, "latitude": "55.95", "longitude": "37.60"},
            CONTACT,
            NOW,
        )
    place_market_order(
        owner,
        basket_for(owner, listing),
        {shop.pk: choice(shop, "delivery")},
        {**ADDRESS, "latitude": "55.75", "longitude": "37.60"},
        CONTACT,
        NOW,
    )


# --- dashboard ------------------------------------------------------------------------


def test_dashboard_counts_bouquets_and_warns_about_a_missing_zone(client, owner, shop):
    make_listing(shop, stock=3)
    make_listing(shop, stock=0)
    shop.delivery_zone = []
    shop.save()
    client.force_login(owner)
    page = client.get(f"/partner/{shop.slug}/")
    summary = page.context["bouquets"]
    assert (summary["total"], summary["sold_out"], summary["on_sale"]) == (2, 1, 1)
    assert "Зона доставки не нарисована" in page.content.decode()


# --- what buyers see ------------------------------------------------------------------


def test_buyer_pages_show_the_right_price_and_delivery_terms(client, owner, shop):
    shop.own_delivery = True
    shop.save()
    listing = make_listing(shop, delivery_price=2400)
    product = client.get(f"/products/{listing.pk}/").content.decode()
    assert "Самовывоз — 2" in product and "с доставкой магазина — 2" in product
    assert "Доставка магазина · включена в цену" in product
    assert "Доставку Ромео добавим" not in product
    assert (
        "Доставка магазина · включена в цену"
        in client.get(f"/shops/{shop.slug}/").content.decode()
    )
    client.force_login(owner)
    client.post(f"/basket/add/{listing.pk}/", {"quantity": "1"})
    checkout = client.get("/checkout/").content.decode()
    assert "Доставка магазина · включена в цену" in checkout
    assert "Доставка Ромео" not in checkout
    shop.own_delivery = False
    shop.save()
    checkout = client.get("/checkout/").content.decode()
    assert "Доставка Ромео · 350" in checkout


def test_demo_seed_shows_both_delivery_models_and_an_unlimited_bouquet(settings):
    from django.core.management import call_command

    settings.MARKET_DEMO = True
    call_command("seed_marketplace")
    own = Shop.objects.get(slug="garden-studio")
    assert own.own_delivery and validate_zone(own.delivery_zone) is None
    assert all(row.delivery_price for row in own.listings.all())
    assert not Shop.objects.get(slug="petal-studio").own_delivery
    assert sum(1 for row in Listing.objects.all() if row.always_in_stock) == 1
    assert all(row.pickup_price for row in Listing.objects.all())


def test_administrator_edits_commission_and_delivery_fee(client, shop):
    from django.contrib.auth import get_user_model

    admin = get_user_model().objects.create_superuser(
        "boss", "b@example.com", "pw-12345"
    )
    client.force_login(admin)
    page = client.get(f"/operator/{shop.slug}/")
    assert page.status_code == 200
    assert {"commission_percent", "delivery_fee", "status"} <= page.context[
        "form"
    ].fields.keys()
    data = {
        "name": shop.name,
        "description": "",
        "settlement": shop.settlement_id,
        "address": shop.address,
        "phone": "",
        "delivery_enabled": "on",
        "pickup_enabled": "on",
        "delivery_settlements": [shop.settlement_id],
        "minimum_order": "0",
        "prep_minutes": "60",
        "status": "active",
        "commission_percent": "12.50",
        "delivery_fee": "400",
    }
    assert client.post(f"/operator/{shop.slug}/", data).status_code == 302
    shop.refresh_from_db()
    assert (shop.commission_percent, shop.delivery_fee) == (
        Decimal("12.50"),
        Decimal(400),
    )
    # The administrator's form does not wipe the zone the seller drew.
    assert shop.delivery_zone
