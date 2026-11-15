"""Retaining walls, cliffs and steep scarps for a 3D scene package (layer "slopes").

On a landslide these are the key elements: a wall holds a slope, a scarp edge is where the ground breaks
away. Two sources:
    OpenStreetMap (Overpass)  - barrier=retaining_wall and natural=cliff as mapped by people
                                (sparse in most places: OSM rarely maps walls outside cities)
    USGS 3DEP lidar (USA)     - scarp edges found in the bare-earth heights: the upper edge of every
                                slope steeper than STEEP_DEG that is at least MIN_RELIEF_M high and
                                MIN_LENGTH_M long. This covers the whole AOI, mapped or not.

Every line is stored so that the lower side is on its right (the OSM rule for cliffs and walls), so the
viewer can draw the downhill ticks without the terrain.

With --insar-data (the InSAR frontend package of the same AOI) every object also gets the ground motion
along it: points every ~50 m are read from the cumulative displacement frames (img/cum_XX.png), and the
point that moved most gives the series (like the classes of the team's results, which also use the
strongest point of an object). Line-of-sight millimetres, decoded from the RdBu colours (build_timeline.py).

Usage (from the repo root):
    python3 backend/scripts/build_slopes.py <scene data folder> [--insar-data <InSAR data folder>] [--cache DIR]
Writes <scene>/slopes.geojson and adds "slopes" to meta.json (layers + sources).
"""
import argparse
import json
import math
import sys
import time
import urllib.parse
from pathlib import Path

import numpy as np
from osgeo import gdal, ogr, osr

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_scene3d import DEM_URL, OVERPASS_URLS, WGS84, http_get, in_usa, line_geom, simple_parts  # noqa: E402
from build_timeline import decode, mercator_y, rdbu_lut  # noqa: E402

gdal.UseExceptions()
ogr.UseExceptions()

RES_M = 5.0            # lidar cell size used for the scarps
TILE_PX = 2000         # lidar is fetched in tiles of this size (the service allows 8000)
STEEP_DEG = 35.0       # a slope steeper than this is a scarp / cliff face
MIN_RELIEF_M = 12.0    # ... at least this high
MIN_LENGTH_M = 60.0    # ... with an upper edge at least this long
SAMPLE_M = 50.0        # spacing of the ground-motion samples along an object


def utm_for(lon: float, lat: float) -> osr.SpatialReference:
    srs = osr.SpatialReference()
    srs.ImportFromEPSG((32600 if lat >= 0 else 32700) + int((lon + 180) // 6) + 1)
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return srs


# ------------------------------------------------------------------ OSM
def fetch_osm(bbox, cache: Path | None) -> list[dict]:
    w, s, e, n = bbox
    cached = cache / "osm_slopes.json" if cache else None
    if cached and cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))["elements"]
    box = f"({s},{w},{n},{e})"
    query = f'[out:json][timeout:180];(way[barrier=retaining_wall]{box};way[natural=cliff]{box};);out body geom;'
    body = urllib.parse.urlencode({"data": query}).encode()
    last = None
    for url in OVERPASS_URLS * 3:
        try:
            raw = http_get(url, data=body, timeout=240, tries=1)
            elements = json.loads(raw)["elements"]
            if cached:
                cached.write_bytes(raw)
            return elements
        except Exception as err:  # noqa: BLE001
            last = err
            time.sleep(8)
    raise RuntimeError(f"Overpass failed: {last}")


def osm_features(elements, clip: ogr.Geometry) -> list[tuple[ogr.Geometry, dict]]:
    out = []
    for el in elements:
        tags = el.get("tags") or {}
        coords = [(p["lon"], p["lat"]) for p in el.get("geometry") or [] if p]
        if el["type"] != "way" or len(coords) < 2:
            continue
        kind = "retaining_wall" if tags.get("barrier") == "retaining_wall" else "cliff"
        props = {"kind": kind, "name": tags.get("name"), "material": tags.get("material"), "height_m": tags.get("height"),
                 "historic": tags.get("historic"), "source": "OpenStreetMap"}
        for part in simple_parts(line_geom(coords).Intersection(clip), "line"):
            out.append((part, props))
    return out


