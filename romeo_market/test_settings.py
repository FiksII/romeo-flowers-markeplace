from romeo_market.settings import *

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
# Tests must not upload to the real bucket configured in the developer's .env.
S3_ENABLED = False
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
OSM_ADDRESS_SEARCH = False
# Never call paid/external providers using local .env keys during tests.
DADATA_TOKEN = ""
ADDRESS_DEMO = True
YANDEX_TILES_API_KEY = ""
