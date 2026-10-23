from io import BytesIO

import pytest
from django.core.cache import cache
from django.core.files.base import ContentFile
from PIL import Image

from market.thumbnails import listing_photo


@pytest.fixture
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    # sorl keeps thumbnail records in the cache; other tests must not see them.
    cache.clear()
    yield tmp_path
    cache.clear()


def png(size=(1254, 1254)):
    buffer = BytesIO()
    Image.new("RGB", size, (232, 180, 190)).save(buffer, "PNG")
    return ContentFile(buffer.getvalue())


def thumbnail_files(media, srcset):
    for candidate in srcset.split(", "):
        url, width = candidate.split(" ")
        yield media / url.removeprefix("/media/"), width


def test_card_photo_is_resized_to_webp_in_four_widths(listing, media):
    listing.photo.save("rose.png", png())
    photo = listing_photo(listing, "card")
    assert photo["src"].startswith("/media/cache/") and photo["src"].endswith(".webp")
    files = list(thumbnail_files(media, photo["srcset"]))
    assert [width for _, width in files] == ["320w", "400w", "600w", "800w"]
    assert photo["src"] == f"/media/{files[0][0].relative_to(media).as_posix()}"
    sizes = []
    for path, _ in files:
        with Image.open(path) as image:
            assert image.format == "WEBP"
            sizes.append(image.size)
    assert sizes == [(320, 400), (400, 500), (600, 750), (800, 1000)]


def test_small_photo_is_not_upscaled(listing, media):
    listing.photo.save("small.png", png((300, 300)))
    photo = listing_photo(listing, "card")
    files = list(thumbnail_files(media, photo["srcset"]))
    for path, width in files:
        with Image.open(path) as image:
            assert image.width <= 300 and width == f"{image.width}w"


def test_listing_without_photo_keeps_its_original_image(listing):
    assert listing_photo(listing, "card") == {"src": listing.image_url, "srcset": ""}


def test_unreadable_photo_falls_back_to_the_original(listing, media):
    listing.photo.save("broken.png", ContentFile(b"not an image"))
    assert listing_photo(listing, "detail") == {
        "src": listing.image_url,
        "srcset": "",
    }


def test_catalogue_card_and_product_page_use_resized_photos(client, listing, media):
    listing.photo.save("rose.png", png())
    html = client.get("/").content.decode()
    assert 'srcset="/media/cache/' in html and ".webp 800w" in html
    assert 'sizes="(max-width: 759px) calc((100vw - 44px) / 2)' in html
    assert 'loading="eager"' in html
    assert listing.photo.url not in html
    detail = client.get(f"/products/{listing.pk}/").content.decode()
    assert 'class="detail-photo" src="/media/cache/' in detail
    assert ".webp 1000w" in detail


def test_small_photo_has_no_duplicate_srcset_widths(listing, media):
    listing.photo.save("small.png", png((300, 300)))
    photo = listing_photo(listing, "card")
    widths = [width for _, width in thumbnail_files(media, photo["srcset"])]
    assert len(widths) == len(set(widths))


def test_s3_thumbnail_urls_are_stable_same_origin(listing, media, settings, client):
    listing.photo.save("rose.png", png())
    settings.S3_ENABLED = True
    first = listing_photo(listing, "card")
    assert first == listing_photo(listing, "card")
    assert first["src"].startswith("/photos/")
    assert client.get(first["src"]).status_code == 200
