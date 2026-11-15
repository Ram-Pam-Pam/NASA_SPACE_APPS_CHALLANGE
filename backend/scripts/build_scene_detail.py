"""Close-up detail for a large (city scale) 3D scene package made by build_scene3d.py.

1. Detail photo tiles: the land part of the AOI as tiles of 64 x 64 terrain cells with an aerial photo
   of about 1.2 m per pixel (the whole-AOI ground.jpg of a 30 km scene has about 3.7 m per pixel).
   The viewer loads the tiles near the camera only.
2. Trees from the near-infrared band of the same photo: vegetation (NDVI) that is dark or rough
   (crowns cast shadows, lawns are smooth and bright) and not on a building, road or water. This finds
   the street and garden trees that a land-cover map counts as "built-up".
3. Building footprints: the real outline of every building (simplified to ~0.4 m), its height and the
   roof colour taken from the photo, in a compact binary file.

Usage (from the repo root; --cache = the same folder as for build_scene3d.py, it holds the downloads):
    python3 backend/scripts/build_scene_detail.py <aoi.geojson> <Dane/model3d_<id>/data> --cache DIR
build_scene3d.py runs this by itself for AOIs larger than BIG_AOI_KM.
"""
import argparse
import json
import math
import random
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from osgeo import gdal, ogr

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_scene3d as base  # noqa: E402 - sibling script

gdal.UseExceptions()
ogr.UseExceptions()

TILE_CELLS = 64             # detail tile = this many terrain cells per side
TILE_PX = 1536              # photo pixels across a tile (~1.2 m for 1.9 km tiles)
NIR_FACTOR = 2              # the infrared band is fetched at half the photo resolution
TREE_SPACING_M = 10.0       # one tree per jittered grid cell of canopy
MAX_TREES = 320_000
SIMPLIFY_M = 0.4            # footprint simplification
MAX_POINTS = 48             # footprints with more corners are simplified harder


def tile_bounds(meta, r, c, n_tiles):
    w, s, e, n = meta["bbox"]
    dx, dy = (e - w) / n_tiles, (n - s) / n_tiles
    return (w + c * dx, n - (r + 1) * dy, w + (c + 1) * dx, n - r * dy)


def terrain_grid(meta, data: Path) -> np.ndarray:
    t = meta["terrain"]
    raw = np.frombuffer((data / t["file"]).read_bytes(), dtype="<u2").reshape(t["height"], t["width"])
    return raw.astype(np.float32) / 65535 * (t["max_m"] - t["min_m"]) + t["min_m"]


def land_tiles(meta, dem: np.ndarray) -> list[tuple[int, int]]:
    cells = dem.shape[0] - 1
    n_tiles = cells // TILE_CELLS
    sea = meta["terrain"].get("sea_level_m")
    level = (sea if sea is not None else dem.min()) + 1.0
    out = []
    for r in range(n_tiles):
        for c in range(n_tiles):
            block = dem[r * TILE_CELLS:(r + 1) * TILE_CELLS + 1, c * TILE_CELLS:(c + 1) * TILE_CELLS + 1]
            if (block > level).mean() > 0.02:
                out.append((r, c))
    return out


