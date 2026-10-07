import hashlib
import json
from decimal import Decimal, InvalidOperation
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.core.exceptions import ValidationError

SALT = "market-receiving-address"
SELECTION_SALT = "market-dadata-selection"
DADATA_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# ISO 3166-2 codes of the two launch regions as OpenStreetMap reports them.
NOMINATIM_REGIONS = {"RU-MOW": "77", "RU-MOS": "50"}
# Moscow and the oblast: left, top, right, bottom as Nominatim expects them.
NOMINATIM_VIEWBOX = "34.8,57.2,40.6,54.0"
DEMO_ADDRESSES = [
    {
        "value": "Москва, Тверская улица, дом 1",
        "region": "77",
        "city": "Москва",
        "latitude": "55.756700",
        "longitude": "37.613700",
    },
    {
        "value": "Химки, Московская улица, дом 1",
        "region": "50",
        "city": "Химки",
        "latitude": "55.889700",
        "longitude": "37.444300",
    },
    {
        "value": "Балашиха, проспект Ленина, дом 1",
        "region": "50",
        "city": "Балашиха",
        "latitude": "55.796000",
        "longitude": "37.938000",
    },
]


def validate_address(address):
    try:
        lat, lon = (
            Decimal(str(address.get("latitude"))),
            Decimal(str(address.get("longitude"))),
        )
        if (
            not lat.is_finite()
            or not lon.is_finite()
            or not -90 <= lat <= 90
            or not -180 <= lon <= 180
        ):
            raise ValueError
        if (
            address.get("region") not in {"77", "50"}
            or not address.get("value")
            or not address.get("city")
        ):
            raise ValueError
    except (InvalidOperation, TypeError, ValueError, AttributeError):
        raise ValidationError(
            "Выберите адрес в Москве или Московской области из подсказок."
        )
    return address


def address_from_suggestion(item):
    data = item.get("data", {})
    if not data.get("house"):
        raise ValidationError("Уточните адрес до дома.")
    if str(data.get("qc_geo")) != "0":
        raise ValidationError(
            "Для этого дома нет точных координат. Выберите другой адрес."
        )
    address = validate_address(
        {
            "value": item.get("value", "")[:300],
            "region": (data.get("region_kladr_id") or "")[:2],
            "city": data.get("city") or data.get("settlement") or data.get("region"),
            "latitude": data.get("geo_lat"),
            "longitude": data.get("geo_lon"),
        }
    )
    for field in ("latitude", "longitude"):
        address[field] = str(Decimal(str(address[field])).quantize(Decimal("0.000001")))
    return address


def address_from_nominatim(item):
    """OpenStreetMap result to our address; only exact houses in regions 77 and 50."""
    data = item.get("address", {})
    if not data.get("house_number"):
        raise ValidationError("Уточните адрес до дома.")
    city = (
        data.get("city")
        or data.get("town")
        or data.get("village")
        or data.get("municipality")
        or data.get("state")
    )
    street = data.get("road") or data.get("pedestrian") or data.get("hamlet") or ""
    value = ", ".join(
        part for part in [city, street, f"дом {data['house_number']}"] if part
    )
    return validate_address(
        {
            "value": value[:300],
            "region": NOMINATIM_REGIONS.get(data.get("ISO3166-2-lvl4"), ""),
            "city": city,
            "latitude": item.get("lat"),
            "longitude": item.get("lon"),
        }
    )


def _suggestions(items, convert, cache_key):
    results = []
    for item in items:
        try:
            address = convert(item)
        except ValidationError:
            continue
        results.append(
            {
                "value": address["value"],
                "token": encode_address(address),
                "latitude": str(address["latitude"]),
                "longitude": str(address["longitude"]),
            }
        )
    cache.set(cache_key, results, 300)
    return results


def encode_address(address):
    return signing.dumps(validate_address(address), salt=SALT, compress=True)


def decode_address(token):
    try:
        return validate_address(
            signing.loads(token, salt=SALT, max_age=7 * 24 * 60 * 60)
        )
    except signing.BadSignature:
        raise ValidationError("Адрес устарел. Выберите его из подсказок ещё раз.")


def demo_address_search():
    return settings.MARKET_DEMO and settings.ADDRESS_DEMO


