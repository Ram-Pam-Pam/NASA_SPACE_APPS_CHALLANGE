"""Open-data objects for the Tabasco 3D model: roads, rail, power lines, industrial areas, villages and towns,
rivers and lagoons, buildings and trees - the layers the model had empty (it had only photo, terrain and the
team's results).  Works on Windows without GDAL (shapely + rasterio + numpy); the same kind of sources as
build_scene3d.py, which needs GDAL (its Overpass queries time out on a 100 km box, so everything comes from
Overture Maps, which carries the OpenStreetMap roads, water and places):

    Overture Maps              buildings, roads, rail, power lines, water, industrial areas, towns (ODbL / CDLA)
    ESA WorldCover 10 m 2021   tree cover (trees are planted from it) and permanent water          (CC BY 4.0)

Download the Overture types once into the cache (about a minute, needs `pip install overturemaps`):
    for t in building segment water infrastructure land_use division:
        overturemaps download --bbox=-93.83,17.7,-92.83,18.51 -f geojsonseq -t $t -o <cache>/overture_$t.geojsonl

Run AFTER build_tabasco_scene.py (it writes pipelines/hazards/timeline...; this script adds the rest and
updates meta.json).  Re-running is safe: raw downloads are cached in --cache, delete it to refresh.

Usage (from backend/, venv with requirements-tools.txt + overturemaps):
    python scripts/build_tabasco_objects.py ..\\Dane\\model3d_tabasco_mexico\\data --cache data\\cache_tabasco

Besides the layers it (1) flattens the terrain model under lagoons and wide rivers - the Copernicus DEM is a
surface model and the radar noise over water showed up as ripples - and (2) stores buildings as oriented
rectangles in buildings.bin (the viewer draws them as instanced boxes).
"""
import argparse
import json
import math
import random
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import rasterio
import shapely
from rasterio import features as rfeatures
from rasterio.transform import from_bounds
from rasterio.windows import Window
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box, mapping, shape
from shapely.ops import polygonize, unary_union

sys.stdout.reconfigure(encoding="utf-8")
UA = "scene3d-builder/1.0"
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter"]
WC_URL = "/vsicurl/https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"
M_LAT = 110_540.0
ROAD_WIDTH_M = {"motorway": 14, "trunk": 12, "primary": 10, "secondary": 8, "tertiary": 7, "residential": 5.5, "unclassified": 5.5}
TREE_CLASSES = (10, 95)           # WorldCover: tree cover, mangroves
WATER_CLASS = 80                  # permanent water bodies (the open sea too)
MIN_LAGOON_HA = 3                 # smaller water bodies are not drawn
TREE_CANDIDATES = 300_000         # grid cells tried for trees (about 60 % pass the photo test)
FOCUS_KM = 14                     # around meta.home everything is kept; farther away only what is big (the viewer draws it all every frame)
SMALL_BUILDING_M2 = 250
FOCUS_TREE_M, COARSE_TREE_M = 34.0, 180.0   # tree spacing inside / outside the focus area
PLACE_KINDS = ("city", "town", "village")
RIVER_WIDTH_M = {"river": 45, "canal": 18}


def http(url, data=None, timeout=420):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


def num(text):
    try:
        return round(float(str(text).replace('"', "").replace("in", "").replace("m", "").strip()), 2)
    except (TypeError, ValueError):
        return None


def feat(geom, props, digits=6):
    def rnd(o):
        return round(o, digits) if isinstance(o, float) else [rnd(i) for i in o]
    g = mapping(geom)
    return {"type": "Feature", "properties": {k: v for k, v in props.items() if v is not None}, "geometry": {"type": g["type"], "coordinates": rnd(g["coordinates"])}}


def write_geojson(path, feats):
    path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


# ------------------------------------------------------------------ Overture Maps (OSM-derived transport, water, places)
def overture_rows(cache, kind):
    path = cache / f"overture_{kind}.geojsonl"
    if not path.exists():
        raise SystemExit(f"missing {path} - download it: overturemaps download --bbox=W,S,E,N -f geojsonseq -t {kind} -o {path}")
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip("\x1e \n")
            if line:
                f = json.loads(line)
                yield shape(f["geometry"]), f["properties"]


