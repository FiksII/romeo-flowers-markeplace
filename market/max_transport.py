"""Synchronous MAX transport backed by MAX's verified ``maxapi`` SDK.

The pinned SDK predates header authentication and the current API hostname. The
session adapter changes only that connection boundary; SDK Bot methods, request
models, media upload processing and response models remain in use. No polling
runtime is started. See https://dev.max.ru/docs-api.
"""

import asyncio
import ipaddress
import ssl
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from asgiref.sync import async_to_sync
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from maxapi import Bot
from maxapi.connection.base import BaseConnection
from maxapi.enums.http_method import HTTPMethod
from maxapi.enums.upload_type import UploadType
from maxapi.exceptions.invalid_token import InvalidToken
from maxapi.exceptions.max import MaxConnection, MaxUploadFileFailed
from maxapi.types.attachments.attachment import ButtonsPayload, OtherAttachmentPayload
from maxapi.types.attachments.buttons.callback_button import CallbackButton
from maxapi.types.attachments.buttons.message_button import MessageButton
from maxapi.types.attachments.image import Image
from maxapi.types.command import BotCommand
from maxapi.types.input_media import InputMediaBuffer
from maxapi.utils.message import process_input_media
from pydantic import ValidationError as ModelValidationError

API_URL = "https://platform-api2.max.ru"
MAX_PHOTO = 5 * 1024 * 1024
MAX_TEXT = 4000
MAX_ATTACHMENTS = 12
# MAX's documentation uses iu.oneme.ru/fu.oneme.ru and *.okcdn.ru for
# attachments; received image URLs also use i.oneme.ru and st.max.ru.
MEDIA_DOMAINS = ("oneme.ru", "okcdn.ru", "max.ru")
PHOTO_ERROR = "Используйте JPEG, PNG или WebP до 5 МБ."


class _ApiError(Exception):
    def __init__(self, status, code=""):
        # Never retain remote messages: they can contain credentials/media URLs.
        self.status = status
        self.media = code.startswith("attachment.") and code != "attachment.not.ready"
        self.gone = status == 403 or code in {
            "chat.not.found",
            "user.not.found",
            "bot.blocked",
            "bot.removed",
        }
        self.not_ready = code == "attachment.not.ready"
        super().__init__(f"MAX HTTP {status}")


def _ssl_context():
    context = ssl.create_default_context()
    context.load_verify_locations(
        cafile=str(Path(__file__).with_name("certs") / "russian_trusted_root_ca.pem")
    )
    return context


class _ApiSession:
    """Keep the SDK's serialization while updating its obsolete connection."""

    def __init__(self, token):
        self.client = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25))
        self.token = token
        self.ssl = _ssl_context()

    async def request(self, method, url, **kwargs):
        params = dict(kwargs.pop("params", {}))
        params.pop("access_token", None)
        response = await self.client.request(
            method,
            API_URL + url,
            params=params,
            headers={"Authorization": self.token},
            ssl=self.ssl,
            allow_redirects=False,
            **kwargs,
        )
        if not response.ok:
            try:
                raw = await response.json()
                code = str(raw.get("code", "")) if isinstance(raw, dict) else ""
            except (ValueError, aiohttp.ClientError):
                code = ""
            response.release()
            # Raising here also avoids upstream logging raw error bodies/tokens.
            raise _ApiError(response.status, code)
        return response

    async def close(self):
        await self.client.close()


class _PublicResolver(aiohttp.resolver.DefaultResolver):
    """Validate the DNS answers actually used by the connection (no rebinding)."""

    async def resolve(self, host, port=0, family=0):
        answers = await super().resolve(host, port, family)
        if not answers or any(
            not ipaddress.ip_address(answer["host"]).is_global for answer in answers
        ):
            raise ValidationError(PHOTO_ERROR)
        return answers


