import json
from datetime import timedelta
from io import BytesIO

import pytest
from django.apps import apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image

from market.models import Listing, MaxBotEvent, MessengerLink, TelegramBouquetDraft

pytestmark = pytest.mark.django_db


@pytest.fixture
def configured_max(settings):
    settings.MAX_BOT_TOKEN = "local-test-token"
    settings.MAX_BOT_USERNAME = "romeo_test_bot"
    settings.MESSENGER_BACKGROUND = False


def event(text="/add", mid="message-1", chat=555, **message):
    return {
        "update_type": "message_created",
        "timestamp": 1792770000000,
        "message": {
            "sender": {"user_id": 777, "first_name": "Анна", "is_bot": False},
            "recipient": {"chat_id": chat, "chat_type": "dialog"},
            "timestamp": 1792770000000,
            "body": {"mid": mid, "seq": 1, "text": text},
            **message,
        },
    }


def post(client, update, secret=None):
    if secret is None:
        from market.messengers import max_webhook_secret

        secret = max_webhook_secret()
    return client.post(
        "/messengers/max/webhook/",
        json.dumps(update),
        content_type="application/json",
        HTTP_X_MAX_BOT_API_SECRET=secret,
    )


def test_max_webhook_rejects_forged_requests(configured_max, client):
    assert post(client, event(), "forged").status_code == 403


def test_max_webhook_durably_accepts_message_once(configured_max, client, monkeypatch):
    from market import messengers

    scheduled = []
    monkeypatch.setattr(
        messengers, "_in_background", lambda *args: scheduled.append(args)
    )
    with pytest.MonkeyPatch.context() as patch:
        # Run on_commit callbacks here through the existing fixture-independent hook.
        patch.setattr("django.db.transaction.on_commit", lambda fn: fn())
        assert post(client, event()).status_code == 200
        assert post(client, event()).status_code == 200
    model = apps.get_model("market", "MaxBotEvent")
    assert model.objects.count() == 1
    assert model.objects.get().payload["message"]["body"]["mid"] == "message-1"
    assert scheduled


class MaxDialog:
    def __init__(self, client, capture, sent):
        self.client, self.capture, self.sent = client, capture, sent
        self.counter, self.chat, self.answer = 0, 555, {}

    def post(self, update):
        self.last_update = update
        with self.capture(execute=True):
            response = post(self.client, update)
        assert response.status_code == 200
        if self.sent:
            self.answer = self.sent[-1]
        return self.answer

    def send(self, text="", **message):
        self.counter += 1
        update = event(text, f"mid-{self.counter}", self.chat)
        attachments = []
        for photo in message.pop("photo", []):
            attachments.append({"type": "image", "payload": {"url": photo["file_id"]}})
        document = message.pop("document", None)
        if document:
            attachments.append(
                {"type": "file", "payload": {"url": document["file_id"]}}
            )
        update["message"]["body"]["attachments"] = attachments
        update["message"].update(message)
        return self.post(update)

    def click(self, label):
        self.counter += 1
        button = next(
            b
            for row in self.answer["reply_markup"]["inline_keyboard"]
            for b in row
            if label in b["text"]
        )
        self.last_callback = {
            "update_type": "message_callback",
            "timestamp": 1792770000000 + self.counter,
            "callback": {
                "callback_id": f"callback-{self.counter}",
                "user": {"user_id": 777, "first_name": "Анна"},
                "payload": button["callback_data"],
            },
            "message": {
                "recipient": {"chat_id": self.chat, "chat_type": "dialog"},
                "body": {"mid": "outgoing-message", "text": self.answer["text"]},
            },
        }
        return self.post(self.last_callback)


@pytest.fixture
def max_dialog(
    configured_max, client, shop, monkeypatch, django_capture_on_commit_callbacks
):
    from market import max_bot

    sent = []
    monkeypatch.setattr(
        max_bot, "send_reply", lambda receipt: sent.append(receipt.response)
    )
    monkeypatch.setattr(max_bot, "_acknowledge", lambda update: None)
    MessengerLink.objects.create(
        shop=shop,
        channel="max",
        chat_id="555",
        linked_at=timezone.now(),
        can_manage_catalogue=True,
    )
    return MaxDialog(client, django_capture_on_commit_callbacks, sent)


