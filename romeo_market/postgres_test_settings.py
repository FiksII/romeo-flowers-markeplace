from .test_settings import *

DATABASES = {"default": dj_database_url.parse(env("MARKET_TEST_DATABASE_URL"))}
