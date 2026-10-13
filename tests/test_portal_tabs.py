import pytest
from django.contrib.auth import get_user_model

from tests.test_table_views import make_part

pytestmark = pytest.mark.django_db


def test_partner_tabs_show_orders_and_settings(client, owner, shop):
    client.force_login(owner)
    page = client.get(f"/partner/{shop.slug}/").content.decode()
    assert f'href="/partner/{shop.slug}/orders/"' in page
    assert f'href="/partner/{shop.slug}/settings/"' in page


def test_partner_orders_are_scoped_and_have_data_url(client, owner, shop, other_shop):
    own = make_part(shop, "own-tab-order")
    make_part(other_shop, "foreign-tab-order")
    client.force_login(owner)
    response = client.get(f"/partner/{shop.slug}/orders/")
    assert response.status_code == 200
    assert [part.pk for part in response.context["parts"]] == [own.pk]
    html = response.content.decode()
    assert "own-tab-order" in html and "foreign-tab-order" not in html
    assert f'data-orders-url="/partner/{shop.slug}/orders/data/"' in html
    assert client.get(f"/partner/{other_shop.slug}/orders/").status_code == 404
    assert client.get(f"/partner/{other_shop.slug}/settings/").status_code == 404


def test_superadmin_orders_cover_all_shops(client, shop, other_shop):
    own = make_part(shop, "first-tab-order")
    other = make_part(other_shop, "second-tab-order")
    admin = get_user_model().objects.create_superuser(
        "tabs-admin", "admin@example.com", "long-password"
    )
    client.force_login(admin)
    response = client.get("/operator/orders/")
    assert response.status_code == 200
    assert {part.pk for part in response.context["parts"]} == {own.pk, other.pk}
    assert 'data-orders-url="/operator/orders/data/"' in response.content.decode()
    dashboard = client.get("/operator/").content.decode()
    assert 'href="/operator/orders/"' in dashboard
    assert 'href="/operator/settings/"' in dashboard


@pytest.mark.parametrize("role", ["partner", "admin"])
def test_unconnected_notification_channels_are_disabled(client, owner, shop, role):
    user = owner
    if role == "admin":
        user = get_user_model().objects.create_superuser(
            "settings-admin", "settings@example.com", "long-password"
        )
    client.force_login(user)
    url = (
        "/operator/settings/" if role == "admin" else f"/partner/{shop.slug}/settings/"
    )
    response = client.get(url)
    assert response.status_code == 200
    assert set(response.context["form"].fields) == {"notify_telegram", "notify_vk"}
    assert all(
        field.disabled and not field.initial
        for field in response.context["form"].fields.values()
    )
    html = response.content.decode()
    assert "Telegram" in html and "ВК" in html and "Канал ещё не подключён" in html
    assert (
        client.post(url, {"notify_telegram": "on", "notify_vk": "on"}).status_code
        == 405
    )


def test_operator_tabs_are_closed_to_partner(client, owner):
    client.force_login(owner)
    assert client.get("/operator/orders/").status_code == 403
    assert client.get("/operator/settings/").status_code == 403


def test_new_tabs_require_authentication(client, shop):
    for url in [
        f"/partner/{shop.slug}/orders/",
        f"/partner/{shop.slug}/settings/",
        "/operator/orders/",
        "/operator/settings/",
    ]:
        assert client.get(url).status_code == 302


def test_orders_tab_preserves_period_and_pagination(client, owner, shop):
    for index in range(21):
        make_part(shop, f"tab-page-{index}")
    make_part(shop, "outside-period", age=15)
    client.force_login(owner)
    response = client.get(f"/partner/{shop.slug}/orders/", {"days": "7", "page": "2"})
    assert response.status_code == 200
    assert response.context["days"] == 7
    assert response.context["parts"].number == 2
    assert response.context["parts"].paginator.count == 21
    assert "outside-period" not in response.content.decode()
