"""Analysis package for Tabasco: what the click panel of view3d.html needs (like the Portuguese Bend package).

Input (Dane/zrodla_tabasco, copied from the Lexar drive: nasa/outputs/tabasco_jak_kalifornia and
nasa/dashboard_tabasco/data/data.js):
    A062_cube.npz, D156_cube.npz  cumulative LOS displacement per date (UTM 15N, 80 m), reliable mask,
                                  velocity, monitoring window classes (cls, okna)
    products.json                 background noise per orbit
    dashboard_data.js             pipeline assessment (levels OK / Watch / Warning / Critical, critical sections)
Scene (Dane/model3d_tabasco_mexico/data): roads.geojson and buildings_fp.bin - the objects people click.

Output (Dane/analiza_tabasco_mexico/data, kind "analysis" - read only by view3d.html, not a map of its own):
    meta.json                     orbits, their dates and time-series grids, thresholds
    ts_<orbit>/<row>_<col>.bin    displacement per pixel and date, int16 mm, tiles of TILE px (EPSG:4326 grid,
                                  80 m); the panel loads only the tile under the click
    buildings_risk.geojson        buildings with a medium or high class (the rest: within the noise)
    roads_risk.geojson            100 m road pieces with a medium or high class (merged when in a row)
    pipelines.geojson             pipelines (whole line) + critical sections from the dashboard assessment
    critical.geojson              empty (no power lines or rail in this scene)

Classes like Portuguese Bend, per orbit, the stronger orbit wins (a signal to verify, not a diagnosis):
    displacement over the period of the strongest of ~5 points of the object (3 x 3 median = a compact zone);
    medium >= 3 sigma of the orbit's background noise, high >= 4 sigma; or a differential motion (change
    between neighbouring 80 m pixels, per 100 m) >= 3 / 4 x sqrt(2) sigma; high also with an alert in the
    last monitoring window. Values are LOS - line of sight, not vertical.

Usage (from the repo root):
    python3 backend/scripts/build_tabasco_analysis.py Dane/zrodla_tabasco Dane/model3d_tabasco_mexico/data Dane/analiza_tabasco_mexico/data
"""
import json
import math
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_tabasco_timeline import SERIES, warp_cube  # noqa: E402

TILE = 128            # time-series tile size, pixels
NODATA = -32768
K_MED, K_HIGH = 3.0, 4.0
ROAD_PIECE_M = 100.0
LEVEL_KLASA = {3: "wysokie", 2: "wysokie", 1: "średnie", 0: "niskie", -1: "brak danych", -2: "brak danych"}
LEVEL_NAME = {3: "Critical", 2: "Warning", 1: "Watch", 0: "OK", -1: "brak danych", -2: "mokradło"}
SEVERITY = {"brak danych": 0, "niskie": 1, "średnie": 2, "wysokie": 3}
ROAD_PL = {"motorway": "autostrada", "trunk": "droga ekspresowa", "primary": "droga główna", "secondary": "droga drugorzędna"}


def nanmedian3(a: np.ndarray) -> np.ndarray:
    pad = np.pad(a, 1, constant_values=np.nan)
    stack = np.stack([pad[dy:dy + a.shape[0], dx:dx + a.shape[1]] for dy in range(3) for dx in range(3)])
    with np.errstate(all="ignore"):
        out = np.nanmedian(stack, axis=0)
    out[np.isnan(a)] = np.nan  # no value invented where the pixel itself has none
    return out


