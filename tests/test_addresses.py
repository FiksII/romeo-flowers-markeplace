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
