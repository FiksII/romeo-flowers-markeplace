"""Shared bot catalogue dialogue using the existing Django product form.

Drafts and update receipts share a locked row, so worker restarts and Telegram
retries cannot lose a step or publish a second bouquet. Only explicit shop grants
allow writes; notification invitations alone carry no catalogue permissions.
"""

import re
import secrets
from datetime import timedelta
from urllib.error import URLError
from urllib.request import urlopen

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from market import messengers
from market.bouquet_options import SIZES, TAGS
from market.forms import ProductForm, save_listing
from market.models import (
    AuditEntry,
    Flower,
    Listing,
    MessengerLink,
    TelegramBouquetDraft,
)
from market.photos import MAX_PHOTOS

ADD = "➕ Добавить букет"
MAX_PHOTO = 5 * 1024 * 1024
PAGE_SIZE = 10
NO_ACCESS = (
    "Нужно право на добавление букетов. Попросите владельца магазина открыть "
    "«Настройки» → Telegram и разрешить добавление букетов для вашего чата."
)


def grants(chat_id, channel=MessengerLink.TELEGRAM):
    return MessengerLink.objects.filter(
        channel=channel,
        chat_id=str(chat_id),
        shop__isnull=False,
        linked_at__isnull=False,
        can_manage_catalogue=True,
    ).select_related("shop")


def menu_for_chat(chat_id, channel=MessengerLink.TELEGRAM):
    if grants(chat_id, channel).exists():
        return {"keyboard": [[{"text": ADD}]], "resize_keyboard": True}
    return None


def no_access(channel):
    return NO_ACCESS.replace("Telegram", dict(MessengerLink.CHANNELS)[channel])


def catalogue_photo(draft, file_id):
    if draft.channel == MessengerLink.MAX:
        from market.max_transport import download_photo as download_max_photo

        return download_max_photo(file_id)
    return download_photo(file_id)


def download_photo(file_id):
    """Download only Telegram-owned paths and enforce the site's upload limit."""
    info = messengers.telegram_call("getFile", file_id=file_id)
    path = info.get("file_path", "") if isinstance(info, dict) else ""
    if (
        not re.fullmatch(r"[A-Za-z0-9_/-]+\.[A-Za-z0-9]+", path)
        or ".." in path
        or path.startswith("/")
        or info.get("file_size", 0) > MAX_PHOTO
    ):
        raise ValidationError("Используйте JPEG, PNG или WebP до 5 МБ.")
    url = f"https://api.telegram.org/file/bot{settings.TELEGRAM_BOT_TOKEN}/{path}"
    try:
        with urlopen(url, timeout=15) as response:
            content = response.read(MAX_PHOTO + 1)
    except (URLError, OSError, TimeoutError):
        raise messengers.MessengerError("Не удалось загрузить фото") from None
    if len(content) > MAX_PHOTO:
        raise ValidationError("Фото больше 5 МБ. Вернитесь назад и отправьте другое.")
    # ProductForm verifies the actual image type and size, including corrupt files.
    return SimpleUploadedFile("bouquet." + path.rsplit(".", 1)[1], content)


def _button(draft, label, action, value=""):
    return {
        "text": label,
        "callback_data": f"bq:{draft.token}:{draft.revision}:{action}:{value}",
    }


def _sequence(draft):
    steps = [
        "shop",
        "photo",
        "title",
        "description",
        "category",
        "flowers",
        "tags",
        "sizes",
    ]
    for size, _ in SIZES:
        if size in draft.data.get("sizes", ["standard"]):
            suffix = "" if size == "standard" else f"_{size}"
            steps.append(f"pickup_price{suffix}")
            if draft.link.shop.own_delivery:
                steps.append(f"delivery_price{suffix}")
    return [*steps, "stock", "review"]


def _advance(draft, backwards=False):
    steps = _sequence(draft)
    draft.step = steps[max(0, steps.index(draft.step) + (-1 if backwards else 1))]


