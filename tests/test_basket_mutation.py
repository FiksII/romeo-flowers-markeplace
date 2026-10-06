import pytest
from oscar.core.loading import get_model

from market.middleware import MarketplaceBasketMiddleware
from tests.test_orders import basket_for

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("operation", ["add", "update"])
def test_stale_request_cannot_edit_frozen_basket(
    client, monkeypatch, owner, listing, operation
):
    client.force_login(owner)
    basket = basket_for(owner, listing)
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
