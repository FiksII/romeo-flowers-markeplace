from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.http import Http404
from oscar.core.loading import get_model

from market.access import get_shop_for_user, shops_for_user
from market.models import Listing, Membership, Settlement

pytestmark = pytest.mark.django_db


def test_outside_region_rejected():
    with pytest.raises(ValidationError):
        Settlement(name="Казань", slug="kazan", region="16").full_clean()


def test_member_access_scoped(shop, other_shop, owner, stranger):
    assert list(shops_for_user(owner)) == [shop]
    with pytest.raises(Http404):
        get_shop_for_user(owner, other_shop.slug)
    assert list(shops_for_user(stranger)) == [other_shop]
    outsider = get_user_model().objects.create_user("outsider")
    assert not shops_for_user(outsider).exists()


def test_superuser_can_manage_all(shop, other_shop):
    admin = get_user_model().objects.create_superuser(
        "admin", "a@example.com", "password"
    )
    assert shops_for_user(admin).count() == 2


def test_listing_rejects_another_partners_stock(shop, other_shop):
    product_class = get_model("catalogue", "ProductClass").objects.create(
        name="Букеты", track_stock=True
    )
    product = get_model("catalogue", "Product").objects.create(
        title="Чужой букет", product_class=product_class
    )
    get_model("partner", "StockRecord").objects.create(
        product=product,
        partner=other_shop.partner,
        partner_sku="x",
        price=Decimal(2000),
        num_in_stock=5,
    )
    with pytest.raises(ValidationError):
        Listing(shop=shop, product=product).full_clean()


def test_membership_does_not_grant_global_staff(owner, shop):
    assert Membership.objects.filter(user=owner, shop=shop).exists()
    owner.refresh_from_db()
    assert not owner.is_staff