def _draft_photos(data):
    if "photos" in data:
        return data["photos"]
    # A draft started by the previous release can still be completed.
    return (
        [
            {
                "file_id": data["photo_id"],
                "document": data.get("photo_document", False),
                "update_id": 0,
            }
        ]
        if data.get("photo_id")
        else []
    )


def _summary(draft):
    data = draft.data
    names = list(
        Flower.objects.filter(pk__in=data.get("flowers", [])).values_list(
            "name", flat=True
        )
    )
    tags = [label for code, label in TAGS if code in data.get("tags", [])]
    text = [
        f"Букет: {data['title']}",
        f"Магазин: {draft.link.shop.name}",
        f"Фотографий: {len(_draft_photos(data))}. Первое фото — главное.",
        f"Категория: {dict(Listing._meta.get_field('category').choices)[data['category']]}",
        f"Описание и состав: {_preview_text(data.get('description') or '—', 900)}",
        f"Цветы: {_preview_text(', '.join(names) or 'не выбраны', 1800)}",
        f"Теги: {', '.join(tags) or 'не выбраны'}",
    ]
    for code, label in SIZES:
        if code in data.get("sizes", ["standard"]):
            suffix = "" if code == "standard" else f"_{code}"
            text.append(f"{label}: самовывоз {data['pickup_price' + suffix]} ₽")
            if draft.link.shop.own_delivery:
                text.append(
                    f"{label}: с доставкой магазина {data['delivery_price' + suffix]} ₽"
                )
    text.append(
        "Наличие: всегда в наличии"
        if data.get("always_in_stock")
        else f"В наличии: {data['stock']} шт."
    )
    text.append("Проверьте карточку перед сохранением.")
    return "\n".join(text)


def _preview_text(text, limit):
    return (
        text
        if len(text) <= limit
        else text[:limit] + "… (полностью сохранится в карточке)"
    )