# ------------------------------------------------------------------ lidar
def fetch_dem(bbox_utm, srs: osr.SpatialReference, cache: Path | None) -> tuple[np.ndarray, tuple]:
    """Bare-earth heights on a RES_M grid in UTM, rows north -> south; NaN without data (e.g. the sea)."""
    x0, y0, x1, y1 = bbox_utm
    cols, rows = math.ceil((x1 - x0) / RES_M), math.ceil((y1 - y0) / RES_M)
    dem = np.full((rows, cols), np.nan, dtype=np.float32)
    epsg = srs.GetAuthorityCode(None)
    for r0 in range(0, rows, TILE_PX):
        for c0 in range(0, cols, TILE_PX):
            tw, th = min(TILE_PX, cols - c0), min(TILE_PX, rows - r0)
            tx0, ty1 = x0 + c0 * RES_M, y1 - r0 * RES_M
            box = (tx0, ty1 - th * RES_M, tx0 + tw * RES_M, ty1)
            cached = cache / f"dem{int(RES_M)}m_{epsg}_{round(box[0])}_{round(box[1])}_{tw}x{th}.tif" if cache else None
            data = cached.read_bytes() if cached and cached.exists() else b""
            params = {"bbox": ",".join(f"{v:.2f}" for v in box), "bboxSR": epsg, "imageSR": epsg, "size": f"{tw},{th}", "format": "tiff",
                      "pixelType": "F32", "interpolation": "RSP_BilinearInterpolation", "f": "image"}
            for _ in range(6):
                if data[:2] in (b"II", b"MM"):
                    break
                try:
                    data = http_get(f"{DEM_URL}/exportImage?{urllib.parse.urlencode(params)}", timeout=240, tries=1)
                except RuntimeError:
                    data = b""
                    time.sleep(5)
            else:
                print(f"  lidar tile {r0},{c0}: service unavailable - skipped")
                continue
            if cached:
                cached.write_bytes(data)
            name = f"/vsimem/dem_{r0}_{c0}.tif"
            gdal.FileFromMemBuffer(name, data)
            src = gdal.Open(name)
            warped = gdal.Warp("", src, format="MEM", outputBounds=box, width=tw, height=th, dstSRS=srs.ExportToWkt(),
                               resampleAlg="bilinear", dstNodata=float("nan"))
            dem[r0:r0 + th, c0:c0 + tw] = warped.ReadAsArray()
            src = warped = None
            gdal.Unlink(name)
            print(f"  lidar tile {r0 // TILE_PX},{c0 // TILE_PX} ok", flush=True)
    dem[~np.isfinite(dem) | (dem < -50) | (dem > 9000)] = np.nan
    return dem, (x0, y1)


def scarp_edges(dem: np.ndarray, origin, srs: osr.SpatialReference) -> list[tuple[ogr.Geometry, dict]]:
    """Upper edges of the steep slopes, as lines in WGS84 with the lower side on the right."""
    x0, y1 = origin
    land = np.isfinite(dem) & (dem > 0.5)
    z = np.where(np.isfinite(dem), dem, 0.0)
    gy, gx = np.gradient(z, RES_M)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    steep = (land & (slope >= STEEP_DEG)).astype(np.uint8)

    mem = gdal.GetDriverByName("MEM").Create("", dem.shape[1], dem.shape[0], 1, gdal.GDT_Byte)
    mem.SetGeoTransform((x0, RES_M, 0, y1, 0, -RES_M))
    mem.SetProjection(srs.ExportToWkt())
    band = mem.GetRasterBand(1)
    band.WriteArray(steep)
    gdal.SieveFilter(band, None, band, 20, 8)  # drop specks smaller than 20 cells (500 m2)
    vec = ogr.GetDriverByName("MEM").CreateDataSource("")
    layer = vec.CreateLayer("steep", srs=srs, geom_type=ogr.wkbPolygon)
    layer.CreateField(ogr.FieldDefn("v", ogr.OFTInteger))
    gdal.Polygonize(band, band, layer, 0, ["8CONNECTED=8"])

    def height(x, y):
        c, r = int((x - x0) / RES_M), int((y1 - y) / RES_M)
        return float(z[r, c]) if 0 <= r < z.shape[0] and 0 <= c < z.shape[1] else float("nan")

    to_wgs = osr.CoordinateTransformation(srs, WGS84)
    out = []
    for f in layer:
        poly = f.GetGeometryRef()
        if poly.GetArea() < 1500:
            continue
        simple = poly.SimplifyPreserveTopology(RES_M * 0.8)
        if simple is None or simple.IsEmpty() or simple.GetGeometryType() != ogr.wkbPolygon:
            continue
        ring = simple.GetGeometryRef(0).Clone()
        ring.Segmentize(RES_M)
        pts = ring.GetPoints()
        hs = np.array([height(x, y) for x, y, *_ in pts])
        lo, hi = np.nanpercentile(hs, 3), np.nanpercentile(hs, 97)
        relief = hi - lo
        if not np.isfinite(relief) or relief < MIN_RELIEF_M:
            continue
        top = hs >= lo + 0.65 * relief
        runs, cur = [], []
        for p, t in zip(pts, top):
            if t:
                cur.append(p[:2])
            elif cur:
                runs.append(cur)
                cur = []
        if cur:
            if runs and top[0]:
                runs[0] = cur + runs[0]  # the ring wraps around
            else:
                runs.append(cur)
        rr, cc = _cells(poly, x0, y1, dem.shape)
        face = slope[rr, cc][steep[rr, cc] > 0]
        steep_deg = float(np.median(face)) if face.size else STEEP_DEG
        for run in runs:
            line = ogr.Geometry(ogr.wkbLineString)
            for x, y in run:
                line.AddPoint_2D(x, y)
            if line.Length() < MIN_LENGTH_M:
                continue
            line = _chaikin(line.SimplifyPreserveTopology(RES_M * 1.2))  # no pixel staircase
            if not _lower_on_right(line, height):
                line = _reversed(line)
            line.Transform(to_wgs)
            out.append((line, {"kind": "scarp", "relief_m": round(float(relief)), "slope_deg": round(steep_deg), "source": "USGS 3DEP lidar"}))
    return out


