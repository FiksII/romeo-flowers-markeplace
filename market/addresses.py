import json
from decimal import Decimal, InvalidOperation
from urllib.error import URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.core.exceptions import ValidationError

SALT = "market-receiving-address"
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
            {"value": row["value"], "token": encode_address(row)}
            for row in DEMO_ADDRESSES
            if query.casefold() in row["value"].casefold()
        ]
    if not settings.DADATA_TOKEN:
        return []
    cached = cache.get("address:" + query.casefold())
    if cached is not None:
        return cached
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
    results = []
    for item in items:
        try:
            address = address_from_suggestion(item)
            results.append(
                {"value": address["value"], "token": encode_address(address)}
            )
        except ValidationError:
            continue
    cache.set("address:" + query.casefold(), results, 300)
    return results
