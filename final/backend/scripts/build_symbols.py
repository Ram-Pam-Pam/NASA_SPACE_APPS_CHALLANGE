"""Build simplified map symbols (buildings, railways, power lines) for the frontend.

Inputs: the clipped OSM layers in the 3d/ folder (GeoPackage, EPSG:4326).
Outputs (into the dataset's data/ folder):
    buildings_3d.bin   - every building as an oriented rectangle + height class + risk class
    buildings_3d.json  - header for the .bin (count, bounding box, field layout)
    rail_3d.geojson    - railway centre lines with a simple kind (main/yard/light/disused)
    power_3d.geojson   - power lines (overhead/cable) and pylon points

Needs GDAL Python bindings and numpy (e.g. Homebrew python3). Run from the repo root:
    python3 backend/scripts/build_symbols.py 3d Dane/dla_frontendu_portuguese_bend/data
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
from osgeo import ogr

ogr.UseExceptions()

# Height class per OSM building type: (class id, typical height in metres). The frontend reads
# the same table from buildings_3d.json, so the two never drift apart.
HEIGHT_CLASSES = [
    {"id": 0, "name": "dom", "h": 6},
    {"id": 1, "name": "blok / hotel", "h": 15},
    {"id": 2, "name": "usługi / szkoła", "h": 10},
    {"id": 3, "name": "przemysł / magazyn", "h": 9},
    {"id": 4, "name": "garaż / wiata", "h": 3},
    {"id": 5, "name": "kościół", "h": 12},
]
TYPE_TO_CLASS = {
    "apartments": 1, "hotel": 1, "dormitory": 1,
    "commercial": 2, "retail": 2, "office": 2, "school": 2, "civic": 2, "government": 2,
    "university": 2, "hospital": 2, "public": 2, "kindergarten": 2, "college": 2,
    "industrial": 3, "warehouse": 3, "factory": 3, "hangar": 3, "manufacture": 3,
    "garage": 4, "garages": 4, "shed": 4, "carport": 4, "roof": 4, "static_caravan": 4,
    "church": 5, "chapel": 5, "cathedral": 5,
}
RISK_CODES = {"niskie": 1, "średnie": 2, "wysokie": 3}
M_PER_DEG_LAT = 110_540.0


def min_area_rect(points: np.ndarray):
    """Oriented minimum-area rectangle of a convex hull (metres). Returns cx, cy, half_l, half_w, angle."""
    edges = np.diff(points, axis=0)
    angles = np.unique(np.mod(np.arctan2(edges[:, 1], edges[:, 0]), math.pi / 2))
    best = None
    for a in angles:
        c, s = math.cos(a), math.sin(a)
        rx = points[:, 0] * c + points[:, 1] * s
        ry = -points[:, 0] * s + points[:, 1] * c
        area = (rx.max() - rx.min()) * (ry.max() - ry.min())
        if best is None or area < best[0]:
            best = (area, a, rx.min(), rx.max(), ry.min(), ry.max())
    _, a, x0, x1, y0, y1 = best
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    c, s = math.cos(a), math.sin(a)
    return mx * c - my * s, mx * s + my * c, (x1 - x0) / 2, (y1 - y0) / 2, a


def build_buildings(src: Path, out: Path, bbox, risk_by_osm: dict) -> None:
    lon0, lat0, lon1, lat1 = bbox
    m_per_deg_lon = 111_320.0 * math.cos(math.radians((lat0 + lat1) / 2))
    ds = ogr.Open(str(src / "budynki_dociete.gpkg"))
    layer = ds.GetLayer(0)
    layer.SetSpatialFilterRect(lon0, lat0, lon1, lat1)
    rows = []
    for feat in layer:
        geom = feat.GetGeometryRef()
        if geom is None or geom.IsEmpty():
            continue
        hull = geom.ConvexHull()
        ring = hull.GetGeometryRef(0) if hull.GetGeometryType() == ogr.wkbPolygon else None
        if ring is None or ring.GetPointCount() < 4:
            continue
        pts = np.array(ring.GetPoints())[:, :2]
        ox, oy = pts[0]
        local = np.column_stack(((pts[:, 0] - ox) * m_per_deg_lon, (pts[:, 1] - oy) * M_PER_DEG_LAT))
        cx, cy, hl, hw, ang = min_area_rect(local)
        lon, lat = ox + cx / m_per_deg_lon, oy + cy / M_PER_DEG_LAT
        if not (lon0 <= lon <= lon1 and lat0 <= lat <= lat1):
            continue
        kind = TYPE_TO_CLASS.get((feat.GetField("type") or "").strip(), 0)
        risk = RISK_CODES.get(risk_by_osm.get(str(feat.GetField("osm_id"))), 0)
        rows.append((lon, lat, hl, hw, ang % math.pi, kind, risk))

    arr = np.array(rows, dtype=np.float64)
    n = len(arr)
    qx = np.round((arr[:, 0] - lon0) / (lon1 - lon0) * 65535).astype("<u2")
    qy = np.round((arr[:, 1] - lat0) / (lat1 - lat0) * 65535).astype("<u2")
    hl = np.clip(np.round(arr[:, 2] * 10), 5, 65535).astype("<u2")  # 0.1 m, at least 0.5 m
    hw = np.clip(np.round(arr[:, 3] * 10), 5, 65535).astype("<u2")
    ang = np.round(arr[:, 4] / math.pi * 255).astype("u1")
    meta = (arr[:, 5].astype("u1") | (arr[:, 6].astype("u1") << 4)).astype("u1")
    # Struct-of-arrays: 4 x Uint16 blocks first (keeps 2-byte alignment), then 2 x Uint8 blocks.
    (out / "buildings_3d.bin").write_bytes(b"".join(a.tobytes() for a in (qx, qy, hl, hw, ang, meta)))
    header = {
        "count": n,
        "bbox": [lon0, lat0, lon1, lat1],
        "layout": "uint16 x[n], y[n], half_len_dm[n], half_wid_dm[n]; uint8 angle[n] (0..255 = 0..180 deg, from east, counter-clockwise), meta[n] (low 4 bits height class, high 4 bits risk 0..3)",
        "classes": HEIGHT_CLASSES,
        "risk": ["brak", "niskie", "średnie", "wysokie"],
        "source": "OpenStreetMap (ODbL), simplified to oriented rectangles",
    }
    (out / "buildings_3d.json").write_text(json.dumps(header, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"buildings: {n} rectangles, {(out / 'buildings_3d.bin').stat().st_size / 1e6:.1f} MB")


def line_coords(geom, digits=6):
    """List of LineString coordinate lists from a (Multi)LineString."""
    geom = geom.Clone()
    geom.FlattenTo2D()
    parts = [geom] if geom.GetGeometryType() == ogr.wkbLineString else [geom.GetGeometryRef(i) for i in range(geom.GetGeometryCount())]
    return [[[round(x, digits), round(y, digits)] for x, y in p.GetPoints()] for p in parts if p.GetPointCount() > 1]


def field(feat, name):
    idx = feat.GetFieldIndex(name)
    return (feat.GetField(idx) or "") if idx >= 0 else ""


def build_rail(src: Path, out: Path, bbox) -> None:
    ds = ogr.Open(str(src / "tory_dociete.gpkg"))
    layer = ds.GetLayer(0)
    layer.SetSpatialFilterRect(*bbox)
    features = []
    for feat in layer:
        geom = feat.GetGeometryRef()
        if geom is None:
            continue
        railway, service = field(feat, "railway"), field(feat, "service")
        if railway in ("disused", "abandoned", "razed"):
            kind = "disused"
        elif railway in ("light_rail", "tram", "subway"):
            kind = "light"
        elif service in ("yard", "spur", "siding", "crossover"):
            kind = "yard"
        else:
            kind = "main"
        props = {"k": kind, "name": field(feat, "name"), "operator": field(feat, "operator")}
        if field(feat, "bridge") not in ("", "no"):
            props["bridge"] = 1
        if field(feat, "tunnel") not in ("", "no"):
            props["tunnel"] = 1
        coords = line_coords(geom)
        if coords:
            features.append({"type": "Feature", "properties": props, "geometry": {"type": "MultiLineString", "coordinates": coords}})
    gj = {"type": "FeatureCollection", "features": features}
    (out / "rail_3d.geojson").write_text(json.dumps(gj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"rail: {len(features)} lines")


def build_power(src: Path, out: Path, bbox) -> None:
    ds = ogr.Open(str(src / "linie_energetyczne_dociete.gpkg"))
    layer = ds.GetLayer(0)
    layer.SetSpatialFilterRect(*bbox)
    features, pylons = [], set()
    for feat in layer:
        geom = feat.GetGeometryRef()
        if geom is None:
            continue
        power, line = field(feat, "power"), field(feat, "line")
        kind = "cable" if power == "cable" else ("station" if line in ("bay", "busbar") else "line")
        volt = field(feat, "voltage").split(";")[0]
        props = {"k": kind, "kv": round(int(volt) / 1000) if volt.isdigit() else None,
                 "name": field(feat, "name"), "operator": field(feat, "operator")}
        coords = line_coords(geom)
        if not coords:
            continue
        features.append({"type": "Feature", "properties": props, "geometry": {"type": "MultiLineString", "coordinates": coords}})
        if kind == "line":  # OSM overhead lines have a node at every tower
            pylons.update(tuple(p) for part in coords for p in part)
    features += [{"type": "Feature", "properties": {"k": "pylon"}, "geometry": {"type": "Point", "coordinates": list(p)}} for p in sorted(pylons)]
    gj = {"type": "FeatureCollection", "features": features}
    (out / "power_3d.geojson").write_text(json.dumps(gj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"power: {len(features) - len(pylons)} lines, {len(pylons)} pylons")


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    (lat0, lon0), (lat1, lon1) = meta["overlay_bounds"]
    bbox = (lon0, lat0, lon1, lat1)
    risk_by_osm = {}
    risk_file = out / "buildings_risk.geojson"
    if risk_file.exists():
        for f in json.loads(risk_file.read_text(encoding="utf-8"))["features"]:
            risk_by_osm[str(f["properties"].get("osm_id"))] = f["properties"].get("klasa")
    build_buildings(src, out, bbox, risk_by_osm)
    build_rail(src, out, bbox)
    build_power(src, out, bbox)


if __name__ == "__main__":
    main()
