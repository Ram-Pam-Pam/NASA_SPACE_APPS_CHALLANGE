"""Timeline anomalies = ground motion AT the pipelines (not the biggest clusters anywhere).

An anomaly is a stretch of the pipeline corridor (pixels a pipeline passes through, widened by one pixel,
i.e. about +-100 m) where the cumulative line-of-sight change on the last date is at least PEAK_FACTOR x the
series' display threshold, with at least MIN_PIPE_M of pipe in it. Such a
stretch is a possible risk to the pipes (bending and strain where the ground moves) - a signal to verify,
not a diagnosis. Ranked by the pipe length in moving ground times the typical change.

Used by build_tabasco_timeline.py (from the displacement cubes). It also runs on its own, on the PNG frames
a timeline already has (the transparency of a frame encodes the millimetres between the threshold and
full_colour_mm; stronger changes read as full_colour_mm, marked as "peak_saturated"):
    python3 backend/scripts/pipeline_anomalies.py <scene data folder>
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
from osgeo import gdal

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_timeline import clusters  # noqa: E402

gdal.UseExceptions()

SAMPLE_M = 40.0        # pipeline sample spacing
MIN_PIPE_M = 400.0     # shorter stretches of pipe in moving ground are not marked
PEAK_FACTOR = 2.0      # anomaly = ground that moved at least 2 x the display threshold (= 4 x background noise)
MAX_ANOMALIES = 12
MERGE_M = 1000.0       # stretches of the same kind closer than this are one anomaly (one marker, not a row of them)


def pipe_samples(pipes: list[dict], bounds, shape):
    """Points every SAMPLE_M along every pipeline: (row, col, lon, lat, pipe index)."""
    west, south, east, north = bounds
    h, w = shape
    k = 111_320 * math.cos(math.radians((south + north) / 2))
    out = []
    for i, f in enumerate(pipes):
        g = f["geometry"]
        parts = [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]
        for coords in parts:
            for (ax, ay, *_), (bx, by, *_) in zip(coords, coords[1:]):
                n = max(1, int(math.hypot((bx - ax) * k, (by - ay) * 110_540) // SAMPLE_M))
                for j in range(n):
                    lon, lat = ax + (bx - ax) * j / n, ay + (by - ay) * j / n
                    r, c = int((north - lat) / (north - south) * h), int((lon - west) / (east - west) * w)
                    if 0 <= r < h and 0 <= c < w:
                        out.append((r, c, lon, lat, i))
    return out


def merge_close(found: list[dict], max_px: float) -> list[dict]:
    """Join clusters of the same sign whose nearest pixels are at most max_px apart."""
    parent = list(range(len(found)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    boxes = [(c["ys"].min(), c["ys"].max(), c["xs"].min(), c["xs"].max()) for c in found]
    for i in range(len(found)):
        for j in range(i + 1, len(found)):
            a, b = boxes[i], boxes[j]
            if found[i]["sign"] != found[j]["sign"] or a[0] - b[1] > max_px or b[0] - a[1] > max_px or a[2] - b[3] > max_px or b[2] - a[3] > max_px:
                continue
            pa = np.stack([found[i]["ys"], found[i]["xs"]], 1).astype(np.float32)
            pb = np.stack([found[j]["ys"], found[j]["xs"]], 1).astype(np.float32)
            d = np.sqrt(((pa[:, None, :] - pb[None, :, :]) ** 2).sum(-1)).min() if len(pa) * len(pb) < 4e6 else 0
            if d <= max_px:
                parent[root(i)] = root(j)
    groups: dict[int, list[dict]] = {}
    for i, c in enumerate(found):
        groups.setdefault(root(i), []).append(c)
    return [{"sign": g[0]["sign"], "ys": np.concatenate([c["ys"] for c in g]), "xs": np.concatenate([c["xs"] for c in g])} for g in groups.values()]


def pipeline_anomalies(frames: list[np.ndarray], dates: list[str], bounds, threshold: float, pipes: list[dict],
                       full_colour_mm: float | None = None) -> list[dict]:
    west, south, east, north = bounds
    h, w = frames[-1].shape
    samples = pipe_samples(pipes, bounds, (h, w))
    if not samples:
        return []
    corridor = np.zeros((h, w), dtype=bool)
    rows = np.array([s[0] for s in samples]); cols = np.array([s[1] for s in samples])
    corridor[rows, cols] = True
    grown = corridor.copy()
    grown[1:] |= corridor[:-1]; grown[:-1] |= corridor[1:]; grown[:, 1:] |= corridor[:, :-1]; grown[:, :-1] |= corridor[:, 1:]
    last = np.where(grown, frames[-1], np.nan)
    strong = PEAK_FACTOR * threshold  # an anomaly = ground that moved clearly more than the display threshold
    m2 = ((east - west) / w * 111_320 * math.cos(math.radians((north + south) / 2))) * ((north - south) / h * 110_540)
    by_pixel: dict[tuple[int, int], list[int]] = {}
    for n, (r, c, *_rest) in enumerate(samples):
        for dr, dc in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)):  # a sample counts for its pixel and the 4 around (the corridor)
            by_pixel.setdefault((r + dr, c + dc), []).append(n)

    px_m = math.sqrt(m2)
    found = []
    for cl in merge_close(clusters(last, strong), MERGE_M / px_m):
        ys, xs, sign = cl["ys"], cl["xs"], cl["sign"]
        values = frames[-1][ys, xs] * sign
        hit = sorted({n for y, x in zip(ys, xs) for n in by_pixel.get((int(y), int(x)), [])})
        # per pipeline (by id: one pipeline can be stored as many overlapping parts) the pixels it crosses
        pid = lambda n: pipes[samples[n][4]]["properties"].get("id") or samples[n][4]  # noqa: E731
        pipe_m = len({(pid(n), samples[n][0], samples[n][1]) for n in hit}) * px_m
        stretch_m = len({(samples[n][0], samples[n][1]) for n in hit}) * px_m  # length of the corridor (parallel pipes once)
        peak = float(values.max())
        if pipe_m < MIN_PIPE_M:
            continue
        # the marker sits on a pipe, at the sample with the strongest change
        best = max(hit, key=lambda n: abs(np.nan_to_num(frames[-1][samples[n][0], samples[n][1]])))
        lines = sorted({samples[n][4] for n in hit})
        props = [pipes[i]["properties"] for i in lines]
        uniq = {}
        for p in props:
            uniq.setdefault(p.get("id") or p.get("name"), p)
        first = next((dates[e] for e, f in enumerate(frames) if np.mean(np.nan_to_num(f[ys, xs]) * sign >= strong) >= 0.25), dates[-1])
        median = float(np.median(values))
        found.append({
            "kind": "pipeline", "direction": "down" if sign < 0 else "up",
            "lon": round(samples[best][2], 6), "lat": round(samples[best][3], 6),
            "area_ha": round(len(ys) * m2 / 1e4), "pipe_length_m": round(pipe_m), "stretch_m": round(min(stretch_m, pipe_m), -1),
            "pipes_count": len(uniq),
            "pipes": [{k: p.get(k) for k in ("name", "status", "commodity", "diameter_in") if p.get(k) is not None} for p in list(uniq.values())[:4]],
            "pipes_active": sum(1 for p in uniq.values() if p.get("status") == "czynny"),
            "peak_mm": round(peak), "peak_saturated": bool(full_colour_mm and peak >= full_colour_mm - 1),
            "median_mm": round(median), "first_date": first,
            "series_mm": [round(float(np.nanmedian(f[ys, xs]))) if np.isfinite(f[ys, xs]).any() else 0 for f in frames],
            "score": pipe_m * median,
        })
    found.sort(key=lambda a: -a["score"])
    for rank, a in enumerate(found[:MAX_ANOMALIES], start=1):
        a["rank"] = rank
        del a["score"]
    return found[:MAX_ANOMALIES]


def decode_frame(path: Path, threshold: float, full: float) -> np.ndarray:
    """Millimetres back from a timeline frame: colour = sign, alpha = 0.35 + 0.55 * strength."""
    a = gdal.Open(str(path)).ReadAsArray().astype(np.float32)
    alpha = a[3] / 255
    strength = np.clip((alpha - 0.35) / 0.55, 0, 1)
    mag = threshold + strength * (full - threshold)
    sign = np.where(a[0] > a[2], -1.0, 1.0)  # red = away from the satellite
    return np.where(alpha > 0, sign * mag, 0.0)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    scene = Path(sys.argv[1])
    meta_path = scene / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    pipes = json.loads((scene / "pipelines.geojson").read_text(encoding="utf-8"))["features"]
    tl = meta["timeline"]
    for series in tl.get("series") or [tl]:
        thr, full = float(series["threshold_mm"]), float(series["full_colour_mm"])
        frames = [decode_frame(scene / series["dir"] / f, thr, full) for f in series["files"]]
        series["anomalies"] = pipeline_anomalies(frames, series["dates"], series["bbox"], thr, pipes, full)
        series["anomaly_kind"] = "pipeline"
        print(f"{series.get('id', 'timeline')}: {len(series['anomalies'])} anomalies at the pipelines")
        for a in series["anomalies"]:
            print(f"  {a['rank']}: {a['direction']} peak {a['peak_mm']} mm, {a['pipe_length_m']} m of pipe, {a['pipes_count']} pipelines "
                  f"({a['pipes_active']} active), from {a['first_date']} at {a['lon']},{a['lat']}")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
