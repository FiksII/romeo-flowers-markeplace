from pathlib import Path

import dj_database_url
import environ
from oscar import INSTALLED_APPS as OSCAR_APPS
from oscar.defaults import *

BASE_DIR = Path(__file__).resolve().parent.parent
env = environ.Env(DEBUG=(bool, False))
environ.Env.read_env(BASE_DIR / ".env")
DEBUG = env("DEBUG", default=False)
SECRET_KEY = env("SECRET_KEY", default="local-only-marketplace-change-in-production")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
INSTALLED_APPS = ["sorl.thumbnail", "market.apps.MarketConfig"] + OSCAR_APPS
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
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
EMAIL_BACKEND = "django.core.mail.backends.dummy.EmailBackend"
WSGI_APPLICATION = "romeo_market.wsgi.application"
ASGI_APPLICATION = "romeo_market.asgi.application"
