"""Build a 3D scene package (buildings, roads, rail, water, pipelines, forest, terrain, RGB ground photo)
for one AOI, ready for frontend/view3d.html.

Usage (from the repo root):
    python3 backend/scripts/build_scene3d.py <aoi.geojson> <Dane/model3d_<id>/data>
        [--name "Display name"] [--overture PATH] [--cache DIR] [--insar-data <Dane/.../data>] [--power <file>]

Sources (all open, fetched live so the model is as current as the sources are):
    OpenStreetMap via Overpass API  - roads, rail, water, pipelines, industrial areas, places, masts
    Overture Maps (latest release)  - building footprints (OSM + Microsoft ML Buildings)
    USGS NAIP Plus image service    - RGB aerial photo used as the ground texture (USA only)
    ESA WorldCover 10 m (2021)      - tree cover, used to plant the trees
    USGS 3DEP elevation service     - lidar terrain heights (USA only); water bodies newer than the
                                      lidar survey, and swamp channels, are cut down to the water level
    outside the USA                 - Sentinel-2 cloudless 2024 (EOX) photo + close-up tiles at its full 10 m,
                                      FABDEM bare-earth terrain (Copernicus DEM as the fallback)
    --power (optional)              - the team's own power-line layer, used instead of the OSM power lines
    --insar-data (optional)         - an InSAR frontend folder of the same AOI: its pipeline layer (course
                                      only, no risk classes), its active hazard zone and the ground-motion
                                      timeline for the 2D view (build_timeline.py) are added

The level of detail follows the AOI size: a small AOI (up to 10 km) keeps every building footprint and
service road; a large one stores buildings as oriented rectangles in buildings.bin and drops minor roads.

Needs: GDAL Python bindings + numpy (e.g. Homebrew python3) and the `overturemaps` command
(pip install overturemaps; pass its path with --overture if it is not on PATH).

Output: GeoJSON layers (WGS84), ground.jpg, terrain.bin, trees.bin, optionally buildings.bin, and
meta.json that lists them.
"""
import argparse
import datetime
import json
import math
import random
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from osgeo import gdal, ogr, osr

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_timeline import build_timeline  # noqa: E402 - sibling script

gdal.UseExceptions()
ogr.UseExceptions()

USER_AGENT = "scene3d-builder/1.0"
OVERPASS_URLS = ["https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter"]
NAIP_URL = "https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPPlus/ImageServer"
DEM_URL = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer"
WORLDCOVER_URL = "/vsicurl/https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"
GROUND_TILE_PX = 1024       # the photo is fetched in tiles of this size
BIG_AOI_KM = 10             # above this the scene switches to the lighter "city scale" detail
MAX_FOOTPRINT_BUILDINGS = 5000  # more buildings than this are stored as rectangles (buildings.bin)
TREE_CLASS = 10             # ESA WorldCover "tree cover"
MAX_TREES = 350_000         # upper limit of planted trees (instanced in the viewer)
SETTLED_MIN = 60            # buildings per 250 m cell that make it a settlement (dense trees there)
ROAD_WIDTH_M = {"motorway": 14, "trunk": 12, "primary": 10, "secondary": 8, "tertiary": 7, "residential": 5.5,
                "unclassified": 5.5, "service": 4, "track": 3}
MAIN_ROADS = "motorway|trunk|primary|secondary|tertiary|unclassified|residential"
M_PER_DEG_LAT = 110_540.0
WGS84 = osr.SpatialReference()
WGS84.ImportFromEPSG(4326)
WGS84.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)  # lon, lat - like GeoJSON
# Our own pipeline layers describe the medium in words; the viewer wants these keys.
SUBSTANCE_KEYS = {"natural gas": "gas", "gas": "gas", "oil": "oil", "crude oil": "oil", "water": "water", "hydrogen": "hydrogen"}
# OGIM COMMODITY values (PEMEX pipelines in Mexico) -> the same viewer keys
OGIM_COMMODITY = {
    "OIL AND GAS": "oil_gas", "MULTIPHASE": "oil_gas", "OIL AND WATER": "oil_gas", "OIL, GAS AND WATER": "oil_gas",
    "OIL": "oil", "LIGHT OIL": "oil", "ISTHMUS": "oil", "OLMECA": "oil", "MAYA": "oil", "TERTIARY": "oil",  # crude grades
    "DRIED": "gas", "SOUR GAS": "gas", "SWEET GAS": "gas", "FUEL GAS": "gas", "WET": "gas", "WET AND SWEET": "gas",
    "SOUR VAPORS": "gas", "PNEUMATIC PUMPING": "gas", "CONDENSED": "ngl", "GASOLINE": "products",
    "CONGENITA WATER": "brine", "SERVICE WATER": "water", "NITROGEN": "nitrogen",
}
OGIM_STATUS = {"OPERATING": "czynny", "NON-OPERATIONAL": "nieczynny", "TEMPORARILY CLOSED": "czasowo zamknięty", "INACTIVE": "nieczynny"}


def http_get(url: str, data: bytes | None = None, timeout: int = 180, tries: int = 4) -> bytes:
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return res.read()
        except Exception as err:  # noqa: BLE001 - network errors of any kind are retried
            last = err
            time.sleep(4 * (attempt + 1))
    raise RuntimeError(f"GET {url[:90]} failed: {last}")


# ---------------------------------------------------------------- geometry helpers
def ring_geom(coords) -> ogr.Geometry:
    ring = ogr.Geometry(ogr.wkbLinearRing)
    for lon, lat in coords:
        ring.AddPoint_2D(lon, lat)
    ring.CloseRings()
    poly = ogr.Geometry(ogr.wkbPolygon)
    poly.AddGeometry(ring)
    return poly


def line_geom(coords) -> ogr.Geometry:
    line = ogr.Geometry(ogr.wkbLineString)
    for lon, lat in coords:
        line.AddPoint_2D(lon, lat)
    return line


def simple_parts(geom: ogr.Geometry, kind: str) -> list[ogr.Geometry]:
    """Flatten any (multi/collection) geometry to its Polygon or LineString parts."""
    wanted = ogr.wkbPolygon if kind == "polygon" else ogr.wkbLineString
    if geom is None or geom.IsEmpty():
        return []
    if ogr.GT_Flatten(geom.GetGeometryType()) == wanted:
        return [geom.Clone()]
    parts = []
    for i in range(geom.GetGeometryCount()):
        parts += simple_parts(geom.GetGeometryRef(i), kind)
    return parts


def rounded(obj, digits=7):
    if isinstance(obj, (list, tuple)):
        return [rounded(v, digits) for v in obj]
    return round(obj, digits) if isinstance(obj, float) else obj


def feature(geom: ogr.Geometry, props: dict, digits: int = 7) -> dict:
    geom.FlattenTo2D()
    gj = json.loads(geom.ExportToJson())
    gj["coordinates"] = rounded(gj["coordinates"], digits)
    return {"type": "Feature", "properties": {k: v for k, v in props.items() if v not in (None, "")}, "geometry": gj}


def write_geojson(path: Path, features: list[dict]) -> None:
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def min_area_rect(points: np.ndarray):
    """Oriented minimum-area rectangle of a convex hull (metres): cx, cy, half_len, half_wid, angle of the long side."""
    edges = np.diff(points, axis=0)
    best = None
    for a in np.unique(np.mod(np.arctan2(edges[:, 1], edges[:, 0]), math.pi / 2)):
        c, s = math.cos(a), math.sin(a)
        rx = points[:, 0] * c + points[:, 1] * s
        ry = -points[:, 0] * s + points[:, 1] * c
        area = (rx.max() - rx.min()) * (ry.max() - ry.min())
        if best is None or area < best[0]:
            best = (area, a, rx.min(), rx.max(), ry.min(), ry.max())
    _, a, x0, x1, y0, y1 = best
    mx, my, hl, hw = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    c, s = math.cos(a), math.sin(a)
    if hw > hl:
        hl, hw, a = hw, hl, a + math.pi / 2
    return mx * c - my * s, mx * s + my * c, hl, hw, a % math.pi


