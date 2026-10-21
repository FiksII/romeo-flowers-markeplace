import json
from datetime import timedelta
from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image

from market.models import Flower, Listing, MessengerLink
from tests.test_messengers import bot as configured_bot
from tests.test_messengers import telegram_post, tg_update

bot = configured_bot

pytestmark = pytest.mark.django_db


def test_notification_invitation_does_not_grant_catalogue_access(bot, client, shop):
    MessengerLink.objects.create(
        shop=shop, channel="telegram", chat_id="555", linked_at=timezone.now()
    )
    answer = telegram_post(client, tg_update("/add")).json()
    assert "право" in answer["text"].lower()
    assert "reply_markup" not in answer


def test_allowed_shop_can_start_bouquet_creation(bot, client, shop):
    link = MessengerLink.objects.create(
        shop=shop, channel="telegram", chat_id="555", linked_at=timezone.now()
    )
    # Permission comes from the shop's settings, never from an incoming update.
    MessengerLink.objects.filter(pk=link.pk).update(can_manage_catalogue=True)
    answer = telegram_post(client, tg_update("/add")).json()
    assert "фото" in answer["text"].lower()
    assert "Отмена" in json.dumps(answer, ensure_ascii=False)


class Dialog:
    def __init__(self, client, chat=555):
        self.client, self.chat, self.counter = client, chat, 100
        self.answer = {}

    def send(self, text="", **message):
        update = tg_update(text, self.chat)
        update["message"].update(message)
        return self.post(update)

    def post(self, update):
        self.counter += 1
        update["update_id"] = self.counter
        self.last_update = update
        self.answer = telegram_post(self.client, update).json()
        return self.answer

    def click(self, label):
        buttons = self.answer["reply_markup"]["inline_keyboard"]
        button = next(b for row in buttons for b in row if label in b["text"])
        self.last_callback = {
            "id": str(self.counter),
            "from": {"id": self.chat},
            "message": {
                "message_id": self.counter,
                "chat": {"id": self.chat, "type": "private"},
            },
            "data": button["callback_data"],
        }
        return self.post({"callback_query": self.last_callback})


@pytest.fixture
def dialog(bot, client, shop):
    MessengerLink.objects.create(
        shop=shop,
        channel="telegram",
        chat_id="555",
        linked_at=timezone.now(),
        can_manage_catalogue=True,
    )
    return Dialog(client)


def through_category(dialog):
    dialog.send("/add")
    if "Выберите магазин" in dialog.answer["text"]:
        dialog.click("Тестовые цветы")
    dialog.send(photo=[{"file_id": "photo-1", "file_size": 300}])
    dialog.send("Весенний букет")
    dialog.send("Розы и тюльпаны")
    dialog.click("Монобукеты")


def through_review(dialog, *, sizes=False, delivery=False):
    through_category(dialog)
    dialog.click("Готово")
    dialog.click("8 марта")
    dialog.click("23 февраля")
    dialog.click("Готово")
    if sizes:
        dialog.click("Стандартный")  # remove the default size
        dialog.click("S")
        dialog.click("M")
    dialog.click("Готово")
    for price in ["1500", "2500"] if sizes else ["2500"]:
        dialog.send(price)
        if delivery:
            dialog.send(str(int(price) + 400))
    dialog.send("4")


@pytest.fixture
def photos(monkeypatch, settings, tmp_path):
    from market import telegram_catalogue

    settings.MEDIA_ROOT = tmp_path
    output = BytesIO()
    Image.new("RGB", (20, 20), "red").save(output, "JPEG")
    monkeypatch.setattr(
        telegram_catalogue,
        "download_photo",
        lambda file_id: SimpleUploadedFile(
            "bouquet.jpg", output.getvalue(), "image/jpeg"
        ),
    )


