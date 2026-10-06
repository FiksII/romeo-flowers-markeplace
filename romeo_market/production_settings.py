from django.core.exceptions import ImproperlyConfigured

from .settings import *

SECRET_KEY = env("SECRET_KEY")
if len(SECRET_KEY) < 50 or SECRET_KEY.startswith("local-only"):
    raise ImproperlyConfigured(
        "Задайте случайный SECRET_KEY длиной не менее 50 символов."
    )
DATABASES = {"default": dj_database_url.parse(env("DATABASE_URL"), conn_max_age=60)}
if "postgresql" not in DATABASES["default"]["ENGINE"]:
    raise ImproperlyConfigured("Для рабочего маркетплейса требуется PostgreSQL.")
DEBUG = False
MARKET_DEMO = False
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
OSCAR_BASKET_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_CONTENT_TYPE_NOSNIFF = True
if env.bool("TRUST_PROXY_HTTPS", default=False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
