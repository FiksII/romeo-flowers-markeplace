"""Order notifications in Telegram and VK.

A person links a chat by sending a one-time code to the Telegram bot (``/start CODE``
from a t.me link) or to the VK community (a vk.me link or the code as a message). New
order parts become ``MessengerMessage`` rows in the same transaction as the order or
its payment; after the commit a thread delivers them, and ``send_notifications`` (run
by the minute timer) retries what is left.

Both APIs need only a couple of plain HTTPS calls, so they use urllib like the other
providers here; bot frameworks (python-telegram-bot, aiogram, vk_api) would add async
runtimes or polling loops the site does not need.
Docs: https://core.telegram.org/bots/api and https://dev.vk.com/ru/api/callback/getting-started
"""

import json
import logging
import re
import secrets
import threading
from datetime import timedelta
from io import BytesIO
from math import ceil
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import close_old_connections, connection, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac
from PIL import Image, ImageOps
from urllib3.filepost import encode_multipart_formdata

from market.models import MessengerLink, MessengerMessage
from market.templatetags.market_tags import romeo_payment_instruction, rub

logger = logging.getLogger(__name__)

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
# Invitation links are forwarded by the shop, so they live a week.
CODE_LIFETIME = timedelta(days=7)
MAX_SHOP_NUMBERS = 10
CODE_PATTERN = re.compile(rf"\b[{CODE_ALPHABET}]{{{CODE_LENGTH}}}\b")
MAX_ATTEMPTS = 5
VK_API_VERSION = "5.199"


class MessengerError(Exception):
    """The messenger could not take the message now; it may work later."""


class RecipientGone(MessengerError):
    """The person blocked the bot or forbade the community to write; stop sending."""


class TelegramMediaError(MessengerError):
    """Telegram rejected the media; the order can still be delivered as text."""


# Channels


def telegram_enabled():
    return bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_BOT_USERNAME)


def vk_enabled():
    return bool(settings.VK_GROUP_ID and settings.VK_GROUP_TOKEN)


def channel_enabled(channel):
    return telegram_enabled() if channel == MessengerLink.TELEGRAM else vk_enabled()


def telegram_webhook_secret():
    # Telegram repeats it in X-Telegram-Bot-Api-Secret-Token; allowed: A-Z a-z 0-9 _ -.
    return salted_hmac(
        "market.telegram-webhook", settings.TELEGRAM_BOT_TOKEN
    ).hexdigest()[:48]


def vk_callback_secret():
    return salted_hmac("market.vk-callback", settings.VK_GROUP_TOKEN).hexdigest()[:32]


def _post_json(url, params, timeout=10):
    headers = {}
    if any(isinstance(value, tuple) for value in params.values()):
        body, headers["Content-Type"] = encode_multipart_formdata(params)
    else:
        body = urlencode(params).encode()
    request = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as error:
        # Telegram answers errors with 4xx and a JSON description.
        try:
            return json.load(error)
        except (ValueError, OSError):
            raise MessengerError(f"HTTP {error.code}") from None
    except (URLError, TimeoutError, OSError, ValueError) as error:
        raise MessengerError(type(error).__name__) from None


def telegram_call(method, **params):
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/{method}"
    payload = {
        key: json.dumps(value) if isinstance(value, dict | list) else value
        for key, value in params.items()
    }
    result = _post_json(url, payload)
    if not isinstance(result, dict) or not result.get("ok"):
        code = result.get("error_code") if isinstance(result, dict) else None
        text = str(result.get("description", "")) if isinstance(result, dict) else ""
        if code == 403 or "chat not found" in text:
            raise RecipientGone(text[:200] or "forbidden")
        if code == 400 and method in {"sendPhoto", "sendMediaGroup"}:
            raise TelegramMediaError("Telegram rejected order photos")
        raise MessengerError(f"{code}: {text}"[:200])
    return result.get("result")


def vk_call(method, **params):
    payload = {"access_token": settings.VK_GROUP_TOKEN, "v": VK_API_VERSION, **params}
    result = _post_json(f"https://api.vk.ru/method/{method}", payload)
    if not isinstance(result, dict):
        raise MessengerError("bad response")
    if "error" in result:
        error = result["error"] if isinstance(result["error"], dict) else {}
        code, text = error.get("error_code"), str(error.get("error_msg", ""))
        # 900: the user blacklisted the community; 901: no permission to write.
        if code in {900, 901, 902}:
            raise RecipientGone(f"{code}: {text}"[:200])
        raise MessengerError(f"{code}: {text}"[:200])
    return result.get("response")


