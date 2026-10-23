from datetime import timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from market import messengers
from market.models import AuditEntry, MessengerLink, MessengerMessage
from tests.test_payments import order_part

pytestmark = pytest.mark.django_db


@pytest.fixture
def max_bot(settings, monkeypatch):
    settings.MAX_BOT_TOKEN = "max-test-token"
    settings.MAX_BOT_USERNAME = "romeo_max_bot"
    settings.SITE_URL = "https://flowers.test"
    calls = []

    def send(chat_id, text, photos=(), **kwargs):
        calls.append((chat_id, text, list(photos)))
        return {"body": {"mid": "test-message"}}

    monkeypatch.setattr(messengers, "max_send", send, raising=False)
    return calls


def linked(shop=None, user=None, chat_id="1001", phone="+79990000001"):
    return MessengerLink.objects.create(
        shop=shop, user=user, channel="max", chat_id=chat_id,
        phone=phone if shop else "", linked_at=timezone.now(),
    )


def test_max_availability_is_independent_and_unknown_channels_are_disabled(settings):
    settings.TELEGRAM_BOT_TOKEN = settings.TELEGRAM_BOT_USERNAME = "telegram"
    settings.VK_GROUP_ID = settings.VK_GROUP_TOKEN = "vk"
    settings.MAX_BOT_TOKEN = ""
    settings.MAX_BOT_USERNAME = "romeo_max_bot"
    assert not messengers.channel_enabled("max")
    assert not messengers.channel_enabled("unknown")
    assert messengers.channel_enabled("telegram")
    assert messengers.channel_enabled("vk")
    settings.MAX_BOT_TOKEN = "max-token"
    assert messengers.channel_enabled("max")
    settings.MAX_BOT_USERNAME = ""
    assert not messengers.channel_enabled("max")


def test_max_invitation_is_scoped_to_channel_and_expires(max_bot, shop):
    link = messengers.add_shop_number(shop, "max", "+79990000001")
    assert messengers.connect_url(link) == f"https://max.ru/romeo_max_bot?start={link.code}"
    assert messengers.confirm_link("telegram", link.code, "1001", "wrong") is None
    assert messengers.confirm_link("max", link.code, "1001", "MAX person") == link
    link.refresh_from_db()
    assert link.is_linked and link.title == "MAX person"
    assert messengers.confirm_link("max", link.code, "1002", "second") is None
    expired = messengers.add_shop_number(shop, "max", "+79990000002", now=timezone.now() - timedelta(days=8))
    assert messengers.confirm_link("max", expired.code, "1002", "expired") is None


@pytest.mark.parametrize("method,payment,status,expected", [
    ("pickup", "cash", "pending", "Получить с покупателя 3\xa0000 ₽"),
    ("delivery", "cash", "pending", "Получать деньги с клиента не нужно."),
    ("pickup", "online", "paid", "Оплачено онлайн. Получать деньги с клиента не нужно."),
    ("pickup", "online", "pending", "Ожидается онлайн-оплата."),
])
def test_max_order_outbox_keeps_partner_payment_instructions_and_photos(
    max_bot, owner, shop, listing, method, payment, status, expected,
    django_capture_on_commit_callbacks,
):
    listing.seed_image = "market/bouquet.jpg"
    listing.save(update_fields=["seed_image"])
    part = order_part(owner, shop, listing, method, payment)
    part.payment_status = status
    part.save(update_fields=["payment_status"])
    link = linked(shop)
    with django_capture_on_commit_callbacks(execute=True):
        messengers.notify_new_part(part)
    assert MessengerMessage.objects.get(link=link, part=part).status == "sent"
    chat_id, text, photos = max_bot[-1]
    assert chat_id == "1001" and expected in text
    assert part.contact_phone not in text
    assert photos == ["https://flowers.test/static/market/bouquet.jpg"]


def test_max_admin_outbox_has_full_long_order_text_photos_and_courier_instruction(
    max_bot, owner, shop, listing, django_capture_on_commit_callbacks,
):
    admin = get_user_model().objects.create_superuser("max-admin", "admin@example.com", "pw")
    listing.seed_image = "market/bouquet.jpg"
    listing.save(update_fields=["seed_image"])
    part = order_part(owner, shop, listing, "delivery", "cash")
    part.delivery_total = Decimal(350)
    part.instructions = "Подробная инструкция " * 100
    part.save(update_fields=["instructions", "delivery_total"])
    link = linked(user=admin)
    with django_capture_on_commit_callbacks(execute=True):
        messengers.notify_new_part(part)
    assert MessengerMessage.objects.get(link=link).status == "sent"
    _, text, photos = max_bot[-1]
    assert part.contact_phone in text and part.contact_name in text
    assert part.instructions in text and len(text) > 1024
    assert "курьер" in text.lower() and "3\xa0350 ₽" in text
    assert photos == ["https://flowers.test/static/market/bouquet.jpg"]