def test_publish_preserves_flowers_tags_and_stock(dialog, photos, shop):
    Flower.objects.update(rank=100)
    rose, _ = Flower.objects.update_or_create(name="Роза красная", defaults={"rank": 0})
    tulip, _ = Flower.objects.update_or_create(name="Тюльпан", defaults={"rank": 1})
    through_category(dialog)
    dialog.click("Роза красная")
    dialog.click("Тюльпан")
    dialog.click("Готово")
    dialog.click("8 марта")
    dialog.click("23 февраля")
    dialog.click("Готово")
    dialog.click("Готово")
    dialog.send("2500,50")
    dialog.send("4")
    assert "Роза красная" in dialog.answer["text"]
    assert "23 февраля" in dialog.answer["text"]
    assert not Listing.objects.exists()
    dialog.click("Опубликовать")
    listing = Listing.objects.get(shop=shop)
    assert listing.product.title == "Весенний букет"
    assert listing.product.is_public
    assert listing.product.description == "Розы и тюльпаны"
    assert set(listing.flowers.all()) == {rose, tulip}
    assert set(listing.tags) == {"march8", "feb23"}
    assert str(listing.pickup_price) == "2500.50"
    assert listing.stockrecord.num_in_stock == 4
    assert listing.photo.read().startswith(b"\xff\xd8")
    # A retried webhook and a second click must not create a second product.
    telegram_post(dialog.client, dialog.last_update)
    dialog.post({"callback_query": dialog.last_callback})
    assert Listing.objects.count() == 1


def test_multiple_sizes_own_delivery_and_hidden_save(dialog, photos, shop):
    shop.own_delivery = True
    shop.save()
    through_review(dialog, sizes=True, delivery=True)
    dialog.click("Сохранить скрытым")
    listing = Listing.objects.get()
    assert not listing.product.is_public
    assert listing.size_prices == {
        "s": {"pickup": "1500", "delivery": "1900"},
        "m": {"pickup": "2500", "delivery": "2900"},
    }


def test_revoked_permission_blocks_publish(dialog, photos):
    through_review(dialog)
    MessengerLink.objects.update(can_manage_catalogue=False)
    dialog.click("Опубликовать")
    assert not Listing.objects.exists()
    assert "право" in dialog.answer["text"].lower()


def test_back_and_cancel_do_not_publish(dialog):
    dialog.send("/add")
    dialog.send(photo=[{"file_id": "photo-1"}])
    dialog.send("Первое название")
    dialog.click("Назад")
    dialog.send("Исправленное название")
    dialog.click("Отмена")
    assert "отменено" in dialog.answer["text"].lower()
    assert not Listing.objects.exists()


@pytest.mark.parametrize("value", ["", "-1", "NaN", "abc", "0", "1000000000"])
def test_invalid_price_keeps_the_price_step(dialog, value):
    through_category(dialog)
    dialog.click("Готово")
    dialog.click("Готово")
    dialog.click("Готово")
    dialog.send(value)
    assert "цен" in dialog.answer["text"].lower()
    dialog.send("1250")
    assert "налич" in dialog.answer["text"].lower()
    assert not Listing.objects.exists()


def test_old_buttons_cannot_change_another_step(dialog):
    through_category(dialog)
    dialog.click("Готово")
    stale = dialog.last_callback.copy()
    dialog.click("Готово")
    dialog.post({"callback_query": stale})
    assert "размер" in dialog.answer["text"].lower()


def test_shop_settings_grants_only_its_telegram_link(
    bot, client, owner, shop, other_shop
):
    client.force_login(owner)
    own = MessengerLink.objects.create(
        shop=shop, channel="telegram", chat_id="555", linked_at=timezone.now()
    )
    other = MessengerLink.objects.create(
        shop=other_shop, channel="telegram", chat_id="777", linked_at=timezone.now()
    )
    url = f"/partner/{shop.slug}/settings/"
    client.post(url, {"action": "catalogue-on", "link": other.pk})
    other.refresh_from_db()
    assert not other.can_manage_catalogue
    client.post(url, {"action": "catalogue-on", "link": own.pk})
    own.refresh_from_db()
    assert own.can_manage_catalogue
    assert "добавление букетов" in client.get(url).content.decode().lower()
    client.post(url, {"action": "renew", "link": own.pk})
    own.refresh_from_db()
    assert not own.can_manage_catalogue


def test_all_flower_pages_remember_selection(dialog):
    through_category(dialog)
    first = next(
        b["text"]
        for row in dialog.answer["reply_markup"]["inline_keyboard"]
        for b in row
        if ":toggle:" in b.get("callback_data", "")
    )
    dialog.click(first)
    dialog.click("Цветы →")
    assert first in dialog.answer["text"]
    dialog.click("← Цветы")
    assert any(
        b["text"] == "✓ " + first
        for row in dialog.answer["reply_markup"]["inline_keyboard"]
        for b in row
    )


