"""Local administrative presets and their city names for coarse catalogue filters."""

import json
from functools import lru_cache

from django.conf import settings


def normalized(text):
    return text.casefold().replace("ё", "е")


@lru_cache(maxsize=1)
def area_index():
    path = settings.BASE_DIR / "static/market/delivery-areas/index.json"
    return {
        row["id"]: row for row in json.loads(path.read_text(encoding="utf-8"))["areas"]
    }


def preset_city_ids(zone, cities):
    """None means a custom/legacy zone; exact addresses always use its geometry."""
    if not isinstance(zone, dict):
        return None
    presets = area_index()
    areas = [
        presets[feature.get("properties", {}).get("area_id")]
        for feature in zone["features"]
        if feature.get("properties", {}).get("area_id") in presets
    ]
    if not areas:
        return None
    return [
        city.pk
        for city in cities
        if any(
            city.region == area["region"]
            and (
                city.region == "77"
                or normalized(city.name)
                in normalized(area["name"] + " " + area.get("aliases", ""))
            )
            for area in areas
        )
    ]