def canopy_mask(rgb: np.ndarray, nir: np.ndarray) -> np.ndarray:
    """Tree crowns: green in the near infrared, and dark (shade) or rough (crown texture)."""
    r, g, b = (band.astype(np.float32) for band in rgb)
    n = nir.astype(np.float32)
    ndvi = (n - r) / (n + r + 1e-6)
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    pad = np.pad(lum, 1, mode="edge")
    windows = np.stack([pad[dy:dy + lum.shape[0], dx:dx + lum.shape[1]] for dy in range(3) for dx in range(3)])
    rough = windows.std(axis=0)
    return (ndvi > 0.2) & ((lum < 85) | ((rough > 12) & (lum < 125)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("aoi", type=Path)
    parser.add_argument("data", type=Path)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    run(args.aoi, args.data, args.cache)


def run(aoi_path: Path, data: Path, cache: Path) -> None:
    meta = json.loads((data / "meta.json").read_text(encoding="utf-8"))
    w, s, e, n = meta["bbox"]
    sx, sz = meta["size_m"]
    m_per_deg_lon = 111_320.0 * math.cos(math.radians((s + n) / 2))
    dem = terrain_grid(meta, data)
    n_tiles = (dem.shape[0] - 1) // TILE_CELLS
    tiles = land_tiles(meta, dem)
    tile_dir = data / "ground_tiles"
    tile_dir.mkdir(exist_ok=True)
    print(f"detail: {len(tiles)} land tiles of {sx / n_tiles:.0f} m", flush=True)

    # ---- 1. photo tiles (and the infrared band for the trees)
    canopy, tile_rgb_small = {}, {}

    def one_tile(rc):
        r, c = rc
        tb = tile_bounds(meta, r, c, n_tiles)
        aspect = (tb[3] - tb[1]) / (tb[2] - tb[0])  # square pixels in degrees
        size = (TILE_PX, round(TILE_PX * aspect))
        rgb = base.fetch_mosaic(tb, size, cache, tag="detail", workers=4)
        base.save_jpeg(rgb, tile_dir / f"{r}_{c}.jpg", 80)
        small = (size[0] // NIR_FACTOR, size[1] // NIR_FACTOR)
        nir = base.fetch_mosaic(tb, small, cache, bands="3", tag="nir", workers=1)[0]
        rgb_small = rgb[:, :small[1] * NIR_FACTOR, :small[0] * NIR_FACTOR].reshape(3, small[1], NIR_FACTOR, small[0], NIR_FACTOR).mean(axis=(2, 4))
        return rc, canopy_mask(rgb_small, nir), rgb_small.astype(np.uint8)

    # the image service is slow (~30 s a request) but takes many at once: several tiles in parallel
    with ThreadPoolExecutor(max_workers=5) as pool:
        for i, (rc, mask, small_rgb) in enumerate(pool.map(one_tile, tiles)):
            canopy[rc], tile_rgb_small[rc] = mask, small_rgb
            if (i + 1) % 10 == 0 or i + 1 == len(tiles):
                print(f"  tiles {i + 1}/{len(tiles)}", flush=True)
    meta["ground"]["tiles"] = {"dir": "ground_tiles", "grid": n_tiles, "cells": TILE_CELLS, "list": [[r, c] for r, c in tiles],
                               "resolution_m": round(sx / n_tiles / TILE_PX, 2),
                               "layout": "<dir>/<row>_<col>.jpg; row 0 = north; tile (r, c) covers terrain cells r*cells..(r+1)*cells x c*cells..(c+1)*cells"}

    # ---- 2. building footprints with roof colours
    with tempfile.TemporaryDirectory() as tmp:
        buildings = base.overture_buildings("", tuple(meta["bbox"]), Path(tmp), cache)
    print(f"footprints: {len(buildings)} buildings", flush=True)
    tol = SIMPLIFY_M / m_per_deg_lon
    recs, wkbs = [], []
    by_tile: dict[tuple[int, int], list[int]] = {}
    for wkb, props in buildings:
        geom = ogr.CreateGeometryFromWkb(wkb)
        wkbs.append(wkb)
        simple = geom.SimplifyPreserveTopology(tol)
        if simple is None or simple.IsEmpty() or ogr.GT_Flatten(simple.GetGeometryType()) != ogr.wkbPolygon:
            simple = geom
        ring = simple.GetGeometryRef(0)
        pts = ring.GetPoints()[:-1]
        k = 2
        while len(pts) > MAX_POINTS and k < 64:
            ring = geom.SimplifyPreserveTopology(tol * k).GetGeometryRef(0)
            pts = ring.GetPoints()[:-1]
            k *= 2
        if len(pts) < 3:
            continue
        area = geom.GetArea() * base.M_PER_DEG_LAT * m_per_deg_lon
        height, _ = base.building_height(props.get("height"), props.get("levels"), area)
        xy = np.array(pts)[:, :2]
        local = np.column_stack(((xy[:, 0] - xy[0, 0]) * m_per_deg_lon, (xy[:, 1] - xy[0, 1]) * base.M_PER_DEG_LAT))
        _, _, hl, hw, _ = base.min_area_rect(np.vstack([local, local[:1]]))
        pitched = area < 450 and area / max(4 * hl * hw, 0.01) > 0.6
        cx, cy = xy.mean(axis=0)
        r = min(n_tiles - 1, max(0, int((n - cy) / (n - s) * n_tiles)))
        c = min(n_tiles - 1, max(0, int((cx - w) / (e - w) * n_tiles)))
        by_tile.setdefault((r, c), []).append(len(recs))
        recs.append({"xy": xy, "h": height, "pitched": pitched, "rgb": None})

    # roof colour: median of a few points inside the footprint, read from the detail tile it lies in
    ground = gdal.Open(str(data / meta["ground"]["file"])).ReadAsArray()
    gh, gw = ground.shape[1:]
    for key, idx in by_tile.items():
        tile_file = tile_dir / f"{key[0]}_{key[1]}.jpg"
        arr = gdal.Open(str(tile_file)).ReadAsArray() if tile_file.exists() else None
        tb = tile_bounds(meta, key[0], key[1], n_tiles)
        for i in idx:
            xy = recs[i]["xy"]
            c0 = xy.mean(axis=0)
            probes = [c0] + [c0 + 0.45 * (xy[j] - c0) for j in range(0, len(xy), max(1, len(xy) // 4))][:4]
            samples = []
            for lon, lat in probes:
                if arr is not None:
                    th, tw = arr.shape[1:]
                    px, py = int((lon - tb[0]) / (tb[2] - tb[0]) * tw), int((tb[3] - lat) / (tb[3] - tb[1]) * th)
                    if 1 <= px < tw - 1 and 1 <= py < th - 1:
                        samples.append(arr[:, py - 1:py + 2, px - 1:px + 2].reshape(3, -1).mean(axis=1))
                        continue
                px, py = int((lon - w) / (e - w) * gw), int((n - lat) / (n - s) * gh)
                if 0 <= px < gw and 0 <= py < gh:
                    samples.append(ground[:, py, px].astype(np.float32))
            recs[i]["rgb"] = np.median(np.array(samples), axis=0) if samples else np.array([200, 196, 188])

    blob = bytearray()
    for rec in recs:
        xy = rec["xy"]
        qx = np.clip(np.round((xy[:, 0] - w) / (e - w) * 65535), 0, 65535).astype("<u2")
        qy = np.clip(np.round((n - xy[:, 1]) / (n - s) * 65535), 0, 65535).astype("<u2")
        rgb = np.clip(rec["rgb"], 0, 255).astype("u1")
        blob += bytes([len(xy), min(255, round(rec["h"] * 2)), 1 if rec["pitched"] else 0, *rgb.tolist()])
        blob += np.column_stack([qx, qy]).astype("<u2").tobytes()
    (data / "buildings_fp.bin").write_bytes(bytes(blob))
    meta["buildings_fp"] = {"file": "buildings_fp.bin", "count": len(recs),
                            "layout": "per building: uint8 points, height_half_m, flags (1 = small, pitched roof), roof r, g, b; then points x uint16 x, y (little endian; 0..65535 = west..east, north..south)"}
    old = meta.pop("buildings_bin", None)
    if old and (data / old["file"]).exists():
        (data / old["file"]).unlink()
    print(f"footprints: {len(recs)} written, {len(blob) / 1e6:.1f} MB", flush=True)

    # ---- 3. trees from the infrared
    layers = {name: json.loads((data / f"{name}.geojson").read_text(encoding="utf-8"))["features"]
              for name in ("roads", "rail", "water", "pipelines") if (data / f"{name}.geojson").exists()}
    keep_px = 12_000
    keep = base.keep_out_mask(tuple(meta["bbox"]), m_per_deg_lon, layers, wkbs, keep_px, 1.0)
    sea = meta["terrain"].get("sea_level_m")
    land_level = (sea - meta["terrain"]["min_m"] if sea is not None else 0) + 0.6 + meta["terrain"]["min_m"]
    total_canopy = sum(float(mask.mean()) * (sx / n_tiles) * (sz / n_tiles) for mask in canopy.values())
    spacing = max(TREE_SPACING_M, math.sqrt(total_canopy / MAX_TREES))
    rng = random.Random(42)
    xs, ys, hs, cols, kinds = [], [], [], [], []
    for (r, c), mask in canopy.items():
        tb = tile_bounds(meta, r, c, n_tiles)
        mh, mw = mask.shape
        rgb_small = tile_rgb_small[(r, c)]
        tw_m, th_m = sx / n_tiles, sz / n_tiles
        for iy in range(int(th_m / spacing)):
            for ix in range(int(tw_m / spacing)):
                u = (ix + rng.uniform(0.1, 0.9)) * spacing / tw_m
                v = (iy + rng.uniform(0.1, 0.9)) * spacing / th_m
                px, py = int(u * mw), int(v * mh)
                if not mask[py, px]:
                    continue
                lon, lat = tb[0] + u * (tb[2] - tb[0]), tb[3] - v * (tb[3] - tb[1])
                gu, gv = (lon - w) / (e - w), (n - lat) / (n - s)
                if keep[min(keep_px - 1, int(gv * keep_px)), min(keep_px - 1, int(gu * keep_px))]:
                    continue
                gi = min(dem.shape[0] - 1, int(gv * (dem.shape[0] - 1) + 0.5)), min(dem.shape[1] - 1, int(gu * (dem.shape[1] - 1) + 0.5))
                if dem[gi] <= land_level:
                    continue  # kelp and wet sand look like canopy in the photo
                patch = rgb_small[:, max(0, py - 1):py + 2, max(0, px - 1):px + 2].reshape(3, -1).mean(axis=1)
                k = rng.uniform(1.0, 1.3)
                # crowns: the photo colour pulled a little towards a leaf green, so dry-season crowns stay readable
                col = [min(255, (0.6 * patch[0] + 0.4 * 70) * k), min(255, (0.6 * patch[1] + 0.4 * 104) * k), min(255, (0.6 * patch[2] + 0.4 * 58) * k)]
                xs.append(gu); ys.append(gv); cols.append(col)
                hs.append(rng.triangular(6, 18, 10))
                kinds.append(1 if rng.random() < 0.18 else 0)
    count = len(xs)
    cols_arr = np.array(cols, dtype=np.float32).reshape(count, 3).astype("u1")
    blocks = [np.round(np.array(xs) * 65535).astype("<u2"), np.round(np.array(ys) * 65535).astype("<u2"),
              np.round(np.array(hs) * 10).astype("u1"), cols_arr[:, 0].copy(), cols_arr[:, 1].copy(), cols_arr[:, 2].copy(), np.array(kinds, dtype="u1")]
    (data / "trees.bin").write_bytes(b"".join(b.tobytes() for b in blocks))
    meta["trees"].update({"count": count, "spacing_m": round(spacing, 1), "source": "NAIP near infrared (NDVI) + texture"})
    print(f"trees: {count} (spacing {spacing:.1f} m)", flush=True)

    src = "USGS NAIP Plus: kafle szczegółowe ~1,2 m i drzewa z podczerwieni (NDVI)"
    if src not in meta["sources"]:
        meta["sources"].append(src)
    (data / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print("detail written to", data)


if __name__ == "__main__":
    main()