def test_multiple_shops_require_explicit_selection(dialog, other_shop):
    MessengerLink.objects.create(
        shop=other_shop,
        channel="telegram",
        chat_id="555",
        phone="+79990000002",
        linked_at=timezone.now(),
        can_manage_catalogue=True,
    )
    dialog.send("/add")
    assert "Выберите магазин" in dialog.answer["text"]
    dialog.click(other_shop.name)
    assert "фото" in dialog.answer["text"]


def test_empty_sizes_cannot_continue(dialog):
    through_category(dialog)
    dialog.click("Готово")
    dialog.click("Готово")
    dialog.click("Стандартный")
    dialog.click("Готово")
    assert "хотя бы один" in dialog.answer["text"]


def test_always_available_bouquet(dialog, photos):
    through_category(dialog)
    dialog.click("Готово")
    dialog.click("Готово")
    dialog.click("Готово")
    dialog.send("2800")
    dialog.click("Всегда в наличии")
    dialog.click("Опубликовать")
    assert Listing.objects.get().always_in_stock


def test_summary_fits_telegram_with_long_description_and_all_flowers(dialog):
    from market.models import TelegramBouquetDraft

    through_review(dialog)
    draft = TelegramBouquetDraft.objects.get()
    draft.data["description"] = "Я" * 1500
    flowers = [Flower.objects.create(name=f"{i:03}" + "Р" * 77) for i in range(50)]
    draft.data["flowers"] = [str(f.pk) for f in flowers]
    draft.save()
    dialog.send("Показать карточку")
    assert len(dialog.answer["text"]) <= 4096


def test_cancel_command_cannot_leave_a_publishable_card(dialog, photos):
    through_review(dialog)
    buttons = dialog.answer["reply_markup"]["inline_keyboard"]
    publish = next(
        b["callback_data"]
        for row in buttons
        for b in row
        if b["text"] == "Опубликовать"
    )
    dialog.send("/cancel")
    answer = dialog.post(
        {
            "callback_query": {
                "id": "c",
                "from": {"id": 555},
                "message": {"chat": {"id": 555, "type": "private"}},
                "data": publish,
            }
        }
    )
    assert not Listing.objects.exists()
    assert "отменено" in answer["text"].lower()


def test_download_enforces_actual_size_and_rejects_untrusted_paths(bot, monkeypatch):
    from django.core.exceptions import ValidationError

    from market import telegram_catalogue

    bot.answer = {"ok": True, "result": {"file_path": "../../secret.jpg"}}
    with pytest.raises(ValidationError):
        telegram_catalogue.download_photo("x")
    bot.answer = {"ok": True, "result": {"file_path": "photos/file.jpg"}}
    monkeypatch.setattr(
        telegram_catalogue,
        "urlopen",
        lambda *args, **kwargs: BytesIO(b"x" * (5 * 1024 * 1024 + 1)),
    )
    with pytest.raises(ValidationError):
        telegram_catalogue.download_photo("x")


def test_download_reads_a_real_telegram_file(bot, monkeypatch):
    from market import telegram_catalogue

    bot.answer = {
        "ok": True,
        "result": {"file_path": "photos/file.jpg", "file_size": 3},
    }
    monkeypatch.setattr(
        telegram_catalogue, "urlopen", lambda *args, **kwargs: BytesIO(b"abc")
    )
    assert telegram_catalogue.download_photo("x").read() == b"abc"


def test_delivery_settings_change_requires_price_review(dialog, photos, shop):
    through_review(dialog)
    shop.own_delivery = True
    shop.save()
    dialog.click("Опубликовать")
    assert not Listing.objects.exists()
    assert "размер" in dialog.answer["text"].lower()
    dialog.click("Готово")
    dialog.send("2500")
    dialog.send("2900")
    dialog.send("3")
    dialog.click("Опубликовать")
    assert Listing.objects.get().delivery_price == 2900


def test_switching_shop_reviews_delivery_prices(dialog, photos, other_shop):
    other_shop.own_delivery = True
    other_shop.save()
    MessengerLink.objects.create(
        shop=other_shop,
        channel="telegram",
        phone="+79990000002",
        chat_id="555",
        linked_at=timezone.now(),
        can_manage_catalogue=True,
    )
    through_review(dialog)
    for _ in range(11):
        if "Выберите магазин" in dialog.answer["text"]:
            break
        dialog.click("Назад")
    dialog.click(other_shop.name)
    dialog.send(photo=[{"file_id": "photo-2"}])
    dialog.send("Другой букет")
    dialog.send("Описание")
    dialog.click("Монобукеты")
    dialog.click("Готово")
    dialog.click("Готово")
    dialog.click("Готово")
    dialog.send("2000")
    dialog.send("2300")
    dialog.send("1")
    dialog.click("Опубликовать")
    assert Listing.objects.get().shop == other_shop