def test_max_notification_retries_and_disables_blocked_recipient(max_bot, monkeypatch, shop):
    link = linked(shop)
    message = MessengerMessage.objects.create(link=link, text="test")

    def unavailable(*args, **kwargs):
        raise messengers.MessengerError("Temporary MAX failure")

    monkeypatch.setattr(messengers, "max_send", unavailable)
    assert messengers.deliver(message.pk) == "pending"
    message.refresh_from_db()
    assert message.attempts == 1 and message.error == "Temporary MAX failure"

    def blocked(*args, **kwargs):
        raise messengers.RecipientGone("blocked")

    monkeypatch.setattr(messengers, "max_send", blocked)
    assert messengers.deliver(message.pk) == "failed"
    link.refresh_from_db()
    assert not link.enabled


def test_max_settings_allow_multiple_numbers_catalogue_grants_and_inline_test(
    max_bot, client, owner, shop, other_shop,
):
    client.force_login(owner)
    url = f"/partner/{shop.slug}/settings/"
    for phone in ["+79990000001", "+79990000002"]:
        client.post(url, {"action": "add", "channel": "max", "phone": phone})
    links = list(MessengerLink.objects.filter(shop=shop, channel="max"))
    assert len(links) == 2
    html = client.get(url).content.decode()
    assert f"https://max.ru/romeo_max_bot?start={links[0].code}" in html
    messengers.confirm_link("max", links[0].code, "1001", "Person")
    client.post(url, {"action": "catalogue-on", "link": links[0].pk})
    links[0].refresh_from_db()
    assert links[0].can_manage_catalogue
    assert "MAX" in AuditEntry.objects.latest("pk").action
    assert "Запретить добавление букетов" in client.get(url).content.decode()
    response = client.post(url, {"action": "test", "link": links[0].pk}, HTTP_ACCEPT="application/json")
    assert response.json()["ok"] is True and max_bot[-1][0] == "1001"
    foreign = linked(other_shop, chat_id="9999")
    client.post(url, {"action": "catalogue-on", "link": foreign.pk})
    foreign.refresh_from_db()
    assert not foreign.can_manage_catalogue
    client.post(url, {"action": "renew", "link": links[0].pk})
    links[0].refresh_from_db()
    assert not links[0].can_manage_catalogue and not links[0].is_linked


def test_setup_max_only_registers_sdk_webhook_and_commands_without_printing_secrets(
    max_bot, settings, monkeypatch,
):
    calls = []

    def call(method, **params):
        calls.append((method, params))
        return {"username": "romeo_max_bot"} if method == "get_me" else {"success": True}

    monkeypatch.setattr(messengers, "max_call", call, raising=False)
    output = StringIO()
    call_command("setup_messengers", channel="max", stdout=output)
    assert [method for method, _ in calls] == ["get_me", "subscribe_webhook", "set_commands"]
    params = calls[1][1]
    assert params["url"] == "https://flowers.test/messengers/max/webhook/"
    assert set(params["update_types"]) == {"bot_started", "bot_stopped", "message_created", "message_callback"}
    assert params["secret"] == messengers.max_webhook_secret()
    assert [command["name"] for command in calls[2][1]["commands"]] == ["start", "stop", "add", "cancel"]
    assert settings.MAX_BOT_TOKEN not in output.getvalue()
    assert params["secret"] not in output.getvalue()


def test_setup_max_rejects_different_bot_identity(max_bot, monkeypatch):
    monkeypatch.setattr(messengers, "max_call", lambda *a, **kw: {"username": "another_bot"}, raising=False)
    with pytest.raises(CommandError, match="MAX_BOT_USERNAME"):
        call_command("setup_messengers", channel="max", stdout=StringIO())


def test_setup_unconfigured_max_does_not_touch_other_bots(settings, monkeypatch):
    settings.MAX_BOT_TOKEN = settings.MAX_BOT_USERNAME = ""
    settings.SITE_URL = "https://flowers.test"

    def forbidden(*args, **kwargs):
        pytest.fail("MAX-only setup called another messenger")

    monkeypatch.setattr(messengers, "telegram_call", forbidden)
    monkeypatch.setattr(messengers, "vk_call", forbidden)
    output = StringIO()
    call_command("setup_messengers", channel="max", stdout=output)
    assert "MAX: не настроен" in output.getvalue()
