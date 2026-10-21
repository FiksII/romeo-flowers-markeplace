import json

import pytest
from django.core.exceptions import ValidationError

from market.forms import ShopForm, ShopInfoForm
from market.models import Shop
from market.zones import clean_zone, point_in_zone
from tests.test_partner_workflows import shop_data

pytestmark = pytest.mark.django_db

RING = [[37.5, 55.6], [37.8, 55.6], [37.8, 55.9], [37.5, 55.9], [37.5, 55.6]]
SECOND = [[38.4, 55.6], [38.6, 55.6], [38.6, 55.9], [38.4, 55.9], [38.4, 55.6]]


def collection(*rings):
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"area_id": str(i), "name": "Территория"},
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            }
            for i, ring in enumerate(rings)
        ],
    }


@pytest.mark.parametrize("form_class", [ShopForm, ShopInfoForm])
@pytest.mark.parametrize(
    "own,pickup", [(False, False), (False, True), (True, False), (True, True)]
)
def test_receiving_options_always_enable_one_delivery_provider(
    shop, form_class, own, pickup
):
    Shop.objects.filter(pk=shop.pk).update(delivery_enabled=False)
    shop.refresh_from_db()
    data = {
        **shop_data(shop),
        "own_delivery": own,
        "pickup_enabled": pickup,
        "delivery_zone": json.dumps(collection(RING)),
    }
    data.pop("delivery_enabled", None)
    form = form_class(data, instance=shop)
    assert "delivery_enabled" not in form.fields
    assert form.is_valid(), form.errors
    saved = form.save()
    saved.refresh_from_db()
    assert saved.delivery_enabled
    assert saved.own_delivery == own
    assert saved.pickup_enabled == pickup
    assert point_in_zone(55.75, 37.6, saved.delivery_zone)


@pytest.mark.parametrize("value", ["", "[]", json.dumps(collection())])
def test_zone_is_required_for_shop_setup(shop, value):
    form = ShopForm({**shop_data(shop), "delivery_zone": value}, instance=shop)
    assert not form.is_valid()
    assert "delivery_zone" in form.errors


def test_disjoint_delivery_areas_do_not_deliver_between_them():
    zone = clean_zone(collection(RING, SECOND))
    assert point_in_zone(55.75, 37.6, zone)
    assert point_in_zone(55.75, 38.5, zone)
    assert not point_in_zone(55.75, 38.1, zone)
    assert point_in_zone(55.6, 37.5, zone)


def test_delivery_area_holes_are_excluded():
    zone = collection(RING)
    zone["features"][0]["geometry"]["coordinates"].append(
        [[37.6, 55.7], [37.7, 55.7], [37.7, 55.8], [37.6, 55.8], [37.6, 55.7]]
    )
    zone = clean_zone(zone)
    assert not point_in_zone(55.75, 37.65, zone)
    assert point_in_zone(55.65, 37.55, zone)


@pytest.mark.parametrize(
    "geometry",
    [
        {"type": "Point", "coordinates": [37.6, 55.7]},
        {
            "type": "Polygon",
            "coordinates": [
                [[37.5, 55.6], [37.8, 55.9], [37.8, 55.6], [37.5, 55.9], [37.5, 55.6]]
            ],
        },
        {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
    ],
)
def test_invalid_delivery_geometries_rejected(geometry):
    zone = collection(RING)
    zone["features"][0]["geometry"] = geometry
    with pytest.raises(ValidationError):
        clean_zone(zone)


@pytest.mark.parametrize(
    "route",
    [
        "/partner/new/",
        "/operator/new/",
        "/operator/{slug}/",
        "/partner/{slug}/info/",
        "/partner/{slug}/payout/",
    ],
)
def test_every_shop_setup_page_has_editable_zone_map(client, owner, shop, route):
    owner.is_superuser = True
    owner.save()
    client.force_login(owner)
    response = client.get(route.format(slug=shop.slug))
    assert response.status_code == 200
    html = response.content.decode()
    assert 'data-shop-map="zone"' in html
    assert "leaflet-geoman.min.js" in html
    assert "data-area-picker" in html
    assert 'name="delivery_enabled"' not in html


def test_all_bundled_administrative_boundaries_are_valid():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "static/market/delivery-areas"
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    assert len(index["areas"]) == 200
    assert len({row["id"] for row in index["areas"]}) == 200
    for row in index["areas"]:
        feature = json.loads(
            (root / (row["id"] + ".geojson")).read_text(encoding="utf-8")
        )
        assert clean_zone({"type": "FeatureCollection", "features": [feature]})


@pytest.mark.parametrize(
    "area,lat,lon",
    [
        ("osm-184108", 55.796, 37.938),
        ("osm-1703097", 55.784, 38.444),
        ("osm-1703094", 55.889, 37.445),
        ("osm-181346", 55.678, 37.277),
    ],
)
def test_real_area_contains_its_city_center(area, lat, lon):
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "static/market/delivery-areas"
    feature = json.loads((root / (area + ".geojson")).read_text(encoding="utf-8"))
    assert point_in_zone(lat, lon, {"type": "FeatureCollection", "features": [feature]})


def test_preset_selection_also_updates_city_only_delivery_search(shop):
    from pathlib import Path

    from market.models import Settlement

    city = Settlement.objects.create(
        name="Химки", slug="khimki-preset-test", region="50"
    )
    root = Path(__file__).resolve().parents[1] / "static/market/delivery-areas"
    feature = json.loads((root / "osm-1703094.geojson").read_text(encoding="utf-8"))
    form = ShopInfoForm(
        {
            **shop_data(shop),
            "delivery_zone": json.dumps(
                {"type": "FeatureCollection", "features": [feature]}
            ),
        },
        instance=shop,
    )
    assert form.is_valid(), form.errors
    form.save()
    assert list(shop.delivery_settlements.all()) == [city]


def test_migration_enables_romeo_delivery_without_overwriting_manual_options(shop):
    from importlib import import_module

    from django.apps import apps
    from django.db import connection

    Shop.objects.filter(pk=shop.pk).update(
        delivery_enabled=False,
        pickup_enabled=True,
        own_delivery=False,
        notify_admins=False,
    )
    previous_zone = shop.delivery_zone
    migration = import_module("market.migrations.0021_enable_shop_delivery")
    migration.enable_delivery(apps, connection.schema_editor(atomic=False))
    shop.refresh_from_db()
    assert shop.delivery_enabled
    assert shop.pickup_enabled and not shop.own_delivery and not shop.notify_admins
    assert shop.delivery_zone == previous_zone