# ---------------------------------------------------------------------- OSM
def fetch_osm(bbox, cache: Path | None, big: bool, with_buildings: bool, wanted: set[str] | None = None,
              road_kinds: str | None = None) -> list[dict]:
    """Only the object types the scene uses (a plain 'everything in the box' query is far too big for a city).
    `wanted` = the layers to fetch (None = all), `road_kinds` = highway values joined by '|'."""
    w, s, e, n = bbox
    tag = "" if wanted is None and not road_kinds else "_" + "-".join(sorted(wanted or [])) + ("_" + road_kinds.replace("|", "-") if road_kinds else "")
    cached = cache / f"osm{tag}.json" if cache else None
    if cached and cached.exists():
        print("OSM: using cached", cached)
        return json.loads(cached.read_text(encoding="utf-8"))["elements"]
    box = f"({s},{w},{n},{e})"
    roads = road_kinds or (MAIN_ROADS if big else MAIN_ROADS + "|service|track")
    by_layer = {"roads": [f'way[highway~"^({roads})(_link)?$"]{box};'],
                "rail": [f'way[railway~"^(rail|light_rail|tram|narrow_gauge|disused|abandoned)$"]{box};'],
                "pipelines": [f'way[man_made=pipeline]{box};'], "power": [f'way[power~"^(line|minor_line|cable)$"]{box};'],
                "water": [f'nwr[natural=water]{box};'], "areas": [f'nwr[landuse~"^(industrial|reservoir|basin)$"]{box};'],
                "places": [f'node[place]{box};', f'node[man_made~"^(mast|tower|communications_tower)$"]{box};']}
    parts = [q for name, qs in by_layer.items() if wanted is None or name in wanted for q in qs] + [f'way[natural=coastline]{box};']
    if with_buildings and (wanted is None or "buildings" in wanted):
        parts.append(f"nwr[building]{box};")
    query = f"[out:json][timeout:300][maxsize:1073741824];({''.join(parts)});out body geom;"
    body = urllib.parse.urlencode({"data": query}).encode()
    last = None
    for url in OVERPASS_URLS * 2:
        try:
            raw = http_get(url, data=body, timeout=420, tries=1)
            elements = json.loads(raw)["elements"]
            if cached:
                cached.write_bytes(raw)
            return elements
        except Exception as err:  # noqa: BLE001
            last = err
            time.sleep(10)
    raise RuntimeError(f"Overpass failed: {last}")


def way_coords(el) -> list:
    return [(p["lon"], p["lat"]) for p in el.get("geometry", []) if p]


def element_polygon(el) -> ogr.Geometry | None:
    """Polygon of a closed way or a multipolygon relation."""
    if el["type"] == "way":
        coords = way_coords(el)
        return ring_geom(coords) if len(coords) >= 4 and coords[0] == coords[-1] else None
    if el["type"] != "relation":
        return None
    rings = {"outer": ogr.Geometry(ogr.wkbMultiLineString), "inner": ogr.Geometry(ogr.wkbMultiLineString)}
    for member in el.get("members", []):
        coords = [(p["lon"], p["lat"]) for p in member.get("geometry") or []]
        if member.get("type") == "way" and len(coords) >= 2:
            rings["inner" if member.get("role") == "inner" else "outer"].AddGeometry(line_geom(coords))
    try:
        outer = ogr.BuildPolygonFromEdges(rings["outer"], 0, 1)
        if rings["inner"].GetGeometryCount():
            outer = outer.Difference(ogr.BuildPolygonFromEdges(rings["inner"], 0, 1))
    except RuntimeError:
        return None
    return outer.MakeValid() if not outer.IsValid() else outer


def parse_number(text) -> float | None:
    try:
        return round(float(str(text).replace('"', "").replace("in", "").replace("m", "").strip()), 2)
    except (TypeError, ValueError):
        return None


def power_props(power, line, voltage, name, operator) -> dict:
    """Common description of a power line: kind (line = overhead on pylons, cable = underground,
    station = short pieces inside a substation), voltage in kV, name, operator."""
    kind = "cable" if power == "cable" else "station" if line in ("bay", "busbar") else "line"
    volt = str(voltage or "").split(";")[0].strip()
    return {"kind": kind, "kv": round(int(volt) / 1000) if volt.isdigit() else None, "name": name, "operator": operator}


def power_from_file(path: Path, aoi: ogr.Geometry, digits: int) -> list[dict]:
    """Power lines from the team's own layer (GeoPackage, GeoJSON, ... in any CRS), clipped to the AOI."""
    ds = ogr.Open(str(path))
    layer = ds.GetLayer(0)
    src = layer.GetSpatialRef()
    transform = None
    if src is not None and not src.IsSame(WGS84):
        src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        transform = osr.CoordinateTransformation(src, WGS84)
    field = lambda f, k: f.GetField(k) if f.GetFieldIndex(k) >= 0 else None  # noqa: E731
    out = []
    for f in layer:
        geom = f.GetGeometryRef()
        if geom is None:
            continue
        geom = geom.Clone()
        if transform:
            geom.Transform(transform)
        props = power_props(field(f, "power"), field(f, "line"), field(f, "voltage"), field(f, "name"), field(f, "operator"))
        for part in simple_parts(geom.Intersection(aoi), "line"):
            out.append(feature(part, props, digits))
    return out


def pipelines_from_file(spec: str, aoi: ogr.Geometry, digits: int) -> list[dict]:
    """The team's own pipeline layer (e.g. an OGIM GeoPackage; `file.gpkg:Layer`, default: its first line
    layer), clipped to the AOI. OGIM fields are translated (commodity -> viewer key, mm -> inches)."""
    path, _, name = spec.partition(":")
    ds = ogr.Open(path)
    layer = ds.GetLayerByName(name) if name else next(
        (ds.GetLayer(i) for i in range(ds.GetLayerCount()) if ogr.GT_Flatten(ds.GetLayer(i).GetGeomType()) in (ogr.wkbLineString, ogr.wkbMultiLineString)), None)
    if layer is None:
        raise SystemExit(f"no line layer in {spec}")
    src = layer.GetSpatialRef()
    transform = None
    if src is not None and not src.IsSame(WGS84):
        src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        transform = osr.CoordinateTransformation(src, WGS84)
    field = lambda f, k: (lambda v: None if v in (None, "", "N/A", -999, -999.0) else v)(f.GetField(k) if f.GetFieldIndex(k) >= 0 else None)  # noqa: E731
    out = []
    for f in layer:
        geom = f.GetGeometryRef()
        if geom is None:
            continue
        geom = geom.Clone()
        if transform:
            geom.Transform(transform)
        commodity = field(f, "COMMODITY") or field(f, "substance")
        mm = field(f, "PIPE_DIAMETER_MM")
        props = {"name": field(f, "FAC_NAME") or field(f, "name"), "operator": field(f, "OPERATOR") or field(f, "operator"),
                 "substance": OGIM_COMMODITY.get(str(commodity).upper()) or SUBSTANCE_KEYS.get(str(commodity).lower()) if commodity else None,
                 "commodity": commodity, "diameter_in": round(mm / 25.4, 1) if mm else None,
                 "status": OGIM_STATUS.get(field(f, "FAC_STATUS") or "", None), "id": field(f, "OGIM_ID")}
        for part in simple_parts(geom.Intersection(aoi), "line"):
            out.append(feature(part, props, digits))
    return out


