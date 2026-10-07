import pytest
from django.core.exceptions import ValidationError

from market.addresses import address_from_suggestion, decode_address, encode_address


def suggestion(region="77", latitude="55.75", house="1"):
    return {
        "value": "г Москва, ул Тестовая, д 1",
        "data": {
            "region_kladr_id": region + "00000000000",
            "geo_lat": latitude,
            "geo_lon": "37.61",
            "house": house,
            "city": "Москва",
            "qc_geo": "0",
        },
    }


def test_outside_region_and_non_house_rejected():
    for item in [
        suggestion("16"),
        suggestion(latitude=None),
        suggestion(house=None),
        suggestion(latitude="NaN"),
    ]:
        with pytest.raises(ValidationError):
            address_from_suggestion(item)


def test_signed_address_rejects_tampering():
    address = address_from_suggestion(suggestion())
    token = encode_address(address)
    assert decode_address(token)["region"] == "77"
    with pytest.raises(ValidationError):
        decode_address(token + "bad")


# --- OpenStreetMap search (used when there is no DaData token) -----------------------


def osm_item(**address):
    return {
        "lat": "55.757000",
        "lon": "37.613000",
        "address": {
            "house_number": "1",
            "road": "Тверская улица",
            "city": "Москва",
            "ISO3166-2-lvl4": "RU-MOW",
            **address,
        },
    }


@pytest.fixture
def osm(settings, monkeypatch):
    import io
    import json

    from django.core.cache import cache

    from market import addresses

    cache.clear()
    settings.MARKET_DEMO, settings.DADATA_TOKEN = False, ""
    settings.OSM_ADDRESS_SEARCH = True
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        return io.BytesIO(json.dumps(fake_urlopen.items).encode())

    fake_urlopen.items = [osm_item()]
    monkeypatch.setattr(addresses, "urlopen", fake_urlopen)
    yield fake_urlopen, calls
    cache.clear()


def test_osm_result_becomes_a_signed_address():
    from market.addresses import address_from_nominatim

    address = address_from_nominatim(osm_item())
    assert address == {
        "value": "Москва, Тверская улица, дом 1",
        "region": "77",
        "city": "Москва",
        "latitude": "55.757000",
        "longitude": "37.613000",
    }
    oblast = address_from_nominatim(
        osm_item(city="Химки", **{"ISO3166-2-lvl4": "RU-MOS"})
    )
    assert (oblast["region"], oblast["city"]) == ("50", "Химки")
    assert decode_address(encode_address(address))["value"] == address["value"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"house_number": None},
        {"ISO3166-2-lvl4": "RU-SPE"},
        {"ISO3166-2-lvl4": None},
    ],
)
def test_osm_rejects_other_regions_and_places_without_a_house(overrides):
    from market.addresses import address_from_nominatim

    item = osm_item()
    item["address"].update(overrides)
    with pytest.raises(ValidationError):
        address_from_nominatim(item)


def test_osm_search_runs_without_a_key_and_is_cached(osm):
    from market.addresses import suggest_addresses

    _, calls = osm
    results = suggest_addresses("Тверская 1")
    assert [row["value"] for row in results] == ["Москва, Тверская улица, дом 1"]
    row = results[0]
    assert (row["latitude"], row["longitude"]) == ("55.757000", "37.613000")
    assert decode_address(row["token"])["region"] == "77"
    request = calls[0]
    assert request.get_header("User-agent") == "romeo-marketplace/1.0"
    assert "countrycodes=ru" in request.full_url and "bounded=1" in request.full_url
    assert suggest_addresses("Тверская 1") == results
    assert len(calls) == 1


def test_osm_search_is_throttled_to_protect_the_public_service(osm):
    from market.addresses import suggest_addresses

    _, calls = osm
    assert suggest_addresses("Тверская")
    assert suggest_addresses("Арбат") == []
    assert len(calls) == 1


def test_osm_failure_and_opt_out_return_no_suggestions(osm, settings):
    from urllib.error import URLError

    from market import addresses
    from market.addresses import suggest_addresses

    fake, calls = osm

    def broken(request, timeout):
        raise URLError("offline")

    addresses.urlopen = broken
    assert suggest_addresses("Тверская") == []
    addresses.urlopen = fake
    settings.OSM_ADDRESS_SEARCH = False
    assert suggest_addresses("Покровка") == []
    assert not calls