def parts(geom, kind):
    gt = "Polygon" if kind == "polygon" else "LineString"
    return [g for g in shapely.get_parts(geom) if g.geom_type == gt and not g.is_empty]


def pname(p):
    return (p.get("names") or {}).get("primary")


def tag(p, key):
    return next((v for k, v in (p.get("source_tags") or []) if k == key), None)


def overture_layers(cache, aoi):
    """roads / rail (segments), power lines (infrastructure), water bodies and rivers, industrial areas, towns and villages."""
    layers = {k: [] for k in ("roads", "rail", "power", "water", "areas", "places")}
    rivers = []  # (line, width, name)
    for g, p in overture_rows(cache, "segment"):
        if p.get("subtype") == "road" and p.get("class") in ROAD_WIDTH_M:
            ref = next((r.get("ref") for r in (p.get("routes") or []) if r.get("ref")), None)
            for part in parts(g.intersection(aoi), "line"):
                layers["roads"].append(feat(part, {"kind": p["class"], "name": pname(p), "ref": ref, "width_m": ROAD_WIDTH_M[p["class"]]}))
        elif p.get("subtype") == "rail":
            for part in parts(g.intersection(aoi), "line"):
                layers["rail"].append(feat(part, {"kind": "abandoned" if "is_abandoned" in str(p.get("rail_flags")) else "rail", "name": pname(p)}))
    for g, p in overture_rows(cache, "infrastructure"):
        if p.get("class") == "power_line":
            volt = str(tag(p, "voltage") or "").split(";")[0].strip()
            for part in parts(g.intersection(aoi), "line"):
                layers["power"].append(feat(part, {"kind": "line", "kv": round(int(volt) / 1000) if volt.isdigit() else None, "name": pname(p)}))
    water = []
    for g, p in overture_rows(cache, "water"):
        cls = p.get("class")
        if cls in ("ocean", "swimming_pool", "fountain", "ditch", "drain", "stream", "wastewater"):
            continue
        if g.geom_type in ("Polygon", "MultiPolygon"):
            for part in parts(g.intersection(aoi), "polygon"):
                water.append((part, {"kind": "reservoir" if cls in ("reservoir", "basin") else "water", "name": pname(p), "source": "Overture Maps"}))
        elif cls in RIVER_WIDTH_M:
            rivers.append((g.intersection(aoi), RIVER_WIDTH_M[cls], pname(p)))
    layers["water"] = water
    for g, p in overture_rows(cache, "land_use"):
        if p.get("class") == "industrial":
            for part in parts(g.intersection(aoi), "polygon"):
                layers["areas"].append(feat(part, {"kind": "industrial", "name": pname(p)}))
    for g, p in overture_rows(cache, "division"):
        if p.get("subtype") == "locality" and p.get("class") in PLACE_KINDS and pname(p) and aoi.contains(g):
            layers["places"].append(feat(g, {"kind": "place", "name": pname(p), "place": p["class"]}))
    return layers, rivers