def _photo_upload(photo):
    """Load a trusted listing URL ourselves when Telegram cannot fetch it."""
    try:
        with urlopen(photo, timeout=15) as response:
            data = response.read(10 * 1024 * 1024 + 1)
    except HTTPError as error:
        if error.code in {400, 401, 403, 404, 410}:
            raise TelegramMediaError("Bouquet photo is unavailable") from None
        raise MessengerError("Could not load the bouquet photo") from None
    except (URLError, TimeoutError, OSError):
        raise MessengerError("Could not load the bouquet photo") from None
    if len(data) > 10 * 1024 * 1024:
        raise TelegramMediaError("Bouquet photo is too large")
    try:
        with Image.open(BytesIO(data)) as source:
            if source.width * source.height > 25_000_000:
                raise TelegramMediaError("Bouquet photo has too many pixels")
            image = ImageOps.exif_transpose(source)
            image.thumbnail((2000, 2000))
            # A white background preserves transparent PNGs and Telegram's 20:1 limit.
            width = max(image.width, ceil(image.height / 20))
            height = max(image.height, ceil(image.width / 20))
            canvas = Image.new("RGB", (width, height), "white")
            rgba = image.convert("RGBA")
            canvas.paste(
                rgba, ((width - image.width) // 2, (height - image.height) // 2), rgba
            )
            output = BytesIO()
            canvas.save(output, format="JPEG", quality=85)
    except (OSError, ValueError, Image.DecompressionBombError):
        raise TelegramMediaError("Bouquet photo cannot be decoded") from None
    return ("bouquet.jpg", output.getvalue(), "image/jpeg")


def _telegram_photos(link, photos, caption, upload=False):
    if len(photos) == 1:
        return telegram_call(
            "sendPhoto",
            chat_id=link.chat_id,
            photo=_photo_upload(photos[0]) if upload else photos[0],
            caption=caption,
        )
    media, files = [], {}
    for index, photo in enumerate(photos):
        key = f"photo{index}"
        if upload:
            files[key] = _photo_upload(photo)
        media.append({"type": "photo", "media": f"attach://{key}" if upload else photo})
    media[0]["caption"] = caption
    return telegram_call("sendMediaGroup", chat_id=link.chat_id, media=media, **files)


def send(link, text, photos=(), caption=None):
    if link.channel == MessengerLink.TELEGRAM:
        if not telegram_enabled():
            raise MessengerError("Telegram is not configured")
        if photos:
            try:
                caption = caption if caption is not None else text
                try:
                    _telegram_photos(link, photos, caption)
                except TelegramMediaError:
                    logger.info("Telegram rejected photo URLs; uploading image files")
                    _telegram_photos(link, photos, caption, upload=True)
                return
            except TelegramMediaError:
                logger.warning("Order photos rejected; sending text notification")
        telegram_call(
            "sendMessage",
            chat_id=link.chat_id,
            text=text,
            link_preview_options={"is_disabled": True},
        )
    else:
        if not vk_enabled():
            raise MessengerError("VK is not configured")
        vk_call(
            "messages.send",
            peer_id=link.chat_id,
            message=text,
            random_id=secrets.randbelow(2**31),
            dont_parse_links=1,
        )


# Linking


def _new_code():
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        if not MessengerLink.objects.filter(code=code).exists():
            return code


def ensure_code(link, now=None):
    """Give an unlinked row a valid invitation code; returns the row."""
    now = now or timezone.now()
    if link.is_linked:
        return link
    if (
        not link.code
        or not link.code_created_at
        or link.code_created_at < now - CODE_LIFETIME
    ):
        link.code, link.code_created_at = _new_code(), now
        link.save(update_fields=["code", "code_created_at"])
    return link


def link_with_code(user, channel, now=None):
    """The superadmin's personal link for ``channel``, with a code while unlinked."""
    link, _ = MessengerLink.objects.get_or_create(user=user, channel=channel)
    return ensure_code(link, now)


def new_invitation(link, now=None):
    """Forget the chat (if any) and issue a fresh link; the old one stops working."""
    link.chat_id = link.title = ""
    link.code, link.code_created_at = _new_code(), now or timezone.now()
    link.enabled, link.linked_at = True, None
    link.can_manage_catalogue = False
    link.save()
    return link


def add_shop_number(shop, channel, phone, now=None):
    """Add a number to the shop's list; ValueError if it is there or the list is full."""
    numbers = MessengerLink.objects.filter(shop=shop, channel=channel)
    if numbers.filter(phone=phone).exists():
        raise ValueError("Этот номер уже есть в списке.")
    if numbers.count() >= MAX_SHOP_NUMBERS:
        raise ValueError(f"В списке может быть не больше {MAX_SHOP_NUMBERS} номеров.")
    link = MessengerLink.objects.create(shop=shop, channel=channel, phone=phone)
    return ensure_code(link, now)


def connect_url(link):
    if link.channel == MessengerLink.TELEGRAM:
        return f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start={link.code}"
    return f"https://vk.me/club{settings.VK_GROUP_ID}?ref={link.code}"


def find_code(*texts):
    for text in texts:
        match = CODE_PATTERN.search(str(text or "").upper())
        if match:
            return match.group(0)
    return None


@transaction.atomic
def confirm_link(channel, code, chat_id, title, now=None):
    """Attach the chat to the row whose invitation has ``code``; None if unknown."""
    now = now or timezone.now()
    link = (
        MessengerLink.objects.select_for_update(of=("self",))
        .select_related("user", "shop")
        .filter(channel=channel, code=code, code_created_at__gte=now - CODE_LIFETIME)
        .first()
    )
    if link is None or (link.user_id and not link.user.is_active):
        return None
    link.chat_id, link.title = str(chat_id), title[:100]
    link.enabled, link.linked_at = True, now
    link.code = link.code_created_at = None
    link.save()
    return link


def forget_chat(channel, chat_id):
    """The person blocked the bot or forbade messages: stop writing to that chat."""
    return MessengerLink.objects.filter(channel=channel, chat_id=str(chat_id)).update(
        enabled=False
    )


def linked_reply(link):
    if link.shop_id:
        scope = f"новые заказы магазина «{link.shop.name}»"
    else:
        scope = (
            "заказы с доставкой Ромео из магазинов, отмеченных в списке "
            "магазинов супер администратора"
        )
    return (
        f"Готово! Сюда будут приходить {scope}.\n"
        "Отключить уведомления можно командой /stop или в кабинете Ромео."
    )


BAD_CODE_REPLY = (
    "Ссылка не подошла или устарела. Попросите новую ссылку в кабинете Ромео "
    "(раздел «Настройки»)."
)


def help_reply():
    return (
        "Это бот уведомлений Ромео о заказах магазинов-партнёров.\n"
        "Чтобы получать сообщения, откройте ссылку-приглашение: её выдаёт магазин "
        "в кабинете Ромео, раздел «Настройки»."
    )


def handle_telegram_update(update):
    """Process a webhook update; returns the reply text for the chat, if any."""
    message = update.get("message") if isinstance(update, dict) else None
    if not isinstance(message, dict):
        return None, None
    chat = message.get("chat") or {}
    if chat.get("type") != "private" or "id" not in chat:
        return None, None
    text = str(message.get("text") or "")
    if text.startswith("/stop"):
        forget_chat(MessengerLink.TELEGRAM, chat["id"])
        return chat["id"], "Уведомления в этот чат отключены."
    code = find_code(text)
    if code:
        sender = message.get("from") or {}
        title = (
            f"@{sender['username']}"
            if sender.get("username")
            else " ".join(
                filter(None, [sender.get("first_name"), sender.get("last_name")])
            )
        )
        link = confirm_link(
            MessengerLink.TELEGRAM, code, chat["id"], title or "Telegram"
        )
        if link:
            return chat["id"], linked_reply(link)
        return chat["id"], BAD_CODE_REPLY
    return chat["id"], help_reply()


def handle_vk_event(event):
    """Process a Callback API event (the secret is already checked); returns a reply."""
    kind, data = event.get("type"), event.get("object") or {}
    if kind == "message_deny":
        forget_chat(MessengerLink.VK, data.get("user_id"))
        return None, None
    if kind != "message_new":
        return None, None
    message = data.get("message") or {}
    peer, sender = message.get("peer_id"), message.get("from_id")
    # Only private dialogs: group chats have peer ids above 2e9.
    if not peer or peer != sender or int(peer) <= 0:
        return None, None
    code = find_code(message.get("ref"), message.get("text"))
    if code:
        link = confirm_link(MessengerLink.VK, code, peer, _vk_name(peer))
        if link:
            return peer, linked_reply(link)
        return peer, BAD_CODE_REPLY
    return peer, help_reply()


def _vk_name(user_id):
    try:
        users = vk_call("users.get", user_ids=user_id) or []
        if users:
            return f"{users[0].get('first_name', '')} {users[0].get('last_name', '')}".strip()
    except MessengerError:
        pass
    return f"id{user_id}"


def reply_later(channel, chat_id, text):
    """Answer a chat outside the webhook request (VK wants a quick "ok")."""
    link = MessengerLink(channel=channel, chat_id=str(chat_id))
    _in_background(_reply, link, text)


def _reply(link, text):
    try:
        send(link, text)
    except MessengerError as error:
        logger.warning("Could not answer %s chat: %s", link.channel, error)


# Order notifications


def _order_url(part):
    if not settings.SITE_URL:
        return ""
    return settings.SITE_URL + reverse(
        "market:partner-order", args=[part.shop.slug, part.pk]
    )


def order_text(part, for_admin=False, compact=False):
    lines = [
        f"Новый заказ № {part.order.number}",
        part.shop.name[:80] if compact else part.shop.name,
    ]
    when = "время уточнить у получателя"
    if part.slot_start:
        start, end = (
            timezone.localtime(part.slot_start),
            timezone.localtime(part.slot_end),
        )
        when = f"{start:%d.%m}, {start:%H:%M}–{end:%H:%M}"
    lines.append(f"{part.receiving_label} · {when}")
    items = list(part.items.all())
    for item in [] if compact else items:
        size = f", {item.size_label}" if item.size_label else ""
        lines.append(f"• {item.title}{size} × {item.quantity}")
    if compact:
        lines.append(
            f"Букетов: {sum(item.quantity for item in items)}. Полный состав — в заказе."
        )
    if for_admin:
        name = part.contact_name[:60] if compact else part.contact_name
        lines.append(f"Получатель: {name} · {part.contact_phone}")
        if part.method == "delivery":
            if part.needs_delivery_confirmation:
                lines.append(
                    "Уточните у получателя адрес"
                    + (
                        " и время доставки."
                        if not part.slot_start
                        else ". Интервал доставки уже выбран."
                    )
                )
            elif not compact:
                lines.append(f"Адрес: {part.address}")
                if part.address_details:
                    lines.append(part.address_details)
                if part.delivery_contact == "no_call":
                    lines.append("Доставить без звонка получателю.")
            if not compact and part.instructions:
                lines.append(f"Комментарий: {part.instructions}")
        lines.append(romeo_payment_instruction(part))
        lines.append(
            f"Сумма заказа {rub(part.total)}, магазину {rub(part.partner_total)}"
        )
    else:
        if part.payment_status == "refunded":
            lines.append(
                "Оплата возвращена. Не выдавайте заказ без уточнения в кабинете."
            )
        elif part.payment_status == "paid":
            paid = (
                "Оплачено онлайн"
                if part.payment_method == "online"
                else "Оплата получена"
            )
            lines.append(f"{paid}. Получать деньги с клиента не нужно.")
        elif part.method == "pickup" and part.payment_method == "cash":
            lines.append(f"Получить с покупателя {rub(part.total)}")
        elif part.payment_method == "online":
            lines.append("Ожидается онлайн-оплата. Получать деньги с клиента не нужно.")
        else:
            lines.append("Получать деньги с клиента не нужно.")
        if part.platform_collects and part.payment_status != "refunded":
            lines.append(f"Вам {rub(part.partner_total)}")
    url = _order_url(part)
    if url:
        lines.append(f"Открыть заказ: {url}")
        if for_admin and part.can_set_destination:
            label = (
                "Уточнить место получения"
                if part.needs_delivery_confirmation
                else "Изменить место получения"
            )
            lines.append(f"{label}: {url}destination/")
    return "\n".join(lines)


def order_photos(part):
    """Fresh storage URLs (including S3 signatures) for the ordered bouquets."""
    photos = []
    for item in part.items.select_related("listing"):
        listing = item.listing
        if listing is None or not (listing.photo or listing.seed_image):
            continue
        photo = urljoin(settings.SITE_URL + "/", listing.image_url)
        parsed = urlsplit(photo)
        # Telegram cannot send the SVG placeholder as a photo.
        if parsed.scheme not in {"https", "http"} or parsed.path.lower().endswith(
            ".svg"
        ):
            continue
        if photo not in photos:
            photos.append(photo)
        if len(photos) == 10:
            break
    return photos


def admin_wants(part):
    """Superadmins hear about the Romeo deliveries of the shops ticked in their list."""
    return (
        part.shop.notify_admins
        and part.method == "delivery"
        and part.delivery_owner == "platform"
    )


def recipients(part):
    """The shop's numbers and, for a Romeo delivery of a ticked shop, superadmins."""
    owners = Q(shop_id=part.shop_id)
    if admin_wants(part):
        owners |= Q(user__is_superuser=True, user__is_active=True)
    channels = [
        channel
        for channel in (MessengerLink.TELEGRAM, MessengerLink.VK)
        if channel_enabled(channel)
    ]
    return (
        MessengerLink.objects.filter(owners)
        .filter(channel__in=channels, enabled=True)
        .exclude(chat_id="")
        .distinct()
    )


def notify_new_part(part):
    """Queue «new order» messages for ``part``; delivery starts after the commit."""
    created, chats = [], set()
    # Superadmins first: a chat that is on both lists gets the fuller message once.
    for link in sorted(recipients(part), key=lambda link: link.user_id is None):
        if (link.channel, link.chat_id) in chats:
            continue
        chats.add((link.channel, link.chat_id))
        message, made = MessengerMessage.objects.get_or_create(
            link=link,
            part=part,
            defaults={"text": order_text(part, for_admin=link.user_id is not None)},
        )
        if made:
            created.append(message.pk)
    if created:
        transaction.on_commit(lambda: _in_background(deliver_many, created))
    return created


def send_test(link):
    """Send a check message right away; raises MessengerError on failure."""
    send(link, "Проверка: уведомления Ромео о новых заказах приходят в этот чат.")


def deliver(message_id, now=None):
    """Send one pending message; safe to call from several processes at once."""
    with transaction.atomic():
        message = (
            MessengerMessage.objects.select_for_update(skip_locked=True, of=("self",))
            .select_related("link", "part__shop")
            .filter(pk=message_id, status=MessengerMessage.PENDING)
            .first()
        )
        if message is None:
            return None
        link = message.link
        message.attempts += 1
        try:
            if not link.enabled or not link.chat_id:
                raise RecipientGone("link disabled")
            if message.part_id and link.channel == MessengerLink.TELEGRAM:
                part = message.part
                message.text = order_text(part, for_admin=link.user_id is not None)
                caption = message.text
                if len(caption.encode("utf-16-le")) // 2 > 1024:
                    caption = order_text(
                        part, for_admin=link.user_id is not None, compact=True
                    )
                send(link, message.text, photos=order_photos(part), caption=caption)
            else:
                send(link, message.text)
        except RecipientGone as error:
            message.status, message.error = MessengerMessage.FAILED, str(error)[:200]
            if link.enabled and link.chat_id:
                link.enabled = False
                link.save(update_fields=["enabled"])
        except MessengerError as error:
            message.error = str(error)[:200]
            if message.attempts >= MAX_ATTEMPTS:
                message.status = MessengerMessage.FAILED
        else:
            message.status, message.error = MessengerMessage.SENT, ""
            message.sent_at = now or timezone.now()
        message.save(update_fields=["text", "attempts", "status", "error", "sent_at"])
        return message.status


def deliver_many(ids):
    for pk in ids:
        try:
            deliver(pk)
        except Exception:
            logger.exception("Messenger notification %s failed", pk)


def deliver_pending(now=None, min_age=timedelta(seconds=30), limit=200):
    """Retry messages the background thread did not manage to send."""
    now = now or timezone.now()
    ids = list(
        MessengerMessage.objects.filter(
            status=MessengerMessage.PENDING, created_at__lte=now - min_age
        )
        .order_by("created_at")
        .values_list("pk", flat=True)[:limit]
    )
    sent = 0
    for pk in ids:
        if deliver(pk, now=now) == MessengerMessage.SENT:
            sent += 1
    return sent, len(ids)


def _in_background(function, *args):
    if not settings.MESSENGER_BACKGROUND:
        function(*args)
        return

    def run():
        try:
            function(*args)
        finally:
            close_old_connections()
            connection.close()

    threading.Thread(target=run, daemon=True).start()