def _cells(poly: ogr.Geometry, x0, y1, shape):
    """Raster indices of the cells in a polygon's envelope (with the steep mask that is close enough)."""
    minx, maxx, miny, maxy = poly.GetEnvelope()
    c0, c1 = max(0, int((minx - x0) / RES_M)), min(shape[1] - 1, int((maxx - x0) / RES_M))
    r0, r1 = max(0, int((y1 - maxy) / RES_M)), min(shape[0] - 1, int((y1 - miny) / RES_M))
    rr, cc = np.mgrid[r0:r1 + 1, c0:c1 + 1]
    return rr.ravel(), cc.ravel()


def _chaikin(line: ogr.Geometry, iterations: int = 2) -> ogr.Geometry:
    """Corner cutting on an open line (the ends stay where they are)."""
    pts = [p[:2] for p in line.GetPoints()]
    for _ in range(iterations):
        if len(pts) < 3:
            break
        out = [pts[0]]
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            out += [(0.75 * ax + 0.25 * bx, 0.75 * ay + 0.25 * by), (0.25 * ax + 0.75 * bx, 0.25 * ay + 0.75 * by)]
        pts = out + [pts[-1]]
    smooth = ogr.Geometry(ogr.wkbLineString)
    for x, y in pts:
        smooth.AddPoint_2D(x, y)
    return smooth


def _lower_on_right(line: ogr.Geometry, height) -> bool:
    pts = line.GetPoints()
    score = 0.0
    for (ax, ay, *_), (bx, by, *_) in zip(pts, pts[1:]):
        dx, dy = bx - ax, by - ay
        n = math.hypot(dx, dy) or 1
        mx, my, rx, ry = (ax + bx) / 2, (ay + by) / 2, dy / n * 12, -dx / n * 12  # 12 m to the right / left
        r, l = height(mx + rx, my + ry), height(mx - rx, my - ry)
        if np.isfinite(r) and np.isfinite(l):
            score += l - r
    return score >= 0


def _reversed(line: ogr.Geometry) -> ogr.Geometry:
    out = ogr.Geometry(ogr.wkbLineString)
    for x, y, *_ in reversed(line.GetPoints()):
        out.AddPoint_2D(x, y)
    return out


