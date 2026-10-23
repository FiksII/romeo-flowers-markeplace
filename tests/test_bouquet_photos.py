from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from oscar.core.loading import get_model
from PIL import Image

from market.forms import ProductForm, save_listing
from tests.test_cabinet import product_data

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def local_media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


def photo(name="rose.png", color="pink"):
    stream = BytesIO()
    Image.new("RGB", (40, 50), color).save(stream, "PNG")
    return SimpleUploadedFile(name, stream.getvalue(), content_type="image/png")


def upload(client, owner, shop, listing=None, **data):
    client.force_login(owner)
    path = (
        f"/partner/{shop.slug}/products/{listing.pk}/"
        if listing
        else f"/partner/{shop.slug}/products/new/"
    )
    return client.post(path, product_data(**data))


def test_cabinet_uploads_all_photos_and_uses_first_as_primary(client, owner, shop):
    response = upload(client, owner, shop, photo=[photo(), photo("side.png", "red")])
    assert response.status_code == 302
    listing = shop.listings.get()
    images = list(listing.product.images.all())
    assert len(images) == 2
    assert [i.display_order for i in images] == [0, 1]
    assert listing.photo.name == images[0].original.name
    page = client.get(f"/partner/{shop.slug}/products/{listing.pk}/")
    assert b"multiple" in page.content
    assert b'name="remove_photos"' in page.content
    assert b'name="primary_photo"' in page.content


def test_every_upload_is_validated_before_saving(client, owner, shop):
    broken = SimpleUploadedFile("bad.png", b"not an image", content_type="image/png")
    response = upload(client, owner, shop, photo=[broken, photo()])
    assert response.status_code == 200
    assert not shop.listings.exists()
    assert "photo" in response.context["form"].errors


def test_twenty_one_images_are_rejected(client, owner, shop):
    response = upload(client, owner, shop, photo=[photo(f"{i}.png") for i in range(21)])
    assert response.status_code == 200
    assert not shop.listings.exists()


def test_change_primary_delete_and_append(client, owner, shop):
    upload(client, owner, shop, photo=[photo(), photo("side.png"), photo("back.png")])
    listing = shop.listings.get()
    first, second, third = listing.product.images.all()
    response = upload(
        client,
        owner,
        shop,
        listing,
        primary_photo=str(third.pk),
        remove_photos=[str(first.pk)],
        photo=[photo("new.png")],
    )
    assert response.status_code == 302
    listing.refresh_from_db()
    images = list(listing.product.images.all())
    assert [i.pk for i in images[:2]] == [third.pk, second.pk]
    assert len(images) == 3
    assert listing.photo.name == third.original.name
    assert first.original.storage.exists(first.original.name)


def test_primary_removal_promotes_remaining_photo(client, owner, shop):
    upload(client, owner, shop, photo=[photo(), photo("side.png")])
    listing = shop.listings.get()
    first, second = listing.product.images.all()
    response = upload(
        client,
        owner,
        shop,
        listing,
        primary_photo=str(first.pk),
        remove_photos=[str(first.pk)],
    )
    assert response.status_code == 302
    listing.refresh_from_db()
    assert listing.photo.name == second.original.name


def test_legacy_photo_is_preserved_when_adding_images(client, owner, shop, listing):
    listing.photo.save("legacy.png", photo())
    old = listing.photo.name
    response = upload(client, owner, shop, listing, photo=[photo("new.png")])
    assert response.status_code == 302
    listing.refresh_from_db()
    assert listing.product.images.first().original.name == old
    assert listing.product.images.count() == 2
    assert listing.photo.name == old


@pytest.mark.parametrize("field", ["remove_photos", "primary_photo"])
def test_cannot_change_another_bouquets_photos(
    client, owner, shop, listing, other_shop, field
):
    other = save_listing(other_shop, product_data(photo=photo()))
    foreign = (
        other.product.images.get()
        if other.product.images.exists()
        else get_model("catalogue", "ProductImage").objects.create(
            product=other.product, original=other.photo.name
        )
    )
    value = [str(foreign.pk)] if field == "remove_photos" else str(foreign.pk)
    response = upload(client, owner, shop, listing, **{field: value})
    assert response.status_code == 200
    assert get_model("catalogue", "ProductImage").objects.filter(pk=foreign.pk).exists()
    listing.refresh_from_db()
    assert listing.product.title == "Нежность"


def test_existing_photos_count_towards_limit(client, owner, shop):
    upload(client, owner, shop, photo=[photo(f"{i}.png") for i in range(20)])
    listing = shop.listings.get()
    response = upload(client, owner, shop, listing, photo=[photo("extra.png")])
    assert response.status_code == 200
    assert listing.product.images.count() == 20


