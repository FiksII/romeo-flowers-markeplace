"""Exercise the real MAX SDK at the aiohttp HTTP boundary."""

import json
import ssl
from io import BytesIO
from unittest.mock import AsyncMock

import aiohttp
import pytest
from django.core.exceptions import ValidationError
from PIL import Image

from market import max_transport
from market.messengers import MessengerError, RecipientGone

USER = {"user_id": 7, "first_name": "Romeo", "is_bot": True, "last_activity_time": 0}
MESSAGE = {
    "message": {
        "sender": USER,
        "recipient": {"chat_id": 42, "chat_type": "dialog"},
        "timestamp": 1,
        "body": {"mid": "mid-1", "seq": 1, "text": "sent"},
    }
}


class Response:
    def __init__(self, payload, status=200, headers=None, data=b"image"):
        self.payload, self.status = payload, status
        self.ok = 200 <= status < 300
        self.headers = headers or {}
        self.data = data
        self.content = self

    async def json(self):
        return self.payload

    async def text(self):
        return json.dumps(self.payload)

    async def iter_chunked(self, size):
        for offset in range(0, len(self.data), size):
            yield self.data[offset : offset + size]

    def release(self):
        pass


@pytest.fixture(autouse=True)
def config(settings, monkeypatch):
    settings.MAX_BOT_TOKEN = "secret-max-token"
    settings.MAX_BOT_USERNAME = "romeo"
    monkeypatch.setattr(max_transport.asyncio, "sleep", AsyncMock())


@pytest.fixture
def http(monkeypatch):
    requests, responses = [], []

    async def request(self, method, url, **kwargs):
        requests.append((method, str(url), kwargs))
        return responses.pop(0) if responses else Response(MESSAGE)

    monkeypatch.setattr(aiohttp.ClientSession, "request", request)
    return requests, responses


def test_sdk_identity_uses_current_endpoint_header_and_trusted_tls(http):
    requests, responses = http
    responses.append(Response(USER))
    assert max_transport.call("get_me")["user_id"] == 7
    method, url, kwargs = requests[0]
    assert (method, url) == ("GET", "https://platform-api2.max.ru/me")
    assert kwargs["headers"]["Authorization"] == "secret-max-token"
    assert "access_token" not in kwargs.get("params", {})
    assert kwargs["allow_redirects"] is False
    assert isinstance(kwargs["ssl"], ssl.SSLContext)
    assert kwargs["ssl"].verify_mode == ssl.CERT_REQUIRED
    assert kwargs["ssl"].check_hostname


def test_sdk_subscription_and_commands_serialization(http):
    requests, responses = http
    responses.extend([Response({"success": True}), Response({"commands": []})])
    assert max_transport.call(
        "subscribe_webhook",
        url="https://example.com/max",
        update_types=["bot_started"],
        secret="abc123",
    )["success"]
    assert requests[0][2]["json"] == {
        "url": "https://example.com/max",
        "update_types": ["bot_started"],
        "secret": "abc123",
    }
    max_transport.call("set_commands", commands=[{"name": "add", "description": "Add"}])
    assert requests[1][1].endswith("/me/commands")
    assert requests[1][2]["json"] == {
        "commands": [{"name": "add", "description": "Add"}]
    }


def test_real_sdk_keyboard_and_photo_attachments(http):
    requests, _ = http
    result = max_transport.send(
        "42",
        "order",
        photos=["https://storage.example/a.jpg"],
        reply_markup={
            "inline_keyboard": [[{"text": "Choose", "callback_data": "bq:yes"}]]
        },
    )
    assert result["message"]["body"]["mid"] == "mid-1"
    assert "bot" not in result["message"]
    attachments = requests[0][2]["json"]["attachments"]
    assert attachments[0]["type"] == "image"
    assert attachments[0]["payload"]["url"] == "https://storage.example/a.jpg"
    assert attachments[1]["payload"]["buttons"][0][0]["payload"] == "bq:yes"
    assert requests[0][2]["params"]["chat_id"] == 42
    json.dumps(requests[0][2]["json"])


def test_sdk_persistent_menu_maps_to_message_button(http):
    requests, _ = http
    max_transport.send(
        42, "menu", reply_markup={"keyboard": [[{"text": "➕ Добавить букет"}]]}
    )
    button = requests[0][2]["json"]["attachments"][0]["payload"]["buttons"][0][0]
    assert button == {"type": "message", "text": "➕ Добавить букет"}


