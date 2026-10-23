"""Durable MAX webhook receipts and the shared catalogue dialogue.

MAX webhook answers acknowledge delivery only. Replies go through maxapi after
the receipt commits; the existing notification timer retries interrupted work.
"""

import hashlib
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from market import messengers
from market.models import MaxBotEvent, MessengerLink, TelegramBouquetDraft
from market.telegram_catalogue import _handle, menu_for_chat

logger = logging.getLogger(__name__)
KINDS = {"bot_started", "bot_stopped", "message_created", "message_callback"}


def identity(update):
    """Validate the private-dialog envelope before retaining any user content."""
    if not isinstance(update, dict) or update.get("update_type") not in KINDS:
        return None
    kind = update["update_type"]
    if kind in {"bot_started", "bot_stopped"}:
        chat, user = update.get("chat_id"), update.get("user")
        reference = update.get("timestamp")
    else:
        message = update.get("message")
        if not isinstance(message, dict):
            return None
        recipient, body = message.get("recipient"), message.get("body")
        if not isinstance(recipient, dict) or recipient.get("chat_type") != "dialog":
            return None
        if not isinstance(body, dict):
            return None
        if not isinstance(body.get("mid"), str) or not body["mid"]:
            return None
        chat = recipient.get("chat_id")
        if kind == "message_callback":
            callback = update.get("callback")
            if not isinstance(callback, dict):
                return None
            user, reference = callback.get("user"), callback.get("callback_id")
        else:
            user, reference = message.get("sender"), body.get("mid")
    if (
        not isinstance(chat, int)
        or isinstance(chat, bool)
        or not isinstance(user, dict)
        or user.get("is_bot")
        or not isinstance(user.get("user_id"), int)
        or not isinstance(reference, (str, int))
        or not str(reference)
        or len(str(reference)) > 256
    ):
        return None
    key = f"{kind}:{chat}:{user['user_id']}:{reference}"
    return str(chat), hashlib.sha256(key.encode()).hexdigest()


def accept(update):
    envelope = identity(update)
    if not envelope:
        return None
    chat_id, key = envelope
    receipt, _ = MaxBotEvent.objects.get_or_create(
        event_key=key,
        defaults={"chat_id": chat_id, "payload": update},
    )
    if receipt.status == "pending":
        transaction.on_commit(lambda: messengers._in_background(process, receipt.pk))
    return receipt.pk


def _binding_reply(receipt, message, user, payload=""):
    text = str(message.get("text") or "")
    if text.split(" ", 1)[0] == "/stop":
        messengers.forget_chat(MessengerLink.MAX, receipt.chat_id)
        return {"text": "Уведомления в этот чат отключены."}
    code = messengers.find_code(payload, text)
    if code:
        title = " ".join(
            str(user.get(k) or "") for k in ("first_name", "last_name")
        ).strip()
        link = messengers.confirm_link(
            MessengerLink.MAX, code, receipt.chat_id, title or "MAX"
        )
        text = messengers.linked_reply(link) if link else messengers.BAD_CODE_REPLY
    else:
        text = messengers.help_reply()
    return {
        "text": text,
        "reply_markup": menu_for_chat(receipt.chat_id, MessengerLink.MAX),
    }


def _reply(receipt):
    update, chat = receipt.payload, receipt.chat_id
    kind = update["update_type"]
    update_id = receipt.pk * 100
    if kind == "bot_stopped":
        _handle(chat, update_id, {"text": "/stop"}, None, MessengerLink.MAX)
        messengers.forget_chat(MessengerLink.MAX, chat)
        return {}
    if kind == "bot_started":
        return _binding_reply(
            receipt, {"text": "/start"}, update["user"], update.get("payload", "")
        )
    message = update["message"]
    body = message["body"]
    if kind == "message_callback":
        callback = update["callback"]
        data = str(callback.get("payload") or "")
        if not data.startswith("bq:"):
            return {}
        return (
            _handle(
                chat,
                update_id,
                None,
                {
                    "data": data,
                    "message": {
                        "message_id": body["mid"],
                        "text": body.get("text", ""),
                    },
                },
                MessengerLink.MAX,
            )
            or {}
        )
    normalized = {"text": str(body.get("text") or "")}
    photos = []
    attachments = body.get("attachments") or []
    if isinstance(attachments, list):
        for attachment in attachments[:30]:
            if not isinstance(attachment, dict):
                continue
            payload = attachment.get("payload") or {}
            if not isinstance(payload, dict) or not isinstance(payload.get("url"), str):
                continue
            kind = attachment.get("type")
            if kind not in {"image", "file"}:
                continue
            photo = {"file_id": payload["url"]}
            size = attachment.get("size", payload.get("size"))
            if isinstance(size, int):
                photo["file_size"] = size
            photos.append(
                {"photo": [photo]} if kind == "image" else {"document": photo}
            )
    if photos:
        response = None
        for index, photo in enumerate(photos):
            response = _handle(
                chat,
                update_id + index,
                normalized
                | photo
                | {
                    "media_group_id": str(body["mid"]),
                },
                None,
                MessengerLink.MAX,
            )
    else:
        response = _handle(chat, update_id, normalized, None, MessengerLink.MAX)
    return (
        response
        if response is not None
        else _binding_reply(receipt, normalized, message["sender"])
    )


