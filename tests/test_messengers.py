import json
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from market import messengers
from market.models import MessengerLink, MessengerMessage
from market.payments import record_online_payment
from tests.test_payments import order_part

pytestmark = pytest.mark.django_db


@pytest.fixture
def bot(monkeypatch, settings):
    """Both channels configured; HTTP calls are recorded instead of being sent."""
    settings.TELEGRAM_BOT_TOKEN = "123:secret"
    settings.TELEGRAM_BOT_USERNAME = "romeo_test_bot"
    settings.VK_GROUP_ID = "42"
    settings.VK_GROUP_TOKEN = "vk-token"
    settings.VK_CONFIRMATION_CODE = "c0nfirm"

    def fake(url, params, timeout=10):
        fake.calls.append((url, params))
        if fake.answer is not None:
            return fake.answer
        if url.endswith("users.get"):
            return {"response": [{"first_name": "Анна", "last_name": "Цветкова"}]}
        if "api.vk.ru" in url:
            return {"response": 1}
        return {"ok": True, "result": {}}

    fake.calls, fake.answer = [], None
    monkeypatch.setattr(messengers, "_post_json", fake)
    return fake


@pytest.fixture
def admin(db):
    return get_user_model().objects.create_superuser(
        "notify-admin", "notify-admin@example.com", "long-secret-pass"
    )


def linked(user, channel, chat_id):
    return MessengerLink.objects.create(
        user=user, channel=channel, chat_id=chat_id, linked_at=timezone.now()
    )


def texts(bot):
    return [
        (
            url.rsplit("/", 1)[-1],
            params.get("chat_id") or params.get("peer_id"),
            params.get("text") or params.get("message"),
        )
        for url, params in bot.calls
    ]


def test_cash_order_notifies_shop_people_and_superadmins(
    bot, owner, shop, listing, stranger, admin, django_capture_on_commit_callbacks
):
    linked(owner, "telegram", "1001")
    linked(admin, "vk", "2002")
    linked(stranger, "telegram", "3003")
    with django_capture_on_commit_callbacks(execute=True):
        part = order_part(owner, shop, listing, "delivery", payment="cash")
    sent = texts(bot)
    assert sorted((method, chat) for method, chat, _ in sent) == [
        ("messages.send", "2002"),
        ("sendMessage", "1001"),
    ]
    by_chat = {chat: text for _, chat, text in sent}
    shop_text, admin_text = by_chat["1001"], by_chat["2002"]
    assert f"Новый заказ № {part.order.number}" in shop_text
    assert "Нежность × 1" in shop_text and shop.name in shop_text
    assert f"https://flowers.test/partner/{shop.slug}/orders/{part.pk}/" in shop_text
    # Buyer prices are for the superadmin; the shop sees what it collects.
    # Romeo's courier collects the cash, so the shop sees its own part only.
    assert "Вам 2 500 ₽" in shop_text and "Сумма заказа" not in shop_text
    assert "Сумма заказа" in admin_text
    # No personal data of the buyer leaves the site.
    assert part.contact_phone not in shop_text and part.contact_name not in shop_text
    assert set(MessengerMessage.objects.values_list("status", flat=True)) == {"sent"}


def test_online_order_is_announced_once_after_payment(
    bot, owner, shop, listing, django_capture_on_commit_callbacks
):
    linked(owner, "telegram", "1001")
    with django_capture_on_commit_callbacks(execute=True):
        part = order_part(owner, shop, listing, "delivery", payment="online")
    assert bot.calls == []
    with django_capture_on_commit_callbacks(execute=True):
        record_online_payment(part)
        record_online_payment(part)
    assert len(bot.calls) == 1
    assert "Онлайн · Оплачен" in bot.calls[0][1]["text"]
    with django_capture_on_commit_callbacks(execute=True):
        messengers.notify_new_part(part)
    assert len(bot.calls) == 1


def test_disabled_link_and_unconfigured_channel_get_nothing(
    bot, settings, owner, shop, listing, admin, django_capture_on_commit_callbacks
):
    link = linked(owner, "telegram", "1001")
    link.enabled = False
    link.save()
    linked(admin, "vk", "2002")
    settings.VK_GROUP_TOKEN = ""
    with django_capture_on_commit_callbacks(execute=True):
        order_part(owner, shop, listing, payment="cash")
    assert bot.calls == []


