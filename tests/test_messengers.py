import json
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from market import messengers
from market.models import MessengerLink, MessengerMessage, Shop
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


def number(shop, channel, chat_id, phone="+79990000001"):
    return MessengerLink.objects.create(
        shop=shop,
        channel=channel,
        phone=phone,
        chat_id=chat_id,
        linked_at=timezone.now(),
    )


def personal(user, channel, chat_id):
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


def test_shop_numbers_get_the_order_of_their_shop_only(
    bot, owner, shop, other_shop, listing, django_capture_on_commit_callbacks
):
    number(shop, "telegram", "1001", "+79990000001")
    number(shop, "telegram", "1002", "+79990000002")
    number(shop, "vk", "2001", "+79990000001")
    number(other_shop, "telegram", "3003")
    with django_capture_on_commit_callbacks(execute=True):
        part = order_part(owner, shop, listing, "delivery", payment="cash")
    sent = texts(bot)
    assert sorted((method, chat) for method, chat, _ in sent) == [
        ("messages.send", "2001"),
        ("sendMessage", "1001"),
        ("sendMessage", "1002"),
    ]
    text = sent[0][2]
    assert f"Новый заказ № {part.order.number}" in text
    assert "Нежность × 1" in text and shop.name in text
    assert f"https://flowers.test/partner/{shop.slug}/orders/{part.pk}/" in text
    # Romeo's courier collects the cash, so the shop sees its own part only.
    assert "Вам 2\xa0500 ₽" in text and "Сумма заказа" not in text
    # No personal data of the buyer leaves the site.
    assert part.contact_phone not in text and part.contact_name not in text
    assert set(MessengerMessage.objects.values_list("status", flat=True)) == {"sent"}


def test_superadmins_get_romeo_deliveries_of_ticked_shops(
    bot, owner, shop, listing, admin, django_capture_on_commit_callbacks
):
    personal(admin, "vk", "9009")
    with django_capture_on_commit_callbacks(execute=True):
        order_part(owner, shop, listing, "delivery", payment="cash")
    assert bot.calls == []  # the shop is not ticked

    Shop.objects.filter(pk=shop.pk).update(notify_admins=True)
    shop.refresh_from_db()
    with django_capture_on_commit_callbacks(execute=True):
        order_part(owner, shop, listing, "pickup", payment="cash")
    assert bot.calls == []  # pickup is not a Romeo delivery

    with django_capture_on_commit_callbacks(execute=True):
        part = order_part(owner, shop, listing, "delivery", payment="cash")
    assert [(chat, "Сумма заказа" in text) for _, chat, text in texts(bot)] == [
        ("9009", True)
    ]
    # The shop's own delivery is not Romeo's business.
    part.delivery_owner = "partner"
    assert not messengers.admin_wants(part)


def test_a_chat_on_both_lists_gets_one_message(
    bot, owner, shop, listing, admin, django_capture_on_commit_callbacks
):
    Shop.objects.filter(pk=shop.pk).update(notify_admins=True)
    shop.refresh_from_db()
    personal(admin, "telegram", "1001")
    number(shop, "telegram", "1001")
    with django_capture_on_commit_callbacks(execute=True):
        order_part(owner, shop, listing, "delivery", payment="cash")
    assert len(bot.calls) == 1
    assert "Сумма заказа" in bot.calls[0][1]["text"]


def test_online_order_is_announced_once_after_payment(
    bot, owner, shop, listing, django_capture_on_commit_callbacks
):
    number(shop, "telegram", "1001")
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


def test_disabled_number_and_unconfigured_channel_get_nothing(
    bot, settings, owner, shop, listing, django_capture_on_commit_callbacks
):
    link = number(shop, "telegram", "1001")
    link.enabled = False
    link.save()
    number(shop, "vk", "2002")
    settings.VK_GROUP_TOKEN = ""
    with django_capture_on_commit_callbacks(execute=True):
        order_part(owner, shop, listing, payment="cash")
    assert bot.calls == []


def test_blocked_bot_disables_the_number(bot, owner, shop, listing):
    link = number(shop, "telegram", "1001")
    bot.answer = {"ok": False, "error_code": 403, "description": "bot was blocked"}
    order_part(owner, shop, listing, payment="cash")
    message = MessengerMessage.objects.get()
    assert messengers.deliver(message.pk) == "failed"
    link.refresh_from_db()
    assert not link.enabled