def send_reply(receipt):
    from market import max_transport

    response = receipt.response or {}
    if response.get("text"):
        max_transport.send(
            receipt.chat_id,
            response["text"],
            reply_markup=response.get("reply_markup"),
            message_id=response.get("message_id")
            if response.get("method") == "editMessageText"
            else None,
        )


def _acknowledge(update):
    if update.get("update_type") == "message_callback":
        from market import max_transport

        try:
            max_transport.call(
                "send_callback", callback_id=update["callback"]["callback_id"]
            )
        except messengers.MessengerError:
            # MAX callbacks expire quickly; the durable selection remains saved.
            logger.debug("MAX callback acknowledgement unavailable")


def process(receipt_id):
    if not messengers.channel_enabled(MessengerLink.MAX):
        return None
    with transaction.atomic():
        chat = (
            MaxBotEvent.objects.filter(pk=receipt_id)
            .values_list("chat_id", flat=True)
            .first()
        )
        if chat is None:
            return None
        # Serialize a dialog, including binding events, across all Gunicorn workers.
        draft, _ = TelegramBouquetDraft.objects.get_or_create(
            channel=MessengerLink.MAX, chat_id=chat
        )
        TelegramBouquetDraft.objects.select_for_update().get(pk=draft.pk)
        receipt = (
            MaxBotEvent.objects.select_for_update()
            .filter(chat_id=chat, status="pending")
            .order_by("pk")
            .first()
        )
        if receipt is None:
            return None
        receipt.attempts += 1
        try:
            if receipt.response is None:
                with transaction.atomic():
                    receipt.response = _reply(receipt)
            _acknowledge(receipt.payload)
            send_reply(receipt)
        except messengers.RecipientGone:
            messengers.forget_chat(MessengerLink.MAX, chat)
            receipt.status, receipt.error = "failed", "MAX recipient unavailable"
        except Exception as error:  # noqa: BLE001 - preserve retryable receipts after worker failures
            # Retain reply and catalogue state; a retry sends it without repeating writes.
            receipt.error = type(error).__name__
            if receipt.attempts >= messengers.MAX_ATTEMPTS:
                receipt.status = "failed"
            logger.warning(
                "MAX event %s deferred: %s", receipt.pk, type(error).__name__
            )
        else:
            receipt.status, receipt.error = "sent", ""
            receipt.processed_at = timezone.now()
        receipt.save(
            update_fields=["response", "attempts", "status", "error", "processed_at"]
        )
        if receipt.status != "pending":
            next_id = (
                MaxBotEvent.objects.filter(chat_id=chat, status="pending")
                .order_by("pk")
                .values_list("pk", flat=True)
                .first()
            )
            if next_id:
                transaction.on_commit(
                    lambda: messengers._in_background(process, next_id)
                )
        return receipt.status


def deliver_pending(limit=100, min_age=timedelta(seconds=30)):
    rows = (
        MaxBotEvent.objects.filter(
            status="pending", created_at__lte=timezone.now() - min_age
        )
        .order_by("pk")
        .values_list("pk", "chat_id")[:limit]
    )
    tried, completed, chats = 0, 0, set()
    for pk, chat in list(rows):
        if chat in chats:
            continue
        chats.add(chat)
        status = process(pk)
        tried += status is not None
        completed += status == "sent"
    return completed, tried