def test_blocked_bot_disables_the_link(bot, owner, shop, listing):
    link = linked(owner, "telegram", "1001")
    bot.answer = {"ok": False, "error_code": 403, "description": "bot was blocked"}
    order_part(owner, shop, listing, payment="cash")
    message = MessengerMessage.objects.get()
    assert messengers.deliver(message.pk) == "failed"
    link.refresh_from_db()
    assert not link.enabled


def test_temporary_errors_are_retried_by_the_timer(bot, owner, shop, listing):
    linked(owner, "telegram", "1001")
    bot.answer = {"ok": False, "error_code": 429, "description": "Too Many Requests"}
    order_part(owner, shop, listing, payment="cash")
    message = MessengerMessage.objects.get()
    later = timezone.now() + timedelta(minutes=1)
    assert messengers.deliver_pending(now=later) == (0, 1)
    message.refresh_from_db()
    assert (message.status, message.attempts) == ("pending", 1)
    bot.answer = None
    assert messengers.deliver_pending(now=later) == (1, 1)
    message.refresh_from_db()
    assert message.status == "sent"
    assert messengers.deliver_pending(now=later) == (0, 0)


def test_message_fails_after_the_last_attempt(bot, owner, shop, listing):
    linked(owner, "telegram", "1001")
    bot.answer = {"ok": False, "error_code": 500, "description": "Internal"}
    order_part(owner, shop, listing, payment="cash")
    message = MessengerMessage.objects.get()
    for _ in range(messengers.MAX_ATTEMPTS):
        messengers.deliver(message.pk)
    message.refresh_from_db()
    assert (message.status, message.attempts) == ("failed", messengers.MAX_ATTEMPTS)


def telegram_post(client, update, secret=None):
    return client.post(
        "/messengers/telegram/webhook/",
        json.dumps(update),
        content_type="application/json",
        HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=secret
        if secret is not None
        else messengers.telegram_webhook_secret(),
    )


def tg_update(text, chat_id=555, username="flower_seller"):
    return {
        "update_id": 1,
        "message": {
            "message_id": 7,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "first_name": "Анна", "username": username},
            "text": text,
        },
    }


def test_telegram_start_with_code_links_the_chat(bot, client, owner, shop):
    link = messengers.link_with_code(owner, "telegram")
    assert messengers.connect_url(link) == (
        f"https://t.me/romeo_test_bot?start={link.code}"
    )
    assert (
        telegram_post(client, tg_update(f"/start {link.code}"), "wrong").status_code
        == 403
    )
    response = telegram_post(client, tg_update(f"/start {link.code}"))
    assert response.status_code == 200
    answer = response.json()
    assert answer["method"] == "sendMessage" and answer["chat_id"] == 555
    assert "Готово" in answer["text"] and shop.name in answer["text"]
    link.refresh_from_db()
    assert (link.chat_id, link.title, link.code, link.enabled) == (
        "555",
        "@flower_seller",
        None,
        True,
    )
    # The code works once.
    again = telegram_post(client, tg_update(f"/start {link.code or 'ABCDEFGH'}"))
    assert "Код не подошёл" in again.json()["text"]
    assert "Чтобы получать" in telegram_post(client, tg_update("привет")).json()["text"]
    telegram_post(client, tg_update("/stop"))
    link.refresh_from_db()
    assert not link.enabled


def test_telegram_ignores_expired_codes_and_group_chats(bot, client, owner):
    link = messengers.link_with_code(owner, "telegram")
    MessengerLink.objects.filter(pk=link.pk).update(
        code_created_at=timezone.now() - timedelta(hours=1)
    )
    assert (
        "Код не подошёл" in telegram_post(client, tg_update(link.code)).json()["text"]
    )
    group = tg_update(link.code)
    group["message"]["chat"]["type"] = "group"
    assert telegram_post(client, group).json() == {}
    link.refresh_from_db()
    assert link.chat_id == ""
    # An expired code is replaced when the settings page asks for one.
    assert messengers.link_with_code(owner, "telegram").code != link.code


def test_telegram_webhook_is_closed_without_a_bot(client, owner):
    assert telegram_post(client, tg_update("/start X"), "").status_code == 403


def vk_post(client, event):
    return client.post(
        "/messengers/vk/callback/", json.dumps(event), content_type="application/json"
    )


def vk_message(text="", ref=None, peer=777, event_id="e1"):
    message = {"peer_id": peer, "from_id": peer, "text": text}
    if ref:
        message["ref"] = ref
    return {
        "type": "message_new",
        "group_id": 42,
        "event_id": event_id,
        "secret": messengers.vk_callback_secret(),
        "object": {"message": message},
    }


