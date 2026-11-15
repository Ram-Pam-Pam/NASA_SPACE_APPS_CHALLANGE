"""Ground-motion timeline for the 2D view of a 3D scene package.

Takes the cumulative displacement frames of an InSAR frontend package (img/cum_XX.png, one per date,
coloured with the RdBu scale of +-meta.scale_mm) and turns them into the simplest possible frames:
    red  = the ground moved down (away from the satellite)
    blue = the ground moved up (towards the satellite)
    clear = no change or a change smaller than THRESHOLD_MM (the same for every date)
The stronger the colour, the bigger the change. The ANOMALY_COUNT biggest clusters of change on the last
date (ranked by the summed change, so both size and strength count) are listed as anomalies. Values are read back from the RdBu colours (checked
against timeseries.json: median difference 1 mm) and the frames are resampled from Web Mercator rows to
the scene's lon/lat grid.

Usage (from the repo root):
    python3 backend/scripts/build_timeline.py <InSAR data folder> <scene data folder>
Writes <scene>/timeline/frame_XX.png and the "timeline" entry of <scene>/meta.json.
build_scene3d.py calls this too when it gets --insar-data.
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
from osgeo import gdal

gdal.UseExceptions()

# ColorBrewer RdBu, 11 classes (= matplotlib "RdBu"), from the lowest value (dark red) to the highest (dark blue)
RDBU = ["#67001f", "#b2182b", "#d6604d", "#f4a582", "#fddbc7", "#f7f7f7", "#d1e5f0", "#92c5de", "#4393c3", "#2166ac", "#053061"]
DOWN_RGB, UP_RGB = (214, 39, 40), (31, 104, 196)
THRESHOLD_MM = 20.0         # changes smaller than this are transparent - one value for all dates (test value)
FULL_COLOUR_MM = 100.0      # a change this big (or bigger) gets the strongest colour
ANOMALY_COUNT = 3           # how many main clusters are marked on the map


def rdbu_lut(scale_mm: float, steps: int = 601):
    anchors = np.array([[int(h[i:i + 2], 16) for i in (1, 3, 5)] for h in RDBU], dtype=np.float64)
    pos = np.linspace(0, 10, steps)
    lo = np.clip(np.floor(pos).astype(int), 0, 9)
    f = (pos - lo)[:, None]
    return anchors[lo] * (1 - f) + anchors[lo + 1] * f, np.linspace(-scale_mm, scale_mm, steps)


def decode(frame: Path, lut: np.ndarray, values: np.ndarray) -> np.ndarray:
    """Millimetres per pixel from an RdBu-coloured RGBA frame; NaN where the frame is transparent."""
    a = gdal.Open(str(frame)).ReadAsArray()
    h, w = a.shape[1:]
    rgb = a[:3].reshape(3, -1).T
    out = np.full(h * w, np.nan)
    keep = a[3].reshape(-1) > 0
    colours, inverse = np.unique(rgb[keep], axis=0, return_inverse=True)
    nearest = ((colours[:, None, :].astype(np.float64) - lut[None, :, :]) ** 2).sum(-1).argmin(1)
    out[keep] = values[nearest][inverse.reshape(-1)]
    return out.reshape(h, w)


def clusters(mm: np.ndarray, threshold: float) -> list[dict]:
    """Connected areas where the change passes the threshold, one sign at a time. Pixels one cell apart
    are joined (a dilated mask is labelled), so a cluster with small gaps stays one cluster."""
    from collections import deque
    h, w = mm.shape
    out = []
    for sign in (-1, 1):
        hit = np.isfinite(mm) & (mm * sign >= threshold)
        grown = hit.copy()
        grown[1:] |= hit[:-1]; grown[:-1] |= hit[1:]; grown[:, 1:] |= hit[:, :-1]; grown[:, :-1] |= hit[:, 1:]
        seen = np.zeros_like(grown)
        for y0, x0 in zip(*np.nonzero(grown)):
            if seen[y0, x0]:
                continue
            queue, cells = deque([(y0, x0)]), []
            seen[y0, x0] = True
            while queue:
                y, x = queue.popleft()
                cells.append((y, x))
                for yy, xx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                    if 0 <= yy < h and 0 <= xx < w and grown[yy, xx] and not seen[yy, xx]:
                        seen[yy, xx] = True
                        queue.append((yy, xx))
            ys, xs = np.array(cells).T
            real = hit[ys, xs]
            if real.sum() >= 3:
                out.append({"sign": sign, "ys": ys[real], "xs": xs[real], "volume": float(np.abs(mm[ys[real], xs[real]]).sum())})
    return sorted(out, key=lambda c: -c["volume"])


def mercator_y(lat: float) -> float:
    return 6378137.0 * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def build_timeline(insar_dir: Path, scene_dir: Path) -> dict | None:
    meta = json.loads((insar_dir / "meta.json").read_text(encoding="utf-8"))
    dates = meta.get("dates") or []
    if not dates or not meta.get("overlay_bounds") or not (insar_dir / "img" / "cum_00.png").exists():
        return None
    (south, west), (north, east) = meta["overlay_bounds"]
    scale = float(meta.get("scale_mm") or 150)
    lut, values = rdbu_lut(scale)
    out_dir = scene_dir / "timeline"
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for e, d in enumerate(dates):
        mm = decode(insar_dir / "img" / f"cum_{e:02d}.png", lut, values)
        h, w = mm.shape
        # source rows are equal steps of Web Mercator y; the scene wants equal steps of latitude
        lats = north - (np.arange(h) + 0.5) / h * (north - south)
        rows = np.clip(((mercator_y(north) - np.array([mercator_y(v) for v in lats])) / (mercator_y(north) - mercator_y(south)) * h).astype(int), 0, h - 1)
        mm = mm[rows]
        frames.append(mm)
        # one fixed threshold for every date, so a spot either grows over time or it does not
        thr = THRESHOLD_MM
        mag = np.abs(mm)
        strength = np.clip((mag - thr) / max(FULL_COLOUR_MM - thr, 1), 0, 1)
        alpha = np.where(np.isfinite(mm) & (mag >= thr), 0.35 + 0.55 * strength, 0)
        rgba = np.zeros((4, h, w), dtype=np.uint8)
        for band, (dn, up) in enumerate(zip(DOWN_RGB, UP_RGB)):
            rgba[band] = np.where(mm < 0, dn, up)
        rgba[3] = np.round(alpha * 255).astype(np.uint8)
        mem = gdal.GetDriverByName("MEM").Create("", w, h, 4, gdal.GDT_Byte)
        mem.WriteArray(rgba)
        gdal.GetDriverByName("PNG").CreateCopy(str(out_dir / f"frame_{e:02d}.png"), mem)
    for aux in out_dir.glob("*.aux.xml"):
        aux.unlink()

    # main anomalies: the biggest clusters on the last date, with when they first showed up
    h, w = frames[-1].shape
    m_per_px = ((east - west) / w * 111_320 * math.cos(math.radians((north + south) / 2))) * ((north - south) / h * 110_540)
    anomalies = []
    for c in clusters(frames[-1], THRESHOLD_MM)[:ANOMALY_COUNT]:
        ys, xs, sign = c["ys"], c["xs"], c["sign"]
        last = frames[-1][ys, xs] * sign
        weight = last - THRESHOLD_MM + 1  # centre pulled towards the strongest part
        cy, cx = float((ys * weight).sum() / weight.sum()), float((xs * weight).sum() / weight.sum())
        first = next((dates[e] for e, f in enumerate(frames) if np.mean(np.nan_to_num(f[ys, xs]) * sign >= THRESHOLD_MM) >= 0.25), dates[-1])
        peak = float(last.max())
        anomalies.append({
            "rank": len(anomalies) + 1, "direction": "down" if sign < 0 else "up",
            "lon": round(west + (cx + 0.5) / w * (east - west), 6), "lat": round(north - (cy + 0.5) / h * (north - south), 6),
            "area_ha": round(len(ys) * m_per_px / 1e4), "peak_mm": round(peak), "peak_saturated": peak >= scale - 1,
            "median_mm": round(float(np.median(last))), "first_date": first,
            "series_mm": [round(float(np.nanmedian(f[ys, xs]) if np.isfinite(f[ys, xs]).any() else 0)) for f in frames],
        })
    return {"dir": "timeline", "files": [f"frame_{e:02d}.png" for e in range(len(dates))], "dates": dates,
            "bbox": [west, south, east, north], "threshold_mm": THRESHOLD_MM, "full_colour_mm": FULL_COLOUR_MM, "anomalies": anomalies,
            "source": meta.get("stack"),
            "note": "Cumulative ground motion since the first date, in the satellite's line of sight. Red = down, blue = up, clear = less than threshold_mm."}


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    insar_dir, scene_dir = Path(sys.argv[1]), Path(sys.argv[2])
    timeline = build_timeline(insar_dir, scene_dir)
    if timeline is None:
        sys.exit("No cumulative frames (img/cum_XX.png) in the InSAR folder.")
    meta_path = scene_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["timeline"] = timeline
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"timeline: {len(timeline['dates'])} frames, threshold {timeline['threshold_mm']} mm -> {scene_dir / 'timeline'}")
    for a in timeline["anomalies"]:
        print(f"  anomaly {a['rank']}: {a['direction']} {a['area_ha']} ha, peak {a['peak_mm']} mm, from {a['first_date']} at {a['lon']},{a['lat']}")


if __name__ == "__main__":
    main()
