"""Small T-Bank internet acquiring (API v2) client: signed calls and notification checks.

Docs: https://developer.tbank.ru/eacq/api/init and .../intro/developer/token
"""

import hashlib
import hmac
import json
import logging
import ssl
from decimal import Decimal
from functools import cache
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from django.conf import settings

logger = logging.getLogger(__name__)
# T-Bank's API certificate is issued by the Russian Trusted Root CA, which the usual
# Linux and Python trust stores do not include. It is trusted for these calls only.
RUSSIAN_ROOT_CA = Path(__file__).with_name("certs") / "russian_trusted_root_ca.pem"


class TbankError(Exception):
    """The bank refused the request or could not be reached."""


def kopecks(amount):
    return int((Decimal(amount) * 100).quantize(Decimal(1)))


def _value(value):
    # The bank signs booleans as lowercase words and numbers as plain digits.
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def make_token(params, password=None):
    """SHA-256 of the root-level values plus Password, sorted by key."""
    pairs = {
        key: value
        for key, value in params.items()
        if key != "Token" and not isinstance(value, dict | list) and value is not None
    }
    pairs["Password"] = settings.TBANK_PASSWORD if password is None else password
    joined = "".join(_value(pairs[key]) for key in sorted(pairs))
    return hashlib.sha256(joined.encode()).hexdigest()


def notification_is_genuine(data):
    if not settings.TBANK_PASSWORD or not isinstance(data, dict):
        return False
    if data.get("TerminalKey") != settings.TBANK_TERMINAL_KEY:
        return False
    return hmac.compare_digest(str(data.get("Token", "")), make_token(data))


@cache
def ssl_context():
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=RUSSIAN_ROOT_CA)
    return context


def call(method, params):
    """POST a signed request and return the bank's answer; raise TbankError on failure."""
    body = {"TerminalKey": settings.TBANK_TERMINAL_KEY, **params}
    body["Token"] = make_token(body)
    request = Request(
        settings.TBANK_API_URL.rstrip("/") + "/" + method,
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=15, context=ssl_context()) as response:
            result = json.load(response)
    except (URLError, TimeoutError, OSError, ValueError) as error:
        logger.warning("T-Bank %s failed: %s", method, type(error).__name__)
        raise TbankError("Платёжный сервис не отвечает. Попробуйте ещё раз.") from None
    if not isinstance(result, dict) or not result.get("Success"):
        result = result if isinstance(result, dict) else {}
        logger.warning(
            "T-Bank %s refused: %s %s %s",
            method,
            result.get("ErrorCode"),
            result.get("Message"),
            result.get("Details"),
        )
        raise TbankError(result.get("Message") or "Платёжный сервис отклонил запрос.")
    return result
