from hashlib import file_digest, sha256
from pathlib import Path, PurePosixPath

from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.exceptions import SuspiciousFileOperation
from django.core.files import File
from django.core.files.storage import FileSystemStorage, default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from market.models import Listing


class Command(BaseCommand):
    help = "Копирует локальные фото товаров в S3, сохраняя исходники."

    def add_arguments(self, parser):
        parser.add_argument("--source-dir", type=Path, default=settings.MEDIA_ROOT)
        parser.add_argument("--include-seed", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        if not settings.S3_ENABLED or isinstance(default_storage, FileSystemStorage):
            raise CommandError("Для переноса настройте S3_ENABLED=True.")
        source_root = options["source_dir"].resolve()
        query = ~Q(photo="")
        if options["include_seed"]:
            query |= Q(photo="") & ~Q(seed_image="")
        migrated = 0
        listings = Listing.objects.filter(query).only("pk", "photo", "seed_image")
        for listing in listings.order_by("pk").iterator():
            original = listing.photo.name
            try:
                name = original or f"listings/seed/{listing.seed_image}"
                # Validate both database filenames and the destination key.
                relative = PurePosixPath(original or listing.seed_image)
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or "\\" in str(relative)
                ):
                    raise CommandError(f"Недопустимый путь фото товара {listing.pk}.")
                if original:
                    source = (source_root / original).resolve()
                    if not source.is_relative_to(source_root):
                        raise CommandError(f"Фото товара {listing.pk} вне source-dir.")
                else:
                    found = finders.find(listing.seed_image)
                    source = Path(found) if found else None
                if original and source.is_file():
                    # Imported keys cannot collide with partner uploads, whose
                    # upload_to path is listings/YYYY/MM/. A content hash also
                    # makes retrying an interrupted import reuse the same key.
                    with source.open("rb") as photo:
                        fingerprint = file_digest(photo, "sha256").hexdigest()
                    name = f"listings/imported/{fingerprint}{source.suffix.lower()}"
                if options["dry_run"]:
                    self.stdout.write(f"Товар {listing.pk}: {source} -> {name}")
                    migrated += 1
                    continue
                exists = default_storage.exists(name)
                if source is None or not source.is_file():
                    if original and exists:
                        self.stdout.write(f"Товар {listing.pk}: уже в S3 ({name}).")
                        continue
                    raise CommandError(f"Исходник фото товара {listing.pk} не найден.")
                with source.open("rb") as photo:
                    if exists:
                        source_hash = file_digest(photo, "sha256").digest()
                        with default_storage.open(name, "rb") as remote:
                            # S3 files support read(), but may not support readinto().
                            digest = sha256()
                            for chunk in iter(lambda: remote.read(1024 * 1024), b""):
                                digest.update(chunk)
                        if source_hash != digest.digest():
                            raise CommandError(
                                f"Фото {name} в S3 отличается от исходника; перенос остановлен."
                            )
                    else:
                        name = default_storage.save(
                            name,
                            File(photo),
                            max_length=Listing._meta.get_field("photo").max_length,
                        )
                # Do not overwrite a photo replaced by a partner during the copy.
                updated = Listing.objects.filter(
                    pk=listing.pk, photo=original, seed_image=listing.seed_image
                ).update(photo=name)
                migrated += updated
                self.stdout.write(f"Товар {listing.pk}: {name}")
            except (
                OSError,
                BotoCoreError,
                ClientError,
                SuspiciousFileOperation,
            ) as error:
                raise CommandError(
                    f"Ошибка переноса фото товара {listing.pk}: {error}"
                ) from error
        self.stdout.write(
            f"{'Проверка без изменений' if options['dry_run'] else 'Перенос завершён'}: {migrated} фото."
        )