class Orbit:
    """One orbit on the scene's lon/lat grid: series, smoothed total, gradient, velocity, monitoring windows."""

    def __init__(self, src: Path, stack: str, label: str, noise_mm: float, bbox):
        with np.load(src / f"{stack}_cube.npz", allow_pickle=False) as z:
            cum, reliable, x, y, epsg = z["cum"], z["reliable"], z["x"], z["y"], int(z["epsg"])
            vel, cls, okna = z["vel"], z["cls"].astype(np.float32), [str(o) for o in z["okna"]]
            self.dates = [str(d).replace("-", "") for d in z["dates"]]
        cum[:, ~reliable] = np.nan
        vel = np.where(reliable, vel, np.nan)
        self.cum, self.bounds = warp_cube(cum, x, y, epsg, bbox)
        self.vel = warp_cube(vel[None], x, y, epsg, bbox)[0][0]
        self.cls = warp_cube(cls, x, y, epsg, bbox)[0]
        self.id, self.label, self.stack, self.noise = stack.lower(), label, stack, noise_mm
        self.okna = okna
        self.h, self.w = self.cum.shape[1:]
        self.total = nanmedian3(self.cum[-1])
        west, south, east, north = self.bounds
        self.px_m = (north - south) / self.h * 110_540
        gy = np.abs(np.diff(self.total, axis=0, append=np.nan))
        gx = np.abs(np.diff(self.total, axis=1, append=np.nan))
        with np.errstate(all="ignore"):
            self.grad = np.fmax(gx, gy) / self.px_m * 100  # mm per 100 m
        self.windows_moving = np.nansum(self.cls > 0, axis=0)
        self.alert_last = np.isin(self.cls[-1], (2, 3))
        self.t_med, self.t_high = K_MED * noise_mm, K_HIGH * noise_mm
        g = math.sqrt(2) * noise_mm / self.px_m * 100
        self.g_med, self.g_high = K_MED * g, K_HIGH * g

    def pixels(self, lon: np.ndarray, lat: np.ndarray):
        west, south, east, north = self.bounds
        r = ((north - lat) / (north - south) * self.h).astype(int)
        c = ((lon - west) / (east - west) * self.w).astype(int)
        ok = (r >= 0) & (r < self.h) & (c >= 0) & (c < self.w)
        return np.clip(r, 0, self.h - 1), np.clip(c, 0, self.w - 1), ok

    def assess(self, lon: np.ndarray, lat: np.ndarray) -> dict:
        """Class of one object from its sample points (arrays of lon, lat)."""
        r, c, ok = self.pixels(lon, lat)
        t = np.where(ok, self.total[r, c], np.nan)
        finite = np.isfinite(t)
        if finite.mean() < 0.5:
            return {"klasa": "brak danych", "powod": "za mało wiarygodnych pikseli (woda, utrata spójności)", "orbit": self.label}
        k = int(np.nanargmax(np.abs(t)))
        tv, g = float(t[k]), float(np.nanmax(np.where(ok, self.grad[r, c], np.nan))) if np.isfinite(self.grad[r, c]).any() else 0.0
        a, alert = abs(tv), bool(self.alert_last[r[k], c[k]])
        reasons = []
        if a >= self.t_high or (g >= self.g_high and a >= self.t_med) or (alert and a >= self.t_med):
            klasa = "wysokie"
        elif a >= self.t_med or g >= self.g_med:
            klasa = "średnie"
        else:
            klasa = "niskie"
        if a >= self.t_med:
            reasons.append(f"|przemieszczenie|={a:.0f} mm (próg {self.t_high if klasa == 'wysokie' and a >= self.t_high else self.t_med:.0f})")
        if g >= self.g_med:
            reasons.append(f"ruch zróżnicowany {g:.0f} mm/100m (próg {self.g_high if g >= self.g_high else self.g_med:.0f})")
        if alert and a >= self.t_med:
            reasons.append("alert w ostatnim oknie monitoringu")
        return {"klasa": klasa, "powod": ("; ".join(reasons) if reasons else "w granicach szumu"), "orbit": self.label,
                "v": round(float(self.vel[r[k], c[k]]), 1) if np.isfinite(self.vel[r[k], c[k]]) else None,
                "przem_mm": round(tv, 1), "gradient": round(g, 1) if g else None,
                "okna_ruch": int(self.windows_moving[r[k], c[k]]), "okna_n": len(self.okna),
                "pewnosc": "pełna" if finite.all() else "częściowa"}

    def write_tiles(self, out: Path) -> dict:
        d = out / f"ts_{self.id}"
        d.mkdir(parents=True, exist_ok=True)
        q = np.where(np.isfinite(self.cum), np.clip(np.round(self.cum), -32767, 32767), NODATA).astype("<i2")
        q = np.transpose(q, (1, 2, 0))  # rows, cols, dates
        tiles = []
        for tr in range(math.ceil(self.h / TILE)):
            for tc in range(math.ceil(self.w / TILE)):
                block = q[tr * TILE:(tr + 1) * TILE, tc * TILE:(tc + 1) * TILE]
                if (block != NODATA).any():
                    (d / f"{tr}_{tc}.bin").write_bytes(block.tobytes())
                    tiles.append([tr, tc])
        west, south, east, north = self.bounds
        return {"id": self.id, "label": self.label, "stack": self.stack, "dates": self.dates,
                "grid": {"west": west, "south": south, "east": east, "north": north, "w": self.w, "h": self.h,
                         "tile": TILE, "epochs": len(self.dates), "dir": d.name, "tiles": tiles, "nodata": NODATA,
                         "layout": "int16 little endian, [row][col][date] inside a tile; tile (r, c) = rows r*tile.., cols c*tile..; edge tiles are smaller"},
                "noise_mm": self.noise, "thresholds": {"przem_med_mm": round(self.t_med), "przem_high_mm": round(self.t_high),
                                                       "grad_med_mm_100m": round(self.g_med), "grad_high_mm_100m": round(self.g_high)},
                "windows": self.okna}


