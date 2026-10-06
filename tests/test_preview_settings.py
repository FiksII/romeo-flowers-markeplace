import os
import subprocess
import sys


def test_public_preview_keeps_production_security():
    environment = os.environ | {
        "SECRET_KEY": "preview-test-only-" + "x" * 60,
        "DATABASE_URL": "postgresql://preview:unused@127.0.0.1:5432/preview_test",
        "ALLOWED_HOSTS": "preview.example.invalid",
        "TRUST_PROXY_HTTPS": "True",
    }
    checked = subprocess.run(
        [
            sys.executable,
            "-c",
            "from romeo_market import preview_settings as s; assert not s.DEBUG; assert s.MARKET_DEMO; assert s.SECURE_SSL_REDIRECT; assert s.SESSION_COOKIE_SECURE and s.CSRF_COOKIE_SECURE and s.OSCAR_BASKET_COOKIE_SECURE; assert s.SECURE_PROXY_SSL_HEADER == ('HTTP_X_FORWARDED_PROTO', 'https'); assert 'postgresql' in s.DATABASES['default']['ENGINE']; assert s.ALLOWED_HOSTS == ['preview.example.invalid']",
        ],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert checked.returncode == 0, checked.stderr
