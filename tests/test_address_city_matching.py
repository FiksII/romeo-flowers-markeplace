import pytest

from market.addresses import encode_address
from market.availability import location_matches
from market.context import FulfillmentContext
from market.forms import ShopForm
from market.models import Settlement
from tests.test_orders import ADDRESS
from tests.test_partner_workflows import shop_data

pytestmark = pytest.mark.django_db


def test_provider_city_spelling_does_not_reject_valid_shop_address(shop):
    city = Settlement.objects.get(name='Щёлково')
    address = {**ADDRESS, 'city':'Щелково', 'region':'50', 'value':'Щелково, Тестовая, д 1'}
    form = ShopForm({**shop_data(shop), 'settlement':city.pk,
                    'address':address['value'], 'address_token':encode_address(address)}, instance=shop)
    assert form.is_valid(), form.errors
    assert form.save().settlement_id == city.pk


def test_pickup_city_comparison_ignores_yo_but_preserves_region(shop):
    shop.settlement = Settlement.objects.get(name='Щёлково')
    context = FulfillmentContext(method='pickup', address={**ADDRESS,'city':'Щелково','region':'50'})
    assert location_matches(shop, 'pickup', context)
    context.address['region'] = '77'
    assert not location_matches(shop, 'pickup', context)