def _media_url(url):
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if (
            parts.scheme != "https"
            or parts.username
            or parts.password
            or parts.port not in (None, 443)
            or parts.fragment
            or not any(
                host == domain or host.endswith("." + domain)
                for domain in MEDIA_DOMAINS
            )
        ):
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ValidationError(PHOTO_ERROR) from None
    return url


def _media_session():
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=_ssl_context(), resolver=_PublicResolver()),
        timeout=aiohttp.ClientTimeout(total=25),
    )


class _UploadConnection(BaseConnection):
    """SDK upload processing with the same TLS and redirect policy as downloads."""

    async def upload_file_buffer(self, filename, url, buffer, type):
        _media_url(url)
        form = aiohttp.FormData()
        form.add_field(
            "data", buffer, filename=filename, content_type="application/octet-stream"
        )
        async with _media_session() as session:
            response = await session.request(
                "POST", url, data=form, allow_redirects=False
            )
            try:
                if not response.ok:
                    error = _ApiError(response.status, "attachment.invalid")
                    error.gone = False
                    raise error
                return await response.text()
            finally:
                response.release()


async def _invoke(bot, method, params):
    if method in {"set_commands", "set_my_commands"}:
        commands = [
            BotCommand.model_validate(command).model_dump(
                mode="json", exclude_none=True
            )
            for command in params["commands"]
        ]
        # The SDK's old PATCH /me expects a User response; the current endpoint
        # returns only commands. Reuse its connection and BotCommand model.
        return await bot.request(
            HTTPMethod.PATCH,
            "/me/commands",
            is_return_raw=True,
            json={"commands": commands},
            params={},
        )
    if method not in {
        "get_me",
        "get_subscriptions",
        "subscribe_webhook",
        "send_message",
        "edit_message",
        "send_callback",
    }:
        raise ValueError("Unsupported MAX SDK method")
    return await getattr(bot, method)(**params)


async def _execute(method, params):
    token = getattr(settings, "MAX_BOT_TOKEN", "")
    if not token:
        from market.messengers import MessengerError

        raise MessengerError("MAX is not configured")
    bot = Bot(token, auto_requests=False, auto_check_subscriptions=False)
    bot.API_URL, bot.params = API_URL, {}
    bot.session = _ApiSession(token)
    try:
        if method == "_preview":
            media = InputMediaBuffer(
                params.pop("content"), filename=params.pop("filename")
            )
            if params.pop("document"):
                media.type = UploadType.FILE
            attachment = await process_input_media(_UploadConnection(), bot, media)
            await asyncio.sleep(bot.after_input_media_delay)
            params["attachments"] = [attachment]
            method = "send_message"
        for attempt in range(3):
            try:
                result = await _invoke(bot, method, params)
                break
            except _ApiError as error:
                if not error.not_ready or attempt == 2:
                    raise
                await asyncio.sleep(2**attempt)
        if hasattr(result, "model_dump"):
            result = result.model_dump(mode="json", exclude={"bot"}, exclude_none=True)
        if not isinstance(result, dict) or result.get("success") is False:
            from market.messengers import MessengerError

            raise MessengerError("MAX rejected the request")
        return result
    finally:
        await bot.close_session()


def _run(method, params):
    from market.messengers import MessengerError, RecipientGone

    try:
        return async_to_sync(_execute)(method, params)
    except _ApiError as error:
        if error.gone:
            raise RecipientGone("MAX recipient is unavailable") from None
        raise
    except (
        aiohttp.ClientError,
        TimeoutError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        ModelValidationError,
        InvalidToken,
        MaxConnection,
        MaxUploadFileFailed,
    ):
        raise MessengerError("MAX request failed") from None


def call(method, **params):
    """Run an allowed SDK Bot method and return its JSON-safe response."""
    from market.messengers import MessengerError

    try:
        return _run(method, params)
    except _ApiError as error:
        raise MessengerError(f"MAX HTTP {error.status}") from None


