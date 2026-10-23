"""Stable same-origin URLs and a bounded disk cache for private S3 photos."""

import hashlib
import logging
from pathlib import PurePosixPath

from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.cache import caches
from django.core.files.storage import default_storage
from django.core.signing import BadSignature, Signer
from django.http import HttpResponse
from django.urls import reverse
from django.utils.cache import get_conditional_response, patch_cache_control
from django.views.decorators.http import require_safe

logger = logging.getLogger(__name__)
MAX_BYTES = 8 * 1024 * 1024
MAX_AGE = 30 * 24 * 60 * 60
CONTENT_TYPES = {
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


def valid_name(name):
    return (
        isinstance(name, str)
        and len(name) <= 500
        and "\\" not in name
        and "\x00" not in name
        and ":" not in name
        and not name.startswith("/")
        and ".." not in name.split("/")
        and PurePosixPath(name).suffix.lower() in CONTENT_TYPES
    )


def photo_url(name, storage=None):
    if not settings.S3_ENABLED:
        return (storage or default_storage).url(name)
    token = Signer(salt="market.photo").sign_object(name, compress=True)
    return reverse("market:photo", kwargs={"token": token})


def cached_photo(name):
    """Read storage only on a cache miss. Errors never become cache entries."""
    key = hashlib.sha256(name.encode()).hexdigest()
    backend = caches["photos"]
    content = backend.get(key)
    if content is None:
        with default_storage.open(name, "rb") as source:
            # django-storages downloads the entire object on its first read.
            # Its size is already available from the metadata loaded by open().
            if source.size > MAX_BYTES:
                raise ValueError("Photo exceeds the delivery size limit")
            content = source.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise ValueError("Photo exceeds the delivery size limit")
        backend.set(key, content, timeout=MAX_AGE)
    return content


@require_safe
def photo(request, token):
    try:
        name = Signer(salt="market.photo").unsign_object(token)
    except (BadSignature, ValueError, TypeError):
        return not_found()
    if not valid_name(name):
        return not_found()
    try:
        content = cached_photo(name)
    except FileNotFoundError:
        return not_found()
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {
            "404",
            "NoSuchKey",
            "NotFound",
        }:
            return not_found()
        logger.warning("S3 photo read failed: %s", name)
        return unavailable()
    except (OSError, BotoCoreError, ValueError):
        logger.warning("Photo read failed: %s", name)
        return unavailable()
    etag = '"' + hashlib.sha256(name.encode()).hexdigest() + '"'
    response = get_conditional_response(request, etag=etag)
    if response is None:
        response = HttpResponse(
            content if request.method == "GET" else b"",
            content_type=CONTENT_TYPES[PurePosixPath(name).suffix.lower()],
        )
        response["Content-Length"] = str(len(content))
    response["ETag"] = etag
    if response.status_code in {200, 304}:
        patch_cache_control(response, public=True, max_age=MAX_AGE, immutable=True)
    else:
        response["Cache-Control"] = "no-store"
    return response


def not_found():
    response = HttpResponse(status=404)
    response["Cache-Control"] = "no-store"
    return response


def unavailable():
    response = HttpResponse(
        "Фотография временно недоступна",
        status=503,
        content_type="text/plain; charset=utf-8",
    )
    response["Cache-Control"] = "no-store"
    response["Retry-After"] = "5"
    return response
