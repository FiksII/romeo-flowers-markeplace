from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from oscar.core.loading import get_model

from market.models import Listing, Membership, Settlement, Shop, WeeklyHours


@pytest.fixture
def owner(db):
    return get_user_model().objects.create_user(
        "seller", "seller@example.com", "long-secret-pass"
    )


@pytest.fixture
def stranger(db):
    return get_user_model().objects.create_user(
        "other", "other@example.com", "long-secret-pass"
    )


@pytest.fixture
def city(db):
    return Settlement.objects.create(name="Москва", slug="moscow", region="77")


@pytest.fixture
def oblast(db):
    return Settlement.objects.create(name="Химки", slug="khimki", region="50")


@pytest.fixture
def shop(city, owner):
    partner = get_model("partner", "Partner").objects.create(
        name="Тестовые цветы", code="test-shop"
    )
    shop = Shop.objects.create(
        partner=partner,
        name="Тестовые цветы",
        slug="test-shop",
        settlement=city,
        address="ул. Тестовая, д. 1",
        latitude=Decimal("55.75"),
        longitude=Decimal("37.61"),
        status="active",
        prep_minutes=60,
        delivery_fee=Decimal(350),
        radius_km=Decimal(15),
    )
    Membership.objects.create(shop=shop, user=owner)
    shop.delivery_settlements.add(city)
    for day in range(7):
        for method in ["work", "delivery", "pickup"]:
            WeeklyHours.objects.create(
                shop=shop, weekday=day, method=method, start_minute=0, end_minute=1440
            )
    return shop


@pytest.fixture
def other_shop(oblast, stranger):
    partner = get_model("partner", "Partner").objects.create(
        name="Цветы Химки", code="khimki-shop"
    )
    shop = Shop.objects.create(
        partner=partner,
        name="Цветы Химки",
        slug="khimki-shop",
        settlement=oblast,
        address="ул. Другая, д. 2",
        latitude=Decimal("55.89"),
        longitude=Decimal("37.44"),
        status="active",
        prep_minutes=30,
    )
    Membership.objects.create(shop=shop, user=stranger)
    shop.delivery_settlements.add(oblast)
    for day in range(7):
        for method in ["work", "delivery", "pickup"]:
            WeeklyHours.objects.create(
                shop=shop, weekday=day, method=method, start_minute=0, end_minute=1440
            )
    return shop


@pytest.fixture
def listing(shop):
    cls = get_model("catalogue", "ProductClass").objects.create(
        name="Букеты", track_stock=True, requires_shipping=True
    )
    product = get_model("catalogue", "Product").objects.create(
        title="Нежность", product_class=cls, description="Розы", is_public=True
    )
    get_model("partner", "StockRecord").objects.create(
        product=product,
        partner=shop.partner,
        partner_sku="test-1",
        price=Decimal(2500),
        price_currency="RUB",
        num_in_stock=5,
    )
    return Listing.objects.create(shop=shop, product=product, flower_kind="Розы")
