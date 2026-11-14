"""Risk analysis of linear infrastructure.

1. Split infrastructure lines into segments (e.g. every 100 m).
2. Hazard metrics: distance to the nearest hazard zone, length inside hazard zones.
3. Motion metrics: InSAR ground velocity measured around each segment.
4. Combine everything into a 0-100 risk score and a risk class.
"""
import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString
from shapely.ops import substring

from app.schemas.analysis import RiskParameters
from app.schemas.layer import GeometryKind, LayerRole
from app.services.layer_registry import LayerRegistry

WGS84 = "EPSG:4326"
RISK_CLASSES = ["low", "medium", "high", "very_high"]


class AnalysisError(ValueError):
    """Raised when the analysis cannot run with the given input."""


# ---------- loading ----------

def _require_layer(
    registry: LayerRegistry,
    layer_id: str,
    role: LayerRole,
    geometry_kinds: list[GeometryKind] | None = None,
) -> None:
    """Check that a layer exists, has the expected role and (optionally) geometry kind."""
    layer = registry.get_layer(layer_id)
    if layer is None:
        raise AnalysisError(f"Layer '{layer_id}' not found")
    if layer.role != role:
        raise AnalysisError(
            f"Layer '{layer.name}' has role '{layer.role.value}', expected '{role.value}'"
        )
    if geometry_kinds and layer.geometry_kind not in geometry_kinds:
        expected = ", ".join(k.value for k in geometry_kinds)
        raise AnalysisError(
            f"Layer '{layer.name}' contains {layer.geometry_kind.value} data, expected: {expected}"
        )


def load_layer(registry: LayerRegistry, layer_id: str) -> gpd.GeoDataFrame:
    """Read a layer's data file into a GeoDataFrame in WGS84."""
    gdf = gpd.read_file(registry.data_path(layer_id))
    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)  # GeoJSON is WGS84 by definition
    return gdf.to_crs(WGS84)


def _load_many(registry: LayerRegistry, layer_ids: list[str], columns: list[str]) -> gpd.GeoDataFrame:
    """Load several layers into one GeoDataFrame, keeping only the given attribute columns."""
    frames = []
    for layer_id in layer_ids:
        gdf = load_layer(registry, layer_id)
        missing = [c for c in columns if c not in gdf.columns]
        if missing:
            name = registry.get_layer(layer_id).name
            available = ", ".join(c for c in gdf.columns if c != "geometry")
            raise AnalysisError(f"Layer '{name}' has no attribute(s) {missing}. Available: {available}")
        frames.append(gdf[columns + ["geometry"]].assign(source_layer_id=layer_id))
    return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), geometry="geometry", crs=WGS84)


# ---------- 1. segmentation ----------

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


# ---------- 2. hazard zones ----------

