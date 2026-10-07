from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from oscar.core.loading import get_model

from market.addresses import encode_address
from market.models import AuditEntry, Membership, Shop
from tests.test_orders import ADDRESS
from tests.test_partner_workflows import info_post, shop_data

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin():
    return get_user_model().objects.create_superuser(
        "boss", "boss@example.com", "password"
    )


def operator_data(shop, **changes):
    return {
        **shop_data(shop),
        "status": "active",
        "commission_percent": "12.50",
        "delivery_fee": "400",
        "members": list(shop.memberships.values_list("user_id", flat=True)),
        **changes,
    }


def test_admin_creates_shop_with_selected_owner(client, admin, owner, city):
    client.force_login(admin)
    response = client.post(
        "/operator/new/",
        {
            "name": "Новый магазин",
            "settlement": city.pk,
            "address": ADDRESS["value"],
            "address_token": encode_address(ADDRESS),
            "pickup_enabled": "on",
            "delivery_enabled": "on",
            "delivery_settlements": [city.pk],
            "minimum_order": "1500",
            "prep_minutes": "30",
            "status": "active",
            "commission_percent": "7.50",
            "delivery_fee": "250",
            "members": [owner.pk],
        },
    )
    assert response.status_code == 302
    created = Shop.objects.get(name="Новый магазин")
    assert (created.status, created.commission_percent, created.delivery_fee) == (
        "active",
        Decimal("7.50"),
        Decimal(250),
    )
    assert list(created.memberships.values_list("user_id", flat=True)) == [owner.pk]
    assert created.latitude == Decimal("55.75")
    assert created.delivery_settlements.get() == city
    assert created.partner.name == created.name
    assert AuditEntry.objects.filter(shop=created, actor=admin).exists()
    client.force_login(owner)
    assert client.get(f"/partner/{created.slug}/").status_code == 200
    assert client.get(f"/operator/{created.slug}/").status_code == 403


def test_invalid_creation_does_not_leave_shop_or_partner(client, admin, owner, city):
    client.force_login(admin)
    before = get_model("partner", "Partner").objects.count()
    response = client.post(
        "/operator/new/",
        {
            "name": "Без точного адреса",
            "settlement": city.pk,
            "address": "произвольный адрес",
            "pickup_enabled": "on",
            "minimum_order": "0",
            "prep_minutes": "30",
            "status": "active",
            "commission_percent": "101",
            "delivery_fee": "0",
            "members": [owner.pk],
        },
    )
    assert response.status_code == 200
    assert {"address", "commission_percent"} <= response.context["form"].errors.keys()
    assert not Shop.objects.filter(name="Без точного адреса").exists()
    assert get_model("partner", "Partner").objects.count() == before


@pytest.mark.parametrize("method", ["get", "post"])
def test_partner_cannot_create_shop_through_operator_route(client, owner, shop, method):
    client.force_login(owner)
    response = getattr(client, method)("/operator/new/", operator_data(shop))
    assert response.status_code == 403
    assert Shop.objects.count() == 1


def test_admin_reassigns_access_and_preserves_other_shop(
    client, admin, owner, stranger, shop, other_shop
):
    client.force_login(admin)
    page = client.get(f"/operator/{shop.slug}/")
    assert list(page.context["form"].initial["members"]) == [owner.pk]
    response = client.post(
        f"/operator/{shop.slug}/", operator_data(shop, members=[stranger.pk])
    )
    assert response.status_code == 302
    assert not Membership.objects.filter(shop=shop, user=owner).exists()
    assert Membership.objects.filter(shop=shop, user=stranger).exists()
    assert Membership.objects.filter(shop=other_shop, user=stranger).exists()
    assert AuditEntry.objects.filter(shop=shop, actor=admin).exists()
    client.force_login(owner)
    assert client.get(f"/partner/{shop.slug}/info/").status_code == 404
    client.force_login(stranger)
    assert client.get(f"/partner/{shop.slug}/info/").status_code == 200