@pytest.fixture
def max_photos(monkeypatch, settings, tmp_path):
    from market import max_transport, telegram_catalogue

    settings.MEDIA_ROOT = tmp_path
    output = BytesIO()
    Image.new("RGB", (20, 20), "red").save(output, "JPEG")
    monkeypatch.setattr(
        telegram_catalogue,
        "catalogue_photo",
        lambda draft, uri: SimpleUploadedFile(
            "bouquet.jpg", output.getvalue(), "image/jpeg"
        ),
    )
    monkeypatch.setattr(max_transport, "preview_photo", lambda *args, **kwargs: None)


def test_max_dialogue_publish_and_replay_do_not_duplicate(max_dialog, max_photos, shop):
    from tests.test_telegram_catalogue import through_review

    through_review(max_dialog, sizes=True)
    max_dialog.click("Опубликовать")
    listing = Listing.objects.get()
    assert listing.shop == shop and listing.product.is_public
    assert listing.product.title == "Весенний букет"
    assert listing.size_prices == {"s": {"pickup": "1500"}, "m": {"pickup": "2500"}}
    assert listing.stockrecord.num_in_stock == 4
    max_dialog.post(max_dialog.last_update)
    assert Listing.objects.count() == 1


def test_max_gallery_and_image_documents_preserve_every_photo(max_dialog, max_photos):
    max_dialog.send("/add")
    max_dialog.send(
        photo=[{"file_id": f"https://i.oneme.ru/{i}.jpg"} for i in range(3)]
    )
    max_dialog.send(document={"file_id": "https://i.oneme.ru/document.png"})
    draft = TelegramBouquetDraft.objects.get(channel="max")
    assert len(draft.data["photos"]) == 4
    assert draft.data["photos"][-1]["document"]
    assert draft.data["photos"][0]["file_id"].endswith("/0.jpg")
    max_dialog.click("Далее")
    max_dialog.send("Галерея")
    max_dialog.click("Пропустить")
    max_dialog.click("Монобукет")
    for _ in range(3):
        max_dialog.click("Готово")
    max_dialog.send("2500")
    max_dialog.click("Всегда в наличии")
    max_dialog.click("Сохранить скрытым")
    listing = Listing.objects.get()
    assert listing.product.images.count() == 4 and not listing.product.is_public


def test_max_and_telegram_grants_and_drafts_are_isolated(max_dialog, shop):
    from market.telegram_catalogue import _handle

    MessengerLink.objects.create(
        shop=shop,
        channel="telegram",
        chat_id="555",
        linked_at=timezone.now(),
        can_manage_catalogue=True,
    )
    _handle(555, 1, {"text": "/add"}, None)
    max_dialog.send("/add")
    max_dialog.send(photo=[{"file_id": "https://i.oneme.ru/photo.jpg"}])
    assert TelegramBouquetDraft.objects.filter(chat_id="555").count() == 2
    assert not TelegramBouquetDraft.objects.get(channel="telegram").data.get("photos")
    MessengerLink.objects.filter(channel="max").update(can_manage_catalogue=False)
    max_dialog.send("/add")
    assert "MAX" in max_dialog.answer["text"] and "право" in max_dialog.answer["text"]


def test_max_revoked_grant_blocks_publication(max_dialog, max_photos):
    from tests.test_telegram_catalogue import through_review

    through_review(max_dialog)
    MessengerLink.objects.filter(channel="max").update(can_manage_catalogue=False)
    max_dialog.click("Опубликовать")
    assert not Listing.objects.exists()
    assert "право" in max_dialog.answer["text"]


def test_max_invitation_binding_stop_and_help(max_dialog, shop):
    from market import messengers

    MessengerLink.objects.filter(channel="max").delete()
    link = messengers.add_shop_number(shop, "max", "+79991112233")
    max_dialog.post(
        {
            "update_type": "bot_started",
            "timestamp": 1,
            "chat_id": 555,
            "user": {"user_id": 777, "first_name": "Анна"},
            "payload": link.code,
        }
    )
    link.refresh_from_db()
    assert link.chat_id == "555" and not link.can_manage_catalogue
    max_dialog.send("Привет")
    assert "приглашение" in max_dialog.answer["text"]
    max_dialog.send("/stop")
    link.refresh_from_db()
    assert not link.enabled and link.chat_id == "555"


def test_max_failed_reply_retries_without_repeating_step(max_dialog, monkeypatch):
    from market import max_bot, messengers

    monkeypatch.setattr(
        max_bot,
        "send_reply",
        lambda receipt: (_ for _ in ()).throw(messengers.MessengerError("offline")),
    )
    max_dialog.send("/add")
    receipt = MaxBotEvent.objects.get()
    assert receipt.status == "pending" and receipt.response is not None
    revision = TelegramBouquetDraft.objects.get(channel="max").revision
    monkeypatch.setattr(max_bot, "send_reply", lambda receipt: None)
    assert max_bot.deliver_pending(min_age=timedelta(0)) == (1, 1)
    assert TelegramBouquetDraft.objects.get(channel="max").revision == revision
    receipt.refresh_from_db()
    assert receipt.status == "sent" and receipt.attempts == 2