def test_vk_confirmation_and_secret(bot, client):
    response = vk_post(client, {"type": "confirmation", "group_id": 42})
    assert response.content == b"c0nfirm"
    assert vk_post(client, {"type": "confirmation", "group_id": 1}).status_code == 403
    wrong = vk_message("hi")
    wrong["secret"] = "nope"
    assert vk_post(client, wrong).status_code == 403


def test_vk_link_by_ref_or_text_code(bot, client, owner, admin):
    link = messengers.link_with_code(owner, "vk")
    assert messengers.connect_url(link) == f"https://vk.me/club42?ref={link.code}"
    assert vk_post(client, vk_message("Начать", ref=link.code)).content == b"ok"
    link.refresh_from_db()
    assert (link.chat_id, link.title) == ("777", "Анна Цветкова")
    reply = bot.calls[-1][1]
    assert reply["peer_id"] == "777" and "Готово" in reply["message"]

    admin_link = messengers.link_with_code(admin, "vk")
    vk_post(
        client,
        vk_message(f"мой код {admin_link.code.lower()}", peer=888, event_id="e2"),
    )
    admin_link.refresh_from_db()
    assert admin_link.chat_id == "888"
    assert "всех магазинов" in bot.calls[-1][1]["message"]

    # A repeated event is not processed twice.
    calls = len(bot.calls)
    vk_post(client, vk_message("привет", peer=888, event_id="e2"))
    assert len(bot.calls) == calls

    vk_post(
        client,
        {
            "type": "message_deny",
            "group_id": 42,
            "event_id": "e3",
            "secret": messengers.vk_callback_secret(),
            "object": {"user_id": 888},
        },
    )
    admin_link.refresh_from_db()
    assert not admin_link.enabled


def settings_url(shop):
    return f"/partner/{shop.slug}/settings/"


@pytest.mark.parametrize("role", ["partner", "admin"])
def test_settings_without_configured_channels(client, owner, shop, role):
    user = owner
    url = settings_url(shop)
    if role == "admin":
        user = get_user_model().objects.create_superuser(
            "settings-admin", "settings@example.com", "long-password"
        )
        url = "/operator/settings/"
    client.force_login(user)
    response = client.get(url)
    assert response.status_code == 200
    html = response.content.decode()
    assert "Telegram" in html and "ВК" in html
    assert html.count("Канал ещё не подключён") == 2
    assert "t.me/" not in html and not MessengerLink.objects.exists()
    assert client.post(url, {"channel": "telegram", "action": "on"}).status_code == 302


def test_settings_connect_toggle_test_and_unlink(bot, client, owner, stranger, shop):
    client.force_login(owner)
    html = client.get(settings_url(shop)).content.decode()
    tg = MessengerLink.objects.get(user=owner, channel="telegram")
    vk = MessengerLink.objects.get(user=owner, channel="vk")
    assert f"https://t.me/romeo_test_bot?start={tg.code}" in html
    assert f"https://vk.me/club42?ref={vk.code}" in html
    # Reloading the page keeps the code the person may already have sent.
    client.get(settings_url(shop))
    assert MessengerLink.objects.get(pk=tg.pk).code == tg.code

    messengers.confirm_link("telegram", tg.code, "1001", "@seller")
    other = linked(stranger, "telegram", "3003")
    html = client.get(settings_url(shop)).content.decode()
    assert "@seller" in html and "Уведомления включены" in html

    client.post(settings_url(shop), {"channel": "telegram", "action": "off"})
    tg.refresh_from_db()
    assert not tg.enabled
    client.post(settings_url(shop), {"channel": "telegram", "action": "on"})
    tg.refresh_from_db()
    assert tg.enabled

    response = client.post(
        settings_url(shop), {"channel": "telegram", "action": "test"}, follow=True
    )
    assert "Проверочное сообщение отправлено" in response.content.decode()
    assert bot.calls[-1][1]["chat_id"] == "1001"

    bot.answer = {"ok": False, "error_code": 403, "description": "Forbidden"}
    response = client.post(
        settings_url(shop), {"channel": "telegram", "action": "test"}, follow=True
    )
    assert "заблокированы" in response.content.decode()
    tg.refresh_from_db()
    assert not tg.enabled

    client.post(settings_url(shop), {"channel": "telegram", "action": "unlink"})
    assert not MessengerLink.objects.filter(pk=tg.pk).exists()
    assert MessengerLink.objects.filter(pk=other.pk, enabled=True).exists()