def osm_layers(elements: list[dict], aoi: ogr.Geometry, big: bool) -> dict[str, list[dict]]:
    layers = {k: [] for k in ("roads", "rail", "water", "pipelines", "areas", "places", "buildings", "power")}
    digits = 6 if big else 7
    place_kinds = ("city", "town", "suburb", "village") if big else None  # a city has hundreds of neighbourhood nodes
    for el in elements:
        tags = el.get("tags") or {}
        if not tags:
            continue
        if el["type"] == "node":
            point = ogr.Geometry(ogr.wkbPoint)
            point.AddPoint_2D(el["lon"], el["lat"])
            if not aoi.Contains(point):
                continue
            if tags.get("place") and tags.get("name") and (place_kinds is None or tags["place"] in place_kinds):
                layers["places"].append(feature(point, {"kind": "place", "name": tags.get("name"), "place": tags["place"]}))
            elif tags.get("man_made") in ("mast", "tower", "communications_tower") and (not big or tags.get("tower:type") == "communication" or tags.get("man_made") == "mast"):
                layers["places"].append(feature(point, {"kind": "mast", "name": tags.get("name"), "height": parse_number(tags.get("height"))}))
            continue

        if tags.get("highway") and el["type"] == "way":
            kind = tags["highway"].replace("_link", "")
            if kind not in ROAD_WIDTH_M or (big and kind in ("service", "track")):
                continue
            for part in simple_parts(line_geom(way_coords(el)).Intersection(aoi), "line"):
                layers["roads"].append(feature(part, {
                    "kind": kind, "name": tags.get("name"), "ref": tags.get("ref"), "surface": tags.get("surface"),
                    "lanes": tags.get("lanes"), "bridge": 1 if tags.get("bridge") not in (None, "no") else None,
                    "tunnel": 1 if tags.get("tunnel") not in (None, "no") else None,
                    "width_m": ROAD_WIDTH_M[kind]}, digits))
        elif tags.get("railway") in ("rail", "light_rail", "tram", "narrow_gauge", "disused", "abandoned") and el["type"] == "way":
            for part in simple_parts(line_geom(way_coords(el)).Intersection(aoi), "line"):
                layers["rail"].append(feature(part, {
                    "kind": tags["railway"], "name": tags.get("name"), "operator": tags.get("operator"),
                    "service": tags.get("service"), "bridge": 1 if tags.get("bridge") not in (None, "no") else None,
                    "tunnel": 1 if tags.get("tunnel") not in (None, "no") else None}, digits))
        elif tags.get("power") in ("line", "minor_line", "cable") and el["type"] == "way":
            for part in simple_parts(line_geom(way_coords(el)).Intersection(aoi), "line"):
                layers["power"].append(feature(part, power_props(tags.get("power"), tags.get("line"), tags.get("voltage"), tags.get("name"), tags.get("operator")), digits))
        elif tags.get("man_made") == "pipeline" and el["type"] == "way":
            for part in simple_parts(line_geom(way_coords(el)).Intersection(aoi), "line"):
                layers["pipelines"].append(feature(part, {
                    "name": tags.get("name"), "operator": tags.get("operator"), "substance": tags.get("substance"),
                    "diameter_in": parse_number(tags.get("diameter")), "location": tags.get("location"),
                    "ref": tags.get("ref"), "usage": tags.get("usage"), "start_date": tags.get("start_date")}, digits))
        elif tags.get("natural") == "water" or tags.get("landuse") in ("industrial", "reservoir", "basin") or tags.get("building"):
            poly = element_polygon(el)
            if poly is None:
                continue
            target, props = "water", {"kind": tags.get("water") or "water", "name": tags.get("name")}
            if tags.get("building"):
                target, props = "buildings", {"kind": tags["building"], "name": tags.get("name"), "height": parse_number(tags.get("height")),
                                              "levels": tags.get("building:levels"), "source": "OpenStreetMap"}
            elif tags.get("landuse") == "industrial":
                target, props = "areas", {"kind": tags.get("industrial") or "industrial", "name": tags.get("name"), "operator": tags.get("operator")}
            if "sinkhole" in (tags.get("name") or "").lower():
                props["kind"] = "sinkhole"
            clipped = poly if target == "buildings" else poly.Intersection(aoi)
            if target == "buildings" and not aoi.Contains(poly.Centroid()):
                continue
            for part in simple_parts(clipped, "polygon"):
                layers[target].append(feature(part, props, digits))
    return layers


# ------------------------------------------------------------------ Overture
def overture_download(tool: str, bbox, kind: str, workdir: Path, cache: Path | None) -> Path:
    """Line-delimited GeoJSON, so a city's worth of buildings can be read one feature at a time."""
    out = (cache or workdir) / f"overture_{kind}.geojsonl"
    if not (cache and out.exists() and out.stat().st_size > 0):
        tmp = out.with_suffix(".part")
        subprocess.run([tool, "download", f"--bbox={bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}", "-f", "geojsonseq", "-t", kind, "-o", str(tmp)],
                       check=True, capture_output=True)
        tmp.replace(out)
    return out