def test_gallery_chunks_twenty_photos_in_order_and_preserves_text(http):
    requests, _ = http
    photos = [f"https://storage.example/{index}.jpg" for index in range(20)]
    text = "Полная информация " * 600
    max_transport.send(42, text, photos=photos)
    sent = [kwargs["json"] for _, _, kwargs in requests]
    assert all(len(body["text"] or "") <= 4000 for body in sent)
    assert "".join(body["text"] or "" for body in sent) == text
    assert [
        att["payload"]["url"] for body in sent for att in body["attachments"]
    ] == photos
    assert all(len(body["attachments"]) <= 12 for body in sent)


def test_edit_and_callback_use_sdk_routes(http):
    requests, responses = http
    responses.extend([Response({"success": True}), Response({"success": True})])
    max_transport.send(
        42, "updated", message_id="old-mid", reply_markup={"inline_keyboard": []}
    )
    max_transport.call("send_callback", callback_id="cb")
    assert requests[0][0] == "PUT"
    assert requests[0][2]["params"]["message_id"] == "old-mid"
    assert requests[0][2]["json"]["attachments"] == []
    assert requests[1][1].endswith("/answers")


@pytest.mark.parametrize(
    "status,code,error",
    [
        (403, "bot.blocked", RecipientGone),
        (404, "chat.not.found", RecipientGone),
        (401, "auth.invalid", MessengerError),
        (429, "rate.limit", MessengerError),
        (500, "internal", MessengerError),
    ],
)
def test_http_errors_are_safe_and_retryable_where_needed(
    http, caplog, status, code, error
):
    _, responses = http
    responses.append(
        Response(
            {
                "code": code,
                "message": "secret-max-token https://private/url?key=secret",
            },
            status,
        )
    )
    with pytest.raises(error) as caught:
        max_transport.send(42, "order")
    assert "secret" not in str(caught.value) + caplog.text
    assert "private/url" not in str(caught.value) + caplog.text


def test_permanent_bad_media_falls_back_to_complete_order_text(http):
    requests, responses = http
    responses.append(
        Response({"code": "attachment.invalid", "message": "bad image"}, 400)
    )
    max_transport.send(
        42, "order and payment instructions", photos=["https://storage.example/a.jpg"]
    )
    assert requests[-1][2]["json"]["text"] == "order and payment instructions"
    assert requests[-1][2]["json"]["attachments"] == []


@pytest.mark.parametrize(
    "url",
    [
        "http://i.oneme.ru/a",
        "https://127.0.0.1/a",
        "https://evil.test/a",
        "https://oneme.ru.evil.test/a",
        "https://user:pass@i.oneme.ru/a",
        "https://i.oneme.ru:444/a",
        "https://i.oneme.ru/a#fragment",
    ],
)
def test_download_rejects_untrusted_media_before_network(http, url):
    requests, _ = http
    with pytest.raises(ValidationError):
        max_transport.download_photo(url)
    assert requests == []


def test_download_never_authenticates_or_follows_redirects(http):
    requests, responses = http
    responses.append(
        Response({}, headers={"Content-Type": "image/png"}, data=b"PNG-content")
    )
    upload = max_transport.download_photo("https://i.oneme.ru/i?r=media-secret")
    assert upload.read() == b"PNG-content"
    assert upload.name.endswith(".png")
    assert requests[0][2]["allow_redirects"] is False
    assert "Authorization" not in requests[0][2].get("headers", {})


def test_download_blocks_redirects_and_large_payloads(http):
    _, responses = http
    responses.extend(
        [
            Response({}, 302, {"Location": "http://127.0.0.1/"}),
            Response({}, headers={"Content-Length": str(5 * 1024 * 1024 + 1)}),
        ]
    )
    with pytest.raises(ValidationError):
        max_transport.download_photo("https://i.oneme.ru/a")
    with pytest.raises(ValidationError):
        max_transport.download_photo("https://i.oneme.ru/a")


def test_download_bounds_stream_without_content_length(http):
    _, responses = http
    responses.append(Response({}, data=b"x" * (5 * 1024 * 1024 + 1)))
    with pytest.raises(ValidationError):
        max_transport.download_photo("https://i.oneme.ru/a")


def test_missing_token_is_disabled_before_network(settings, http):
    settings.MAX_BOT_TOKEN = ""
    with pytest.raises(MessengerError, match="configured"):
        max_transport.call("get_me")
    assert http[0] == []