def test_max_groups_and_malformed_events_are_not_retained(configured_max, client):
    update = event()
    update["message"]["recipient"]["chat_type"] = "chat"
    assert post(client, update).status_code == 200
    assert (
        post(client, {"update_type": "message_created", "message": []}).status_code
        == 200
    )
    assert not MaxBotEvent.objects.exists()


def test_max_stale_buttons_and_cancel_keep_draft_safe(max_dialog):
    from tests.test_telegram_catalogue import through_category

    through_category(max_dialog)
    max_dialog.click("Готово")
    stale = max_dialog.last_callback.copy()
    max_dialog.click("Готово")
    stale["callback"] = stale["callback"] | {"callback_id": "fresh-click-on-old-button"}
    max_dialog.post(stale)
    assert "устарела" in max_dialog.answer["text"]
    max_dialog.send("/cancel")
    assert TelegramBouquetDraft.objects.get(channel="max").step == "done"
    assert not Listing.objects.exists()


def test_max_own_delivery_hidden_sizes_and_explicit_shop_choice(
    max_dialog, max_photos, shop, other_shop
):
    from tests.test_telegram_catalogue import through_review

    shop.own_delivery = True
    shop.save()
    MessengerLink.objects.create(
        shop=other_shop,
        channel="max",
        chat_id="555",
        linked_at=timezone.now(),
        can_manage_catalogue=True,
    )
    through_review(max_dialog, sizes=True, delivery=True)
    max_dialog.click("Сохранить скрытым")
    listing = Listing.objects.get()
    assert listing.shop == shop and not listing.product.is_public
    assert listing.size_prices == {
        "s": {"pickup": "1500", "delivery": "1900"},
        "m": {"pickup": "2500", "delivery": "2900"},
    }


def test_max_gallery_limit_and_clear(max_dialog):
    max_dialog.send("/add")
    max_dialog.send(
        photo=[{"file_id": f"https://i.oneme.ru/{i}.jpg"} for i in range(21)]
    )
    draft = TelegramBouquetDraft.objects.get(channel="max")
    assert len(draft.data["photos"]) == 20
    assert "20" in max_dialog.answer["text"]
    max_dialog.click("Очистить фото")
    draft.refresh_from_db()
    assert draft.data["photos"] == []


def test_max_rejected_preview_still_delivers_review_and_allows_cancel(
    max_dialog, max_photos, monkeypatch
):
    from django.core.exceptions import ValidationError

    from market import max_transport
    from tests.test_telegram_catalogue import through_review

    def reject_preview(*args, **kwargs):
        raise ValidationError("Фото больше 5 МБ")

    monkeypatch.setattr(max_transport, "preview_photo", reject_preview)
    through_review(max_dialog)
    assert "Букет:" in max_dialog.answer["text"]
    assert not MaxBotEvent.objects.filter(status="pending").exists()
    max_dialog.send("/cancel")
    assert TelegramBouquetDraft.objects.get(channel="max").step == "done"


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgresql
def test_two_max_workers_publish_one_bouquet(max_dialog, max_photos):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import close_old_connections, connection, connections
    from django.test import Client

    from tests.test_telegram_catalogue import through_review

    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL required for actual MAX row-lock checks")
    through_review(max_dialog)
    button = next(
        b
        for row in max_dialog.answer["reply_markup"]["inline_keyboard"]
        for b in row
        if b["text"] == "Опубликовать"
    )
    update = {
        "update_type": "message_callback",
        "timestamp": 1792770009999,
        "callback": {
            "callback_id": "same-publish",
            "user": {"user_id": 777},
            "payload": button["callback_data"],
        },
        "message": {
            "recipient": {"chat_id": 555, "chat_type": "dialog"},
            "body": {"mid": "review", "text": "Публикация"},
        },
    }
    barrier = Barrier(2)

    def publish():
        close_old_connections()
        try:
            barrier.wait(timeout=5)
            return post(Client(), update).status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: publish(), range(2)))
    assert results == [200, 200]
    assert Listing.objects.count() == 1
    assert (
        MaxBotEvent.objects.filter(
            payload__callback__callback_id="same-publish"
        ).count()
        == 1
    )