def test_demo_suggestions_carry_coordinates_for_the_map(settings):
    from market.addresses import suggest_addresses

    settings.MARKET_DEMO = True
    row = suggest_addresses("Тверская")[0]
    assert row["latitude"] == "55.756700" and row["longitude"] == "37.613700"


@pytest.fixture
def dadata(settings, monkeypatch, db):
    import io
    import json

    from django.core.cache import cache

    from market import addresses

    cache.clear()
    settings.MARKET_DEMO = True
    settings.ADDRESS_DEMO = False
    settings.DADATA_TOKEN = "test-only-dadata-token"
    calls = []

    def fake_urlopen(request, timeout):
        body = json.loads(request.data)
        calls.append(body)
        item = suggestion(latitude="55.750001" if body["count"] == 1 else None)
        item["unrestricted_value"] = "125009, г Москва, ул Тестовая, д 1"
        item["data"].update(fias_id="test-house-id", qc_geo="0")
        item["data"].update(fake_urlopen.overrides)
        return io.BytesIO(json.dumps({"suggestions": [item]}).encode())

    fake_urlopen.overrides = {}
    monkeypatch.setattr(addresses, "urlopen", fake_urlopen)
    yield fake_urlopen, calls
    cache.clear()


def test_dadata_lists_houses_without_coordinates_even_in_demo(dadata):
    from market.addresses import suggest_addresses

    _, calls = dadata
    rows = suggest_addresses("Тестовая 1")
    assert len(rows) == 1
    assert rows[0]["value"] == "г Москва, ул Тестовая, д 1"
    assert rows[0]["selection_token"]
    assert "token" not in rows[0]  # Not yet a verified address usable for orders.
    assert suggest_addresses("Тестовая 1") == rows
    assert len(calls) == 1
    assert calls[0]["locations"] == [
        {"kladr_id": "77"},
        {"kladr_id": "50"},
    ]


def test_selected_dadata_house_resolves_to_signed_coordinates(dadata, client):
    _, calls = dadata
    row = client.get("/addresses/", {"q": "Тестовая 1"}).json()["results"][0]
    response = client.get("/addresses/resolve/", {"token": row["selection_token"]})
    assert response.status_code == 200
    address = decode_address(response.json()["result"]["token"])
    assert (address["latitude"], address["longitude"]) == ("55.750001", "37.610000")
    assert address["city"] == "Москва"
    assert calls[-1]["query"] == "125009, г Москва, ул Тестовая, д 1"
    assert calls[-1]["count"] == 1
    assert response["Cache-Control"] == "no-store"
    again = client.get("/addresses/resolve/", {"token": row["selection_token"]})
    assert again.json() == response.json()
    assert len(calls) == 2


def test_selection_token_is_not_an_order_address(dadata):
    from market.addresses import suggest_addresses

    row = suggest_addresses("Тестовая 1")[0]
    with pytest.raises(ValidationError):
        decode_address(row["selection_token"])


def test_resolve_rejects_tampered_token_without_calling_provider(dadata, client):
    _, calls = dadata
    response = client.get("/addresses/resolve/", {"token": "forged"})
    assert response.status_code == 400
    assert response.json()["message"]
    assert not calls


@pytest.mark.parametrize(
    "overrides",
    [
        {"qc_geo": "1"},
        {"qc_geo": "2"},
        {"qc_geo": None},
        {"geo_lat": None},
        {"fias_id": "another-house"},
        {"region_kladr_id": "1600000000000"},
    ],
)
def test_resolve_rejects_imprecise_or_changed_house(dadata, client, overrides):
    fake, _ = dadata
    row = client.get("/addresses/", {"q": "Тестовая 1"}).json()["results"][0]
    fake.overrides = overrides
    response = client.get("/addresses/resolve/", {"token": row["selection_token"]})
    assert response.status_code == 400
    assert "token" not in response.json().get("result", {})


