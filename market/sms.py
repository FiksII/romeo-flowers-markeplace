"""Small SMSPilot transport adapter; code generation belongs to django-allauth."""

import json
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings


class SmsDeliveryError(Exception):
    pass


def send_sms(phone, text):
    if not settings.SMSPILOT_API_KEY:
        raise SmsDeliveryError("Отправка SMS временно недоступна. Попробуйте позже.")
    payload = {
        "apikey": settings.SMSPILOT_API_KEY,
        "to": phone.lstrip("+"),
        "send": text,
        "format": "json",
        "ttl": "5",
    }
    if settings.SMSPILOT_SENDER:
        payload["from"] = settings.SMSPILOT_SENDER
    request = Request(
        "https://smspilot.ru/api.php", data=urlencode(payload).encode(), method="POST"
    )
    try:
        with urlopen(request, timeout=10) as response:
            result = json.load(response)
        sent = result.get("send", [])
        if (
            result.get("error")
            or not sent
            or str(sent[0].get("status")) not in {"0", "1", "2"}
        ):
            raise ValueError("SMS rejected")
    except (
        URLError,
        TimeoutError,
        OSError,
        ValueError,
        TypeError,
        AttributeError,
        KeyError,
        IndexError,
    ):
        # Provider responses and transport exceptions can contain credentials.
        raise SmsDeliveryError("Не удалось отправить SMS. Попробуйте позже.") from None
