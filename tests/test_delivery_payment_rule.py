import pytest
from django.core.exceptions import ValidationError
from oscar.core.loading import get_model

from market.forms import CheckoutContactForm
from market.orders import place_market_order
from tests.test_orders import ADDRESS, CONTACT, NOW, basket_for, choice

pytestmark = pytest.mark.django_db


def test_delivery_cash_is_rejected_before_order_or_reservation(owner, shop, listing):
    basket = basket_for(owner, listing)
    with pytest.raises(ValidationError, match='онлайн'):
        place_market_order(owner, basket, {shop.pk: choice(shop, 'delivery')},
                           ADDRESS, CONTACT, NOW, payment_method='cash')
    assert not get_model('order', 'Order').objects.filter(basket=basket).exists()
    assert not listing.stockrecord.num_allocated


def test_delivery_contact_form_rejects_cash_even_when_posted_manually(settings):
    settings.MARKET_ONLINE_PAYMENTS = True
    form = CheckoutContactForm({'customer_name':'Анна','customer_phone':'+79997654321',
        'name':'Иван','phone':'+79991234567','delivery_contact':'call','payment_method':'cash'},
        has_delivery=True)
    assert not form.is_valid()
    assert 'payment_method' in form.errors


def test_delivery_without_payment_choice_defaults_to_online(settings):
    settings.MARKET_ONLINE_PAYMENTS = True
    form = CheckoutContactForm({'customer_name':'Анна','customer_phone':'+79997654321',
        'name':'Иван','phone':'+79991234567','delivery_contact':'call'}, has_delivery=True)
    assert form.is_valid(), form.errors
    assert form.cleaned_data['payment_method'] == 'online'


def test_delivery_cannot_fall_back_to_cash_when_online_disabled(settings):
    settings.MARKET_ONLINE_PAYMENTS = False
    form = CheckoutContactForm({'customer_name':'Анна','customer_phone':'+79997654321',
        'name':'Иван','phone':'+79991234567','delivery_contact':'call'}, has_delivery=True)
    assert not form.is_valid()
    assert 'payment_method' in form.errors
