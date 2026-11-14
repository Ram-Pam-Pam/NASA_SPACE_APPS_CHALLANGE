import json
import math
from dataclasses import dataclass
from typing import Any, Iterator

from app.schemas.layer import GeometryKind

# GeoJSON geometry types mapped to our simplified kinds
_GEOMETRY_KINDS = {
    "Point": GeometryKind.point,
    "MultiPoint": GeometryKind.point,
    "LineString": GeometryKind.line,
    "MultiLineString": GeometryKind.line,
    "Polygon": GeometryKind.polygon,
    "MultiPolygon": GeometryKind.polygon,
}


class InvalidGeoJSONError(ValueError):
    """Raised when an uploaded file is not usable GeoJSON."""


@dataclass
class GeoJSONInfo:
    geometry_kind: GeometryKind
    feature_count: int
    bbox: tuple[float, float, float, float]  # (min_lon, min_lat, max_lon, max_lat)


def _iter_positions(coords: Any) -> Iterator[tuple[float, float]]:
    """Walk nested coordinate arrays of any geometry and yield (lon, lat) pairs."""
    if not isinstance(coords, list):
        raise TypeError("coordinates must be nested lists of numbers")
    if coords and isinstance(coords[0], (int, float)):
        yield float(coords[0]), float(coords[1])
        return
    for item in coords:
        yield from _iter_positions(item)


def inspect_geojson(content: bytes) -> GeoJSONInfo:
    """Validate a GeoJSON FeatureCollection and extract basic layer info."""
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise InvalidGeoJSONError(f"File is not valid JSON: {e}") from e

    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        raise InvalidGeoJSONError("Expected a GeoJSON FeatureCollection")

    kinds: set[GeometryKind] = set()
    count = 0
    min_x = min_y = math.inf
    max_x = max_y = -math.inf

    for i, feature in enumerate(data.get("features") or []):
        if not isinstance(feature, dict):
            raise InvalidGeoJSONError(f"Feature {i}: not a JSON object")
        geometry = feature.get("geometry")
        if geometry is None:
            continue  # features without geometry are allowed by the spec - skip them
        if not isinstance(geometry, dict):
            raise InvalidGeoJSONError(f"Feature {i}: geometry must be a JSON object")
        geom_type = geometry.get("type")
        if geom_type not in _GEOMETRY_KINDS:
            raise InvalidGeoJSONError(f"Feature {i}: unsupported geometry type '{geom_type}'")
        kinds.add(_GEOMETRY_KINDS[geom_type])
        try:
            for x, y in _iter_positions(geometry.get("coordinates") or []):
                min_x, max_x = min(min_x, x), max(max_x, x)
                min_y, max_y = min(min_y, y), max(max_y, y)
        except (TypeError, IndexError, ValueError) as e:
            raise InvalidGeoJSONError(f"Feature {i}: malformed coordinates") from e
        count += 1

    if count == 0:
        raise InvalidGeoJSONError("The file contains no features with geometry")
    if len(kinds) > 1:
        found = ", ".join(sorted(k.value for k in kinds))
        raise InvalidGeoJSONError(f"Mixed geometry types in one layer ({found}) - split them into separate files")
    if not (-180 <= min_x and max_x <= 180 and -90 <= min_y and max_y <= 90):
        raise InvalidGeoJSONError("Coordinates are outside lon/lat range - the file must be in WGS84 (EPSG:4326)")

    return GeoJSONInfo(kinds.pop(), count, (min_x, min_y, max_x, max_y))