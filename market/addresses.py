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
    return validate_address(
        {
            "value": item.get("value", "")[:300],
            "region": (data.get("region_kladr_id") or "")[:2],
            "city": data.get("city") or data.get("settlement") or data.get("region"),
            "latitude": data.get("geo_lat"),
            "longitude": data.get("geo_lon"),
        }
    )


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


def suggest_addresses(query):
    query = query.strip()[:180]
    if len(query) < 2:
        return []
    if settings.MARKET_DEMO:
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
    cache_key = f"address:{provider}:{digest}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    if provider == "osm":
        return _search_nominatim(query, cache_key)
    body = json.dumps(
        {
            "query": query,
            "count": 5,
            "locations": [
                {"region_kladr_id": "7700000000000"},
                {"region_kladr_id": "5000000000000"},
            ],
        }
    ).encode()
    request = Request(
        "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Token {settings.DADATA_TOKEN}",
        },
    )
    try:
        with urlopen(request, timeout=4) as response:
            items = json.load(response).get("suggestions", [])
    except (URLError, TimeoutError, ValueError):
        return []
    return _suggestions(items, address_from_suggestion, cache_key)


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
