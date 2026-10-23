import pytest
from django.contrib.auth import get_user_model

from market import messengers
from tests.test_messengers import bot as bot, number

pytestmark = pytest.mark.django_db


def test_partner_check_returns_inline_feedback_without_redirect(bot, client, owner, shop):
    link = number(shop, 'telegram', '1001')
    client.force_login(owner)
    response = client.post(f'/partner/{shop.slug}/settings/',
        {'action':'test', 'link':link.pk}, HTTP_ACCEPT='application/json')
    assert response.status_code == 200
    assert response.json()['ok'] is True
    assert response.json()['message'] == 'Проверочное сообщение отправлено.'
    assert len(bot.calls) == 1


def test_inline_check_cannot_send_to_another_shop(bot, client, owner, shop, other_shop):
    link = number(other_shop, 'telegram', '1001')
    client.force_login(owner)
    response = client.post(f'/partner/{shop.slug}/settings/',
        {'action':'test','link':link.pk}, HTTP_ACCEPT='application/json')
    assert response.status_code == 400
    assert response.json()['ok'] is False
    assert not bot.calls


def test_admin_check_reports_delivery_failure_inline(bot, client):
    from market.models import MessengerLink

    user = get_user_model().objects.create_superuser('inline-boss', 'boss@example.com', 'pass')
    link = messengers.link_with_code(user, 'telegram')
    link.chat_id = '2002'
    link.save()
    bot.answer = {'ok':False,'error_code':403,'description':'bot was blocked'}
    client.force_login(user)
    response = client.post('/operator/settings/',
        {'action':'test','channel':MessengerLink.TELEGRAM}, HTTP_ACCEPT='application/json')
    assert response.status_code == 400
    assert response.json()['ok'] is False
    assert 'заблокированы' in response.json()['message']
