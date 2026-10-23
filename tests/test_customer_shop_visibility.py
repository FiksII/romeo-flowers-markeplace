import pytest

from market.orders import place_market_order
from tests.test_orders import ADDRESS, CONTACT, NOW, basket_for, choice

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize('method', ['any', 'delivery', 'pickup'])
def test_shop_identity_is_only_visible_for_selected_pickup(client, listing, method):
    session = client.session
    session['receiving'] = {'method': method}
    session.save()
    for path in ['/', f'/products/{listing.pk}/']:
        html = client.get(path).content.decode()
        assert (listing.shop.name in html) is (method == 'pickup')
        assert (f'/shops/{listing.shop.slug}/' in html) is (method == 'pickup')


@pytest.mark.parametrize('method', ['delivery', 'pickup'])
def test_customer_order_identifies_only_pickup_shop(client, owner, shop, listing, method):
    order = place_market_order(owner, basket_for(owner, listing),
        {shop.pk: choice(shop, method)}, ADDRESS if method == 'delivery' else None,
        CONTACT, NOW)
    client.force_login(owner)
    html = client.get(f'/account/orders/{order.number}/').content.decode()
    assert (shop.name in html) is (method == 'pickup')
    assert 'Итого по магазину' not in html
    assert 'магазинов' not in client.get('/account/orders/').content.decode()


def test_customer_basket_hides_delivery_shop(client, owner, listing):
    client.force_login(owner)
    client.post(f'/basket/add/{listing.pk}/')
    html = client.get('/basket/').content.decode()
    assert listing.shop.name not in html
    assert listing.shop.address not in html
    assert 'Доставку организует Ромео или сам магазин' not in html