def _keyboard(markup):
    if not markup:
        return []
    rows = []
    for row in markup.get("inline_keyboard", markup.get("keyboard", [])):
        buttons = []
        for button in row:
            if "callback_data" in button:
                buttons.append(
                    CallbackButton(text=button["text"], payload=button["callback_data"])
                )
            else:
                buttons.append(MessageButton(text=button["text"]))
        if buttons:
            rows.append(buttons)
    return [ButtonsPayload(buttons=rows).pack()] if rows else []


def send(chat_id, text, photos=(), reply_markup=None, message_id=None):
    """Translate shared catalogue controls and ordered galleries into SDK models."""
    from market.messengers import MessengerError

    keyboard = _keyboard(reply_markup)
    capacity = MAX_ATTACHMENTS - len(keyboard)
    photos = list(photos)[:20]
    galleries = [
        photos[index : index + capacity] for index in range(0, len(photos), capacity)
    ]
    texts = [
        text[index : index + MAX_TEXT] for index in range(0, len(text), MAX_TEXT)
    ] or [None]
    count = max(len(galleries), len(texts))
    if message_id is not None and count > 1:
        raise MessengerError("MAX edited message is too long")
    first = None
    for index in range(count):
        attachments = (
            [
                Image(payload=OtherAttachmentPayload(url=photo))
                for photo in galleries[index]
            ]
            if index < len(galleries)
            else []
        )
        controls = keyboard if index == count - 1 else []
        body = texts[index] if index < len(texts) else None
        params = {"text": body, "attachments": attachments + controls}
        if message_id is not None:
            method = "edit_message"
            params["message_id"] = str(message_id)
        else:
            method = "send_message"
            params["chat_id"] = int(chat_id)
        try:
            result = _run(method, params)
        except _ApiError as error:
            if (
                error.not_ready
                or not attachments
                or not (error.media or error.status in {400, 404, 415})
            ):
                raise MessengerError(f"MAX HTTP {error.status}") from None
            # A permanently broken image must not swallow order/payment details.
            params["attachments"] = controls
            params["text"] = body or "Фото букета недоступны."
            result = call(method, **params)
        if first is None:
            first = result
        if index + 1 < count:
            # MAX permits two messages per second to the same chat.
            async_to_sync(asyncio.sleep)(0.55)
    return first


async def _download(url):
    async with _media_session() as session:
        response = await session.request("GET", url, allow_redirects=False)
        try:
            if 300 <= response.status < 400:
                raise ValidationError(PHOTO_ERROR)
            if not response.ok:
                raise _ApiError(response.status)
            if int(response.headers.get("Content-Length", 0)) > MAX_PHOTO:
                raise ValidationError(PHOTO_ERROR)
            content = bytearray()
            async for chunk in response.content.iter_chunked(64 * 1024):
                content.extend(chunk)
                if len(content) > MAX_PHOTO:
                    raise ValidationError(PHOTO_ERROR)
            mime = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            extension = {"image/png": "png", "image/webp": "webp"}.get(mime, "jpg")
            return SimpleUploadedFile(
                f"bouquet.{extension}", bytes(content), content_type=mime or None
            )
        finally:
            response.release()


def download_photo(file_id):
    """Download bounded public MAX media, without bot credentials or redirects."""
    from market.messengers import MessengerError

    url = _media_url(file_id)
    try:
        return async_to_sync(_download)(url)
    except (aiohttp.ClientError, TimeoutError, OSError, ValueError, _ApiError):
        raise MessengerError("Не удалось загрузить фото из MAX") from None


def preview_photo(chat_id, file_id, document=False):
    """Preview an incoming image/document using the SDK's media upload pipeline."""
    upload = download_photo(file_id)
    try:
        return _run(
            "_preview",
            {
                "chat_id": int(chat_id),
                "content": upload.read(),
                "filename": upload.name,
                "document": document,
            },
        )
    except _ApiError:
        return send(chat_id, "Не удалось показать фото. Отправьте другое фото.")
