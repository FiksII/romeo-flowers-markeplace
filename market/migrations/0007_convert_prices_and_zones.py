from decimal import ROUND_HALF_UP, Decimal
from math import cos, radians, sin, tau

from django.db import migrations

CENT = Decimal("0.01")
LAT_RANGE, LON_RANGE = (54.0, 57.2), (34.8, 40.6)


def _circle(lat, lon, radius_km, points=32):
    lat, lon, radius_km = float(lat), float(lon), float(radius_km)
    d_lat = radius_km / 111.32
    d_lon = radius_km / (111.32 * cos(radians(lat)))
    zone = []
    for step in range(points):
        zone.append(
            [
                round(
                    min(
                        max(lat + d_lat * cos(tau * step / points), LAT_RANGE[0]),
                        LAT_RANGE[1],
                    ),
                    6,
                ),
                round(
                    min(
                        max(lon + d_lon * sin(tau * step / points), LON_RANGE[0]),
                        LON_RANGE[1],
                    ),
                    6,
                ),
            ]
        )
    return zone


def convert(apps, schema_editor):
    """Keep what buyers paid and what shops received.

    Old model: buyers paid base + markup (pickup: minus a discount from the markup) and
    the shop received the base. New model: the shop sets the price buyers pay and the
    platform keeps a commission from it. The pickup price becomes the new price (the
    one shown before for pickup) and the commission is the share of it that used to be
    markup, so the shop still receives the old base amount."""
    Shop = apps.get_model("market", "Shop")
    Listing = apps.get_model("market", "Listing")
    Stock = apps.get_model("partner", "StockRecord")
    db = schema_editor.connection.alias
    for shop in Shop.objects.using(db).all():
        markup, discount = shop.commission_percent, shop.pickup_discount_percent
        effective = markup * (Decimal(100) - discount) / 100
        factor = Decimal(1) + effective / 100
        for listing in Listing.objects.using(db).filter(shop=shop):
            for stock in Stock.objects.using(db).filter(
                product_id=listing.product_id, partner_id=shop.partner_id
            ):
                if stock.price is not None:
                    stock.price = (stock.price * factor).quantize(
                        CENT, rounding=ROUND_HALF_UP
                    )
                    stock.save(update_fields=["price"])
        shop.commission_percent = (effective / (100 + effective) * 100).quantize(
            CENT, rounding=ROUND_HALF_UP
        )
        shop.delivery_zone = _circle(shop.latitude, shop.longitude, shop.radius_km)
        shop.save(update_fields=["commission_percent", "delivery_zone"])


class Migration(migrations.Migration):
    dependencies = [
        ("market", "0006_own_delivery_zone_prices"),
        ("partner", "0007_partneraddress_code"),
    ]
    operations = [migrations.RunPython(convert, migrations.RunPython.noop)]
