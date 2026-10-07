from io import StringIO

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.base import CommandError
from oscar.core.loading import get_model

from market.models import Listing, Shop


@pytest.fixture
def photo_listing(city):
    partner = get_model("partner", "Partner").objects.create(name="Photo shop")
    shop = Shop.objects.create(
        partner=partner,
        name="Photo shop",
        slug="photo-shop",
        settlement=city,
        latitude="55.75",
        longitude="37.61",
    )
    product = get_model("catalogue", "Product").objects.create(title="Rose bouquet")
    return Listing.objects.create(
        shop=shop, product=product, seed_image="market/bouquets/bouquet-01.png"
    )


@pytest.fixture
def target_storage(settings):
    settings.S3_ENABLED = True
    settings.STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }
    return default_storage


def test_seed_photo_moves_to_storage_and_rerun_reuses_it(photo_listing, target_storage):
    call_command("migrate_listing_photos", include_seed=True, stdout=StringIO())
    photo_listing.refresh_from_db()
    assert photo_listing.photo.name == "listings/seed/market/bouquets/bouquet-01.png"
    assert (
        photo_listing.image_url == "/media/listings/seed/market/bouquets/bouquet-01.png"
    )
    with target_storage.open(photo_listing.photo.name) as photo:
        assert photo.read(8) == b"\x89PNG\r\n\x1a\n"
    # Recover from an upload that succeeded before the database was updated.
    photo_listing.photo = ""
    photo_listing.save(update_fields=["photo"])
    call_command("migrate_listing_photos", include_seed=True, stdout=StringIO())
    assert target_storage.listdir("listings/seed/market/bouquets")[1] == [
        "bouquet-01.png"
    ]


def test_local_uploaded_photo_is_copied_without_removing_source(
    photo_listing, target_storage, tmp_path
):
    name = "listings/2026/10/upload.png"
    source = tmp_path / name
    source.parent.mkdir(parents=True)
    source.write_bytes(b"uploaded image")
    photo_listing.photo = name
    photo_listing.save(update_fields=["photo"])
    call_command("migrate_listing_photos", source_dir=tmp_path, stdout=StringIO())
    photo_listing.refresh_from_db()
    assert photo_listing.photo.name.startswith("listings/imported/")
    with target_storage.open(photo_listing.photo.name) as photo:
        assert photo.read() == b"uploaded image"
    assert source.read_bytes() == b"uploaded image"


def test_imported_photo_never_shares_the_key_used_by_new_partner_uploads(
    photo_listing, target_storage, tmp_path
):
    name = "listings/2026/10/rose.png"
    source = tmp_path / name
    source.parent.mkdir(parents=True)
    source.write_bytes(b"old local photo")
    photo_listing.photo = name
    photo_listing.save(update_fields=["photo"])
    target_storage.save(name, ContentFile(b"new partner photo"))
    call_command("migrate_listing_photos", source_dir=tmp_path, stdout=StringIO())
    with target_storage.open(name) as photo:
        assert photo.read() == b"new partner photo"
    photo_listing.refresh_from_db()
    assert photo_listing.photo.name.startswith("listings/imported/")
    with target_storage.open(photo_listing.photo.name) as photo:
        assert photo.read() == b"old local photo"


def test_dry_run_does_not_upload_or_change_listing(photo_listing, target_storage):
    call_command(
        "migrate_listing_photos", include_seed=True, dry_run=True, stdout=StringIO()
    )
    photo_listing.refresh_from_db()
    assert not photo_listing.photo
    assert target_storage.listdir("") == ([], [])


def test_conflicting_remote_photo_is_not_overwritten(photo_listing, target_storage):
    name = "listings/seed/market/bouquets/bouquet-01.png"
    target_storage.save(name, ContentFile(b"another image"))
    with pytest.raises(CommandError, match="different|отличается"):
        call_command("migrate_listing_photos", include_seed=True, stdout=StringIO())
    photo_listing.refresh_from_db()
    assert not photo_listing.photo
    with target_storage.open(name) as photo:
        assert photo.read() == b"another image"


def test_missing_source_does_not_replace_photo(photo_listing, target_storage, tmp_path):
    photo_listing.photo = "listings/missing.png"
    photo_listing.save(update_fields=["photo"])
    with pytest.raises(CommandError, match="missing|не найден"):
        call_command("migrate_listing_photos", source_dir=tmp_path, stdout=StringIO())
    assert target_storage.listdir("") == ([], [])


def test_seed_photos_require_explicit_option(photo_listing, target_storage):
    call_command("migrate_listing_photos", stdout=StringIO())
    photo_listing.refresh_from_db()
    assert not photo_listing.photo
    assert target_storage.listdir("") == ([], [])


def test_migration_rejects_local_storage(settings):
    settings.S3_ENABLED = False
    with pytest.raises(CommandError, match="S3_ENABLED"):
        call_command("migrate_listing_photos", stdout=StringIO())


def test_failed_upload_keeps_current_listing_photo(
    photo_listing, target_storage, monkeypatch
):
    def fail_upload(*args, **kwargs):
        raise OSError("upload unavailable")

    monkeypatch.setattr(target_storage, "save", fail_upload)
    with pytest.raises(CommandError, match="upload unavailable"):
        call_command("migrate_listing_photos", include_seed=True, stdout=StringIO())
    photo_listing.refresh_from_db()
    assert not photo_listing.photo


def test_migration_cannot_overwrite_concurrent_partner_photo(
    photo_listing, target_storage, monkeypatch
):
    save = target_storage.save

    def upload_while_partner_replaces_photo(*args, **kwargs):
        name = save(*args, **kwargs)
        Listing.objects.filter(pk=photo_listing.pk).update(
            photo="listings/new-photo.png"
        )
        return name

    monkeypatch.setattr(target_storage, "save", upload_while_partner_replaces_photo)
    call_command("migrate_listing_photos", include_seed=True, stdout=StringIO())
    photo_listing.refresh_from_db()
    assert photo_listing.photo.name == "listings/new-photo.png"


def test_migration_rejects_paths_outside_source(photo_listing, target_storage):
    photo_listing.photo = "../private.png"
    photo_listing.save(update_fields=["photo"])
    with pytest.raises(CommandError, match="Недопустимый путь"):
        call_command("migrate_listing_photos", stdout=StringIO())
    assert target_storage.listdir("") == ([], [])