def test_resolve_provider_outage_leaves_address_unselected(dadata, client, monkeypatch):
    from urllib.error import URLError

    from market import addresses

    row = client.get("/addresses/", {"q": "Тестовая 1"}).json()["results"][0]

    def offline(request, timeout):
        raise URLError("offline")

    monkeypatch.setattr(addresses, "urlopen", offline)
    response = client.get("/addresses/resolve/", {"token": row["selection_token"]})
    assert response.status_code == 400
    assert response.json()["message"]


def test_resolve_shares_search_rate_limit(dadata, client):
    import hashlib

    from django.core.cache import cache

    row = client.get("/addresses/", {"q": "Тестовая 1"}).json()["results"][0]
    cache.set("addr-limit:" + hashlib.sha256(b"127.0.0.1").hexdigest(), 120, 60)
    response = client.get("/addresses/resolve/", {"token": row["selection_token"]})
    assert response.status_code == 429


@pytest.mark.parametrize(
    "overrides", [{"house": None}, {"region_kladr_id": "1600000000000"}]
)
def test_dadata_suggests_only_houses_in_launch_regions(dadata, overrides):
    from market.addresses import suggest_addresses

    fake, _ = dadata
    fake.overrides = overrides
    assert suggest_addresses("Тестовая 1") == []


def test_resolved_dadata_address_saves_shop_coordinates(dadata, client, owner, shop):
    from tests.test_cabinet import info_post

    row = client.get("/addresses/", {"q": "Тестовая 1"}).json()["results"][0]
    result = client.get(
        "/addresses/resolve/", {"token": row["selection_token"]}
    ).json()["result"]
    client.force_login(owner)
    response = client.post(
        f"/partner/{shop.slug}/info/",
        info_post(
            shop,
            address=result["value"],
            address_token=result["token"],
        ),
    )
    assert response.status_code == 302
    shop.refresh_from_db()
    assert str(shop.latitude) == "55.750001"
    assert str(shop.longitude) == "37.610000"
    assert shop.address == "г Москва, ул Тестовая, д 1"


def test_real_dadata_precision_can_be_saved_to_shop(dadata, client, owner, shop):
    from tests.test_cabinet import info_post

    fake, _ = dadata
    fake.overrides = {"geo_lat": "55.7569854", "geo_lon": "37.6140387"}
    row = client.get("/addresses/", {"q": "Тестовая 1"}).json()["results"][0]
    result = client.get(
        "/addresses/resolve/", {"token": row["selection_token"]}
    ).json()["result"]
    client.force_login(owner)
    response = client.post(
        f"/partner/{shop.slug}/info/",
        info_post(shop, address=result["value"], address_token=result["token"]),
    )
    assert response.status_code == 302
    shop.refresh_from_db()
    assert str(shop.latitude) == "55.756985"
    assert str(shop.longitude) == "37.614039"


def test_live_address_ui_does_not_show_demo_hints_or_private_key(
    dadata, client, owner, shop
):
    client.force_login(owner)
    page = client.get(f"/partner/{shop.slug}/info/")
    assert page.context["address_demo"] is False
    assert "Для примера: Тверская" not in page.content.decode()
    assert "test-only-dadata-token" not in page.content.decode()
    assert "Подсказки DaData" in page.content.decode()


@pytest.mark.parametrize("section", ["info", "payout"])
def test_cabinet_maps_receive_yandex_config_with_escaped_public_key(
    client, owner, shop, settings, section
):
    import json
    from html.parser import HTMLParser

    class MapConfigParser(HTMLParser):
        active = False
        payload = ""

        def handle_starttag(self, tag, attrs):
            if tag == "script":
                self.active = dict(attrs).get("id") == "shop-map-config"

        def handle_data(self, data):
            if self.active:
                self.payload += data

        def handle_endtag(self, tag):
            if tag == "script":
                self.active = False

    settings.YANDEX_TILES_API_KEY = "public-key</script>"
    client.force_login(owner)
    html = client.get(f"/partner/{shop.slug}/{section}/").content.decode()
    parser = MapConfigParser()
    parser.feed(html)
    config = json.loads(parser.payload)
    assert config["yandexTilesKey"] == "public-key</script>"
    assert config["yandexLogo"] == "/static/vendor/yandex-tiles/yandex-logo.svg"
    assert "public-key</script>" not in html