def add_hazard_metrics(segments: gpd.GeoDataFrame, hazards: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Distance to the nearest hazard zone and length of each segment inside hazard zones."""
    nearest = gpd.sjoin_nearest(segments[["geometry"]], hazards, how="left", distance_col="distance")
    nearest = nearest[~nearest.index.duplicated(keep="first")]  # ties -> keep one row per segment

    segments["intersects_hazard"] = nearest["distance"] == 0
    segments["hazard_distance_m"] = nearest["distance"].round(1)
    segments["nearest_hazard_layer_id"] = nearest["source_layer_id"]

    hazard_area = hazards.geometry.union_all()
    segments["length_in_hazard_m"] = segments.geometry.intersection(hazard_area).length.round(1)
    return segments


# ---------- 3. InSAR ground motion ----------

def add_motion_metrics(
    segments: gpd.GeoDataFrame,
    points: gpd.GeoDataFrame,
    buffer_m: float,
    velocity_field: str,
) -> gpd.GeoDataFrame:
    """Statistics of InSAR velocities measured within buffer_m of each segment."""
    points = points.rename(columns={velocity_field: "velocity"})
    points["velocity"] = pd.to_numeric(points["velocity"], errors="coerce")
    points = points[points["velocity"].notna()]

    # A "corridor" around each segment; its index is the segment index
    corridors = gpd.GeoDataFrame(geometry=segments.geometry.buffer(buffer_m), crs=segments.crs)
    joined = gpd.sjoin(points[["velocity", "geometry"]], corridors, how="inner", predicate="within")
    joined["abs_velocity"] = joined["velocity"].abs()

    stats = joined.groupby("index_right").agg(
        insar_point_count=("velocity", "size"),
        mean_velocity_mm_yr=("velocity", "mean"),
        p90_abs_velocity_mm_yr=("abs_velocity", lambda s: s.quantile(0.9)),
    )
    segments = segments.join(stats)
    segments["insar_point_count"] = segments["insar_point_count"].fillna(0).astype(int)
    segments["mean_velocity_mm_yr"] = segments["mean_velocity_mm_yr"].round(2)
    segments["p90_abs_velocity_mm_yr"] = segments["p90_abs_velocity_mm_yr"].round(2)
    return segments


# ---------- 4. risk score ----------

def compute_risk_score(
    segments: gpd.GeoDataFrame,
    params: RiskParameters,
    has_hazards: bool,
    has_motion: bool,
) -> gpd.GeoDataFrame:
    """Weighted 0-100 score from hazard proximity and ground motion, plus a risk class."""
    # Only components that have input data take part in the score
    hazard_weight = params.hazard_weight if has_hazards else 0.0
    motion_weight = params.motion_weight if has_motion else 0.0
    if hazard_weight + motion_weight == 0:
        raise AnalysisError("All weights are zero - nothing to score")

    hazard_component = pd.Series(0.0, index=segments.index)
    if has_hazards:
        # 1.0 when touching a hazard zone, falling linearly to 0 at hazard_influence_m
        hazard_component = (1 - segments["hazard_distance_m"] / params.hazard_influence_m).clip(lower=0)

    motion_component = pd.Series(0.0, index=segments.index)
    if has_motion:
        # 1.0 at velocity_saturation_mm_yr or faster; segments without InSAR points get 0
        motion_component = (
            segments["p90_abs_velocity_mm_yr"] / params.velocity_saturation_mm_yr
        ).clip(upper=1).fillna(0)

    score = 100 * (hazard_weight * hazard_component + motion_weight * motion_component) / (
        hazard_weight + motion_weight
    )
    segments["hazard_score"] = (100 * hazard_component).round(1)
    segments["motion_score"] = (100 * motion_component).round(1)
    segments["risk_score"] = score.round(1)
    segments["risk_class"] = pd.cut(
        score, bins=[0, 25, 50, 75, 100], labels=RISK_CLASSES, include_lowest=True
    ).astype(str)
    return segments


# ---------- main entry point ----------

def run_risk_analysis(
    registry: LayerRegistry,
    infrastructure_layer_id: str,
    hazard_layer_ids: list[str] | None = None,
    deformation_layer_ids: list[str] | None = None,
    params: RiskParameters | None = None,
) -> gpd.GeoDataFrame:
    """Run the full analysis and return risk-scored segments in WGS84."""
    params = params or RiskParameters()
    hazard_layer_ids = hazard_layer_ids or []
    deformation_layer_ids = deformation_layer_ids or []
    if not hazard_layer_ids and not deformation_layer_ids:
        raise AnalysisError("Provide at least one hazard or deformation layer")

    _require_layer(registry, infrastructure_layer_id, LayerRole.infrastructure, [GeometryKind.line])
    for layer_id in hazard_layer_ids:
        _require_layer(registry, layer_id, LayerRole.hazard)
    for layer_id in deformation_layer_ids:
        _require_layer(registry, layer_id, LayerRole.deformation, [GeometryKind.point])

    infrastructure = load_layer(registry, infrastructure_layer_id)
    # Degrees are not metres - switch to a local metric CRS (UTM) for lengths and distances
    metric_crs = infrastructure.estimate_utm_crs()
    segments = segment_lines(infrastructure.to_crs(metric_crs), params.segment_length_m)
    segments["length_m"] = segments.geometry.length.round(1)

    if hazard_layer_ids:
        hazards = _load_many(registry, hazard_layer_ids, columns=[]).to_crs(metric_crs)
        segments = add_hazard_metrics(segments, hazards)

    if deformation_layer_ids:
        points = _load_many(registry, deformation_layer_ids, columns=[params.velocity_field]).to_crs(metric_crs)
        segments = add_motion_metrics(segments, points, params.insar_buffer_m, params.velocity_field)

    segments = compute_risk_score(segments, params, bool(hazard_layer_ids), bool(deformation_layer_ids))
    return segments.to_crs(WGS84)