def combine(results: list[dict]) -> dict:
    """The stronger orbit wins; without data in either orbit -> 'brak danych'."""
    best = max(results, key=lambda r: (SEVERITY[r["klasa"]], abs(r.get("przem_mm") or 0)))
    out = dict(best)
    out["powod"] = f"{best['orbit']}: {best['powod']}"
    out.pop("orbit")
    return out


def footprints(scene: Path, meta: dict):
    """Building outlines from buildings_fp.bin (lon/lat), one array of points per building."""
    w, s, e, n = meta["bbox"]
    blob = (scene / meta["buildings_fp"]["file"]).read_bytes()
    o, out = 0, []
    while o < len(blob):
        k = blob[o]
        xy = np.frombuffer(blob, dtype="<u2", count=2 * k, offset=o + 6).reshape(k, 2).astype(np.float64)
        out.append(np.column_stack([w + xy[:, 0] / 65535 * (e - w), n - xy[:, 1] / 65535 * (n - s)]))
        o += 6 + 4 * k
    return out


def line_points(coords, step_m: float):
    pts = [tuple(coords[0][:2])]
    lat0 = coords[0][1]
    k = 111_320 * math.cos(math.radians(lat0))
    for (ax, ay, *_), (bx, by, *_) in zip(coords, coords[1:]):
        nseg = max(1, int(math.hypot((bx - ax) * k, (by - ay) * 110_540) // step_m))
        for j in range(1, nseg + 1):
            pts.append((ax + (bx - ax) * j / nseg, ay + (by - ay) * j / nseg))
    return np.array(pts), k


def main() -> None:
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    src, scene, out = map(Path, sys.argv[1:])
    out.mkdir(parents=True, exist_ok=True)
    smeta = json.loads((scene / "meta.json").read_text(encoding="utf-8"))
    products = {seg["stack"]: seg for seg in json.loads((src / "products.json").read_text(encoding="utf-8"))["segments"]}
    orbits = [Orbit(src, stack, label, float(products[stack]["background_noise_mm"]), tuple(smeta["bbox"])) for stack, label, _ in SERIES]
    for ob in orbits:
        print(f"{ob.id}: {ob.w} x {ob.h} px, {len(ob.dates)} dates, thresholds {ob.t_med:.0f}/{ob.t_high:.0f} mm, "
              f"gradient {ob.g_med:.0f}/{ob.g_high:.0f} mm/100m", flush=True)

    # ---- buildings: centre + 4 points between the centre and the corners (like the PB package)
    blds = footprints(scene, smeta)
    risky = []
    counts = {k: 0 for k in SEVERITY}
    for i, xy in enumerate(blds):
        c = xy.mean(axis=0)
        pick = xy[:: max(1, len(xy) // 4)][:4]
        pts = np.vstack([c, c + 0.45 * (pick - c)])
        res = combine([ob.assess(pts[:, 0], pts[:, 1]) for ob in orbits])
        counts[res["klasa"]] += 1
        if res["klasa"] in ("średnie", "wysokie"):
            ring = [[round(float(x), 6), round(float(y), 6)] for x, y in xy] + [[round(float(xy[0][0]), 6), round(float(xy[0][1]), 6)]]
            risky.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
                          "properties": {"obiekt_id": f"bld_{i}", "nazwa": "", **res}})
        if (i + 1) % 200_000 == 0:
            print(f"  buildings {i + 1}/{len(blds)}", flush=True)
    (out / "buildings_risk.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": risky}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("buildings:", counts, flush=True)

    # ---- roads: 100 m pieces, consecutive pieces of one class merged
    roads = json.loads((scene / "roads.geojson").read_text(encoding="utf-8"))["features"]
    road_out, road_counts = [], {k: 0.0 for k in SEVERITY}
    for f in roads:
        p = f["properties"]
        parts = [f["geometry"]["coordinates"]] if f["geometry"]["type"] == "LineString" else f["geometry"]["coordinates"]
        for coords in parts:
            pts, _ = line_points(coords, 20.0)
            per = max(2, int(ROAD_PIECE_M / 20))
            pieces = [pts[j:j + per + 1] for j in range(0, len(pts) - 1, per)]
            run = None
            for piece in pieces:
                res = combine([ob.assess(piece[:, 0], piece[:, 1]) for ob in orbits])
                road_counts[res["klasa"]] += len(piece) * 20 / 1000
                if run and run["res"]["klasa"] == res["klasa"]:
                    run["pts"] = np.vstack([run["pts"], piece[1:]])
                    if SEVERITY[res["klasa"]] and abs(res.get("przem_mm") or 0) > abs(run["res"].get("przem_mm") or 0):
                        run["res"] = res
                    continue
                if run:
                    road_out.append(run)
                run = {"pts": piece, "res": res, "p": p}
            if run:
                road_out.append(run)
    risky_roads = [{"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[round(float(x), 6), round(float(y), 6)] for x, y in r["pts"]]},
                    "properties": {"typ": ROAD_PL.get(r["p"].get("kind"), "droga"), "podtyp": r["p"].get("kind"), "nazwa": r["p"].get("name") or r["p"].get("ref") or "",
                                   "zrodlo": "OpenStreetMap", **r["res"]}}
                   for r in road_out if r["res"]["klasa"] in ("średnie", "wysokie")]
    (out / "roads_risk.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": risky_roads}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("roads km:", {k: round(v) for k, v in road_counts.items()}, "risky pieces:", len(risky_roads), flush=True)

    # ---- pipelines: the dashboard assessment (vertical motion from both orbits, 500 m + local anomaly)
    raw = (src / "dashboard_data.js").read_text(encoding="utf-8")
    dash = json.loads(raw[raw.index("{"):raw.rstrip().rstrip(";").rindex("}") + 1])
    thr = dash["meta"]["thresholds"]

    def pipe_type(commodity):
        c = str(commodity or "").upper()
        return "gazociąg" if "GAS" in c and "OIL" not in c else "rurociąg"
    pipes = []
    for f in dash["pipelines"]["features"]:
        p = f["properties"]
        level = int(p.get("level", -1))
        base = 0 if level > 0 else level  # outside its critical sections a line counts as OK
        reason = {0: "w granicach szumu (poza odcinkami krytycznymi)" if level > 0 else "w granicach szumu",
                  -1: "brak wiarygodnych pomiarów wzdłuż rury", -2: "mokradło: sygnał może pochodzić od zmian poziomu wody"}[base]
        pipes.append({"type": "Feature", "geometry": f["geometry"], "properties": {
            "typ": pipe_type(p.get("COMMODITY")), "podtyp": (p.get("COMMODITY") or "").lower(), "nazwa": p.get("FAC_NAME") or "",
            "zrodlo": "ocena rurociągów (dashboard Tabasco)", "klasa": LEVEL_KLASA[base], "powod": reason,
            "przem_mm": p.get("cum_min_mm"), "pewnosc": f"pokrycie pomiarem {round(100 * (p.get('coverage') or 0))}%"}})
    for f in dash["sections"]["features"]:
        p = f["properties"]
        if p.get("asset") != "pipeline":
            continue
        level = int(p["level"])
        pipes.append({"type": "Feature", "geometry": f["geometry"], "properties": {
            "typ": "rurociąg", "podtyp": "odcinek krytyczny", "nazwa": p.get("name") or "", "zrodlo": "ocena rurociągów (dashboard Tabasco)",
            "klasa": LEVEL_KLASA[level],
            "powod": (f"poziom {LEVEL_NAME[level]} na km {p['start_km']}-{p['end_km']}: ruch pionowy (500 m) {p['vs_min']:.0f} mm/rok "
                      f"(próg {thr['watch_vs']:.0f}), anomalia lokalna {p['local_min']:.0f} mm/rok (próg {thr['warn_loc']:.0f}/{thr['crit_loc']:.0f})"),
            "przem_mm": p.get("cum_min_mm"),
            "pewnosc": {"higher": "wyższa (zgodne obie orbity)", "low": "niska"}.get(str(p.get("confidence") or "").split(":")[0], p.get("confidence")),
            "km": p.get("length_km")}})
    (out / "pipelines.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": pipes}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (out / "critical.geojson").write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    print("pipelines:", len(pipes), "features (lines + critical sections)", flush=True)

    series = [ob.write_tiles(out) for ob in orbits]
    meta = {
        "aoi": smeta["aoi"], "name": smeta["name"], "kind": "analysis",
        "note": "Pakiet do panelu po kliknięciu w view3d.html (bez własnej mapy). Klasy = narażenie (ruch gruntu), nie stan obiektu.",
        "series": series,
        "los_note": "Radar mierzy ruch wzdłuż linii widzenia satelity (LOS): orbita wznosząca A062 patrzy z zachodu, zstępująca D156 ze wschodu. "
                    "Nie podaje pełnego kierunku ani samego ruchu pionowego; ujemne = od satelity.",
        "season_note": "Okres czerwiec–październik to pora deszczowa (i mokradła): część zmian może być sezonowa albo pochodzić od wody.",
        "counts": {"buildings": counts, "buildings_risk": len(risky), "roads_risk": len(risky_roads), "pipelines": len(pipes)},
        "sources": ["NISAR L2 GUNW (NASA/ISRO, ASF DAAC): A062 wznosząca, D156 zstępująca", "Overture Maps (budynki)", "OpenStreetMap (drogi)",
                    "OGIM + ocena rurociągów z dashboardu Tabasco (ruch pionowy z obu orbit)"],
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print("written to", out)


if __name__ == "__main__":
    main()
