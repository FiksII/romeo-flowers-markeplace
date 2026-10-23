from pathlib import Path

import pytest
from django.conf import settings
from PIL import Image

from market.models import Flower
from tests.test_catalogue_filters import make_listing

pytestmark = pytest.mark.django_db


def test_whole_flower_directory_has_real_images():
    flowers = list(Flower.objects.all())
    assert len(flowers) == 53
    for flower in flowers:
        image = flower.tag_image
        assert image
        asset = Path(settings.BASE_DIR) / "static" / image
        assert asset.is_file(), (flower.name, image)
        with Image.open(asset) as thumbnail:
            assert thumbnail.format == "WEBP"
            assert max(thumbnail.size) <= 240
            assert thumbnail.mode == "RGBA"
            assert thumbnail.getchannel("A").getextrema() == (0, 255)


def test_flower_rail_and_menu_show_same_available_bouquet_counts(client, shop, listing):
    white = Flower.objects.get(name="Роза белая")
    spray = Flower.objects.get(name="Роза кустовая")
    listing.flowers.set([white, spray])
    make_listing(shop, "Только белые", [white.name], price=4000)
    response = client.get("/")
    rail = {row["pk"]: row for row in response.context["flower_rail"]}
    assert rail[white.pk]["count"] == 2
    assert rail[spray.pk]["count"] == 1
    price_filtered = client.get("/", {"min_price": 3000})
    price_rail = {row["pk"]: row for row in price_filtered.context["flower_rail"]}
    assert price_rail[white.pk]["count"] == 1
    assert spray.pk not in price_rail
    assert rail[white.pk]["image"] != rail[spray.pk]["image"]
    html = response.content.decode()
    for flower, count in [(white, 2), (spray, 1)]:
        assert f'data-flower-count="{flower.pk}">{count}</small>' in html
        assert html.count(f"/static/{flower.tag_image}") >= 2  # Rail and menu.
    narrowed = client.get("/", {"flower": [spray.pk]})
    rail = {row["pk"]: row for row in narrowed.context["flower_rail"]}
    assert rail[white.pk]["count"] == 1
    assert rail[spray.pk]["count"] == 1


def test_selected_empty_flower_keeps_its_image_and_zero_count(client, listing):
    white = Flower.objects.get(name="Роза белая")
    response = client.get("/", {"flower": white.pk})
    selected = next(
        row for row in response.context["flower_rail"] if row["pk"] == white.pk
    )
    assert selected["active"] and selected["count"] == 0
    assert selected["image"] == white.tag_image
