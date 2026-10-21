"""Telegram catalogue dialogue using the existing Django product form and Bot API.

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

ADD = "➕ Добавить букет"
MAX_PHOTO = 5 * 1024 * 1024
PAGE_SIZE = 10
NO_ACCESS = (
    "Нужно право на добавление букетов. Попросите владельца магазина открыть "
    "«Настройки» → Telegram и разрешить добавление букетов для вашего чата."
)


def grants(chat_id):
    return MessengerLink.objects.filter(
        channel=MessengerLink.TELEGRAM,
        chat_id=str(chat_id),
        shop__isnull=False,
        linked_at__isnull=False,
        can_manage_catalogue=True,
    ).select_related("shop")


def menu_for_chat(chat_id):
    if grants(chat_id).exists():
        return {"keyboard": [[{"text": ADD}]], "resize_keyboard": True}
    return None


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
            for link in grants(draft.chat_id)
        ]
    elif step == "photo":
        text = "Отправьте фото букета (JPEG, PNG или WebP до 5 МБ), можно файлом."
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
            "reply_markup": menu_for_chat(draft.chat_id),
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
    photo = download_photo(data["photo_id"])
    form = ProductForm(data, {"photo": photo}, shop=draft.link.shop)
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
                action=f"Букет #{listing.pk} добавлен через Telegram · {draft.link.phone}",
            )
    except Exception:
        if listing and listing.photo:
            listing.photo.delete(save=False)
        raise
    draft.step, draft.data = "done", {}
    url = settings.SITE_URL + reverse(
        "market:product-edit", kwargs={"slug": listing.shop.slug, "pk": listing.pk}
    )
    return {
        "method": "sendMessage",
        "chat_id": draft.chat_id,
        "text": f"Букет {'опубликован' if public else 'сохранён скрытым'}: {listing.product.title}\n{url}",
        "reply_markup": menu_for_chat(draft.chat_id),
    }


def _apply_callback(draft, action, value):
    step = draft.step
    if action == "back":
        _advance(draft, backwards=True)
    elif action == "pick" and step == "shop":
        link = (
            grants(draft.chat_id).filter(pk=value).first()
            if value.isdecimal()
            else None
        )
        if not link:
            raise ValidationError(NO_ACCESS)
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
    elif action == "next" and step in {"description", "flowers", "tags", "sizes"}:
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
    if step == "photo":
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
        data["photo_id"] = str(photo["file_id"])
        data["photo_document"] = not bool(photos)
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
def _handle(chat_id, update_id, message, callback):
    text = str(message.get("text", "")).strip() if message else ""
    command = text.split("@", 1)[0].split(" ", 1)[0]
    start = command == "/add" or text == ADD
    draft = (
        TelegramBouquetDraft.objects.select_for_update()
        .filter(chat_id=str(chat_id))
        .first()
    )
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
            "reply_markup": menu_for_chat(chat_id),
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
    allowed = grants(chat_id)
    if not allowed.exists():
        if draft:
            draft.data, draft.step = {}, "done"
            draft.save()
        return {"method": "sendMessage", "chat_id": chat_id, "text": NO_ACCESS}
    if (
        draft
        and update_id <= draft.last_update_id
        and draft.updated_at >= timezone.now() - timedelta(days=1)
    ):
        return draft.last_response or {}
    if start:
        if not draft:
            # get_or_create handles two workers receiving the first /add together.
            draft, _ = TelegramBouquetDraft.objects.get_or_create(chat_id=str(chat_id))
            draft = TelegramBouquetDraft.objects.select_for_update().get(pk=draft.pk)
            if update_id <= draft.last_update_id:
                return draft.last_response
        links = list(allowed)
        draft.link = links[0] if len(links) == 1 else None
        draft.step = "photo" if draft.link else "shop"
        draft.data, draft.token = {"sizes": ["standard"]}, secrets.token_hex(4)
        if draft.link:
            draft.data["own_delivery"] = draft.link.shop.own_delivery
    elif not draft:
        return {
            "method": "sendMessage",
            "chat_id": chat_id,
            "text": "Нажмите «Добавить букет», чтобы начать.",
            "reply_markup": menu_for_chat(chat_id),
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
        return {"method": "sendMessage", "chat_id": chat_id, "text": NO_ACCESS}
    # Lock the selected grant for the entire write, including publication.
    if draft.link_id:
        link = allowed.select_for_update(of=("self",)).filter(pk=draft.link_id).first()
        if not link:
            draft.data, draft.step = {}, "done"
            draft.save()
            return {"method": "sendMessage", "chat_id": chat_id, "text": NO_ACCESS}
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
                        "reply_markup": menu_for_chat(chat_id),
                    }
                else:
                    response = _apply_callback(draft, parts[3], parts[4])
            else:
                _apply_message(draft, message)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        except messengers.MessengerError:
            error = "Telegram временно недоступен. Попробуйте ещё раз; букет пока не сохранён."
    draft.revision += 1
    if draft.step == "review" and previous_step != "review" and not error:
        try:
            document = draft.data.get("photo_document")
            messengers.telegram_call(
                "sendDocument" if document else "sendPhoto",
                chat_id=chat_id,
                **{"document" if document else "photo": draft.data["photo_id"]},
                caption="Фото нового букета",
            )
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
    draft.last_update_id, draft.last_response = update_id, response
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
    if callback:
        try:
            messengers.telegram_call(
                "answerCallbackQuery", callback_query_id=callback.get("id")
            )
        except messengers.MessengerError:
            pass
    return _handle(chat["id"], update_id, message if not callback else None, callback)