def test_storage_failure_rolls_back_and_cleans_new_files(
    shop, monkeypatch, local_media
):
    from django.core.files.storage import default_storage

    original = default_storage.save
    calls = []

    def failing_save(name, content, max_length=None):
        calls.append(name)
        if len(calls) == 2:
            raise OSError("storage unavailable")
        return original(name, content, max_length=max_length)

    monkeypatch.setattr(default_storage, "save", failing_save)
    form = ProductForm(
        product_data(), {"photo": [photo(), photo("side.png")]}, shop=shop
    )
    assert form.is_valid(), form.errors
    with pytest.raises(OSError, match="storage unavailable"):
        save_listing(shop, form.cleaned_data)
    assert not shop.listings.exists()
    assert not [p for p in local_media.rglob("*") if p.is_file()]


def test_customer_detail_has_each_gallery_photo_and_primary_link(client, owner, shop):
    upload(client, owner, shop, photo=[photo(), photo("side.png"), photo("back.png")])
    listing = shop.listings.get()
    client.logout()
    response = client.get(f"/products/{listing.pk}/")
    html = response.content.decode()
    assert "data-bouquet-gallery" in html
    assert html.count("data-gallery-thumbnail") == 3
    assert '<dialog' in html and 'data-gallery-dialog' in html
    assert 'data-gallery-close' in html
    for image in listing.product.images.all():
        assert image.original.url in html
    assert 'aria-label="Фотографии букета"' in html


def test_customer_single_legacy_photo_has_no_empty_gallery_controls(client, listing):
    listing.photo.save("legacy.png", photo())
    html = client.get(f"/products/{listing.pk}/").content.decode()
    assert 'class="detail-photo"' in html
    assert "data-gallery-thumbnail" not in html
    assert 'data-gallery-dialog' in html


def test_new_upload_can_become_primary(client, owner, shop):
    upload(client, owner, shop, photo=[photo()])
    listing = shop.listings.get()
    old = listing.photo.name
    response = upload(
        client, owner, shop, listing, photo=[photo("side.png")], primary_photo="new"
    )
    assert response.status_code == 302
    listing.refresh_from_db()
    assert listing.photo.name != old
    assert listing.photo.name == listing.product.images.first().original.name


def test_delete_all_photos_clears_primary(client, owner, shop):
    upload(client, owner, shop, photo=[photo(), photo("side.png")])
    listing = shop.listings.get()
    response = upload(
        client,
        owner,
        shop,
        listing,
        remove_photos=[str(p.pk) for p in listing.product.images.all()],
    )
    assert response.status_code == 302
    listing.refresh_from_db()
    assert not listing.photo
    assert not listing.product.images.exists()


def test_legacy_registration_is_idempotent_and_does_not_copy_file(listing):
    import importlib

    from django.apps import apps
    from django.db import connection

    listing.photo.save("legacy.png", photo())
    migration = importlib.import_module("market.migrations.0025_bouquet_gallery")
    from types import SimpleNamespace

    editor = SimpleNamespace(connection=connection)
    migration.register_photos(apps, editor)
    migration.register_photos(apps, editor)
    assert listing.product.images.count() == 1
    assert listing.product.images.get().original.name == listing.photo.name


def test_failed_append_does_not_lose_removed_photo_or_change_primary(
    client, owner, shop, monkeypatch
):
    from django.core.files.storage import default_storage

    upload(client, owner, shop, photo=[photo(), photo("side.png")])
    listing = shop.listings.get()
    first = listing.product.images.first()
    original_name = listing.photo.name

    def fail(*args, **kwargs):
        raise OSError("storage unavailable")

    monkeypatch.setattr(default_storage, "save", fail)
    with pytest.raises(OSError):
        upload(
            client,
            owner,
            shop,
            listing,
            remove_photos=[str(first.pk)],
            photo=[photo("new.png")],
        )
    listing.refresh_from_db()
    assert listing.photo.name == original_name
    assert listing.product.images.count() == 2
    assert default_storage.exists(original_name)


def test_long_upload_name_fits_existing_primary_photo_column(client, owner, shop):
    response = upload(client, owner, shop, photo=[photo("rose" * 50 + ".png")])
    assert response.status_code == 302
    listing = shop.listings.get()
    assert len(listing.photo.name) <= 100
    assert listing.product.images.first().original.name == listing.photo.name


def test_any_new_photo_can_be_selected_as_primary(client, owner, shop):
    response = upload(client, owner, shop, photo=[photo(), photo("side.png"), photo("back.png")], primary_photo="new:2")
    assert response.status_code == 302
    listing = shop.listings.get()
    images = list(listing.product.images.all())
    assert len(images) == 3
    assert images[0].original.name == listing.photo.name
    # The selected third upload is promoted; the first two retain their order.
    assert images[0].pk > images[1].pk
    assert images[1].pk < images[2].pk
