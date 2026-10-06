from romeo_market.settings import *

DEBUG = True
MARKET_DEMO = True
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / ".demo.sqlite3",
    }
}
