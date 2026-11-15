"""Extract per-pair quality layers and LOS geometry from the NISAR L2 GUNW subsets.

Input: the folder with the GUNW subset GeoTIFFs for Portuguese Bend (from gunw_aoi.zip):
  013D_071_<ref>_<sec>.tif, 13 bands (unwrapped_phase_rad, coherence, connected_components, ...,
  incidence_angle_deg, los_east, los_north, water, valid, in_aoi), plus index.json and manifest.json.

Output: public/gunw/gunw.json, sampled at the centres of the 100 m time-series grid (meta.focus,
EPSG:3857), nearest 80 m GUNW pixel, one value per consecutive pair of meta.dates:
  coh   uint8   coherence * 200 (0..200), 255 = invalid
  cc    uint8   connected component label (0 = not unwrapped / unreliable), 255 = invalid
  inc   int16le per-pair LOS displacement, 0.1 mm, = -unwrapped_phase * wavelength / (4 pi);
                 NOT re-referenced (absolute level arbitrary, differences between pixels are valid)
plus LOS geometry over the DEM extent, product identifiers and status (collection name).

Usage: python scripts/prep_gunw.py ../gunw_pb/gunw_aoi/portuguese_bend_california
"""
import base64
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer

ROOT = Path(__file__).resolve().parent.parent
src = Path(sys.argv[1] if len(sys.argv) > 1 else "../gunw_pb/gunw_aoi/portuguese_bend_california")
meta = json.loads((ROOT / "public/data/meta.json").read_text(encoding="utf-8"))
dem = json.loads((ROOT / "public/dem/dem.json").read_text())
index = json.loads((src / "index.json").read_text(encoding="utf-8"))
manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))

F = meta["focus"]
H, W = F["h"], F["w"]
dates = meta["dates"]
pairs = list(zip(dates[:-1], dates[1:]))

# centres of the 100 m grid -> UTM 11N
R = 6378137.0
cols, rows = np.meshgrid(np.arange(W), np.arange(H))
mx = F["x0"] + (cols + 0.5) * F["res"]
my = F["y1"] - (rows + 0.5) * F["res"]
lon = np.degrees(mx / R)
lat = np.degrees(2 * np.arctan(np.exp(my / R)) - np.pi / 2)
to_utm = Transformer.from_crs(4326, 32611, always_xy=True)
ux, uy = to_utm.transform(lon, lat)

coh = np.full((H, W, len(pairs)), 255, np.uint8)
cc = np.full((H, W, len(pairs)), 255, np.uint8)
inc = np.full((H, W, len(pairs)), -32768, np.int16)
water = np.full((H, W), 255, np.uint8)
pair_info = []
geom = None
url_by_id = {g["id"]: g.get("url", "") for g in manifest.get("granules", [])}

for p, (d1, d2) in enumerate(pairs):
    name = f"013D_071_{d1}_{d2}.tif"
    f = src / name
    if not f.exists():
        raise SystemExit(f"missing pair file {name}")
    info = index[name]
    with rasterio.open(f) as ds:
        b = {d: i + 1 for i, d in enumerate(ds.descriptions)}
        inv = ~ds.transform
        c, r = inv * (ux, uy)
        c = np.floor(c).astype(int)
        r = np.floor(r).astype(int)
        ok = (c >= 0) & (r >= 0) & (c < ds.width) & (r < ds.height)
        c, r = np.clip(c, 0, ds.width - 1), np.clip(r, 0, ds.height - 1)
        ph = ds.read(b["unwrapped_phase_rad"])
        co = ds.read(b["coherence"])
        cp = ds.read(b["connected_components"])
        va = ds.read(b["valid"])
        if p == 0:
            wa = ds.read(b["water"])
            water[:] = np.where(ok, (wa[r, c] > 0).astype(np.uint8), 255)
        lam = float(info["wavelength_m"])
        good = ok & (va[r, c] == 1) & np.isfinite(ph[r, c]) & np.isfinite(co[r, c])
        coh[..., p] = np.where(good, np.round(np.clip(co[r, c], 0, 1) * 200), 255).astype(np.uint8)
        cc[..., p] = np.where(good, np.clip(cp[r, c], 0, 254), 255).astype(np.uint8)
        mm = -ph[r, c] * lam / (4 * math.pi) * 1000.0
        inc[..., p] = np.where(good, np.round(mm * 10), -32768).astype(np.int16)

        # mean coherence over the DEM extent (land and water, valid pixels)
        x0, x1, y0, y1 = dem["left"], dem["right"], dem["bottom"], dem["top"]
        (c0, r0), (c1, r1) = inv * (x0, y1), inv * (x1, y0)
        win = (slice(int(r0), int(r1) + 1), slice(int(c0), int(c1) + 1))
        vwin = va[win] == 1
        if geom is None:
            inc_a = ds.read(b["incidence_angle_deg"])[win][vwin]
            le = ds.read(b["los_east"])[win][vwin]
            ln = ds.read(b["los_north"])[win][vwin]
            geom = {
                "incidence_deg_mean": float(np.mean(inc_a)),
                "incidence_deg_min": float(np.min(inc_a)),
                "incidence_deg_max": float(np.max(inc_a)),
                "los_east_mean": float(np.mean(le)),
                "los_north_mean": float(np.mean(ln)),
                "note": "unit vector components of the line of sight over the DEM extent, as stored in the GUNW subset",
            }
        url = url_by_id.get(info["granule"], "")
        m = re.search(r"/NISAR/([A-Za-z0-9_]+)/", url)
        pair_info.append({
            "ref": d1, "sec": d2,
            "granule": info["granule"],
            "collection": m.group(1) if m else None,
            "product_version": info.get("product_version"),
            "temporal_baseline_days": info.get("temporal_baseline_days"),
            "aoi_unwrapped_frac": info.get("aoi_unwrapped_frac"),
            "coherence_mean_dem": float(np.mean(co[win][vwin])),
            "wavelength_m": lam,
        })

out = {
    "source": "NISAR L2 GUNW subsets (gunw_aoi.zip, portuguese_bend_california); sampled at meta.focus pixel centres, nearest 80 m pixel",
    "orbit_direction": index[next(iter(index))]["orbit_direction"],
    "look_direction": index[next(iter(index))]["look_direction"],
    "geometry": geom,
    "pairs": pair_info,
    "shape": [H, W, len(pairs)],
    "coh_b64": base64.b64encode(coh.tobytes()).decode(),
    "cc_b64": base64.b64encode(cc.tobytes()).decode(),
    "inc_b64": base64.b64encode(inc.astype("<i2").tobytes()).decode(),
    "water_b64": base64.b64encode(water.tobytes()).decode(),
    "encoding": {"coh": "uint8, coherence*200, 255 invalid", "cc": "uint8 label, 255 invalid", "inc": "int16le 0.1 mm, -32768 invalid", "water": "uint8 [h, w], 1 = water (GUNW water band, first pair), 255 outside"},
}
(ROOT / "public/gunw").mkdir(exist_ok=True)
(ROOT / "public/gunw/gunw.json").write_text(json.dumps(out))
print("pairs", len(pairs), "collections", {p["collection"] for p in pair_info}, "versions", {p["product_version"] for p in pair_info})
print("geometry", geom)