def test_invalid_member_does_not_change_profile_or_access(client, admin, shop, owner):
    client.force_login(admin)
    response = client.post(
        f"/operator/{shop.slug}/",
        operator_data(shop, name="Не сохранять", members=[999999]),
    )
    assert response.status_code == 200
    assert "members" in response.context["form"].errors
    shop.refresh_from_db()
    assert shop.name == "Тестовые цветы"
    assert shop.memberships.get().user == owner


def test_partner_cannot_change_owners_or_platform_settings(
    client, owner, shop, stranger
):
    client.force_login(owner)
    data = info_post(shop, work_0_start="09:00", work_0_end="18:00")
    data.update(members=[stranger.pk], status="suspended", commission_percent="99")
    assert client.post(f"/partner/{shop.slug}/info/", data).status_code == 302
    shop.refresh_from_db()
    assert shop.status == "active"
    assert shop.commission_percent == 0
    assert shop.memberships.get().user == owner
    assert (
        client.post(f"/operator/{shop.slug}/", operator_data(shop)).status_code == 403
    )


def test_admin_can_edit_other_shops_schedule(client, admin, other_shop):
    client.force_login(admin)
    data = info_post(other_shop, work_0_start="10:00", work_0_end="20:00")
    data["phone"] = "+79991234567"
    assert client.post(f"/partner/{other_shop.slug}/info/", data).status_code == 302
    other_shop.refresh_from_db()
    assert other_shop.phone == "+79991234567"
    assert other_shop.hours.get(weekday=0, method="work").start_minute == 600


def test_suspend_keeps_shop_products_and_owner_access(
    client, admin, owner, shop, listing
):
    client.force_login(admin)
    assert (
        client.post(
            f"/operator/{shop.slug}/", operator_data(shop, status="suspended")
        ).status_code
        == 302
    )
    shop.refresh_from_db()
    assert shop.status == "suspended"
    assert shop.listings.get() == listing
    assert client.get(f"/shops/{shop.slug}/").status_code == 404
    client.force_login(owner)
    assert client.get(f"/partner/{shop.slug}/products/").status_code == 200


def test_operator_shop_search_and_status_intersection(client, admin, shop, other_shop):
    Shop.objects.filter(pk=other_shop.pk).update(status="suspended")
    client.force_login(admin)
    response = client.get("/operator/", {"q": "Химки", "status": "suspended"})
    assert [row.pk for row in response.context["shops"]] == [other_shop.pk]
    assert (
        list(
            client.get("/operator/", {"q": "Химки", "status": "active"}).context[
                "shops"
            ]
        )
        == []
    )
    assert [
        row.pk
        for row in client.get("/operator/", {"q": "seller@example.com"}).context[
            "shops"
        ]
    ] == [shop.pk]


def test_admin_navigation_exposes_shop_management(client, admin, owner, shop):
    client.force_login(admin)
    page = client.get("/operator/").content.decode()
    assert 'href="/operator/new/"' in page
    assert f'href="/partner/{shop.slug}/info/"' in page
    page = client.get(f"/partner/{shop.slug}/info/").content.decode()
    assert f'href="/operator/{shop.slug}/"' in page
    client.force_login(owner)
    assert (
        f'href="/operator/{shop.slug}/"'
        not in client.get(f"/partner/{shop.slug}/info/").content.decode()
    )


def test_shop_pagination_keeps_filters_and_order_page(client, admin, city):
    for index in range(26):
        partner = get_model("partner", "Partner").objects.create(
            name=f"Shop {index}", code=f"shop-{index}"
        )
        Shop.objects.create(
            partner=partner,
            name=f"Shop {index:02}",
            slug=f"shop-{index}",
            settlement=city,
            status="active",
            latitude=Decimal("55.75"),
            longitude=Decimal("37.61"),
        )
    client.force_login(admin)
    response = client.get(
        "/operator/", {"q": "Shop", "status": "active", "days": "30", "page": "2"}
    )
    assert len(response.context["shops"]) == 25
    assert "page=2&amp;shop_page=2#shops" in response.content.decode()
    second = client.get(
        "/operator/",
        {"q": "Shop", "status": "active", "days": "30", "page": "2", "shop_page": "2"},
    )
    assert [shop.name for shop in second.context["shops"]] == ["Shop 25"]
    assert 'name="q" value="Shop"' in second.content.decode()