def test_temporary_errors_are_retried_by_the_timer(bot, owner, shop, listing):
    number(shop, "telegram", "1001")
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
    number(shop, "telegram", "1001")
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


def test_telegram_invitation_links_the_number(bot, client, shop):
    link = messengers.add_shop_number(shop, "telegram", "+79991112233")
    assert messengers.connect_url(link) == (
        f"https://t.me/romeo_test_bot?start={link.code}"
    )
    code = link.code
    assert (
        telegram_post(client, tg_update(f"/start {code}"), "wrong").status_code == 403
    )
    answer = telegram_post(client, tg_update(f"/start {code}")).json()
    assert answer["method"] == "sendMessage" and answer["chat_id"] == 555
    assert "Готово" in answer["text"] and f"«{shop.name}»" in answer["text"]
    link.refresh_from_db()
    assert (link.chat_id, link.title, link.code, link.enabled) == (
        "555",
        "@flower_seller",
        None,
        True,
    )
    # The invitation works once.
    again = telegram_post(client, tg_update(f"/start {code}", chat_id=777))
    assert "Ссылка не подошла" in again.json()["text"]
    assert (
        "ссылку-приглашение"
        in telegram_post(client, tg_update("привет")).json()["text"]
    )
    telegram_post(client, tg_update("/stop"))
    link.refresh_from_db()
    assert not link.enabled


def test_old_or_replaced_invitation_does_not_link(bot, client, shop):
    link = messengers.add_shop_number(shop, "telegram", "+79991112233")
    MessengerLink.objects.filter(pk=link.pk).update(
        code_created_at=timezone.now() - timedelta(days=8)
    )
    assert (
        "Ссылка не подошла"
        in telegram_post(client, tg_update(link.code)).json()["text"]
    )
    link.refresh_from_db()
    assert link.chat_id == ""
    old = link.code
    assert messengers.ensure_code(link).code != old

    replaced = link.code
    messengers.new_invitation(link)
    assert (
        "Ссылка не подошла"
        in telegram_post(client, tg_update(f"/start {replaced}")).json()["text"]
    )
    group = tg_update(link.code)
    group["message"]["chat"]["type"] = "group"
    assert telegram_post(client, group).json() == {}


def test_telegram_webhook_is_closed_without_a_bot(client):
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


def test_vk_link_by_ref_or_text_code(bot, client, shop, admin):
    link = messengers.add_shop_number(shop, "vk", "+79991112233")
    assert messengers.connect_url(link) == f"https://vk.me/club42?ref={link.code}"
    assert vk_post(client, vk_message("Начать", ref=link.code)).content == b"ok"
    link.refresh_from_db()
    assert (link.chat_id, link.title) == ("777", "Анна Цветкова")
    reply = bot.calls[-1][1]
    assert reply["peer_id"] == "777" and shop.name in reply["message"]

    admin_link = messengers.link_with_code(admin, "vk")
    vk_post(
        client,
        vk_message(f"мой код {admin_link.code.lower()}", peer=888, event_id="e2"),
    )
    admin_link.refresh_from_db()
    assert admin_link.chat_id == "888"
    assert "доставкой Ромео" in bot.calls[-1][1]["message"]

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


def test_shop_settings_without_configured_channels(client, owner, shop):
    client.force_login(owner)
    html = client.get(settings_url(shop)).content.decode()
    assert html.count("Канал ещё не подключён") == 2
    client.post(
        settings_url(shop),
        {"action": "add", "channel": "telegram", "phone": "8 (999) 111-22-33"},
    )
    link = MessengerLink.objects.get(shop=shop)
    assert (link.channel, link.phone, link.user_id) == (
        "telegram",
        "+79991112233",
        None,
    )
    html = client.get(settings_url(shop)).content.decode()
    assert "+7 (999) 111-22-33" in html and "t.me/" not in html
    assert "Ссылка появится" in html


