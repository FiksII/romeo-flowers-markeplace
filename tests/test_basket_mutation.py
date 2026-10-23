import pytest
from oscar.core.loading import get_model

from market.middleware import MarketplaceBasketMiddleware
from tests.test_orders import basket_for

pytestmark = pytest.mark.django_db


def test_async_add_returns_count_without_navigation(client, listing):
    response = client.post(
        f"/basket/add/{listing.pk}/", {"quantity": "2"},
        HTTP_ACCEPT="application/json",
    )
    assert response.status_code == 200
    assert response.json()["count"] == 2
    assert response.json()["ok"] is True
    assert "Location" not in response
    assert client.get("/basket/").wsgi_request.basket.num_items == 2


@pytest.mark.parametrize("data", [{"quantity": "999"}, {"size": "invalid"}])
def test_async_add_errors_do_not_redirect_or_change_basket(client, listing, data):
    response = client.post(
        f"/basket/add/{listing.pk}/", data, HTTP_ACCEPT="application/json",
    )
    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert response.json()["message"]
    assert client.get("/basket/").wsgi_request.basket.num_items == 0


@pytest.mark.parametrize("operation", ["add", "update"])
def test_stale_request_cannot_edit_frozen_basket(
    client, monkeypatch, buyer, listing, operation
):
    client.force_login(buyer)
    basket = basket_for(buyer, listing)
    line = basket.all_lines().get()
    get_model("basket", "Basket").objects.filter(pk=basket.pk).update(
        status=basket.FROZEN
    )
    monkeypatch.setattr(
        MarketplaceBasketMiddleware, "get_basket", lambda self, request: basket
    )
    url = (
        f"/basket/add/{listing.pk}/"
        if operation == "add"
        else f"/basket/update/{line.pk}/"
    )
    response = client.post(url, {"quantity": "2"})
    assert response.status_code == 302
    line.refresh_from_db()
    assert line.quantity == 1


def test_site_stays_russian_with_english_browser(client):
    response = client.get("/signup/", HTTP_ACCEPT_LANGUAGE="en-US,en;q=0.9")
    assert response.wsgi_request.LANGUAGE_CODE == "ru"