# ------------------------------------------------------------------ WorldCover
def worldcover(bbox, cache):
    """Land-cover classes over the AOI box on the native grid (1/12000 degree), as (rows, cols) uint8 + its bounds."""
    path = cache / "worldcover.npy"
    w, s, e, n = bbox
    i0, i1 = math.floor((w + 180) * 12000), math.ceil((e + 180) * 12000)
    j0, j1 = math.floor((90 - n) * 12000), math.ceil((90 - s) * 12000)
    if path.exists():
        return np.load(path), (i0, j0)
    arr = np.zeros((j1 - j0, i1 - i0), np.uint8)
    with rasterio.Env(AWS_NO_SIGN_REQUEST="YES", GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="5", GDAL_HTTP_RETRY_DELAY="2"):
        for lat in sorted({math.floor(s / 3) * 3, math.floor(n / 3) * 3}):
            for lon in sorted({math.floor(w / 3) * 3, math.floor(e / 3) * 3}):
                tile = f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}{'E' if lon >= 0 else 'W'}{abs(lon):03d}"
                ti, tj = (lon + 180) * 12000, (90 - (lat + 3)) * 12000  # grid index of the tile's top-left corner
                a0, a1, b0, b1 = max(i0, ti), min(i1, ti + 36000), max(j0, tj), min(j1, tj + 36000)
                if a0 >= a1 or b0 >= b1:
                    continue
                print(f"  WorldCover {tile}: {a1 - a0} x {b1 - b0} px", flush=True)
                with rasterio.open(WC_URL.format(tile=tile)) as src:
                    arr[b0 - j0:b1 - j0, a0 - i0:a1 - i0] = src.read(1, window=Window(a0 - ti, b0 - tj, a1 - a0, b1 - b0))
    np.save(path, arr)
    return arr, (i0, j0)


def crop_to_aoi(arr, origin, bbox):
    w, s, e, n = bbox
    i0, j0 = origin
    c0, c1 = round((w + 180) * 12000) - i0, round((e + 180) * 12000) - i0
    r0, r1 = round((90 - n) * 12000) - j0, round((90 - s) * 12000) - j0
    return arr[r0:r1, c0:c1]


