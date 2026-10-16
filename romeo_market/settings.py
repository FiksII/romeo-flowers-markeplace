from pathlib import Path

import dj_database_url
import environ
from django.core.exceptions import ImproperlyConfigured
from oscar import INSTALLED_APPS as OSCAR_APPS
from oscar.defaults import *

BASE_DIR = Path(__file__).resolve().parent.parent
env = environ.Env(DEBUG=(bool, False))
environ.Env.read_env(BASE_DIR / ".env")
DEBUG = env("DEBUG", default=False)
SECRET_KEY = env("SECRET_KEY", default="local-only-marketplace-change-in-production")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
INSTALLED_APPS = [
    "sorl.thumbnail",
    "market.apps.MarketConfig",
    "allauth",
    "allauth.account",
] + OSCAR_APPS
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "market.middleware.SmsErrorMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "market.middleware.ReservationExpiryMiddleware",
    "market.middleware.MarketplaceBasketMiddleware",
]
ROOT_URLCONF = "romeo_market.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "market.context_processors.market_context",
            ]
        },
    }
]
DATABASES = {
    "default": dj_database_url.parse(
        env("DATABASE_URL", default="postgresql://localhost/romeo_marketplace")
    )
}
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "ru"
LANGUAGES = [("ru", "Русский")]
TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
S3_ENABLED = env.bool("S3_ENABLED", default=False)
if S3_ENABLED:
    s3_required = {
        name: env(name, default="")
        for name in ("S3_BUCKET_NAME", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY")
    }
    for name, value in s3_required.items():
        if not value.strip():
            raise ImproperlyConfigured(f"При S3_ENABLED=True заполните {name}.")
    STORAGES["default"] = {
        # django-storages S3 that sorl-thumbnail can recreate without arguments.
        "BACKEND": "market.storage.MediaS3Storage",
        "OPTIONS": {
            "bucket_name": s3_required["S3_BUCKET_NAME"],
            "access_key": s3_required["S3_ACCESS_KEY_ID"],
            "secret_key": s3_required["S3_SECRET_ACCESS_KEY"],
            "endpoint_url": env(
                "S3_ENDPOINT_URL", default="https://s3.ru1.storage.beget.cloud"
            ),
            "region_name": env("S3_REGION_NAME", default="ru1"),
            "addressing_style": "path",
            "signature_version": "s3v4",
            "location": env("S3_LOCATION", default="media"),
            "default_acl": None,
            "file_overwrite": False,
            "querystring_auth": env.bool("S3_QUERYSTRING_AUTH", default=True),
            "querystring_expire": 3600,
        },
    }
SITE_ID = 1
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
OSCAR_DEFAULT_CURRENCY = "RUB"
OSCAR_SHOP_NAME = "Цветы от Ромео"
OSCAR_ALLOW_ANON_CHECKOUT = False
OSCAR_INITIAL_ORDER_STATUS = "AwaitingPayment"
OSCAR_ORDER_STATUS_PIPELINE = {
    "AwaitingPayment": ("Processing", "Cancelled"),
    "Processing": ("Completed", "Cancelled"),
    "Completed": (),
    "Cancelled": (),
}
HAYSTACK_CONNECTIONS = {
    "default": {"ENGINE": "haystack.backends.simple_backend.SimpleEngine"}
}
HAYSTACK_SIGNAL_PROCESSOR = "haystack.signals.BaseSignalProcessor"
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/account/orders/"
LOGOUT_REDIRECT_URL = "/"
MARKET_DEMO = False
MARKET_RESERVATION_MINUTES = 30
DADATA_TOKEN = env("DADATA_TOKEN", default="")
# A configured DaData key enables live addresses independently of demo orders.
ADDRESS_DEMO = env.bool("ADDRESS_DEMO", default=not bool(DADATA_TOKEN))
YANDEX_TILES_API_KEY = env("YANDEX_TILES_API_KEY", default="")
# Without a DaData token, addresses are searched on OpenStreetMap (no key needed).
OSM_ADDRESS_SEARCH = env.bool("OSM_ADDRESS_SEARCH", default=True)
OSM_USER_AGENT = env("OSM_USER_AGENT", default="romeo-marketplace/1.0")
EMAIL_BACKEND = "django.core.mail.backends.dummy.EmailBackend"

# Passwordless phone accounts; email is collected at the first checkout.
ACCOUNT_ADAPTER = "market.auth_adapter.MarketAccountAdapter"
ACCOUNT_SIGNUP_FIELDS = ["phone*"]
ACCOUNT_LOGIN_METHODS = {"phone"}
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_LOGIN_BY_CODE_ENABLED = True
ACCOUNT_PHONE_VERIFICATION_ENABLED = True
ACCOUNT_PHONE_VERIFICATION_TIMEOUT = 300
ACCOUNT_LOGIN_BY_CODE_TIMEOUT = 300
ACCOUNT_PHONE_VERIFICATION_MAX_ATTEMPTS = 3
ACCOUNT_LOGIN_BY_CODE_MAX_ATTEMPTS = 3
ACCOUNT_PHONE_VERIFICATION_SUPPORTS_RESEND = 3
ACCOUNT_LOGIN_BY_CODE_SUPPORTS_RESEND = 3
ACCOUNT_PHONE_VERIFICATION_CODE_FORMAT = {"length": 6, "numeric": True, "dashed": False}
ACCOUNT_RATE_LIMITS = {
    "verify_phone": "1/m/key,20/h/ip",
    "request_login_code": "1/m/key,20/h/ip",
    "sms_send": "1/m/key,20/h/ip",
    "sms_send_hourly": "5/h/key",
}
SMSPILOT_API_KEY = env("SMSPILOT_API_KEY", default="")
SMSPILOT_SENDER = env("SMSPILOT_SENDER", default="")
# Public contacts for the footer. Empty values are not shown: they await the owner's approval.
SUPPORT_PHONE = env("SUPPORT_PHONE", default="")
SUPPORT_EMAIL = env("SUPPORT_EMAIL", default="")
TELEGRAM_URL = env("TELEGRAM_URL", default="")
INSTAGRAM_URL = env("INSTAGRAM_URL", default="")
# Shared between Gunicorn workers; must be writable by the application user.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "market_cache",
    }
}
WSGI_APPLICATION = "romeo_market.wsgi.application"
ASGI_APPLICATION = "romeo_market.asgi.application"