def overture_features(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip("\x1e \n")
            if line:
                yield json.loads(line)


def overture_buildings(tool: str, bbox, workdir: Path, cache: Path | None) -> list[tuple[bytes, dict]]:
    """(WKB polygon, properties) of every building whose centre is inside the AOI box."""
    w, s, e, n = bbox
    out = []
    for f in overture_features(overture_download(tool, bbox, "building", workdir, cache)):
        geom = ogr.CreateGeometryFromJson(json.dumps(f["geometry"]))
        if geom is None:
            continue
        x0, x1, y0, y1 = geom.GetEnvelope()
        if not (w <= (x0 + x1) / 2 <= e and s <= (y0 + y1) / 2 <= n):
            continue
        p = f["properties"]
        source = (p.get("sources") or [{}])[0]
        props = {"kind": p.get("class") or p.get("subtype") or "building", "name": (p.get("names") or {}).get("primary"),
                 "height": p.get("height"), "levels": p.get("num_floors"), "source": source.get("dataset"), "source_version": source.get("version")}
        for part in simple_parts(geom, "polygon"):
            out.append((part.ExportToWkb(), props))
    return out


def overture_places(tool: str, bbox, aoi: ogr.Geometry, workdir: Path, cache: Path | None) -> list[dict]:
    features = []
    for f in overture_features(overture_download(tool, bbox, "place", workdir, cache)):
        geom = ogr.CreateGeometryFromJson(json.dumps(f["geometry"]))
        name = (f["properties"].get("names") or {}).get("primary")
        if geom is not None and name and aoi.Contains(geom):
            features.append(feature(geom, {"kind": "business", "name": name}))
    return features


def building_height(raw, levels, area: float) -> tuple[float, str]:
    """ML-derived heights are often near zero; fall back to a height typical for the footprint size."""
    if isinstance(raw, (int, float)) and raw >= 2.5:
        return float(raw), "source"
    if levels and str(levels).replace(".", "", 1).isdigit():
        return float(levels) * 3.0, "levels"
    return (3.2 if area < 60 else 4.2 if area < 400 else 6.5 if area < 2000 else 9.0), "estimated from footprint"


def building_features(buildings: list[tuple[bytes, dict]], m_per_deg_lon: float) -> list[dict]:
    features = []
    for wkb, props in buildings:
        geom = ogr.CreateGeometryFromWkb(wkb)
        area = geom.GetArea() * M_PER_DEG_LAT * m_per_deg_lon
        height, how = building_height(props.get("height"), props.get("levels"), area)
        props = {**props, "height": round(height, 1), "height_source": how, "area_m2": round(area)}
        props.pop("levels", None)
        features.append(feature(geom, props))
    return features


def building_rectangles(buildings: list[tuple[bytes, dict]], bbox, m_per_deg_lon: float, out: Path) -> dict:
    """Many buildings: store each as its smallest oriented rectangle + height (11 bytes per building)."""
    w, s, e, n = bbox
    rows = []
    for wkb, props in buildings:
        geom = ogr.CreateGeometryFromWkb(wkb)
        hull = geom.ConvexHull()
        ring = hull.GetGeometryRef(0) if ogr.GT_Flatten(hull.GetGeometryType()) == ogr.wkbPolygon else None
        if ring is None or ring.GetPointCount() < 4:
            continue
        pts = np.array(ring.GetPoints())[:, :2]
        ox, oy = pts[0]
        cx, cy, hl, hw, ang = min_area_rect(np.column_stack(((pts[:, 0] - ox) * m_per_deg_lon, (pts[:, 1] - oy) * M_PER_DEG_LAT)))
        area = geom.GetArea() * M_PER_DEG_LAT * m_per_deg_lon
        height, _ = building_height(props.get("height"), props.get("levels"), area)
        fill = area / max(4 * hl * hw, 0.01)  # an L-shaped house in its rectangle: shrink so the floor area stays right
        shrink = math.sqrt(min(1.0, max(0.45, fill)))
        rows.append((ox + cx / m_per_deg_lon, oy + cy / M_PER_DEG_LAT, hl * shrink, hw * shrink, ang, height, 0 if area < 450 else 1))
    arr = np.array(rows, dtype=np.float64)
    blocks = [np.round((arr[:, 0] - w) / (e - w) * 65535).astype("<u2"), np.round((n - arr[:, 1]) / (n - s) * 65535).astype("<u2"),
              np.clip(np.round(arr[:, 2] * 10), 5, 65535).astype("<u2"), np.clip(np.round(arr[:, 3] * 10), 5, 65535).astype("<u2"),
              np.round(arr[:, 4] / math.pi * 255).astype("u1"), np.clip(np.round(arr[:, 5] * 2), 4, 255).astype("u1"), arr[:, 6].astype("u1")]
    out.write_bytes(b"".join(b.tobytes() for b in blocks))
    return {"file": out.name, "count": len(arr),
            "layout": "uint16 x[n], y[n] (0..65535 = west..east, north..south), half_len_dm[n], half_wid_dm[n]; uint8 angle[n] (0..255 = 0..180 deg of the long side, from east, counter-clockwise), height_half_m[n], kind[n] (0 small with a pitched roof, 1 large with a flat roof)"}


# -------------------------------------------------------------- ground photo
def fetch_mosaic(bbox, size, cache: Path | None, bands: str = "0,1,2", tag: str = "ground", workers: int = 4) -> np.ndarray:
    """Aerial photo bands (NAIP: 0 red, 1 green, 2 blue, 3 near infrared) over exactly the box, as a
    (bands, height, width) uint8 array. Fetched in tiles (big single requests time out).

    The image service only returns the requested box when the pixels are square in the request's
    coordinates (degrees here); otherwise it silently widens the box and every tile would be squeezed
    and overlap its neighbours. So the tiles are asked for with square pixels and the mosaic is then
    resampled to the wanted grid."""
    w, s, e, n = bbox
    width, height = size
    nb = len(bands.split(","))
    d = min((e - w) / width, (n - s) / height)  # pixel size in degrees, the finer of the two directions
    cols, rows = math.ceil((e - w) / d - 1e-6), math.ceil((n - s) / d - 1e-6)
    mosaic = np.zeros((nb, rows, cols), dtype=np.uint8)
    key = f"{w:.6f}_{n:.6f}_{d:.3e}".replace("-", "m")

    def tile(origin):
        x0, y0 = origin
        tw, th = min(GROUND_TILE_PX, cols - x0), min(GROUND_TILE_PX, rows - y0)
        west, north = w + x0 * d, n - y0 * d
        params = {"bbox": f"{west},{north - th * d},{west + tw * d},{north}", "bboxSR": 4326, "imageSR": 4326, "size": f"{tw},{th}",
                  "format": "jpg", "compressionQuality": 95, "bandIds": bands, "interpolation": "RSP_BilinearInterpolation", "f": "image"}
        legacy = cache / f"ground_sq_{cols}x{rows}_{x0}_{y0}.jpg" if cache and tag == "ground" and bands == "0,1,2" else None
        tile_file = legacy if legacy and legacy.exists() else (cache / f"{tag}_{key}_{x0}_{y0}.jpg" if cache else None)
        for _ in range(12):
            if tile_file and tile_file.exists():
                return x0, y0, tw, th, tile_file.read_bytes()
            try:
                data = http_get(f"{NAIP_URL}/exportImage?{urllib.parse.urlencode(params)}", timeout=150, tries=1)
            except RuntimeError:
                data = b""
            if data[:2] == b"\xff\xd8":
                if tile_file:
                    tile_file.write_bytes(data)
                return x0, y0, tw, th, data
            time.sleep(5)
        raise RuntimeError("NAIP image service did not return an image")

    origins = [(x0, y0) for y0 in range(0, rows, GROUND_TILE_PX) for x0 in range(0, cols, GROUND_TILE_PX)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, (x0, y0, tw, th, data) in enumerate(pool.map(tile, origins)):
            name = f"/vsimem/{tag}_{key}_{x0}_{y0}.jpg"  # unique: several mosaics may run in parallel
            gdal.FileFromMemBuffer(name, data)
            arr = gdal.Open(name).ReadAsArray()
            arr = arr.reshape(1, th, tw) if arr.ndim == 2 else arr[:nb]  # a single band can come back as grey RGB
            mosaic[:, y0:y0 + th, x0:x0 + tw] = arr
            gdal.Unlink(name)
    mem = gdal.GetDriverByName("MEM").Create("", cols, rows, nb, gdal.GDT_Byte)
    mem.SetGeoTransform((w, d, 0, n, 0, -d))
    mem.SetSpatialRef(WGS84)
    mem.WriteArray(mosaic)
    exact = gdal.Warp("", mem, format="MEM", outputBounds=(w, s, e, n), width=width, height=height, resampleAlg="cubic")
    return exact.ReadAsArray().reshape(nb, height, width)


def save_jpeg(arr: np.ndarray, out: Path, quality: int) -> None:
    nb, h, w = arr.shape
    mem = gdal.GetDriverByName("MEM").Create("", w, h, nb, gdal.GDT_Byte)
    mem.WriteArray(arr)
    gdal.GetDriverByName("JPEG").CreateCopy(str(out), mem, options=[f"QUALITY={quality}"])
    aux = Path(str(out) + ".aux.xml")
    if aux.exists():
        aux.unlink()


S2_URL = "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2024_3857/default/g/${z}/${y}/${x}.jpg"
COP_DEM_URL = "/vsicurl/https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_{tile}_DEM/Copernicus_DSM_COG_10_{tile}_DEM.tif"
# FABDEM V1-2 (Univ. of Bristol / Fathom): Copernicus GLO-30 with forests and buildings removed - bare earth like
# a lidar terrain. 1 x 1 degree tiles read straight out of the official 10 x 10 degree zip files (range requests).
FABDEM_URL = "/vsizip//vsicurl/https://data.bris.ac.uk/datasets/s5hqmjcdj8yo2ibzi9b4ew3sn/{block}_FABDEM_V1-2.zip/{tile}_FABDEM_V1-2.tif"
S2_SOURCE = "Sentinel-2 cloudless 2024 (EOX IT Services, CC BY-NC-SA 4.0; zawiera zmodyfikowane dane Copernicus Sentinel)"
S2_MAX_ZOOM = 14            # ~9 m pixels: the native 10 m of Sentinel-2 (higher zooms are only upsampled)


def in_usa(bbox) -> bool:
    """NAIP photos and USGS 3DEP lidar cover the USA only (rough box of the 48 states)."""
    w, s, e, n = bbox
    return -125 <= w and e <= -66 and 24 <= s and n <= 50


def s2_mosaic(bbox, z: int, cache: Path | None, quiet: bool = False) -> gdal.Dataset:
    """Sentinel-2 cloudless 2024 (EOX) Web Mercator tiles of zoom z covering bbox, as one in-memory raster.
    The tile server refuses fast bulk downloads (HTTP 403), so the tiles come a few at a time, each one
    retried with growing pauses and kept on disk (an interrupted build resumes where it stopped)."""
    w, s, e, n = bbox
    tiles = 2 ** z

    def tx(lon):
        return (lon + 180) / 360 * tiles

    def ty(lat):
        return (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * tiles

    x0, x1, y0, y1 = int(tx(w)), int(tx(e)), int(ty(n)), int(ty(s))
    tile_dir = (cache or Path(tempfile.mkdtemp())) / f"s2cloudless_2024_z{z}"
    tile_dir.mkdir(parents=True, exist_ok=True)

    def get(xy):
        x, y = xy
        f = tile_dir / f"{x}_{y}.jpg"
        if f.exists() and f.stat().st_size > 0:
            return
        url = S2_URL.replace("${z}", str(z)).replace("${x}", str(x)).replace("${y}", str(y))
        for attempt in range(10):
            try:
                data = http_get(url, timeout=60, tries=1)
                if data[:2] == b"\xff\xd8":
                    f.write_bytes(data)
                    return
            except RuntimeError:
                pass
            time.sleep(min(60, 3 * 2 ** attempt))
        raise RuntimeError(f"Sentinel-2 tile {z}/{x}/{y} not available")

    jobs = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
    if not quiet:
        print(f"  Sentinel-2 cloudless: {len(jobs)} tiles at zoom {z}", flush=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        for i, _ in enumerate(pool.map(get, jobs)):
            if (i + 1) % 300 == 0 and not quiet:
                print(f"  tiles {i + 1}/{len(jobs)}", flush=True)
    cols, rows = x1 - x0 + 1, y1 - y0 + 1
    mosaic = np.zeros((3, rows * 256, cols * 256), dtype=np.uint8)
    for x, y in jobs:
        name = f"/vsimem/s2_{z}_{x}_{y}.jpg"
        gdal.FileFromMemBuffer(name, (tile_dir / f"{x}_{y}.jpg").read_bytes())
        mosaic[:, (y - y0) * 256:(y - y0 + 1) * 256, (x - x0) * 256:(x - x0 + 1) * 256] = gdal.Open(name).ReadAsArray()[:3]
        gdal.Unlink(name)
    world = 2 * 20_037_508.342789244
    res = world / (tiles * 256)
    mem = gdal.GetDriverByName("MEM").Create("", cols * 256, rows * 256, 3, gdal.GDT_Byte)
    mem.SetGeoTransform((-world / 2 + x0 * 256 * res, res, 0, world / 2 - y0 * 256 * res, 0, -res))
    merc = osr.SpatialReference()
    merc.ImportFromEPSG(3857)
    mem.SetSpatialRef(merc)
    mem.WriteArray(mosaic)
    return mem


def s2_zoom(bbox, width_px: int) -> int:
    """Zoom whose pixels are at least as fine as the wanted image (max 14 = the 10 m of Sentinel-2)."""
    w, s, e, n = bbox
    m_per_px = (e - w) * 111_320 * math.cos(math.radians((s + n) / 2)) / width_px
    return min(S2_MAX_ZOOM, max(8, math.ceil(math.log2(156_543.03 * math.cos(math.radians((s + n) / 2)) / m_per_px))))


def fetch_ground_world(bbox, size, out: Path, cache: Path | None) -> dict:
    """Outside the USA: the Sentinel-2 cloudless mosaic 2024 by EOX (10 m), from its Web Mercator tiles."""
    w, s, e, n = bbox
    mem = s2_mosaic(bbox, s2_zoom(bbox, size[0]), cache)
    exact = gdal.Warp("", mem, format="MEM", outputBounds=(w, s, e, n), dstSRS="EPSG:4326", width=size[0], height=size[1], resampleAlg="cubic")
    save_jpeg(exact.ReadAsArray()[:3], out, 86)
    return {"source": S2_SOURCE, "date": "2024", "resolution_m": 10}


def _deg_name(lat: int, lon: int, sep: str = "") -> str:
    return f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}{sep}{'E' if lon >= 0 else 'W'}{abs(lon):03d}"


def fetch_terrain_world(bbox, n: int, cache: Path | None = None) -> tuple[np.ndarray | None, str]:
    """Outside the USA: FABDEM (bare earth: Copernicus DEM without trees and buildings, like the lidar ground
    in the USA); Copernicus DEM GLO-30 (a surface model with trees and roofs) when FABDEM is not reachable.
    Returns the n x n grid and the name of the source used."""
    gdal.SetConfigOption("AWS_NO_SIGN_REQUEST", "YES")
    gdal.SetConfigOption("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    w, s, e, north = bbox
    cells = [(lat, lon) for lat in range(math.floor(s), math.floor(north - 1e-9) + 1) for lon in range(math.floor(w), math.floor(e - 1e-9) + 1)]
    cached = cache / f"fabdem_{n}.tif" if cache else None
    for source in ("fabdem", "copernicus"):
        if source == "fabdem" and cached and cached.exists():
            grid = gdal.Open(str(cached))
        else:
            tiles = []
            for lat, lon in cells:
                if source == "fabdem":
                    b_lat, b_lon = math.floor(lat / 10) * 10, math.floor(lon / 10) * 10
                    path = FABDEM_URL.format(block=f"{_deg_name(b_lat, b_lon)}-{_deg_name(b_lat + 10, b_lon + 10)}", tile=_deg_name(lat, lon))
                else:
                    path = COP_DEM_URL.format(tile=_deg_name(lat, lon, "_00_") + "_00")
                try:
                    gdal.Open(path)
                    tiles.append(path)
                except RuntimeError:
                    pass  # open sea: no tile (or the source is down)
            if not tiles:
                continue
            print(f"  terrain: {source}, {len(tiles)} tiles", flush=True)
            grid = gdal.Warp(str(cached) if source == "fabdem" and cached else "", tiles, format="GTiff" if source == "fabdem" and cached else "MEM",
                             outputBounds=(w, s, e, north), width=n, height=n, resampleAlg="bilinear",
                             dstSRS="EPSG:4326", dstNodata=float("nan"), outputType=gdal.GDT_Float32)
        dem = grid.ReadAsArray().astype(np.float32)
        grid = None
        dem[~np.isfinite(dem) | (dem < -500) | (dem > 9000)] = np.nan
        if not np.isnan(dem).all():
            return dem, source
    return None, ""


def fetch_ground(bbox, size, out: Path, cache: Path | None) -> dict:
    """The aerial photo over exactly the AOI box, saved as one JPEG (`size` pixels), plus its date."""
    w, s, e, n = bbox
    save_jpeg(fetch_mosaic(bbox, size, cache), out, 86)
    info = {"source": "USGS NAIP Plus (USDA aerial photo)", "date": None, "resolution_m": None}
    try:
        query = {"where": "Category=1", "geometry": f"{(w + e) / 2},{(s + n) / 2}", "geometryType": "esriGeometryPoint", "inSR": 4326,
                 "spatialRel": "esriSpatialRelIntersects", "outFields": "Name,acquisition_date,resolution_value", "returnGeometry": "false", "f": "json"}
        tiles = json.loads(http_get(f"{NAIP_URL}/query?{urllib.parse.urlencode(query)}"))["features"]
        dates = [t["attributes"]["acquisition_date"] for t in tiles if t["attributes"].get("acquisition_date")]
        if dates:
            info["date"] = datetime.datetime.fromtimestamp(max(dates) / 1000, datetime.UTC).date().isoformat()
            info["resolution_m"] = tiles[0]["attributes"].get("resolution_value")
    except Exception:  # noqa: BLE001 - the date is nice to have, the image is what matters
        pass
    return info


# ------------------------------------------------------------ forest and trees
def worldcover(bbox, width: int, height: int) -> np.ndarray:
    gdal.SetConfigOption("AWS_NO_SIGN_REQUEST", "YES")
    gdal.SetConfigOption("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    w, s, e, n = bbox
    tiles = set()
    for lat in (s, n):
        for lon in (w, e):
            la, lo = math.floor(lat / 3) * 3, math.floor(lon / 3) * 3
            tiles.add(f"{'N' if la >= 0 else 'S'}{abs(la):02d}{'E' if lo >= 0 else 'W'}{abs(lo):03d}")
    ds = gdal.Warp("", [WORLDCOVER_URL.format(tile=t) for t in sorted(tiles)], format="MEM", outputBounds=(w, s, e, n),
                   width=width, height=height, resampleAlg="near", dstSRS="EPSG:4326")
    return ds.ReadAsArray()


def raster_like(bbox, width: int, height: int) -> gdal.Dataset:
    w, s, e, n = bbox
    ds = gdal.GetDriverByName("MEM").Create("", width, height, 1, gdal.GDT_Byte)
    ds.SetGeoTransform((w, (e - w) / width, 0, n, 0, -(n - s) / height))
    ds.SetSpatialRef(WGS84)
    return ds


def forest_polygons(cover: np.ndarray, bbox) -> list[dict]:
    h, w = cover.shape
    ds = raster_like(bbox, w, h)
    ds.GetRasterBand(1).WriteArray((cover == TREE_CLASS).astype(np.uint8))
    mem = ogr.GetDriverByName("MEM").CreateDataSource("")
    layer = mem.CreateLayer("forest", srs=WGS84, geom_type=ogr.wkbPolygon)
    layer.CreateField(ogr.FieldDefn("v", ogr.OFTInteger))
    gdal.Polygonize(ds.GetRasterBand(1), ds.GetRasterBand(1), layer, 0)
    features = []
    for f in layer:
        geom = f.GetGeometryRef().Simplify(0.00008)
        if geom is not None and not geom.IsEmpty() and geom.GetArea() > 4e-8:  # drop specks under ~4 pixels
            features.append(feature(geom, {"kind": "forest", "source": "ESA WorldCover 2021"}, 6))
    return features


def keep_out_mask(bbox, m_per_deg_lon: float, layers: dict, building_wkb: list[bytes], px: int, clearance: float) -> np.ndarray:
    """Raster (px x px) of places where no tree may stand: roads, rail, pipelines, buildings, water.
    `clearance` = free strip in metres beside roads and buildings (wide in a forest, narrow in a city
    where the trees stand right along the streets)."""
    ds = raster_like(bbox, px, px)
    mem = ogr.GetDriverByName("MEM").CreateDataSource("")
    layer = mem.CreateLayer("mask", srs=WGS84, geom_type=ogr.wkbUnknown)
    pixel = (bbox[2] - bbox[0]) / px
    deg = lambda metres: metres / m_per_deg_lon  # noqa: E731 - small local conversion

    def add(geom, radius):
        row = ogr.Feature(layer.GetLayerDefn())
        row.SetGeometry(geom.Buffer(radius, 4) if radius > pixel else geom)  # ALL_TOUCHED already widens by a pixel
        layer.CreateFeature(row)

    buffers = [("roads", lambda p: deg(p.get("width_m", 4) / 2 + clearance)), ("rail", lambda p: deg(7)), ("pipelines", lambda p: deg(7)),
               ("water", lambda p: deg(1)), ("areas", lambda p: 0)]
    for name, radius in buffers:
        for f in layers.get(name, []):
            add(ogr.CreateGeometryFromJson(json.dumps(f["geometry"])), radius(f["properties"]))
    for wkb in building_wkb:
        add(ogr.CreateGeometryFromWkb(wkb), deg(clearance + 1))
    gdal.RasterizeLayer(ds, [1], layer, burn_values=[1], options=["ALL_TOUCHED=TRUE"])
    return ds.ReadAsArray().astype(bool)


def plant_trees(size_m, spacing: float, cover: np.ndarray, mask: np.ndarray, ground: Path, out: Path) -> int:
    """One tree per jittered grid cell where WorldCover says trees and the photo looks like canopy."""
    rgb = gdal.Open(str(ground)).ReadAsArray().astype(np.float32)  # (3, H, W)
    gh, gw = rgb.shape[1:]
    rng = random.Random(42)  # fixed seed: the same forest on every build
    width_m, height_m = size_m
    # How green is the canopy in this photo? Lush forest is clearly green; in a dry climate the crowns are
    # olive and, seen from above next to roofs and pavement, almost grey - the colour test must allow that.
    probe = random.Random(7)
    ratios = []
    for _ in range(200_000):
        u, v = probe.random(), probe.random()
        if cover[int(v * cover.shape[0]), int(u * cover.shape[1])] == TREE_CLASS:
            px, py = int(u * gw), int(v * gh)
            ratios.append(rgb[1, py, px] / max(rgb[0, py, px], 1.0))
        if len(ratios) >= 4000:
            break
    dry = bool(ratios) and float(np.median(ratios)) < 1.12
    min_gr, min_gb, max_lum = (0.95, 1.02, 135) if dry else (1.04, 1.15, 118)
    xs, ys, hs, cols, kinds = [], [], [], [], []
    for iy in range(int(height_m / spacing)):
        for ix in range(int(width_m / spacing)):
            u = (ix + rng.uniform(0.1, 0.9)) * spacing / width_m    # 0..1 west -> east
            v = (iy + rng.uniform(0.1, 0.9)) * spacing / height_m   # 0..1 north -> south
            if cover[int(v * cover.shape[0]), int(u * cover.shape[1])] != TREE_CLASS or mask[int(v * mask.shape[0]), int(u * mask.shape[1])]:
                continue
            px, py = int(u * gw), int(v * gh)
            patch = rgb[:, max(0, py - 2):py + 3, max(0, px - 2):px + 3].reshape(3, -1).mean(axis=1)
            r, g, b = patch
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            # canopy = green and neither bright (grass, pads, roofs, roads) nor near-black (open water)
            if not (g > r * min_gr and g > b * min_gb and 26 < lum < max_lum):
                continue
            if dry:  # the photo patch is mixed with its surroundings there; pull the crown towards a leaf green
                r, g, b = 0.45 * r + 0.55 * 62, 0.45 * g + 0.55 * 98, 0.45 * b + 0.55 * 52
            # crowns a little lighter than the shaded canopy in the photo, with per-tree variation
            k = rng.uniform(0.9, 1.2) if dry else rng.uniform(1.05, 1.4)
            cols.append([min(255, r * k), min(255, g * k), min(255, b * k)])
            xs.append(u)
            ys.append(v)
            hs.append(rng.triangular(8, 24, 15))
            kinds.append(1 if rng.random() < 0.3 else 0)  # 1 = conical, 0 = round crown
    n = len(xs)
    cols_arr = np.array(cols, dtype=np.float32).reshape(n, 3).astype("u1")
    blocks = [np.round(np.array(xs) * 65535).astype("<u2"), np.round(np.array(ys) * 65535).astype("<u2"),
              np.round(np.array(hs) * 10).astype("u1"), cols_arr[:, 0].copy(), cols_arr[:, 1].copy(), cols_arr[:, 2].copy(), np.array(kinds, dtype="u1")]
    out.write_bytes(b"".join(b.tobytes() for b in blocks))
    return n


def plant_trees_settled(size_m, cover: np.ndarray, mask: np.ndarray, ground: Path, out: Path, building_wkb: list[bytes],
                        bbox, budget: int = MAX_TREES) -> tuple[int, float, float]:
    """For a big AOI without an infrared photo: dense trees where people live, sparse ones elsewhere.

    Close up the viewer shows towns and villages, so there the trees stand about as densely as real crowns
    (WorldCover trees, plus green and dark photo pixels: street and garden trees that the land-cover map
    counts as built-up). In the open country one tree stands for a patch of forest. Both spacings follow from
    `budget`. Returns (count, dense spacing, sparse spacing)."""
    w, s, e, n = bbox
    width_m, height_m = size_m
    rgb = gdal.Open(str(ground)).ReadAsArray().astype(np.float32)
    gh, gw = rgb.shape[1:]
    # settled zone: 250 m cells with at least SETTLED_MIN buildings, widened by one cell
    cell = 250.0
    cw, ch = int(math.ceil(width_m / cell)), int(math.ceil(height_m / cell))
    dens = np.zeros((ch, cw), dtype=np.int32)
    for wkb in building_wkb:
        x0, x1, y0, y1 = ogr.CreateGeometryFromWkb(wkb).GetEnvelope()
        cx, cy = int(((x0 + x1) / 2 - w) / (e - w) * cw), int((n - (y0 + y1) / 2) / (n - s) * ch)
        if 0 <= cx < cw and 0 <= cy < ch:
            dens[cy, cx] += 1
    settled = dens >= SETTLED_MIN
    grown = settled.copy()
    grown[1:] |= settled[:-1]; grown[:-1] |= settled[1:]; grown[:, 1:] |= settled[:, :-1]; grown[:, :-1] |= settled[:, 1:]

    def sample(arr, u, v):
        return arr[np.minimum((v * arr.shape[0]).astype(int), arr.shape[0] - 1), np.minimum((u * arr.shape[1]).astype(int), arr.shape[1] - 1)]

    def candidates(spacing, rng, zone):
        nx, ny = int(width_m / spacing), int(height_m / spacing)
        iy, ix = np.mgrid[0:ny, 0:nx]
        u = ((ix + rng.uniform(0.1, 0.9, ix.shape)) * spacing / width_m).ravel()
        v = ((iy + rng.uniform(0.1, 0.9, iy.shape)) * spacing / height_m).ravel()
        keep = sample(zone, u, v) & ~sample(mask, u, v)
        return u[keep], v[keep]

    def canopy(u, v, urban):
        px, py = np.minimum((u * gw).astype(int), gw - 1), np.minimum((v * gh).astype(int), gh - 1)
        r, g, b = rgb[0, py, px], rgb[1, py, px], rgb[2, py, px]
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        green = (g > r * 1.04) & (g > b * 1.15) & (26 < lum) & (lum < 118)
        tree = sample(cover, u, v) == TREE_CLASS
        return (tree | (urban & green & (lum < 95))) & green, (r, g, b)

    rng = np.random.default_rng(42)
    # how much canopy is there in each zone? (a quick 50 m probe) -> spacings that fit the budget
    pu, pv = candidates(50.0, rng, grown)
    dense_area = float(canopy(pu, pv, True)[0].sum()) * 2500
    pu, pv = candidates(50.0, rng, ~grown)
    sparse_area = float(canopy(pu, pv, False)[0].sum()) * 2500
    dense_sp = max(12.0, math.sqrt(dense_area / (budget * 0.8))) if dense_area else 12.0
    sparse_sp = max(dense_sp, math.sqrt(sparse_area / (budget * 0.2))) if sparse_area else 999.0
    xs, ys, cols, hs, kinds = [], [], [], [], []
    for spacing, zone, urban in ((dense_sp, grown, True), (sparse_sp, ~grown, False)):
        u, v = candidates(spacing, rng, zone)
        ok, (r, g, b) = canopy(u, v, urban)
        u, v, r, g, b = u[ok], v[ok], r[ok], g[ok], b[ok]
        k = rng.uniform(1.05, 1.4, u.size)
        xs.append(u); ys.append(v)
        cols.append(np.stack([np.minimum(255, r * k), np.minimum(255, g * k), np.minimum(255, b * k)], axis=1))
        hs.append(rng.triangular(8, 15, 24, u.size) if not urban else rng.triangular(6, 11, 18, u.size))
        kinds.append((rng.random(u.size) < (0.12 if urban else 0.25)).astype("u1"))
    xs, ys, hs, kinds = np.concatenate(xs), np.concatenate(ys), np.concatenate(hs), np.concatenate(kinds)
    cols_arr = np.concatenate(cols).astype("u1")
    count = int(xs.size)
    blocks = [np.round(xs * 65535).astype("<u2"), np.round(ys * 65535).astype("<u2"), np.round(hs * 10).astype("u1"),
              cols_arr[:, 0].copy(), cols_arr[:, 1].copy(), cols_arr[:, 2].copy(), kinds]
    out.write_bytes(b"".join(b.tobytes() for b in blocks))
    return count, dense_sp, sparse_sp


# ------------------------------------------------------------------ terrain
def fetch_terrain(bbox, n: int, cache: Path | None) -> np.ndarray | None:
    """Lidar ground heights (metres) on an n x n grid over exactly the AOI box, rows north -> south;
    NaN where there is no data.

    Like the photo, the elevation service widens a box whose pixels are not square, so the request uses
    square pixels and the returned GeoTIFF is resampled to the grid by its own georeferencing (whatever
    extent the service decided to send)."""
    w, s, e, north = bbox
    d = min((e - w), (north - s)) / n
    cols, rows = round((e - w) / d), round((north - s) / d)
    cached = cache / f"terrain_sq_{n}.tif" if cache else None
    data = cached.read_bytes() if cached and cached.exists() else b""
    params = {"bbox": f"{w},{north - rows * d},{w + cols * d},{north}", "bboxSR": 4326, "imageSR": 4326, "size": f"{cols},{rows}", "format": "tiff",
              "pixelType": "F32", "interpolation": "RSP_BilinearInterpolation", "f": "image"}
    for _ in range(8):
        if data[:2] in (b"II", b"MM"):
            break
        try:
            data = http_get(f"{DEM_URL}/exportImage?{urllib.parse.urlencode(params)}", timeout=200, tries=1)
        except RuntimeError:
            data = b""
            time.sleep(5)
    else:
        return None
    if cached:
        cached.write_bytes(data)
    gdal.FileFromMemBuffer("/vsimem/terrain.tif", data)
    src = gdal.Open("/vsimem/terrain.tif")
    grid = gdal.Warp("", src, format="MEM", outputBounds=(w, s, e, north), width=n, height=n, resampleAlg="bilinear",
                     srcSRS=None if src.GetProjection() else "EPSG:4326", dstSRS="EPSG:4326", dstNodata=float("nan"))
    dem = grid.ReadAsArray().astype(np.float32)
    src = None
    gdal.Unlink("/vsimem/terrain.tif")
    dem[~np.isfinite(dem) | (dem < -500) | (dem > 9000)] = np.nan
    return None if np.isnan(dem).all() else dem


def carve_water(dem: np.ndarray, bbox, water: list[dict], lowland: bool) -> list[str]:
    """Give mapped water a bed where the lidar shows ground instead of flat open water (swamp
    channels under trees, or a sinkhole that opened after the survey). Returns the names of what was cut.

    Lowland (a few metres of relief): everything goes down to the area's open-water level.
    Hills: each water body is flattened to its own shore level, so a reservoir stays where it is."""
    n = dem.shape[0]
    bodies = []
    for f in water:
        ds = raster_like(bbox, n, n)
        mem = ogr.GetDriverByName("MEM").CreateDataSource("")
        layer = mem.CreateLayer("w", srs=WGS84, geom_type=ogr.wkbPolygon)
        row = ogr.Feature(layer.GetLayerDefn())
        row.SetGeometry(ogr.CreateGeometryFromJson(json.dumps(f["geometry"])))
        layer.CreateFeature(row)
        gdal.RasterizeLayer(ds, [1], layer, burn_values=[1], options=["ALL_TOUCHED=TRUE"])
        mask = ds.ReadAsArray().astype(bool)
        if mask.any():
            bodies.append((f, mask))
    if not bodies:
        return []
    open_water = min(float(np.median(dem[m])) for _, m in bodies)  # the lidar is flattened over water it saw
    carved = []
    for f, mask in bodies:
        inside = dem[mask]
        level = open_water if lowland else float(np.percentile(inside, 10))
        if (np.median(inside) > level + 0.25) if lowland else (inside.std() > 0.3):
            dem[mask] = np.minimum(inside, level)
            carved.append(f["properties"].get("name") or f["properties"].get("kind") or "water")
    return carved


def smooth(grid: np.ndarray, passes: int) -> np.ndarray:
    """Light 3x3 blur: removes lidar speckle under the forest, keeps levees and embankments."""
    for _ in range(passes):
        padded = np.pad(grid, 1, mode="edge")
        grid = (padded[:-2, 1:-1] + padded[2:, 1:-1] + padded[1:-1, :-2] + padded[1:-1, 2:] + 4 * padded[1:-1, 1:-1]) / 8
    return grid


def write_terrain(bbox, n: int, water: list[dict], coast: bool, big: bool, out: Path, cache: Path | None) -> dict | None:
    usa = in_usa(bbox)
    if usa:
        dem, world_source = fetch_terrain(bbox, n, cache), ""
    else:
        dem, world_source = fetch_terrain_world(bbox, n, cache)
    if dem is not None and not usa:
        coast = coast or bool((np.nan_to_num(dem, nan=0.0) <= 0.5).mean() > 0.03)  # no coastline layer in a ground-only scene
    if dem is None:
        print("terrain: elevation service unavailable - the model stays flat")
        return None
    missing = np.isnan(dem)
    dem[missing] = 0.0 if coast else float(np.nanmedian(dem))  # no lidar over the open sea
    carved = carve_water(dem, bbox, water, lowland=float(dem.max() - dem.min()) < 15)
    sea = bool(coast and (dem <= 0.05).mean() > 0.03)
    dem = smooth(dem, 1 if big else 2)
    low, high = float(dem.min()), float(dem.max())
    span = max(high - low, 0.01)
    (out / "terrain.bin").write_bytes(np.round((dem - low) / span * 65535).astype("<u2").tobytes())
    # Flat land needs a strong exaggeration to show any relief; hills need little or none.
    if span > 150:
        default, steps = (2 if big else 1), [1, 2, 3]
    elif span > 15:
        default, steps = 5, [1, 5, 10]
    else:
        default, steps = 30, [1, 5, 15, 30]
    return {"file": "terrain.bin", "width": n, "height": n, "min_m": round(low, 2), "max_m": round(high, 2),
            "layout": "uint16[width*height], rows north -> south; height = min_m + value / 65535 * (max_m - min_m)",
            "default_exaggeration": default, "exaggerations": steps, "sea_level_m": 0.0 if sea else None,
            "carved_water": carved, "source": "USGS 3DEP (lidar)" if usa else "FABDEM V1-2" if world_source == "fabdem" else "Copernicus DEM GLO-30"}


# ------------------------------------------------- extras from an InSAR package
def insar_extras(data: Path, aoi: ogr.Geometry, hazard_name: str | None) -> dict:
    """The team's pipeline layer and the active hazard zone of the same AOI.

    Only where the pipelines run is taken (plus name and operator for the tooltip): the risk classes
    and the medium are left out on purpose, the 3D model does not classify pipelines for now."""
    meta = json.loads((data / "meta.json").read_text(encoding="utf-8"))
    extras = {"pipelines": None, "hazards": []}
    pipes = data / "pipelines.geojson"
    if pipes.exists():
        extras["pipelines"] = []
        for f in json.loads(pipes.read_text(encoding="utf-8"))["features"]:
            p = f["properties"]
            geom = ogr.CreateGeometryFromJson(json.dumps(f["geometry"]))
            for part in simple_parts(geom.Intersection(aoi), "line"):
                extras["pipelines"].append(feature(part, {"name": p.get("nazwa"), "operator": p.get("operator"), "source": "OGIM"}, 6))
    zone = data / "active_zone.geojson"
    if zone.exists():
        for f in json.loads(zone.read_text(encoding="utf-8"))["features"]:
            geom = ogr.CreateGeometryFromJson(json.dumps(f["geometry"]))
            for part in simple_parts(geom, "polygon"):
                if part.GetArea() * M_PER_DEG_LAT * 92_000 > 20_000:  # skip slivers under ~2 ha
                    extras["hazards"].append(feature(part, {"kind": "landslide", "name": hazard_name or "Aktywne osuwisko",
                                                            "note": "Strefa aktywnego ruchu terenu wyznaczona z InSAR", "source": meta.get("stack")}, 6))
    extras["hazard_source"] = meta.get("stack") if extras["hazards"] else None
    return extras


# ---------------------------------------------------------------------- main
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("aoi", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--name", help="display name (default: the AOI's 'name' property)")
    parser.add_argument("--overture", default=shutil.which("overturemaps"), help="path to the overturemaps command")
    parser.add_argument("--cache", type=Path, help="folder for raw downloads of this AOI; delete it to refresh the data")
    parser.add_argument("--insar-data", type=Path, help="InSAR frontend folder of the same AOI (pipeline course and hazard zone)")
    parser.add_argument("--hazard-name", help="label of the hazard zone taken from --insar-data")
    parser.add_argument("--power", type=Path, help="own power-line layer (GPKG/GeoJSON, any CRS) instead of the OSM one")
    parser.add_argument("--pipes-one-class", action="store_true", help="show all pipelines in one colour, one legend entry")
    parser.add_argument("--pipes-thin", action="store_true", help="thin pipes as in a small scene, plus a 1 px line (a big scene with many pipes)")
    parser.add_argument("--pipelines", help="own pipeline layer instead of the OSM one: file[:layer], e.g. tabasco_ogim.gpkg:Oil_Natural_Gas_Pipelines")
    parser.add_argument("--ground-only", action="store_true", help="only the photo and the terrain (an empty map to fill later)")
    parser.add_argument("--layers", help="only these object layers, comma separated, e.g. roads,places,buildings,trees (default: all)")
    parser.add_argument("--road-kinds", help="OSM highway values to keep, e.g. 'motorway|trunk|primary|secondary' (default: by AOI size)")
    args = parser.parse_args()

    aoi_feature = json.loads(args.aoi.read_text(encoding="utf-8"))["features"][0]
    aoi = ogr.CreateGeometryFromJson(json.dumps(aoi_feature["geometry"]))
    w, e, s, n = aoi.GetEnvelope()
    bbox = (w, s, e, n)
    m_per_deg_lon = 111_320.0 * math.cos(math.radians((s + n) / 2))
    size_m = ((e - w) * m_per_deg_lon, (n - s) * M_PER_DEG_LAT)
    big = max(size_m) > BIG_AOI_KM * 1000
    args.out.mkdir(parents=True, exist_ok=True)
    if args.cache:
        args.cache.mkdir(parents=True, exist_ok=True)
    print(f"AOI {bbox}  {size_m[0]:.0f} x {size_m[1]:.0f} m  ({'city scale' if big else 'full detail'})", flush=True)

    usa = in_usa(bbox)
    wanted = set(args.layers.split(",")) if args.layers else None
    keep = lambda name: wanted is None or name in wanted  # noqa: E731
    if args.ground_only:
        elements, args.overture = [], None
    else:
        from_osm = None if wanted is None else wanted - ({"pipelines"} if args.pipelines else set()) - ({"power"} if args.power else set())
        elements = fetch_osm(bbox, args.cache, big, with_buildings=not args.overture, wanted=from_osm, road_kinds=args.road_kinds)
    coast = any((el.get("tags") or {}).get("natural") == "coastline" for el in elements)
    layers = osm_layers(elements, aoi, big)
    del elements
    layers = {name: (features if keep(name) else []) for name, features in layers.items()}
    if wanted is not None and "masts" not in wanted:  # "places" alone = just the place names
        layers["places"] = [f for f in layers["places"] if f["properties"].get("kind") != "mast"]
    print("OSM:", {k: len(v) for k, v in layers.items()}, "| coast:", coast, flush=True)

    # buildings: Overture when available (kept as WKB so a whole city fits in memory)
    overture_release = None
    buildings = [(ogr.CreateGeometryFromJson(json.dumps(f["geometry"])).ExportToWkb(), f["properties"]) for f in layers["buildings"]]
    if args.overture and not keep("buildings"):
        args.overture = None
    if args.overture:
        with tempfile.TemporaryDirectory() as tmp:
            buildings = overture_buildings(args.overture, bbox, Path(tmp), args.cache) or buildings
            if not big:
                layers["places"] += overture_places(args.overture, bbox, aoi, Path(tmp), args.cache)
        overture_release = subprocess.run([args.overture, "releases", "latest"], capture_output=True, text=True).stdout.strip() or None
        print(f"Overture {overture_release}: {len(buildings)} buildings", flush=True)
    elif not args.ground_only:
        print("overturemaps not found - buildings come from OSM only")
    buildings_bin = None
    if len(buildings) > MAX_FOOTPRINT_BUILDINGS:
        buildings_bin = building_rectangles(buildings, bbox, m_per_deg_lon, args.out / "buildings.bin")
        layers["buildings"] = []
    else:
        layers["buildings"] = building_features(buildings, m_per_deg_lon)

    extras = insar_extras(args.insar_data, aoi, args.hazard_name) if args.insar_data else {"pipelines": None, "hazards": [], "hazard_source": None}
    stale = args.out / "insar.png"  # older builds also copied the displacement map
    if stale.exists():
        stale.unlink()
    if extras["pipelines"] is not None:
        layers["pipelines"] = extras["pipelines"]  # the team's own pipeline layer replaces the OSM one
    layers["hazards"] = extras["hazards"] if keep("hazards") else []
    if args.power:
        layers["power"] = power_from_file(args.power, aoi, 6 if big else 7)
        print(f"power: {len(layers['power'])} lines from {args.power}")
    if args.pipelines:
        layers["pipelines"] = pipelines_from_file(args.pipelines, aoi, 6 if big else 7)
        print(f"pipelines: {len(layers['pipelines'])} lines from {args.pipelines}")

    ground_px = 8192 if big else 4096
    scale = ground_px / max(size_m)
    ground_size = (round(size_m[0] * scale), round(size_m[1] * scale))
    ground = (fetch_ground if usa else fetch_ground_world)(bbox, ground_size, args.out / "ground.jpg", args.cache)
    print("ground photo:", ground, ground_size, flush=True)

    spacing = max(20.0, max(size_m) / 1500) if big else 13.0
    if args.ground_only or not keep("trees"):
        layers["forest"], trees = [], 0
    else:
        cover = worldcover(bbox, round(size_m[0] / 10), round(size_m[1] / 10))
        # a big wooded area would get millions of trees: the spacing grows so the viewer keeps a fluid frame rate
        spacing = max(spacing, math.sqrt(float((cover == TREE_CLASS).mean()) * size_m[0] * size_m[1] / MAX_TREES))
        layers["forest"] = [] if big else forest_polygons(cover, bbox)
        mask = keep_out_mask(bbox, m_per_deg_lon, layers, [wkb for wkb, _ in buildings], min(6000, max(2500, round(max(size_m) / 5))), 2.0 if big else 5.0)
        if big and not usa:  # no infrared photo for build_scene_detail: dense in towns, sparse in the country
            trees, spacing, sparse = plant_trees_settled(size_m, cover, mask, args.out / "ground.jpg", args.out / "trees.bin",
                                                         [wkb for wkb, _ in buildings], bbox, budget=2 * MAX_TREES)
            print(f"forest: {100 * (cover == TREE_CLASS).mean():.0f}% tree cover, {trees} trees (every {spacing:.0f} m in settlements, {sparse:.0f} m elsewhere)", flush=True)
        else:
            trees = plant_trees(size_m, spacing, cover, mask, args.out / "ground.jpg", args.out / "trees.bin")
            print(f"forest: {100 * (cover == TREE_CLASS).mean():.0f}% tree cover, {trees} trees", flush=True)

    terrain = write_terrain(bbox, 1025 if big else 769, layers["water"], coast, big, args.out, args.cache)
    if terrain:
        print(f"terrain: {terrain['min_m']} .. {terrain['max_m']} m, sea: {terrain['sea_level_m'] is not None}, cut to water level: {len(terrain['carved_water'])} water bodies")

    for name, features in layers.items():
        write_geojson(args.out / f"{name}.geojson", features)
    props = aoi_feature.get("properties") or {}
    today = datetime.date.today().isoformat()
    meta = {
        "aoi": str(aoi_feature.get("id") or args.aoi.stem),
        "name": args.name or props.get("name") or props.get("nazwa") or args.aoi.stem,
        "kind": "scene3d",
        "built": today,
        "bbox": [w, s, e, n],
        "size_m": [round(size_m[0], 1), round(size_m[1], 1)],
        "ground": {"file": "ground.jpg", **ground},
        "terrain": terrain,
        "trees": {"file": "trees.bin", "count": trees, "spacing_m": round(spacing, 1),
                  "layout": "uint16 x[n], y[n] (0..65535 = west..east, north..south); uint8 height_dm[n], r[n], g[n], b[n], kind[n] (0 round crown, 1 conical)"},
        "buildings_bin": buildings_bin,
        "timeline": build_timeline(args.insar_data, args.out) if args.insar_data else None,
        "layers": {name: {"file": f"{name}.geojson", "count": len(features)} for name, features in layers.items()},
        "only_layers": sorted(wanted) if wanted else None,  # the viewer lists just these
        "pipes_one_class": True if args.pipes_one_class else None,
        "pipe_style": "thin" if args.pipes_thin else None,
        "sources": [s for s in [
            None if args.ground_only else f"OpenStreetMap (ODbL), stan na {today}",
            f"Overture Maps {overture_release} (budynki: OSM + Microsoft ML Buildings)" if overture_release else None,
            f"{ground['source']}{', ' + ground['date'] if ground.get('date') and ground['date'] not in ground['source'] else ''}",
            None if args.ground_only else "ESA WorldCover 10 m 2021 (CC BY 4.0)",
            ("USGS 3DEP (wysokości terenu z lidaru)" if usa else "FABDEM V1-2 (Univ. of Bristol / Fathom, CC BY-NC-SA 4.0): Copernicus DEM GLO-30 bez drzew i budynków"
             if terrain and terrain["source"] == "FABDEM V1-2" else "Copernicus DEM GLO-30 (ESA, model powierzchni)") if terrain else None,
            "OGIM (przebieg rurociągów)" if extras["pipelines"] else None,
            f"Rurociągi i gazociągi: warstwa zespołu ({Path(args.pipelines.split(':')[0]).name}, OGIM / PEMEX)" if args.pipelines else None,
            f"{extras['hazard_source']} (strefa osuwiska)" if extras["hazard_source"] else None,
        ] if s],
    }
    (args.out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print("written to", args.out)
    if big and args.cache and usa and not args.ground_only:  # city scale (USA): close-up photo tiles, infrared trees, real footprints
        import build_scene_detail
        build_scene_detail.run(args.aoi, args.out, args.cache)
    elif big and args.cache and not usa:  # elsewhere: close-up photo tiles at the full 10 m of Sentinel-2
        import build_scene_detail
        build_scene_detail.run_world(args.out, args.cache)


if __name__ == "__main__":
    sys.exit(main())
