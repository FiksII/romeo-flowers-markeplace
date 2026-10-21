"""Delivery zones: legacy latitude/longitude rings or editable GeoJSON collections."""

import json
from functools import lru_cache
from math import cos, radians

from django.core.exceptions import ValidationError
from shapely import get_coordinates
from shapely.errors import GEOSException
from shapely.geometry import Point, shape

MIN_POINTS, MAX_POINTS = 3, 100
# Moscow and Moscow Oblast with a margin; a drawn zone must stay inside this box.
LAT_RANGE, LON_RANGE = (54.0, 57.2), (34.8, 40.6)
EARTH_KM_PER_DEGREE = 111.32


def _points(zone):
    try:
        return [(float(lat), float(lon)) for lat, lon in zone]
    except (TypeError, ValueError):
        raise ValidationError("Зона доставки задана неверно. Нарисуйте её заново.")


def _cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _segments_intersect(a, b, c, d):
    d1, d2, d3, d4 = _cross(c, d, a), _cross(c, d, b), _cross(a, b, c), _cross(a, b, d)
    return d1 * d2 < 0 and d3 * d4 < 0


def _is_self_intersecting(points):
    count = len(points)
    for i in range(count):
        a, b = points[i], points[(i + 1) % count]
        for j in range(i + 2, count):
            if i == 0 and j == count - 1:
                continue
            if _segments_intersect(a, b, points[j], points[(j + 1) % count]):
                return True
    return False


def _area(points):
    total = 0.0
    for index, (lat1, lon1) in enumerate(points):
        lat2, lon2 = points[(index + 1) % len(points)]
        total += lon1 * lat2 - lon2 * lat1
    return abs(total) / 2


def validate_zone(zone):
    """An empty zone is allowed (the shop then has no delivery area yet)."""
    if not zone:
        return
    if isinstance(zone, dict):
        _clean_collection(zone)
        return
    if not isinstance(zone, list) or not all(
        isinstance(item, list | tuple) and len(item) == 2 for item in zone
    ):
        raise ValidationError("Зона доставки задана неверно. Нарисуйте её заново.")
    points = _points(zone)
    if not MIN_POINTS <= len(points) <= MAX_POINTS:
        raise ValidationError(
            f"В зоне доставки должно быть от {MIN_POINTS} до {MAX_POINTS} точек."
        )
    if any(
        not LAT_RANGE[0] <= lat <= LAT_RANGE[1]
        or not LON_RANGE[0] <= lon <= LON_RANGE[1]
        for lat, lon in points
    ):
        raise ValidationError("Зона доставки должна быть в Москве или области.")
    if _area(points) < 1e-8 or _is_self_intersecting(points):
        raise ValidationError(
            "Границы зоны доставки не должны пересекаться. Нарисуйте зону заново."
        )


def clean_zone(zone):
    """Validate a zone from the map editor and round it to the stored precision."""
    if not zone:
        return []
    if isinstance(zone, dict):
        return _clean_collection(zone)
    validate_zone(zone)
    return [[round(lat, 6), round(lon, 6)] for lat, lon in _points(zone)]


def point_in_zone(lat, lon, zone):
    """Ray casting; points on the border count as inside."""
    if not zone:
        return False
    lat, lon = float(lat), float(lon)
    if isinstance(zone, dict):
        return any(
            geom.covers(Point(lon, lat))
            for geom in _collection_shapes(json.dumps(zone, sort_keys=True))
        )
    points = _points(zone)
    inside = False
    for index, (lat1, lon1) in enumerate(points):
        lat2, lon2 = points[(index + 1) % len(points)]
        if abs(_cross((lat1, lon1), (lat2, lon2), (lat, lon))) < 1e-12 and (
            min(lat1, lat2) - 1e-12 <= lat <= max(lat1, lat2) + 1e-12
            and min(lon1, lon2) - 1e-12 <= lon <= max(lon1, lon2) + 1e-12
        ):
            return True
        if (lon1 > lon) != (lon2 > lon):
            crossing = (lat2 - lat1) * (lon - lon1) / (lon2 - lon1) + lat1
            if lat < crossing:
                inside = not inside
    return inside


def circle_zone(lat, lon, radius_km, points=32):
    """A regular polygon around a point; used to turn an old radius into a zone."""
    from math import sin, tau

    lat, lon, radius_km = float(lat), float(lon), float(radius_km)
    d_lat = radius_km / EARTH_KM_PER_DEGREE
    d_lon = radius_km / (EARTH_KM_PER_DEGREE * cos(radians(lat)))
    return [
        [
            round(lat + d_lat * cos(tau * step / points), 6),
            round(lon + d_lon * sin(tau * step / points), 6),
        ]
        for step in range(points)
    ]


@lru_cache(maxsize=16)
def _collection_shapes(serialized):
    return tuple(
        shape(feature["geometry"]) for feature in json.loads(serialized)["features"]
    )


def _clean_collection(zone):
    """Keep disjoint areas and holes; reject invalid geometry before saving."""
    invalid = "Зона доставки задана неверно. Нарисуйте её заново."
    if zone.get("type") != "FeatureCollection" or not isinstance(
        zone.get("features"), list
    ):
        raise ValidationError(invalid)
    if len(zone["features"]) > 300:
        raise ValidationError("Выберите не более 300 территорий.")
    features, count = [], 0
    for feature in zone["features"]:
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise ValidationError(invalid)
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict) or geometry.get("type") not in {
            "Polygon",
            "MultiPolygon",
        }:
            raise ValidationError(invalid)
        try:
            geom = shape(geometry)
            coordinates = get_coordinates(geom)
        except (
            TypeError,
            ValueError,
            KeyError,
            IndexError,
            OverflowError,
            GEOSException,
        ) as error:
            raise ValidationError(invalid) from error
        count += len(coordinates)
        if count > 100000:
            raise ValidationError("В зоне слишком много точек. Упростите границы.")
        if geom.is_empty or not geom.is_valid or geom.area < 1e-8:
            raise ValidationError(
                "Границы зоны доставки не должны пересекаться. Нарисуйте зону заново."
            )
        if not all(
            LON_RANGE[0] <= lon <= LON_RANGE[1] and LAT_RANGE[0] <= lat <= LAT_RANGE[1]
            for lon, lat in coordinates
        ):
            raise ValidationError("Зона доставки должна быть в Москве или области.")
        properties = feature.get("properties") or {}
        if not isinstance(properties, dict):
            raise ValidationError(invalid)
        features.append(
            {
                "type": "Feature",
                "properties": {
                    key: str(properties[key])[:180]
                    for key in ("area_id", "name")
                    if key in properties
                },
                "geometry": geometry,
            }
        )
    return {"type": "FeatureCollection", "features": features} if features else []