def lagoon_polygons(cover, bbox, aoi, m_lon):
    """Permanent water of WorldCover as simplified polygons; the open sea (big, touching the edge) is left to the sea sheet."""
    w, s, e, n = bbox
    step = 3
    h, wd = (cover.shape[0] // step) * step, (cover.shape[1] // step) * step
    mask = (cover[:h, :wd] == WATER_CLASS).reshape(h // step, step, wd // step, step).max(axis=(1, 3)).astype(np.uint8)
    tr = from_bounds(w, s, e, n, mask.shape[1], mask.shape[0])
    out = []
    for geom, val in rfeatures.shapes(mask, mask=mask.astype(bool), transform=tr, connectivity=8):
        poly = shape(geom).simplify(0.00006)
        if poly.is_empty:
            continue
        area_ha = poly.area * m_lon * M_LAT / 1e4
        touches_edge = poly.bounds[3] > n - 0.002 or poly.bounds[1] < s + 0.002 or poly.bounds[0] < w + 0.002 or poly.bounds[2] > e - 0.002
        if area_ha < MIN_LAGOON_HA or (touches_edge and area_ha > 5000):
            continue
        out.append(poly)
    return out


# ------------------------------------------------------------------ buildings
def building_height(raw, levels, area):
    if isinstance(raw, (int, float)) and raw >= 2.5:
        return float(raw)
    if levels and str(levels).replace(".", "", 1).isdigit():
        return float(levels) * 3.0
    return 3.2 if area < 60 else 4.2 if area < 400 else 6.5 if area < 2000 else 9.0


def buildings_bin(path, bbox, m_lon, out, focus=None):
    """Overture footprints -> oriented rectangles (11 bytes each). Returns the meta entry."""
    w, s, e, n = bbox
    rows = []
    strs, props = [], []

    def flush():
        if not strs:
            return
        g = shapely.from_geojson(strs)
        prts, idx = shapely.get_parts(g, return_index=True)
        keep = np.array([p.geom_type == "Polygon" for p in prts])
        prts, idx = prts[keep], idx[keep]
        o = np.array([w, s])
        gm = shapely.transform(prts, lambda c: (c - o) * np.array([m_lon, M_LAT]))
        rect = shapely.minimum_rotated_rectangle(gm)
        ok = shapely.get_num_coordinates(rect) == 5
        area = shapely.area(gm)
        c = shapely.get_coordinates(rect[ok]).reshape(-1, 5, 2)
        e1, e2 = c[:, 1] - c[:, 0], c[:, 2] - c[:, 1]
        l1, l2 = np.hypot(*e1.T), np.hypot(*e2.T)
        long_edge = np.where((l1 >= l2)[:, None], e1, e2)
        ang = np.mod(np.arctan2(long_edge[:, 1], long_edge[:, 0]), math.pi)
        hl, hw = np.maximum(l1, l2) / 2, np.minimum(l1, l2) / 2
        cx, cy = (c[:, 0, 0] + c[:, 2, 0]) / 2, (c[:, 0, 1] + c[:, 2, 1]) / 2
        a = area[ok]
        fill = a / np.maximum(4 * hl * hw, 0.01)
        shrink = np.sqrt(np.clip(fill, 0.45, 1.0))
        lon, lat = w + cx / m_lon, s + cy / M_LAT
        inside = (lon >= w) & (lon <= e) & (lat >= s) & (lat <= n)
        pr = [props[i] for i in idx[ok]]
        if focus is not None:  # outside the focus area only the bigger buildings (houses in the fields are invisible from far anyway)
            inside &= ((lon >= focus[0]) & (lon <= focus[2]) & (lat >= focus[1]) & (lat <= focus[3])) | (a >= SMALL_BUILDING_M2)
        for k in np.flatnonzero(inside):
            rows.append((lon[k], lat[k], hl[k] * shrink[k], hw[k] * shrink[k], ang[k], building_height(pr[k][0], pr[k][1], a[k]), 0 if a[k] < 450 else 1))
        strs.clear()
        props.clear()

    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip("\x1e \n")
            if not line:
                continue
            f = json.loads(line)
            p = f["properties"]
            strs.append(json.dumps(f["geometry"]))
            props.append((p.get("height"), p.get("num_floors")))
            if len(strs) >= 40_000:
                flush()
                print(f"  buildings: {len(rows)} kept", flush=True)
    flush()
    arr = np.array(rows, dtype=np.float64)
    blocks = [np.round((arr[:, 0] - w) / (e - w) * 65535).astype("<u2"), np.round((n - arr[:, 1]) / (n - s) * 65535).astype("<u2"),
              np.clip(np.round(arr[:, 2] * 10), 5, 65535).astype("<u2"), np.clip(np.round(arr[:, 3] * 10), 5, 65535).astype("<u2"),
              np.round(arr[:, 4] / math.pi * 255).astype("u1"), np.clip(np.round(arr[:, 5] * 2), 4, 255).astype("u1"), arr[:, 6].astype("u1")]
    (out / "buildings.bin").write_bytes(b"".join(b.tobytes() for b in blocks))
    return {"file": "buildings.bin", "count": len(arr),
            "layout": "uint16 x[n], y[n] (0..65535 = west..east, north..south), half_len_dm[n], half_wid_dm[n]; uint8 angle[n] (0..255 = 0..180 deg of the long side, from east, counter-clockwise), height_half_m[n], kind[n] (0 small with a pitched roof, 1 large with a flat roof)"}, arr


# ------------------------------------------------------------------ terrain: flatten water
def flatten_water(out, terrain, water_polys, bbox):
    """The DEM is a surface model: over lagoons it carries radar noise. Each water body goes down to its own low level."""
    w, s, e, n = bbox
    N = terrain["width"]
    span = terrain["max_m"] - terrain["min_m"]
    dem = np.fromfile(out / "terrain.bin", "<u2").reshape(N, N).astype(np.float64) / 65535 * span + terrain["min_m"]
    tr = from_bounds(w, s, e, n, N, N)
    ids = rfeatures.rasterize([(p, i + 1) for i, p in enumerate(water_polys)], out_shape=(N, N), transform=tr, fill=0, all_touched=False, dtype="int32")
    flat = ids.ravel()
    order = np.argsort(flat, kind="stable")
    sorted_ids = flat[order]
    starts = np.searchsorted(sorted_ids, np.arange(1, len(water_polys) + 2))
    d = dem.ravel()
    carved = 0
    for i in range(len(water_polys)):
        sel = order[starts[i]:starts[i + 1]]
        if len(sel) < 4:
            continue
        level = float(np.percentile(d[sel], 10))
        d[sel] = np.minimum(d[sel], level)
        carved += 1
    dem = d.reshape(N, N)
    (out / "terrain.bin").write_bytes(np.round(np.clip((dem - terrain["min_m"]) / span, 0, 1) * 65535).astype("<u2").tobytes())
    return carved


# ------------------------------------------------------------------ trees
def plant_trees(out, bbox, size_m, cover, mask, buildings_arr, focus=None):
    w, s, e, n = bbox
    width_m, height_m = size_m
    with rasterio.open(out / "ground.jpg") as src:
        rgb = src.read()  # (3, H, W) uint8
    gh, gw = rgb.shape[1:]
    trees = np.isin(cover, TREE_CLASSES)
    rng = np.random.default_rng(42)

    def grid(u0, u1, v0, v1, spacing):  # jittered grid over a box given in 0..1 (west -> east, north -> south)
        nx, ny = max(1, int((u1 - u0) * width_m / spacing)), max(1, int((v1 - v0) * height_m / spacing))
        ix, iy = np.meshgrid(np.arange(nx), np.arange(ny))
        return (u0 + (ix + rng.uniform(0.1, 0.9, ix.shape)).ravel() * spacing / width_m, v0 + (iy + rng.uniform(0.1, 0.9, iy.shape)).ravel() * spacing / height_m)

    # a close-up forest needs a tree every ~25 m: that is affordable only around the focus area; farther away sparse trees
    if focus:
        fu0, fu1 = (focus[0] - w) / (e - w), (focus[2] - w) / (e - w)
        fv0, fv1 = (n - focus[3]) / (n - s), (n - focus[1]) / (n - s)
        cu, cv = grid(0, 1, 0, 1, COARSE_TREE_M)
        outside = ~((cu >= fu0) & (cu <= fu1) & (cv >= fv0) & (cv <= fv1))
        fu, fv = grid(fu0, fu1, fv0, fv1, FOCUS_TREE_M)
        u, v = np.concatenate((cu[outside], fu)), np.concatenate((cv[outside], fv))
        spacing = FOCUS_TREE_M
    else:
        spacing = float(np.clip(math.sqrt(trees.mean() * width_m * height_m / TREE_CANDIDATES), 18, 90))
        u, v = grid(0, 1, 0, 1, spacing)
    ci, cj = (u * cover.shape[1]).astype(int), (v * cover.shape[0]).astype(int)
    ok = trees[cj, ci] & ~mask[(v * mask.shape[0]).astype(int), (u * mask.shape[1]).astype(int)]
    u, v = u[ok], v[ok]
    px, py = (u * gw).astype(int), (v * gh).astype(int)
    # canopy colour from the photo: mean of the pixel and four neighbours two pixels away
    acc = np.zeros((len(u), 3), np.float32)
    for dx, dy in ((0, 0), (2, 0), (-2, 0), (0, 2), (0, -2)):
        acc += rgb[:, np.clip(py + dy, 0, gh - 1), np.clip(px + dx, 0, gw - 1)].T
    r, g, b = (acc / 5).T
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    dry = float(np.median(g / np.maximum(r, 1))) < 1.12
    min_gr, min_gb, max_lum = (0.95, 1.02, 135) if dry else (1.04, 1.15, 118)
    good = (g > r * min_gr) & (g > b * min_gb) & (lum > 26) & (lum < max_lum)
    u, v, r, g, b = u[good], v[good], r[good], g[good], b[good]
    if dry:
        r, g, b = 0.45 * r + 0.55 * 62, 0.45 * g + 0.55 * 98, 0.45 * b + 0.55 * 52
    k = rng.uniform(0.9, 1.2, len(u)) if dry else rng.uniform(1.05, 1.4, len(u))
    cols = np.clip(np.column_stack((r, g, b)) * k[:, None], 0, 255).astype("u1")
    heights = rng.triangular(8, 15, 24, len(u))
    kinds = (rng.random(len(u)) < 0.3).astype("u1")
    blocks = [np.round(u * 65535).astype("<u2"), np.round(v * 65535).astype("<u2"), np.round(heights * 10).astype("u1"),
              cols[:, 0].copy(), cols[:, 1].copy(), cols[:, 2].copy(), kinds]
    (out / "trees.bin").write_bytes(b"".join(b_.tobytes() for b_ in blocks))
    return len(u), spacing, trees.mean(), dry


def keep_out(bbox, layers, pipelines, water_polys, rect_arr, px):
    w, s, e, n = bbox
    tr = from_bounds(w, s, e, n, px, px)
    shapes = []
    for name in ("roads", "rail"):
        shapes += [(shape(f["geometry"]), 1) for f in layers[name]]
    shapes += [(shape(f["geometry"]), 1) for f in pipelines]
    shapes += [(p, 1) for p in water_polys]
    shapes += [(shape(f["geometry"]), 1) for f in layers["areas"]]
    mask = rfeatures.rasterize(shapes, out_shape=(px, px), transform=tr, fill=0, all_touched=True, dtype="uint8").astype(bool)
    if len(rect_arr):  # buildings: the pixel of the centre and its neighbours
        cx = np.clip(((rect_arr[:, 0] - w) / (e - w) * px).astype(int), 0, px - 1)
        cy = np.clip(((n - rect_arr[:, 1]) / (n - s) * px).astype(int), 0, px - 1)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                mask[np.clip(cy + dy, 0, px - 1), np.clip(cx + dx, 0, px - 1)] = True
    return mask


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("scene", type=Path, help="Dane/model3d_tabasco_mexico/data")
    ap.add_argument("--cache", type=Path, default=Path("data/cache_tabasco"))
    ap.add_argument("--skip-buildings", action="store_true")
    args = ap.parse_args()
    out, cache = args.scene, args.cache
    cache.mkdir(parents=True, exist_ok=True)
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    bbox = tuple(meta["bbox"])
    w, s, e, n = bbox
    m_lon = 111_320.0 * math.cos(math.radians((s + n) / 2))
    size_m = tuple(meta["size_m"])
    aoi = box(w, s, e, n)
    home = meta.get("home")
    focus = None
    if home:
        dy, dx = FOCUS_KM * 1000 / M_LAT, FOCUS_KM * 1000 / m_lon
        focus = (home["lon"] - dx, home["lat"] - dy, home["lon"] + dx, home["lat"] + dy)

    print("Overture transport / water / places...", flush=True)
    layers, rivers = overture_layers(cache, aoi)
    if focus:  # residential streets only near the focus area
        near = lambda f: focus[0] <= f["geometry"]["coordinates"][0][0] <= focus[2] and focus[1] <= f["geometry"]["coordinates"][0][1] <= focus[3]  # noqa: E731
        layers["roads"] = [f for f in layers["roads"] if f["properties"]["kind"] != "residential" or near(f)]
    print("  Overture:", {k: len(v) for k, v in layers.items()}, "rivers:", len(rivers), flush=True)

    print("WorldCover...", flush=True)
    arr, origin = worldcover(bbox, cache)
    cover = crop_to_aoi(arr, origin, bbox)
    print(f"  cover {cover.shape}, water {100 * (cover == WATER_CLASS).mean():.1f}%, trees {100 * np.isin(cover, TREE_CLASSES).mean():.1f}%", flush=True)

    # water: lagoons from WorldCover (named after OSM where they overlap) + OSM polygons that WorldCover missed + rivers
    lagoons = lagoon_polygons(cover, bbox, aoi, m_lon)
    osm_water = layers["water"]  # (geometry, properties) pairs from Overture
    named = [(g, p) for g, p in osm_water if p.get("name")]
    water_feats, water_polys = [], []
    for poly in lagoons:
        name = next((p["name"] for g, p in named if g.intersects(poly)), None)
        water_polys.append(poly)
        water_feats.append(feat(poly, {"kind": "lagoon", "name": name, "source": "ESA WorldCover 2021"}))
    covered = unary_union(lagoons) if lagoons else None
    for g, p in osm_water:
        if g.area * m_lon * M_LAT / 1e4 < MIN_LAGOON_HA or (covered is not None and g.intersection(covered).area > 0.5 * g.area):
            continue
        water_polys.append(g)
        water_feats.append(feat(g, {"kind": p.get("kind"), "name": p.get("name"), "source": "Overture Maps"}))
    for line, width, name in rivers:  # a river drawn as a band of its width (in local metres), so it is visible from the air
        for part in parts(line, "line"):
            band = shapely.transform(part, lambda c: c * [m_lon, M_LAT]).buffer(width / 2, 3)
            band = shapely.transform(band, lambda c: c / [m_lon, M_LAT]).simplify(0.00003)
            water_feats.append(feat(band, {"kind": "river", "name": name, "source": "Overture Maps"}))
    layers["water"] = water_feats
    print(f"  water: {len(lagoons)} lagoons, {len(water_feats)} features", flush=True)

    # terrain: flat water surfaces
    carved = flatten_water(out, meta["terrain"], water_polys, bbox)
    meta["terrain"]["carved_water"] = [f"{carved} water bodies"]
    print(f"  terrain flattened under {carved} water bodies", flush=True)

    # buildings
    bld_meta, rect_arr = None, np.zeros((0, 7))
    ov = cache / "overture_building.geojsonl"
    if not args.skip_buildings and ov.exists():
        print("buildings...", flush=True)
        bld_meta, rect_arr = buildings_bin(ov, bbox, m_lon, out, focus)
        print(f"  {bld_meta['count']} buildings -> buildings.bin", flush=True)
    elif not args.skip_buildings:
        print("  no overture_building.geojsonl in the cache - run: overturemaps download --bbox=... -f geojsonseq -t building -o <cache>\\overture_building.geojsonl")

    # trees
    print("trees...", flush=True)
    pipelines = json.loads((out / "pipelines.geojson").read_text(encoding="utf-8"))["features"]
    mask = keep_out(bbox, layers, pipelines, water_polys, rect_arr, min(6000, max(2500, round(max(size_m) / 5))))
    count, spacing, tree_share, dry = plant_trees(out, bbox, size_m, cover, mask, rect_arr, focus)
    print(f"  {count} trees (spacing {spacing:.0f} m, {100 * tree_share:.0f}% tree cover, {'dry' if dry else 'lush'} canopy)", flush=True)

    # places: keep the businesses from earlier builds, replace towns / villages / masts
    old = json.loads((out / "places.geojson").read_text(encoding="utf-8"))["features"] if (out / "places.geojson").exists() else []
    layers["places"] = [f for f in old if f["properties"].get("kind") == "business"] + layers["places"]
    for name in ("roads", "rail", "power", "water", "areas", "places"):
        write_geojson(out / f"{name}.geojson", layers[name])
    meta["layers"].update({name: {"file": f"{name}.geojson", "count": len(layers[name])} for name in ("roads", "rail", "power", "water", "areas", "places")})
    meta["trees"] = {"file": "trees.bin", "count": count, "spacing_m": round(spacing, 1),
                     "layout": "uint16 x[n], y[n] (0..65535 = west..east, north..south); uint8 height_dm[n], r[n], g[n], b[n], kind[n] (0 round crown, 1 conical)"}
    meta["buildings_bin"] = bld_meta
    src = [x for x in meta.get("sources", []) if not x.startswith(("OpenStreetMap", "Overture", "ESA WorldCover"))]
    src += ["Overture Maps (drogi, koleje, woda, linie energetyczne, miejscowości: OpenStreetMap; budynki: OSM + Microsoft ML Buildings)", "ESA WorldCover 10 m 2021 (CC BY 4.0)"]
    meta["sources"] = [x for x in src if x]
    meta["built_objects"] = time.strftime("%Y-%m-%d")
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print("meta.json updated", {k: v["count"] for k, v in meta["layers"].items()}, flush=True)


if __name__ == "__main__":
    main()
