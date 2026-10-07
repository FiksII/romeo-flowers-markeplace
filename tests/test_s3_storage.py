import json
import os
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

import pytest


def storage_process(script, **values):
    environment = (
        os.environ
        | {
            "PYTHONIOENCODING": "utf-8",
            "DJANGO_SETTINGS_MODULE": "romeo_market.settings",
            "S3_ENABLED": "True",
            "S3_BUCKET_NAME": "test-flowers",
            "S3_ENDPOINT_URL": "https://s3.ru1.storage.beget.cloud",
            "S3_REGION_NAME": "ru1",
            "S3_ACCESS_KEY_ID": "test-access-key",
            "S3_SECRET_ACCESS_KEY": "test-secret-key",
            "S3_LOCATION": "media",
            "S3_QUERYSTRING_AUTH": "True",
        }
        | values
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


URL_SCRIPT = """
import json
from django.core.files.storage import default_storage, storages
print(json.dumps({
    'photo': default_storage.url('listings/2026/10/rose.png'),
    'static': storages['staticfiles'].url('market/bouquets/bouquet-01.png'),
}))
"""


def test_s3_photo_url_uses_beget_bucket_and_signed_private_access():
    result = storage_process(URL_SCRIPT)
    assert result.returncode == 0, result.stderr
    urls = json.loads(result.stdout)
    photo = urlsplit(urls["photo"])
    assert photo.scheme == "https"
    assert photo.netloc == "s3.ru1.storage.beget.cloud"
    assert photo.path == "/test-flowers/media/listings/2026/10/rose.png"
    query = parse_qs(photo.query)
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert "/ru1/s3/aws4_request" in query["X-Amz-Credential"][0]
    assert "X-Amz-Signature" in query
    assert urls["static"] == "/static/market/bouquets/bouquet-01.png"


def test_s3_public_urls_are_opt_in():
    result = storage_process(URL_SCRIPT, S3_QUERYSTRING_AUTH="False")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["photo"] == (
        "https://s3.ru1.storage.beget.cloud/test-flowers/media/listings/2026/10/rose.png"
    )


def test_local_storage_works_with_empty_s3_credentials():
    result = storage_process(
        URL_SCRIPT,
        S3_ENABLED="False",
        S3_ACCESS_KEY_ID="",
        S3_SECRET_ACCESS_KEY="",
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["photo"] == "/media/listings/2026/10/rose.png"


@pytest.mark.parametrize(
    "missing", ["S3_BUCKET_NAME", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"]
)
def test_enabled_s3_rejects_missing_required_configuration(missing):
    result = storage_process(URL_SCRIPT, **{missing: ""})
    assert result.returncode != 0
    assert missing in result.stderr


def test_test_settings_never_use_live_bucket():
    result = storage_process(
        URL_SCRIPT, DJANGO_SETTINGS_MODULE="romeo_market.test_settings"
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["photo"] == "/media/listings/2026/10/rose.png"
