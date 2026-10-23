"""Bouquet gallery integration with Oscar's existing ProductImage model."""

import logging
import uuid

from django import forms
from django.core.exceptions import ValidationError
from oscar.core.loading import get_model

logger = logging.getLogger(__name__)
MAX_PHOTOS = 20
MAX_PHOTO_BYTES = 5 * 1024 * 1024
PHOTO_ERROR = "Используйте JPEG, PNG или WebP до 5 МБ."


def validate_photo(upload):
    if upload.size > MAX_PHOTO_BYTES:
        raise ValidationError(PHOTO_ERROR)
    image = forms.ImageField().clean(upload)
    if image.image.format not in {"JPEG", "PNG", "WEBP"}:
        raise ValidationError(PHOTO_ERROR)
    return image


class MultiplePhotoInput(forms.FileInput):
    allow_multiple_selected = True


class MultiplePhotoField(forms.ImageField):
    widget = MultiplePhotoInput

    def clean(self, data, initial=None):
        if not data:
            return []
        uploads = list(data) if isinstance(data, (list, tuple)) else [data]
        if len(uploads) > MAX_PHOTOS:
            raise ValidationError(
                f"Можно сохранить не больше {MAX_PHOTOS} фотографий букета."
            )
        return [validate_photo(upload) for upload in uploads]


def gallery_items(listing):
    """Existing photos with a virtual legacy row until it is registered on save."""
    if listing is None:
        return []
    images = list(listing.product.images.all())
    rows = [{"id": str(image.pk), "file": image.original} for image in images]
    if listing.photo and not any(i.original.name == listing.photo.name for i in images):
        rows.insert(0, {"id": "legacy", "file": listing.photo})
    return rows


def save_photos(listing, uploads, remove_ids=(), primary_id=None):
    """Called after Product/Listing locks, inside save_listing's atomic block."""
    Image = get_model("catalogue", "ProductImage")
    uploads = (
        list(uploads)
        if isinstance(uploads, (list, tuple))
        else [uploads]
        if uploads
        else []
    )
    # Direct callers (including Telegram) get the same validation as HTTP forms.
    uploads = [validate_photo(upload) for upload in uploads]
    rows = gallery_items(listing)
    valid_ids = {row["id"] for row in rows}
    remove_ids = {str(pk) for pk in remove_ids}
    primary_id = str(primary_id) if primary_id else None
    if (
        not remove_ids.issubset(valid_ids)
        or primary_id not in (None, "new")
        and primary_id not in valid_ids
    ):
        raise ValidationError(
            "Фотографии изменились. Обновите страницу и повторите сохранение."
        )
    if primary_id == "new" and not uploads:
        raise ValidationError("Загрузите новое фото или выберите существующее главным.")
    if len(rows) - len(remove_ids) + len(uploads) > MAX_PHOTOS:
        raise ValidationError(
            f"Можно сохранить не больше {MAX_PHOTOS} фотографий букета."
        )
    images = {str(image.pk): image for image in listing.product.images.all()}
    kept = []
    for row in rows:
        if row["id"] in remove_ids:
            if row["id"] in images:
                # Keep source files: historical links and rollback can still use them.
                Image.objects.filter(pk=row["id"]).delete()
            continue
        image = images.get(row["id"])
        if image is None:
            image = Image.objects.create(
                product=listing.product, original=row["file"].name
            )
        kept.append((row["id"], image))
    written = []
    try:
        for index, upload in enumerate(uploads):
            image = Image(product=listing.product)
            # Listing.photo is a 100-character pointer. Generated names also avoid
            # leaking client filenames into public storage keys.
            name = f"{uuid.uuid4().hex}.{upload.image.format.lower()}"
            image.original.save(name, upload, save=False)
            written.append((image.original.storage, image.original.name))
            image.save()
            kept.append(("new" if index == 0 else f"new:{index}", image))
        if primary_id and primary_id not in remove_ids:
            kept.sort(key=lambda pair: pair[0] != primary_id)
        for order, (_, image) in enumerate(kept):
            if image.display_order != order:
                image.display_order = order
                image.save(update_fields=["display_order"])
        listing.photo = kept[0][1].original.name if kept else ""
        listing.save(update_fields=["photo"])
        listing.product._prefetched_objects_cache = {}
        listing._uploaded_photo_files = written
    except Exception:
        for storage, name in written:
            try:
                storage.delete(name)
            except Exception:
                logger.exception("Could not clean up failed bouquet upload")
        raise
