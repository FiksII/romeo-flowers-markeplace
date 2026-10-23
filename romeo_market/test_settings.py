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
SMSPILOT_API_KEY = ""
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
# Tests talk to a fake T-Bank; the local .env terminal must never be charged.
TBANK_TERMINAL_KEY = ""
TBANK_PASSWORD = ""
TBANK_TAXATION = ""
MARKET_ONLINE_PAYMENTS = False
# Tests talk to fake messengers; a local bot or community must never be messaged.
SITE_URL = "https://flowers.test"
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_BOT_USERNAME = ""
MAX_BOT_TOKEN = ""
MAX_BOT_USERNAME = ""
VK_GROUP_ID = ""
VK_GROUP_TOKEN = ""
VK_CONFIRMATION_CODE = ""
MESSENGER_BACKGROUND = False