def test_week_old_draft_accepts_randomized_update_number(dialog):
    from market.models import TelegramBouquetDraft

    dialog.counter = 999999
    dialog.send("/add")
    TelegramBouquetDraft.objects.update(updated_at=timezone.now() - timedelta(days=8))
    dialog.counter = 5
    dialog.send("/add")
    dialog.send(photo=[{"file_id": "new-photo"}])
    assert "название" in dialog.answer["text"].lower()


def test_replayed_cancel_does_not_cancel_a_new_dialogue(dialog):
    dialog.send("/add")
    dialog.send("/cancel")
    cancelled = dialog.last_update.copy()
    dialog.send("/add")
    telegram_post(dialog.client, cancelled)
    dialog.send(photo=[{"file_id": "photo-1"}])
    assert "название" in dialog.answer["text"].lower()


def test_deleted_selected_grant_cannot_redirect_draft_to_another_shop(
    dialog, other_shop
):
    through_category(dialog)
    MessengerLink.objects.create(
        shop=other_shop,
        channel="telegram",
        chat_id="555",
        phone="+79990000002",
        linked_at=timezone.now(),
        can_manage_catalogue=True,
    )
    MessengerLink.objects.filter(shop__slug="test-shop").delete()
    dialog.click("Готово")
    assert not Listing.objects.exists()
    assert "право" in dialog.answer["text"].lower()


def test_document_is_previewed_as_document(dialog, photos, bot):
    dialog.send("/add")
    dialog.send(
        document={"file_id": "file-1", "mime_type": "image/jpeg", "file_size": 300}
    )
    dialog.send("Букет файлом")
    dialog.click("Пропустить")
    dialog.click("Монобукеты")
    dialog.click("Готово")
    dialog.click("Готово")
    dialog.click("Готово")
    dialog.send("3000")
    dialog.send("1")
    assert any(
        url.endswith("/sendDocument") and data["document"] == "file-1"
        for url, data in bot.calls
    )
    dialog.click("Опубликовать")
    assert Listing.objects.get().product.title == "Букет файлом"


@pytest.mark.postgresql
@pytest.mark.django_db(transaction=True)
def test_two_workers_publish_one_bouquet(dialog, photos):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import close_old_connections, connection, connections
    from django.test import Client

    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL required for actual row-lock checks")
    through_review(dialog)
    button = next(
        b
        for row in dialog.answer["reply_markup"]["inline_keyboard"]
        for b in row
        if b["text"] == "Опубликовать"
    )
    update = {
        "update_id": 9999,
        "callback_query": {
            "id": "duplicate",
            "from": {"id": 555},
            "message": {"chat": {"id": 555, "type": "private"}},
            "data": button["callback_data"],
        },
    }
    barrier = Barrier(2)

    def publish():
        close_old_connections()
        try:
            barrier.wait(timeout=5)
            return telegram_post(Client(), update).json()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: publish(), range(2)))
    assert all("опубликован" in result["text"] for result in results)
    assert Listing.objects.count() == 1


def test_early_delivery_change_preserves_required_steps(dialog, shop):
    dialog.send("/add")
    shop.own_delivery = True
    shop.save()
    dialog.send(photo=[{"file_id": "photo-1"}])
    assert "название" in dialog.answer["text"].lower()


def test_week_old_randomized_stop_still_disables_notifications(dialog):
    from market.models import TelegramBouquetDraft

    dialog.counter = 999999
    dialog.send("/add")
    TelegramBouquetDraft.objects.update(updated_at=timezone.now() - timedelta(days=8))
    dialog.counter = 5
    dialog.send("/stop")
    assert not MessengerLink.objects.get().enabled


def test_expired_message_does_not_block_next_add(dialog):
    from market.models import TelegramBouquetDraft

    dialog.counter = 999999
    dialog.send("/add")
    TelegramBouquetDraft.objects.update(updated_at=timezone.now() - timedelta(days=8))
    dialog.counter = 5
    dialog.send("Продолжить")
    assert "устарел" in dialog.answer["text"]
    dialog.send("/add")
    dialog.send(photo=[{"file_id": "new-photo"}])
    assert "название" in dialog.answer["text"].lower()