def _prompt(draft, error=""):
    rows, step, data = [], draft.step, draft.data
    if step == "shop":
        text = "Выберите магазин для нового букета."
        rows = [
            [_button(draft, link.shop.name[:60], "pick", str(link.pk))]
            for link in grants(draft.chat_id, draft.channel)
        ]
    elif step == "photo":
        count = len(_draft_photos(data))
        text = (
            f"Отправьте фотографии букета: альбомом или по одной, можно файлами. "
            f"До {MAX_PHOTOS} фото JPEG, PNG или WebP, каждое до 5 МБ. "
            f"Добавлено: {count}. Первое фото — главное. Когда закончите, нажмите «Далее»."
        )
        if count:
            rows = [
                [_button(draft, "Далее", "next")],
                [_button(draft, "Очистить фото", "clear_photos")],
            ]
    elif step == "title":
        text = "Как называется букет? Напишите название (до 255 символов)."
    elif step == "description":
        text = "Напишите описание и состав букета (до 1500 символов) или пропустите."
        rows = [[_button(draft, "Пропустить", "next")]]
    elif step == "category":
        text = "Выберите категорию букета."
        rows = [
            [_button(draft, label, "pick", code)]
            for code, label in Listing._meta.get_field("category").choices
        ]
    elif step in {"flowers", "tags", "sizes"}:
        if step == "flowers":
            choices = [
                (str(f.pk), f.name) for f in Flower.objects.order_by("rank", "name")
            ]
            last_page = max(0, (len(choices) - 1) // PAGE_SIZE)
            page = min(data.get("flower_page", 0), last_page)
            visible = choices[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
            text = f"Выберите цветы в составе. Можно несколько. Страница {page + 1}/{last_page + 1}."
        else:
            choices = TAGS if step == "tags" else SIZES
            visible = choices
            text = (
                "Выберите дополнительные теги и поводы. Можно несколько."
                if step == "tags"
                else "Выберите размеры букета. Для каждого укажем цену."
            )
        selected = data.get(step, ["standard"] if step == "sizes" else [])
        rows = [
            [_button(draft, ("✓ " if code in selected else "") + label, "toggle", code)]
            for code, label in visible
        ]
        if step == "flowers":
            navigation = []
            if page > 0:
                navigation.append(_button(draft, "← Цветы", "page", str(page - 1)))
            if page < last_page:
                navigation.append(_button(draft, "Цветы →", "page", str(page + 1)))
            if navigation:
                rows.append(navigation)
            names = [label for code, label in choices if code in selected]
            text += "\nВыбраны: " + _preview_text(", ".join(names) or "пока нет", 1800)
        rows.append([_button(draft, "Готово", "next")])
    elif "_price" in step:
        text = (
            ProductForm(shop=draft.link.shop).fields[step].label + ". Напишите сумму."
        )
    elif step == "stock":
        text = "Сколько букетов в наличии? Напишите количество (0 — распродан) или выберите «Всегда в наличии»."
        rows = [[_button(draft, "Всегда в наличии", "always")]]
    elif step == "review":
        text = _summary(draft)
        rows = [
            [_button(draft, "Опубликовать", "publish")],
            [_button(draft, "Сохранить скрытым", "hidden")],
        ]
    else:
        return {
            "method": "sendMessage",
            "chat_id": draft.chat_id,
            "text": "Добавление завершено. Для нового букета нажмите «Добавить букет».",
            "reply_markup": menu_for_chat(draft.chat_id, draft.channel),
        }
    controls = []
    if step != "shop":
        controls.append(_button(draft, "Назад", "back"))
    controls.append(_button(draft, "Отмена", "cancel"))
    rows.append(controls)
    return {
        "method": "sendMessage",
        "chat_id": draft.chat_id,
        "text": (error + "\n\n" if error else "") + text,
        "reply_markup": {"inline_keyboard": rows},
    }


def _publish(draft, public):
    data = draft.data | {"is_public": public}
    uploads = [
        catalogue_photo(draft, photo["file_id"]) for photo in _draft_photos(data)
    ]
    if not uploads:
        raise ValidationError("Добавьте хотя бы одну фотографию букета.")
    form = ProductForm(data, {"photo": uploads}, shop=draft.link.shop)
    if not form.is_valid():
        raise ValidationError(
            [str(e) for errors in form.errors.values() for e in errors]
        )
    listing = None
    try:
        with transaction.atomic():
            listing = save_listing(draft.link.shop, form.cleaned_data)
            AuditEntry.objects.create(
                shop=draft.link.shop,
                action=f"Букет #{listing.pk} добавлен через {dict(MessengerLink.CHANNELS)[draft.channel]} · {draft.link.phone}",
            )
    except Exception:
        if listing:
            for storage, name in getattr(listing, "_uploaded_photo_files", []):
                storage.delete(name)
        raise
    draft.step, draft.data = "done", {}
    url = settings.SITE_URL + reverse(
        "market:product-edit", kwargs={"slug": listing.shop.slug, "pk": listing.pk}
    )
    return {
        "method": "sendMessage",
        "chat_id": draft.chat_id,
        "text": f"Букет {'опубликован' if public else 'сохранён скрытым'}: {listing.product.title}\n{url}",
        "reply_markup": menu_for_chat(draft.chat_id, draft.channel),
    }


def _apply_callback(draft, action, value, update_id):
    step = draft.step
    if action == "back":
        _advance(draft, backwards=True)
    elif action == "pick" and step == "shop":
        link = (
            grants(draft.chat_id, draft.channel).filter(pk=value).first()
            if value.isdecimal()
            else None
        )
        if not link:
            raise ValidationError(no_access(draft.channel))
        draft.link, draft.step = link, "photo"
        draft.data["own_delivery"] = link.shop.own_delivery
    elif action == "pick" and step == "category":
        draft.data[step] = ProductForm(shop=draft.link.shop).fields[step].clean(value)
        _advance(draft)
    elif action == "toggle" and step in {"flowers", "tags", "sizes"}:
        choices = (
            {str(pk) for pk in Flower.objects.values_list("pk", flat=True)}
            if step == "flowers"
            else dict(TAGS if step == "tags" else SIZES)
        )
        if value not in choices:
            raise ValidationError("Выберите вариант из списка.")
        selected = draft.data.setdefault(step, ["standard"] if step == "sizes" else [])
        if value in selected:
            selected.remove(value)
        else:
            selected.append(value)
    elif action == "page" and step == "flowers" and value.isdecimal():
        draft.data["flower_page"] = min(
            int(value), max(0, (Flower.objects.count() - 1) // PAGE_SIZE)
        )
    elif action == "clear_photos" and step == "photo":
        draft.data["photos"] = []
        draft.data["photo_start_update_id"] = update_id
        draft.data["photo_album_ids"] = []
        draft.data.pop("photo_id", None)
        draft.data.pop("photo_document", None)
    elif action == "next" and step in {
        "photo",
        "description",
        "flowers",
        "tags",
        "sizes",
    }:
        if step == "photo" and not _draft_photos(draft.data):
            raise ValidationError("Добавьте хотя бы одну фотографию букета.")
        if step == "sizes" and not draft.data.get("sizes"):
            raise ValidationError("Выберите хотя бы один размер.")
        if step == "description":
            draft.data[step] = ""
        _advance(draft)
    elif action == "always" and step == "stock":
        draft.data["always_in_stock"] = True
        draft.data["stock"] = None
        _advance(draft)
    elif action in {"publish", "hidden"} and step == "review":
        return _publish(draft, action == "publish")
    return None


def _apply_message(draft, message):
    step, data = draft.step, draft.data
    text = str(message.get("text", "")).strip()
    form = ProductForm(shop=draft.link.shop) if draft.link_id else None
    known_album = message.get("media_group_id") in data.get("photo_album_ids", [])
    if step == "photo" or known_album:
        photos = message.get("photo")
        photo = (
            photos[-1]
            if isinstance(photos, list) and photos
            else message.get("document")
        )
        if not isinstance(photo, dict) or not photo.get("file_id"):
            raise ValidationError("Отправьте фотографию букета.")
        if photo.get("file_size", 0) > MAX_PHOTO:
            raise ValidationError("Используйте JPEG, PNG или WebP до 5 МБ.")
        selected = list(_draft_photos(data))
        file_id = str(photo["file_id"])
        if any(item["file_id"] == file_id for item in selected):
            data.setdefault("photo_update_ids", []).append(
                message["_catalogue_update_id"]
            )
            return
        if len(selected) >= MAX_PHOTOS:
            raise ValidationError(
                f"Можно добавить не больше {MAX_PHOTOS} фотографий. Удалите лишние кнопкой «Очистить фото»."
            )
        selected.append(
            {
                "file_id": file_id,
                "document": not bool(photos),
                "update_id": message["_catalogue_update_id"],
            }
        )
        selected.sort(key=lambda item: item.get("update_id", 0))
        data["photos"] = selected
        album_id = message.get("media_group_id")
        if album_id and album_id not in data.setdefault("photo_album_ids", []):
            data["photo_album_ids"].append(album_id)
        data.setdefault("photo_update_ids", []).append(message["_catalogue_update_id"])
        data["photo_id"] = selected[0]["file_id"]
        data["photo_document"] = selected[0]["document"]
        return
    elif step in {"title", "description"}:
        if step == "description" and len(text) > 1500:
            raise ValidationError("Описание должно быть не длиннее 1500 символов.")
        data[step] = form.fields[step].clean(text)
    elif "_price" in step:
        amount = form.fields[step].clean(text.replace(",", "."))
        if amount is None:
            raise ValidationError("Укажите цену числом.")
        data[step] = format(amount, "f")
    elif step == "stock":
        stock = form.fields["stock"].clean(text)
        if stock is None:
            raise ValidationError("Укажите количество или выберите «Всегда в наличии».")
        data["stock"], data["always_in_stock"] = stock, False
    else:
        raise ValidationError("Выберите действие кнопкой под последним сообщением.")
    _advance(draft)


@transaction.atomic
def _handle(chat_id, update_id, message, callback, channel=MessengerLink.TELEGRAM):
    text = str(message.get("text", "")).strip() if message else ""
    command = text.split("@", 1)[0].split(" ", 1)[0]
    start = command == "/add" or text == ADD
    draft = (
        TelegramBouquetDraft.objects.select_for_update()
        .filter(chat_id=str(chat_id), channel=channel)
        .first()
    )
    if draft and channel == MessengerLink.MAX and not draft.token:
        # MAX also uses this row as the durable lock for invitation/help events.
        draft = None
    if draft and draft.updated_at < timezone.now() - timedelta(days=1):
        # Telegram can randomize update IDs after a week without updates.
        draft.last_update_id, draft.last_response = -1, {}
    if command in {"/cancel", "/stop"}:
        if draft and update_id <= draft.last_update_id:
            return draft.last_response or {}
        response = {
            "method": "sendMessage",
            "chat_id": chat_id,
            "text": "Добавление отменено.",
            "reply_markup": menu_for_chat(chat_id, channel),
        }
        if draft:
            draft.step, draft.data, draft.last_response = "done", {}, response
            draft.last_update_id = max(update_id, draft.last_update_id)
            draft.save()
        return None if command == "/stop" else response
    if command == "/start":
        return None
    if not draft and not start and not callback:
        return None
    allowed = grants(chat_id, channel)
    if not allowed.exists():
        if draft:
            draft.data, draft.step = {}, "done"
            draft.save()
        return {"method": "sendMessage", "chat_id": chat_id, "text": no_access(channel)}
    if draft and update_id in draft.data.get("photo_update_ids", []):
        return draft.last_response or {}
    # Album parts can reach concurrent webhook workers out of update-ID order.
    # Accept media in the photo step or late parts of an already accepted album.
    # Clearing the photos advances the boundary and forgets all previous albums.
    active_photo = (
        draft
        and message
        and (
            draft.step == "photo"
            or message.get("media_group_id") in draft.data.get("photo_album_ids", [])
        )
        and (message.get("photo") or message.get("document"))
        and update_id > draft.data.get("photo_start_update_id", draft.last_update_id)
    )
    if (
        draft
        and update_id <= draft.last_update_id
        and not active_photo
        and draft.updated_at >= timezone.now() - timedelta(days=1)
    ):
        return draft.last_response or {}
    if start:
        if not draft:
            # get_or_create handles two workers receiving the first /add together.
            draft, _ = TelegramBouquetDraft.objects.get_or_create(
                chat_id=str(chat_id), channel=channel
            )
            draft = TelegramBouquetDraft.objects.select_for_update().get(pk=draft.pk)
            if update_id <= draft.last_update_id:
                return draft.last_response
        links = list(allowed)
        draft.link = links[0] if len(links) == 1 else None
        draft.step = "photo" if draft.link else "shop"
        draft.data, draft.token = (
            {"sizes": ["standard"], "photo_start_update_id": update_id},
            secrets.token_hex(4),
        )
        if draft.link:
            draft.data["own_delivery"] = draft.link.shop.own_delivery
    elif not draft:
        return {
            "method": "sendMessage",
            "chat_id": chat_id,
            "text": "Нажмите «Добавить букет», чтобы начать.",
            "reply_markup": menu_for_chat(chat_id, channel),
        }
    elif draft.step == "done":
        return draft.last_response or _prompt(draft)
    elif draft.updated_at < timezone.now() - timedelta(days=1):
        draft.step, draft.data = "done", {}
        response = {
            "method": "sendMessage",
            "chat_id": chat_id,
            "text": "Черновик устарел. Начните заново командой /add.",
        }
        draft.last_update_id, draft.last_response = update_id, response
        draft.save()
        return response
    if draft.step not in {"shop", "done"} and not draft.link_id:
        draft.data, draft.step, draft.last_response = {}, "done", {}
        draft.save()
        return {"method": "sendMessage", "chat_id": chat_id, "text": no_access(channel)}
    # Lock the selected grant for the entire write, including publication.
    if draft.link_id:
        link = allowed.select_for_update(of=("self",)).filter(pk=draft.link_id).first()
        if not link:
            draft.data, draft.step = {}, "done"
            draft.save()
            return {
                "method": "sendMessage",
                "chat_id": chat_id,
                "text": no_access(channel),
            }
        draft.link = link
    response, error = None, ""
    previous_step = draft.step
    configuration_changed = (
        draft.link_id
        and draft.step != "shop"
        and draft.data.get("own_delivery", draft.link.shop.own_delivery)
        != draft.link.shop.own_delivery
    )
    if configuration_changed:
        draft.data["own_delivery"] = draft.link.shop.own_delivery
        if "_price" in draft.step or draft.step in {"stock", "review"}:
            draft.step = "sizes"
            error = "Условия доставки магазина изменились. Проверьте размеры и заново укажите цены."
        else:
            configuration_changed = False
    if not start and not configuration_changed:
        try:
            if callback:
                parts = callback["data"].split(":", 4)
                if (
                    len(parts) != 5
                    or parts[1] != draft.token
                    or parts[2] != str(draft.revision)
                ):
                    error = "Эта кнопка устарела. Используйте кнопки ниже."
                elif parts[3] == "cancel":
                    draft.step, draft.data = "done", {}
                    response = {
                        "method": "sendMessage",
                        "chat_id": chat_id,
                        "text": "Добавление отменено.",
                        "reply_markup": menu_for_chat(chat_id, channel),
                    }
                else:
                    response = _apply_callback(draft, parts[3], parts[4], update_id)
            else:
                _apply_message(draft, message | {"_catalogue_update_id": update_id})
        except ValidationError as exc:
            error = " ".join(exc.messages)
        except messengers.MessengerError:
            error = f"{dict(MessengerLink.CHANNELS)[channel]} временно недоступен. Попробуйте ещё раз; букет пока не сохранён."
    draft.revision += 1
    if draft.step == "review" and previous_step != "review" and not error:
        try:
            document = draft.data.get("photo_document")
            if channel == MessengerLink.MAX:
                from market.max_transport import preview_photo

                preview_photo(chat_id, draft.data["photo_id"], document=bool(document))
            else:
                messengers.telegram_call(
                    "sendDocument" if document else "sendPhoto",
                    chat_id=chat_id,
                    **{"document" if document else "photo": draft.data["photo_id"]},
                    caption="Фото нового букета",
                )
        except ValidationError as exc:
            error = " ".join(exc.messages)
        except messengers.MessengerError:
            pass  # The complete editable summary is still delivered below.
    response = response or _prompt(draft, error)
    # Multiple selections update the current keyboard instead of filling the chat.
    if (
        callback
        and draft.step == previous_step
        and draft.step in {"flowers", "tags", "sizes"}
        and callback.get("message", {}).get("text")
    ):
        response["method"] = "editMessageText"
        response["message_id"] = callback["message"]["message_id"]
    draft.last_update_id, draft.last_response = (
        max(update_id, draft.last_update_id),
        response,
    )
    draft.save()
    return response


def handle_update(update):
    if not isinstance(update, dict):
        return None
    callback = update.get("callback_query")
    if callback:
        if not isinstance(callback, dict) or not str(
            callback.get("data", "")
        ).startswith("bq:"):
            return None
        message = callback.get("message") or {}
    else:
        message = update.get("message") or {}
    if not isinstance(message, dict):
        return None
    chat = message.get("chat") or {}
    if chat.get("type") != "private" or not chat.get("id"):
        return None
    sender = (callback or message).get("from") or {}
    if str(sender.get("id")) != str(chat["id"]):
        return {}
    update_id = update.get("update_id")
    if not isinstance(update_id, int):
        return {}
    response = _handle(
        chat["id"], update_id, message if not callback else None, callback
    )
    if callback:
        messengers.acknowledge_telegram_callback_later(callback.get("id"))
    return response