def test_shop_number_list_per_channel(bot, client, owner, stranger, shop, other_shop):
    client.force_login(owner)
    url = settings_url(shop)
    for channel, phone in [
        ("telegram", "+79990000001"),
        ("telegram", "+79990000002"),
        ("vk", "+79990000001"),
        ("vk", "+79990000003"),
    ]:
        client.post(url, {"action": "add", "channel": channel, "phone": phone})
    response = client.post(
        url, {"action": "add", "channel": "vk", "phone": "+79990000003"}, follow=True
    )
    assert "уже есть в списке" in response.content.decode()
    response = client.post(
        url, {"action": "add", "channel": "vk", "phone": "12345"}, follow=True
    )
    assert "Введите телефон полностью" in response.content.decode()
    assert MessengerLink.objects.filter(shop=shop).count() == 4

    html = client.get(url).content.decode()
    tg = MessengerLink.objects.filter(shop=shop, channel="telegram")
    for link in tg:
        assert f"https://t.me/romeo_test_bot?start={link.code}" in html
    first = tg.get(phone="+79990000001")
    messengers.confirm_link("telegram", first.code, "1001", "@seller")
    html = client.get(url).content.decode()
    assert "@seller" in html and "Ждёт подключения" in html

    client.post(url, {"action": "off", "link": first.pk})
    first.refresh_from_db()
    assert not first.enabled
    client.post(url, {"action": "on", "link": first.pk})
    first.refresh_from_db()
    assert first.enabled
    response = client.post(url, {"action": "test", "link": first.pk}, follow=True)
    assert "Проверочное сообщение отправлено" in response.content.decode()
    assert bot.calls[-1][1]["chat_id"] == "1001"

    client.post(url, {"action": "renew", "link": first.pk})
    first.refresh_from_db()
    assert first.chat_id == "" and first.code

    # Another shop's rows cannot be touched from this page or by a stranger.
    foreign = messengers.add_shop_number(other_shop, "telegram", "+79995556677")
    client.post(url, {"action": "delete", "link": foreign.pk})
    assert MessengerLink.objects.filter(pk=foreign.pk).exists()
    client.force_login(stranger)
    assert client.get(url).status_code == 404

    client.force_login(owner)
    client.post(url, {"action": "delete", "link": first.pk})
    assert not MessengerLink.objects.filter(pk=first.pk).exists()


def test_shop_list_is_limited(bot, shop):
    for index in range(messengers.MAX_SHOP_NUMBERS):
        messengers.add_shop_number(shop, "vk", f"+7999000{index:04d}")
    with pytest.raises(ValueError):
        messengers.add_shop_number(shop, "vk", "+79998887766")


def test_operator_personal_channel(bot, client, admin):
    client.force_login(admin)
    html = client.get("/operator/settings/").content.decode()
    tg = MessengerLink.objects.get(user=admin, channel="telegram")
    assert f"https://t.me/romeo_test_bot?start={tg.code}" in html
    assert "отмечено: 0" in html
    # Reloading the page keeps the code the person may already have sent.
    client.get("/operator/settings/")
    assert MessengerLink.objects.get(pk=tg.pk).code == tg.code
    messengers.confirm_link("telegram", tg.code, "1001", "@boss")
    client.post("/operator/settings/", {"channel": "telegram", "action": "off"})
    tg.refresh_from_db()
    assert not tg.enabled
    client.post("/operator/settings/", {"channel": "telegram", "action": "delete"})
    assert not MessengerLink.objects.filter(pk=tg.pk).exists()


def test_operator_ticks_shops_for_romeo_delivery(
    client, admin, owner, shop, other_shop
):
    client.force_login(admin)
    html = client.get("/operator/").content.decode()
    assert 'name="notify"' in html and "Уведомлять о доставке" in html
    response = client.post(
        "/operator/notify-shops/",
        {
            "shown": [shop.pk, other_shop.pk],
            "notify": [shop.pk],
            "next": "/operator/?q=",
        },
    )
    assert response.status_code == 302 and response["Location"] == "/operator/?q=#shops"
    assert list(Shop.objects.filter(notify_admins=True)) == [shop]
    client.post(
        "/operator/notify-shops/", {"shown": [shop.pk], "next": "https://evil.test/"}
    )
    shop.refresh_from_db()
    assert not shop.notify_admins
    client.force_login(owner)
    assert (
        client.post(
            "/operator/notify-shops/", {"shown": [shop.pk], "notify": [shop.pk]}
        ).status_code
        == 403
    )
