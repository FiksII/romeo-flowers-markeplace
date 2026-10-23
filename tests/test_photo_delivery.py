from io import BytesIO
from unittest.mock import patch

import pytest
from django.core.cache import caches
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from PIL import Image


@pytest.fixture
def photo_delivery(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.S3_ENABLED = True
    settings.CACHES = {
        **settings.CACHES,
        "photos": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "photo-tests",
        },
    }
    caches["photos"].clear()
    output = BytesIO()
    Image.new("RGB", (400, 500), "pink").save(output, "WEBP")
    name = default_storage.save("cache/test.webp", ContentFile(output.getvalue()))
    yield name, output.getvalue()
    caches["photos"].clear()


@pytest.mark.django_db
def test_photo_url_stays_on_site_and_cache_avoids_storage_reads(client, photo_delivery):
    from market.photo_delivery import photo_url

    name, data = photo_delivery
    url = photo_url(name)
    assert url == photo_url(name) and url.startswith("/photos/")
    with patch.object(default_storage, "open", wraps=default_storage.open) as opened:
        first = client.get(url)
        second = client.get(url)
    assert first.status_code == second.status_code == 200
    assert first.content == second.content == data
    assert opened.call_count == 1
    assert first["Content-Type"] == "image/webp"
    assert "public" in first["Cache-Control"] and "immutable" in first["Cache-Control"]
    assert "2592000" in first["Cache-Control"]
    assert "Location" not in first and not first.cookies
    assert client.get(url, HTTP_IF_NONE_MATCH=first["ETag"]).status_code == 304
    head = client.head(url)
    assert head.status_code == 200 and head.content == b""
    assert int(head["Content-Length"]) == len(data)


@pytest.mark.django_db
def test_invalid_photo_tokens_and_paths_are_rejected(client, photo_delivery):
    from django.core.signing import Signer

    from market.photo_delivery import photo_url

    url = photo_url(photo_delivery[0])
    assert client.get(url[:-2] + "x/").status_code == 404
    for name in [
        "../secret.jpg",
        "/etc/secret.jpg",
        "cache/../secret.jpg",
        "cache\\secret.jpg",
        "runtime.env",
        "https://example.com/a.jpg",
    ]:
        token = Signer(salt="market.photo").sign_object(name, compress=True)
        assert client.get(f"/photos/{token}/").status_code == 404
    assert client.post(url).status_code == 405


@pytest.mark.django_db
def test_transient_storage_failure_is_not_cached(client, photo_delivery):
    from market.photo_delivery import photo_url

    url = photo_url(photo_delivery[0])
    with patch.object(default_storage, "open", side_effect=OSError("unavailable")):
        response = client.get(url)
    assert response.status_code == 503
    assert response["Cache-Control"] == "no-store"
    assert response["Retry-After"] == "5"
    assert client.get(url).status_code == 200


@pytest.mark.django_db
def test_new_photo_name_has_new_url_and_missing_file_is_not_success(
    client, photo_delivery
):
    from market.photo_delivery import photo_url

    assert photo_url(photo_delivery[0]) != photo_url("cache/other.webp")
    response = client.get(photo_url("cache/missing.webp"))
    assert response.status_code == 404
    assert response["Cache-Control"] == "no-store"


@pytest.mark.django_db
def test_failed_http_precondition_is_not_publicly_cached(client, photo_delivery):
    from market.photo_delivery import photo_url

    response = client.get(photo_url(photo_delivery[0]), HTTP_IF_MATCH='"wrong-etag"')
    assert response.status_code == 412
    assert response["Cache-Control"] == "no-store"


@pytest.mark.django_db
def test_oversized_image_is_not_cached(client, photo_delivery):
    from market.photo_delivery import MAX_BYTES, photo_url

    with patch.object(default_storage, "open") as opened:
        opened.return_value.__enter__.return_value.size = MAX_BYTES + 1
        opened.return_value.__enter__.return_value.read.return_value = b"x" * (
            MAX_BYTES + 1
        )
        response = client.get(photo_url(photo_delivery[0]))
    assert response.status_code == 503 and response["Cache-Control"] == "no-store"
    opened.return_value.__enter__.return_value.read.assert_not_called()
    assert client.get(photo_url(photo_delivery[0])).status_code == 200


def test_photo_cache_can_be_shared_between_process_backends(photo_delivery, tmp_path):
    from django.core.cache.backends.filebased import FileBasedCache

    first = FileBasedCache(
        str(tmp_path / "private-photo-cache"), {"OPTIONS": {"MAX_ENTRIES": 1000}}
    )
    second = FileBasedCache(str(tmp_path / "private-photo-cache"), {})
    first.set("photo", photo_delivery[1], timeout=2592000)
    assert second.get("photo") == photo_delivery[1]


def test_warm_command_populates_shared_cache_before_requests(
    client, listing, photo_delivery
):
    from django.core.management import call_command

    from market.thumbnails import listing_photo

    listing.photo.name = photo_delivery[0]
    listing.save(update_fields=["photo"])
    call_command("warm_photo_cache")
    url = listing_photo(listing, "card")["src"]
    with patch.object(default_storage, "open", side_effect=OSError("S3 unavailable")):
        assert client.get(url).status_code == 200