def test_preview_uses_sdk_upload_models_and_current_upload_endpoint(http):
    requests, responses = http
    content = BytesIO()
    Image.new("RGB", (2, 2), "red").save(content, format="PNG")
    responses.extend(
        [
            Response(
                {}, data=content.getvalue(), headers={"Content-Type": "image/png"}
            ),
            Response({"url": "https://iu.oneme.ru/uploadImage?apiToken=media-secret"}),
            Response({"photos": {"123": {"token": "uploaded-image-token"}}}),
        ]
    )
    max_transport.preview_photo(42, "https://i.oneme.ru/i?media=123")
    assert requests[1][1] == "https://platform-api2.max.ru/uploads"
    assert requests[1][2]["params"] == {"type": "image"}
    assert requests[2][0] == "POST"
    assert "Authorization" not in requests[2][2].get("headers", {})
    assert requests[2][2]["allow_redirects"] is False
    attachment = requests[3][2]["json"]["attachments"][0]
    assert attachment == {"type": "image", "payload": {"token": "uploaded-image-token"}}


def test_attachment_processing_retries_before_sending_duplicate_text(http):
    requests, responses = http
    responses.append(Response({"code": "attachment.not.ready", "message": "wait"}, 400))
    max_transport.send(42, "order", photos=["https://storage.example/a.jpg"])
    assert len(requests) == 2
    assert (
        requests[0][2]["json"]["attachments"] == requests[1][2]["json"]["attachments"]
    )


def test_unready_media_remains_retryable_after_sdk_retries(http):
    requests, responses = http
    responses.extend(
        Response({"code": "attachment.not.ready", "message": "wait"}, 400)
        for _ in range(3)
    )
    photos = ["https://storage.example/a.jpg"]
    with pytest.raises(MessengerError):
        max_transport.send(42, "order", photos=photos)
    assert len(requests) == 3
    assert all(len(request[2]["json"]["attachments"]) == 1 for request in requests)

    # The outbox can retry the same original notification with its photo intact.
    max_transport.send(42, "order", photos=photos)
    assert requests[-1][2]["json"]["attachments"][0]["payload"]["url"] == photos[0]


def test_media_resolver_rejects_private_dns_answers(monkeypatch):
    from asgiref.sync import async_to_sync

    monkeypatch.setattr(
        aiohttp.resolver.DefaultResolver,
        "resolve",
        AsyncMock(return_value=[{"host": "127.0.0.1"}]),
    )

    async def resolve():
        resolver = max_transport._PublicResolver()
        try:
            await resolver.resolve("i.oneme.ru")
        finally:
            await resolver.close()

    with pytest.raises(ValidationError):
        async_to_sync(resolve)()


def test_sdk_subscriptions_response_is_json_serializable(http):
    _, responses = http
    responses.append(
        Response(
            {
                "subscriptions": [
                    {
                        "url": "https://example.com/max",
                        "time": 1,
                        "update_types": ["message_created"],
                    }
                ]
            }
        )
    )
    assert (
        max_transport.call("get_subscriptions")["subscriptions"][0]["url"]
        == "https://example.com/max"
    )


def test_sdk_connection_baseexception_becomes_safe_retryable_error(monkeypatch):
    async def request(*args, **kwargs):
        raise aiohttp.ClientConnectionError("secret-max-token https://secret/private")

    monkeypatch.setattr(aiohttp.ClientSession, "request", request)
    with pytest.raises(MessengerError) as caught:
        max_transport.send(42, "order")
    assert "secret" not in str(caught.value)


def test_expired_preview_upload_does_not_disable_recipient(http):
    requests, responses = http
    content = BytesIO()
    Image.new("RGB", (2, 2), "red").save(content, format="PNG")
    responses.extend(
        [
            Response({}, data=content.getvalue()),
            Response({"url": "https://iu.oneme.ru/uploadImage?apiToken=expired"}),
            Response({}, status=403),
        ]
    )
    max_transport.preview_photo(42, "https://i.oneme.ru/i?media=123")
    assert "Отправьте другое фото" in requests[-1][2]["json"]["text"]


def test_document_preview_keeps_original_image_as_sdk_file_attachment(http):
    requests, responses = http
    content = BytesIO()
    Image.new("RGB", (2, 2), "red").save(content, format="PNG")
    responses.extend(
        [
            Response(
                {}, data=content.getvalue(), headers={"Content-Type": "image/png"}
            ),
            Response({"url": "https://fu.oneme.ru/api/upload.do?sig=secret"}),
            Response({"token": "uploaded-file-token"}),
        ]
    )
    max_transport.preview_photo(42, "https://fu.oneme.ru/file?media=123", document=True)
    assert requests[1][2]["params"] == {"type": "file"}
    assert requests[-1][2]["json"]["attachments"] == [
        {"type": "file", "payload": {"token": "uploaded-file-token"}}
    ]
