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
