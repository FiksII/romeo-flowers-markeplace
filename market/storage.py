from django.conf import settings
from storages.backends.s3 import S3Storage


class MediaS3Storage(S3Storage):
    """Beget S3 storage that keeps the project options when created without arguments.

    sorl-thumbnail remembers the storage class path of every thumbnail and later
    recreates the storage without arguments, so the options from ``STORAGES``
    are applied here and not only by Django.
    """

    def __init__(self, **overrides):
        options = settings.STORAGES["default"].get("OPTIONS", {})
        super().__init__(**{**options, **overrides})
