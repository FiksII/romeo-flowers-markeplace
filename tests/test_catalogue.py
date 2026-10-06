from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from market.catalogue import public_listings
from market.context import FulfillmentContext

pytestmark = pytest.mark.django_db
NOW = datetime(2026, 10, 6, 10, tzinfo=ZoneInfo("Europe/Moscow"))


def test_listing_without_address_and_with_city(listing, oblast):
    assert public_listings(FulfillmentContext(), {}, NOW) == [listing]
    assert not public_listings(
        FulfillmentContext(city_id=oblast.pk, method="delivery"), {}, NOW
    )


def test_price_and_flower_filters(listing):
    assert not public_listings(FulfillmentContext(), {"max_price": "1000"}, NOW)
    assert public_listings(
        FulfillmentContext(), {"min_price": "2000", "flower": "Розы"}, NOW
    )
    assert not public_listings(FulfillmentContext(), {"flower": "Пионы"}, NOW)
    assert public_listings(
        FulfillmentContext(), {"min_price": "NaN", "max_price": "oops"}, NOW
    )


def test_no_stock_or_private_shop_hidden(listing, shop):
    stock = listing.stockrecord
    stock.num_in_stock = 0
    stock.save()
    assert not public_listings(FulfillmentContext(), {}, NOW)
    stock.num_in_stock = 5
    stock.save()
    shop.status = "review"
    shop.save()
    assert not public_listings(FulfillmentContext(), {}, NOW)
