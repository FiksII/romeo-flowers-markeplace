"""Resized listing photos from sorl-thumbnail, with the original photo as a fallback."""

import logging

from sorl.thumbnail import get_thumbnail

logger = logging.getLogger(__name__)

# Photo frames are 4:5. The second size is for high-density screens via srcset.
PRESETS = {
    "card": ("400x500", "800x1000"),
    "detail": ("640x800", "1000x1250"),
    "line": ("100x125", "200x250"),
}
OPTIONS = {"crop": "center", "format": "WEBP", "quality": 80, "upscale": False}


def listing_photo(listing, preset):
    """``src`` and ``srcset`` of the listing photo resized for one of ``PRESETS``."""
    original = {"src": listing.image_url, "srcset": ""}
    if not listing.photo:
        return original
    return image_photo(listing.photo, preset)


def image_photo(photo, preset):
    """Use the same thumbnail presets for every Oscar gallery image."""
    original = {"src": photo.url, "srcset": ""}
    try:
        images = [
            get_thumbnail(photo, size, **OPTIONS) for size in PRESETS[preset]
        ]
    except Exception:
        logger.exception("Could not resize bouquet photo %s", photo.name)
        return original
    # sorl returns an image without size when it cannot read the source.
    if not all(image.size for image in images):
        return original
    return {
        "src": images[0].url,
        "srcset": ", ".join(f"{image.url} {image.width}w" for image in images),
    }
