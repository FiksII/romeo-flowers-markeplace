from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from market.zones import point_in_zone, validate_zone

BEFORE = [("market", "0005_flower_colour_variants")]
AFTER = [("market", "0008_remove_radius_and_pickup_discount")]


def migrate(targets):
    executor = MigrationExecutor(connection)
    executor.migrate(targets)
    return MigrationExecutor(connection).loader.project_state(targets).apps


@pytest.mark.django_db(transaction=True)
def test_conversion_keeps_buyer_prices_and_what_the_shop_receives():
    old = migrate(BEFORE)
    try:
        Settlement, Shop, Listing = (
            old.get_model("market", name) for name in ("Settlement", "Shop", "Listing")
        )
        Partner = old.get_model("partner", "Partner")
        ProductClass = old.get_model("catalogue", "ProductClass")
        Product = old.get_model("catalogue", "Product")
        Stock = old.get_model("partner", "StockRecord")
        city = Settlement.objects.create(name="Москва", slug="moscow", region="77")
        cls = ProductClass.objects.create(name="Цветы", slug="flowers")
        rows = {}
        for slug, markup, discount in [("with-markup", 10, 50), ("no-markup", 0, 50)]:
            partner = Partner.objects.create(name=slug, code=slug)
            shop = Shop.objects.create(
                partner=partner,
                name=slug,
                slug=slug,
                settlement=city,
                address="Тверская, 1",
                latitude=Decimal("55.7567"),
                longitude=Decimal("37.6137"),
                radius_km=Decimal(10),
                markup_percent=Decimal(markup),
                pickup_discount_percent=Decimal(discount),
            )
            product = Product.objects.create(title=slug, product_class=cls)
            Stock.objects.create(
                product=product,
                partner=partner,
                partner_sku=slug,
                price=Decimal(1000),
                price_currency="RUB",
                num_in_stock=1,
            )
            Listing.objects.create(product=product, shop=shop)
            rows[slug] = product.pk

        new = migrate(AFTER)
        Shop, Stock = (
            new.get_model("market", "Shop"),
            new.get_model("partner", "StockRecord"),
        )
        marked = Shop.objects.get(slug="with-markup")
        # Pickup used to cost 1000 + 10% less half of it = 1050; that is the price now,
        # and the commission is the 50 that used to be markup, so the shop still gets 1000.
        price = Stock.objects.get(product_id=rows["with-markup"]).price
        assert price == Decimal("1050.00")
        assert marked.commission_percent == Decimal("4.76")
        payout = price - (price * marked.commission_percent / 100).quantize(
            Decimal("0.01")
        )
        assert abs(payout - 1000) <= Decimal("0.05")
        assert not marked.own_delivery
        assert (
            not new.get_model("market", "Listing")
            .objects.exclude(delivery_price=None)
            .exists()
        )
        plain = Shop.objects.get(slug="no-markup")
        assert plain.commission_percent == 0
        assert Stock.objects.get(product_id=rows["no-markup"]).price == Decimal(1000)
        # The old 10 km radius became a polygon around the shop.
        validate_zone(marked.delivery_zone)
        assert len(marked.delivery_zone) == 32
        assert point_in_zone(55.7567, 37.6137, marked.delivery_zone)
        assert point_in_zone(55.7567 + 9 / 111.32, 37.6137, marked.delivery_zone)
        assert not point_in_zone(55.7567 + 12 / 111.32, 37.6137, marked.delivery_zone)
        assert not hasattr(marked, "radius_km")
        assert not hasattr(marked, "pickup_discount_percent")
    finally:
        migrate(
            [
                (app, name)
                for app, name in MigrationExecutor(connection).loader.graph.leaf_nodes()
                if app == "market"
            ]
        )
