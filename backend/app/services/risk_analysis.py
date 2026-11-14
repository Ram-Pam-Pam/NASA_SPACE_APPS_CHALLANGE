"""Risk analysis, part 1: split linear infrastructure into segments and
measure how each segment relates to hazard zones (e.g. landslides)."""
import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString
from shapely.ops import substring

from app.schemas.layer import GeometryKind, LayerRole
from app.services.layer_registry import LayerRegistry

WGS84 = "EPSG:4326"


class AnalysisError(ValueError):
    """Raised when the analysis cannot run with the given input."""


def _require_layer(registry: LayerRegistry, layer_id: str, role: LayerRole) -> None:
    """Check that a layer exists and has the expected role."""
    layer = registry.get_layer(layer_id)
    if layer is None:
        raise AnalysisError(f"Layer '{layer_id}' not found")
    if layer.role != role:
        raise AnalysisError(
            f"Layer '{layer.name}' has role '{layer.role.value}', expected '{role.value}'"
        )


def load_layer(registry: LayerRegistry, layer_id: str) -> gpd.GeoDataFrame:
    """Read a layer's data file into a GeoDataFrame in WGS84."""
    gdf = gpd.read_file(registry.data_path(layer_id))
    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)  # GeoJSON is WGS84 by definition
    return gdf.to_crs(WGS84)


def split_line(line: LineString, segment_length_m: float) -> list[tuple[float, float, LineString]]:
    """Cut a line (in a metric CRS) into equal pieces; the last one may be shorter.

    Returns (start_m, end_m, piece) tuples, where start/end are distances along the line.
    """
    pieces = []
    start = 0.0
    while start < line.length:
        end = min(start + segment_length_m, line.length)
        pieces.append((start, end, substring(line, start, end)))
        start = end
    return pieces


def segment_lines(lines: gpd.GeoDataFrame, segment_length_m: float) -> gpd.GeoDataFrame:
    """Split every line feature into segments. Input must be in a metric CRS."""
    # MultiLineString -> separate LineStrings; index becomes (feature_index, part_index)
    parts = lines.explode(index_parts=True)

    records = []
    for (feature_index, part_index), line in parts.geometry.items():
        for segment_index, (start, end, piece) in enumerate(split_line(line, segment_length_m)):
            records.append({
                "feature_index": int(feature_index),
                "part_index": int(part_index),
                "segment_index": segment_index,
                "chainage_start_m": round(start, 1),
                "chainage_end_m": round(end, 1),
                "geometry": piece,
            })
    if not records:
        raise AnalysisError("No line geometries to analyse")

    segments = gpd.GeoDataFrame(records, geometry="geometry", crs=lines.crs)
    # Copy attributes of the original feature (name, diameter, ...) to each segment
    attributes = lines.drop(columns="geometry")
    return segments.join(attributes, on="feature_index", rsuffix="_attr")


def analyse_hazard_proximity(
    registry: LayerRegistry,
    infrastructure_layer_id: str,
    hazard_layer_ids: list[str],
    segment_length_m: float = 100.0,
) -> gpd.GeoDataFrame:
    """For each infrastructure segment: distance to nearest hazard and length inside hazards."""
    if not hazard_layer_ids:
        raise AnalysisError("At least one hazard layer is required")
    if segment_length_m <= 0:
        raise AnalysisError("Segment length must be positive")

    _require_layer(registry, infrastructure_layer_id, LayerRole.infrastructure)
    if registry.get_layer(infrastructure_layer_id).geometry_kind != GeometryKind.line:
        raise AnalysisError("Infrastructure layer must contain lines")
    for layer_id in hazard_layer_ids:
        _require_layer(registry, layer_id, LayerRole.hazard)

    infrastructure = load_layer(registry, infrastructure_layer_id)
    hazards = gpd.GeoDataFrame(
        pd.concat(
            [load_layer(registry, i)[["geometry"]].assign(hazard_layer_id=i) for i in hazard_layer_ids],
            ignore_index=True,
        ),
        geometry="geometry",
        crs=WGS84,
    )

    # Degrees are not metres - switch to a local metric CRS (UTM) for lengths and distances
    metric_crs = infrastructure.estimate_utm_crs()
    segments = segment_lines(infrastructure.to_crs(metric_crs), segment_length_m)
    hazards_m = hazards.to_crs(metric_crs)

    # Nearest hazard for each segment (distance = 0 means the segment touches/crosses it)
    nearest = gpd.sjoin_nearest(segments, hazards_m, how="left", distance_col="hazard_distance_m")
    nearest = nearest[~nearest.index.duplicated(keep="first")]  # ties -> keep one row per segment
    segments["hazard_distance_m"] = nearest["hazard_distance_m"].round(1)
    segments["nearest_hazard_layer_id"] = nearest["hazard_layer_id"]
    segments["intersects_hazard"] = segments["hazard_distance_m"] == 0

    # How many metres of each segment run through any hazard zone
    hazard_area = hazards_m.geometry.union_all()
    segments["length_m"] = segments.geometry.length.round(1)
    segments["length_in_hazard_m"] = segments.geometry.intersection(hazard_area).length.round(1)

    return segments.to_crs(WGS84)