def suggest_addresses(query):
    query = query.strip()[:180]
    if len(query) < 2:
        return []
    if demo_address_search():
        return [
            {
                "value": row["value"],
                "token": encode_address(row),
                "latitude": row["latitude"],
                "longitude": row["longitude"],
            }
            for row in DEMO_ADDRESSES
            if query.casefold() in row["value"].casefold()
        ]
    provider = "dadata" if settings.DADATA_TOKEN else "osm"
    if provider == "osm" and not settings.OSM_ADDRESS_SEARCH:
        return []
    digest = hashlib.sha256(query.casefold().encode()).hexdigest()
    cache_key = f"address:v2:{provider}:{digest}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    if provider == "osm":
        return _search_nominatim(query, cache_key)
    try:
        items = _request_dadata(query, count=5)
    except ValidationError:
        return []
    results = []
    for item in items:
        data = item.get("data") or {}
        region = (data.get("region_kladr_id") or "")[:2]
        value = item.get("value")
        full_value = item.get("unrestricted_value")
        if (
            region not in {"77", "50"}
            or not data.get("house")
            or not isinstance(value, str)
            or not value
            or not isinstance(full_value, str)
            or not 1 <= len(full_value) <= 300
        ):
            continue
        selection = {
            "query": full_value,
            "region": region,
            "fias_id": data.get("fias_id"),
        }
        results.append(
            {
                "value": value[:300],
                "selection_token": signing.dumps(
                    selection, salt=SELECTION_SALT, compress=True
                ),
            }
        )
    cache.set(cache_key, results, 300)
    return results


def _request_dadata(query, *, count):
    body = json.dumps(
        {
            "query": query,
            "count": count,
            "locations": [
                {"kladr_id": "77"},
                {"kladr_id": "50"},
            ],
        }
    ).encode()
    request = Request(
        DADATA_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Token {settings.DADATA_TOKEN}",
        },
    )
    try:
        with urlopen(request, timeout=4) as response:
            payload = json.load(response)
        if not isinstance(payload, dict) or not isinstance(
            payload.get("suggestions"), list
        ):
            raise TypeError
        items = payload["suggestions"]
        if any(
            not isinstance(item, dict) or not isinstance(item.get("data"), dict)
            for item in items
        ):
            raise TypeError
    except (URLError, TimeoutError, ValueError, TypeError, OSError):
        raise ValidationError("Подсказки сейчас недоступны. Попробуйте ещё раз.")
    return items


def resolve_address(token):
    try:
        if not isinstance(token, str) or len(token) > 2000:
            raise ValueError
        selection = signing.loads(token, salt=SELECTION_SALT, max_age=15 * 60)
        if (
            not isinstance(selection, dict)
            or not isinstance(selection.get("query"), str)
            or not 1 <= len(selection["query"]) <= 300
            or selection.get("region") not in {"77", "50"}
        ):
            raise ValueError
    except (signing.BadSignature, ValueError, TypeError):
        raise ValidationError("Подсказка устарела. Найдите адрес ещё раз.")
    if demo_address_search() or not settings.DADATA_TOKEN:
        raise ValidationError("Подсказки сейчас недоступны. Найдите адрес ещё раз.")
    digest = hashlib.sha256(json.dumps(selection, sort_keys=True).encode()).hexdigest()
    key = f"address:resolved:{digest}"
    address = cache.get(key)
    if address is None:
        items = _request_dadata(selection["query"], count=1)
        if not items:
            raise ValidationError("Адрес не найден. Выберите другой дом из подсказок.")
        item = items[0]
        if item.get("unrestricted_value") != selection["query"] or (
            selection.get("fias_id")
            and item["data"].get("fias_id") != selection["fias_id"]
        ):
            raise ValidationError("Адрес изменился. Выберите дом из подсказок ещё раз.")
        address = address_from_suggestion(item)
        if address["region"] != selection["region"]:
            raise ValidationError("Выберите адрес в Москве или Московской области.")
        cache.set(key, address, 300)
    return {
        "value": address["value"],
        "token": encode_address(address),
        "latitude": str(address["latitude"]),
        "longitude": str(address["longitude"]),
    }


def _search_nominatim(query, cache_key):
    """The public OpenStreetMap geocoder needs no key. Its usage policy allows about one
    request per second with an identifying User-Agent, so bursts are dropped."""
    if not cache.add("address:osm:throttle", 1, 1):
        return []
    params = urlencode(
        {
            "q": query,
            "format": "jsonv2",
            "addressdetails": 1,
            "limit": 8,
            "countrycodes": "ru",
            "accept-language": "ru",
            "viewbox": NOMINATIM_VIEWBOX,
            "bounded": 1,
        }
    )
    request = Request(
        f"{NOMINATIM_URL}?{params}",
        headers={"Accept": "application/json", "User-Agent": settings.OSM_USER_AGENT},
    )
    try:
        with urlopen(request, timeout=4) as response:
            items = json.load(response)
    except (URLError, TimeoutError, ValueError):
        return []
    if not isinstance(items, list):
        return []
    return _suggestions(items, address_from_nominatim, cache_key)