# ------------------------------------------------------------------ ground motion
class Motion:
    """Cumulative LOS displacement (mm) per date from the InSAR package's frames, read at lon/lat."""

    def __init__(self, insar_dir: Path):
        meta = json.loads((insar_dir / "meta.json").read_text(encoding="utf-8"))
        (self.south, self.west), (self.north, self.east) = meta["overlay_bounds"]
        self.dates = meta["dates"]
        lut, values = rdbu_lut(float(meta.get("scale_mm") or 150))
        self.frames = np.stack([decode(insar_dir / "img" / f"cum_{e:02d}.png", lut, values) for e in range(len(self.dates))])
        self.h, self.w = self.frames.shape[1:]
        self.my0, self.my1 = mercator_y(self.north), mercator_y(self.south)

    def series(self, lon, lat):
        c = int((lon - self.west) / (self.east - self.west) * self.w)
        r = int((self.my0 - mercator_y(lat)) / (self.my0 - self.my1) * self.h)  # frame rows are Web Mercator
        if not (0 <= r < self.h and 0 <= c < self.w):
            return None
        s = self.frames[:, r, c]
        return s if np.isfinite(s).sum() >= len(s) - 2 else None

    def along(self, geom: ogr.Geometry) -> dict | None:
        """The strongest point of the object (largest change on the last date) and its series."""
        pts = geom.GetPoints()
        lat0 = pts[0][1]
        k = 111_320 * math.cos(math.radians(lat0))
        samples = [pts[0][:2]]
        for (ax, ay, *_), (bx, by, *_) in zip(pts, pts[1:]):
            d = math.hypot((bx - ax) * k, (by - ay) * 110_540)
            for i in range(1, max(1, int(d // SAMPLE_M)) + 1):
                f = i / max(1, int(d // SAMPLE_M))
                samples.append((ax + (bx - ax) * f, ay + (by - ay) * f))
        best, best_abs, n_ok = None, -1.0, 0
        for lon, lat in samples:
            s = self.series(lon, lat)
            if s is None:
                continue
            n_ok += 1
            last = float(s[np.isfinite(s)][-1])
            if abs(last) > best_abs:
                best, best_abs = s, abs(last)
        if best is None:
            return None
        return {"motion_mm": round(float(best[np.isfinite(best)][-1])), "series_mm": [None if not np.isfinite(v) else round(float(v)) for v in best],
                "samples": n_ok}


# ------------------------------------------------------------------ main
def length_m(geom: ogr.Geometry) -> float:
    pts = geom.GetPoints()
    k = 111_320 * math.cos(math.radians(pts[0][1]))
    return sum(math.hypot((b[0] - a[0]) * k, (b[1] - a[1]) * 110_540) for a, b in zip(pts, pts[1:]))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("scene", type=Path)
    ap.add_argument("--insar-data", type=Path)
    ap.add_argument("--cache", type=Path)
    ap.add_argument("--no-lidar", action="store_true", help="only the OSM walls and cliffs")
    args = ap.parse_args()
    meta_path = args.scene / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    w, s, e, n = meta["bbox"]
    clip = ogr.CreateGeometryFromWkt(f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))")
    if args.cache:
        args.cache.mkdir(parents=True, exist_ok=True)

    found = osm_features(fetch_osm((w, s, e, n), args.cache), clip)
    print(f"OSM: {sum(p['kind'] == 'retaining_wall' for _, p in found)} retaining walls, {sum(p['kind'] == 'cliff' for _, p in found)} cliffs", flush=True)
    lidar = False
    if in_usa((w, s, e, n)) and not args.no_lidar:
        srs = utm_for((w + e) / 2, (s + n) / 2)
        to_utm = osr.CoordinateTransformation(WGS84, srs)
        corners = [to_utm.TransformPoint(x, y)[:2] for x in (w, e) for y in (s, n)]
        xs, ys = [c[0] for c in corners], [c[1] for c in corners]
        print(f"lidar {RES_M:.0f} m ...", flush=True)
        dem, origin = fetch_dem((min(xs), min(ys), max(xs), max(ys)), srs, args.cache)
        scarps = [(g, p) for g, p in scarp_edges(dem, origin, srs) if clip.Intersects(g)]
        print(f"lidar: {len(scarps)} scarp edges (>= {STEEP_DEG:.0f} deg, >= {MIN_RELIEF_M:.0f} m high, >= {MIN_LENGTH_M:.0f} m long)", flush=True)
        found += scarps
        lidar = True

    motion = Motion(args.insar_data) if args.insar_data else None
    features = []
    for geom, props in found:
        props = {k: v for k, v in props.items() if v is not None}
        props["length_m"] = round(length_m(geom))
        if motion:
            m = motion.along(geom)
            if m:
                props.update(m)
        features.append({"type": "Feature", "geometry": json.loads(geom.ExportToJson()), "properties": props})
    for f in features:  # 6 decimals (~10 cm) is plenty
        f["geometry"]["coordinates"] = [[round(x, 6), round(y, 6)] for x, y, *_ in f["geometry"]["coordinates"]]
    out = args.scene / "slopes.geojson"
    out.write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    meta.setdefault("layers", {})["slopes"] = {"file": "slopes.geojson", "count": len(features),
                                               "note": "Lines with the lower side on the right. kind: retaining_wall | cliff (OSM) | scarp (lidar)."
                                                       + (" motion_mm / series_mm: LOS ground motion at the strongest point, dates of the timeline." if motion else "")}
    srcs = [x for x in meta.get("sources", []) if not x.startswith("Mury oporowe") and not x.startswith("Skarpy")]
    srcs.append("Mury oporowe i klify: OpenStreetMap (barrier=retaining_wall, natural=cliff)")
    if lidar:
        srcs.append(f"Skarpy: USGS 3DEP lidar {RES_M:.0f} m (krawędź zboczy > {STEEP_DEG:.0f}°, wys. ≥ {MIN_RELIEF_M:.0f} m)")
    meta["sources"] = srcs
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    moved = sum(1 for f in features if abs(f["properties"].get("motion_mm", 0)) >= 20)
    print(f"written {out} ({len(features)} objects, {moved} with >= 20 mm ground motion)")


if __name__ == "__main__":
    main()
