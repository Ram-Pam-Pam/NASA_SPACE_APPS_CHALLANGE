"""Import a case study from the team's QGIS results folder (e.g. Dane/TABASCO/tabasco_front).

The folder holds raw analysis results: GeoTIFFs in UTM (both orbits, vertical decomposition,
per-pair interferograms, monitoring), GeoPackages (pipelines with risk classes, zones, alerts,
profiles) and CSV/JSON (profiles over time, GNSS). This script reprojects everything to one
Web Mercator grid and writes the data package the page draws:

  core (same as build_location.py data.js, so the existing page works as is)
    grid, payload.cum / coh / vwin, zones (with peak per date and first date), contours
  extensions (optional keys, only used when present)
    views    - side-panel views of the main results: pipelines at risk, subsidence zones, phase analysis
    vectors  - pipelines (network + at-risk fragments), profiles
    focus    - phase-analysis crop (Tokal): wrapped fringes and displacement at 20 m, contours per date
    layers   - vertical velocity from two orbits (quoted in the point popup only)
    display  - default quality filter and the noise level below which pixels are transparent

and registers it as a location. Run from the backend/ folder:
    pip install -r requirements-tools.txt
    python scripts/import_team_results.py "C:\\...\\Dane\\TABASCO\\tabasco_front" --id tabasco_mexico ^
        --name Tabasco --region "Meksyk (Cárdenas – Villahermosa)" --hazard "Osiadanie terenu nad złożami" --order 2
"""
import argparse
import base64
import csv
import gzip
import json
import math
import sys
from pathlib import Path

import numpy as np
import pyogrio
import rasterio
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.warp import calculate_default_transform, reproject
from shapely import from_wkb, line_merge, unary_union
from shapely.geometry import mapping
from shapely.ops import transform as shp_transform

# Make the "app" package importable when running this file directly
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas.location import Location, LocationUpdate  # noqa: E402
from app.services.location_package import parse_package  # noqa: E402
from app.services.location_registry import get_location_registry  # noqa: E402

ND16, ND8 = -32768, 255
HALF_WAVELENGTH_MM = 120.98
MERC = "EPSG:3857"


# ----------------------------------------------------------------- helpers
def pack(arr: np.ndarray) -> str:
    """gzip + base64 - the page unpacks it with DecompressionStream('gzip')."""
    return base64.b64encode(gzip.compress(np.ascontiguousarray(arr).tobytes(), compresslevel=9)).decode("ascii")


def to_i16(a: np.ndarray, scale: float) -> np.ndarray:
    """Float array (NaN = no data) -> int16 where value = raw * scale."""
    out = np.where(np.isnan(a), ND16, np.clip(np.round(a / scale), -32767, 32767))
    return out.astype("<i2")


def to_u8(a: np.ndarray) -> np.ndarray:
    return np.where(np.isnan(a), ND8, np.clip(np.round(a), 0, 254)).astype(np.uint8)


def block_reduce(a: np.ndarray, f: int, how="mean") -> np.ndarray:
    """Downsample the last two axes by factor f (NaN-aware)."""
    *lead, h, w = a.shape
    h2, w2 = h // f, w // f
    v = a[..., : h2 * f, : w2 * f].reshape(*lead, h2, f, w2, f)
    with np.errstate(all="ignore"):
        if how == "mode":                                   # categorical: first valid value in the block
            flat = np.moveaxis(v, -3, -2).reshape(*lead, h2, w2, f * f)
            out = np.full(flat.shape[:-1], np.nan, dtype=np.float32)
            for k in range(f * f - 1, -1, -1):
                cell = flat[..., k]
                out = np.where(np.isnan(cell), out, cell)
            return out
        return np.nanmean(np.nanmean(v, axis=-1), axis=-2).astype(np.float32)


class Grid:
    """Target Web Mercator grid built from the source UTM grid."""

    def __init__(self, ref_tif: Path):
        with rasterio.open(ref_tif) as ds:
            self.src_crs, self.src_bounds = ds.crs, ds.bounds
            tr, w, h = calculate_default_transform(ds.crs, MERC, ds.width, ds.height, *ds.bounds)
        res = max(abs(tr.a), abs(tr.e))                     # square pixels
        self.res, self.w, self.h = res, int(round(w * abs(tr.a) / res)), int(round(h * abs(tr.e) / res))
        self.x0, self.y1 = tr.c, tr.f
        self.transform = rasterio.Affine(res, 0, self.x0, 0, -res, self.y1)
        to_ll = Transformer.from_crs(MERC, "EPSG:4326", always_xy=True)
        w_lon, n_lat = to_ll.transform(self.x0, self.y1)
        e_lon, s_lat = to_ll.transform(self.x0 + self.w * res, self.y1 - self.h * res)
        self.bounds = [[s_lat, w_lon], [n_lat, e_lon]]
        cols, rows = np.meshgrid(np.arange(self.w) + 0.5, np.arange(self.h) + 0.5)
        self.lon, self.lat = to_ll.transform(self.x0 + cols * res, self.y1 - rows * res)

    def warp(self, path: Path, bands=None, categorical=False) -> np.ndarray:
        """Reproject bands of a GeoTIFF onto the grid -> float32 [bands][h][w], NaN = no data."""
        with rasterio.open(path) as ds:
            idx = bands or list(range(1, ds.count + 1))
            out = np.full((len(idx), self.h, self.w), np.nan, dtype=np.float32)
            for k, b in enumerate(idx):
                src = ds.read(b).astype(np.float32)
                if ds.nodata is not None and not np.isnan(ds.nodata):
                    src[src == ds.nodata] = np.nan
                reproject(src, out[k], src_transform=ds.transform, src_crs=ds.crs, dst_transform=self.transform,
                          dst_crs=MERC, src_nodata=np.nan, dst_nodata=np.nan,
                          resampling=Resampling.nearest if categorical else Resampling.bilinear)
            return out


def read_layer(gpkg: Path, layer: str):
    """Read a GeoPackage layer -> list of (shapely geometry in EPSG:4326, properties dict)."""
    meta, _, geoms, fields = pyogrio.raw.read(gpkg, layer=layer)
    names = list(meta["fields"])
    crs = meta.get("crs") or "EPSG:4326"
    tr = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    out = []
    for i, wkb in enumerate(geoms):
        if wkb is None:
            continue
        g = shp_transform(tr.transform, from_wkb(wkb))
        props = {}
        for j, n in enumerate(names):
            v = fields[j][i]
            if isinstance(v, (np.floating, float)):
                v = None if math.isnan(float(v)) else round(float(v), 2)
            elif isinstance(v, np.integer):
                v = int(v)
            elif isinstance(v, (np.datetime64,)):
                v = str(v)[:10]
            elif v is not None and not isinstance(v, (str, bool)):
                v = str(v)
            if v in ("", "None", "nan"):
                v = None
            props[n] = v
        out.append((g, props))
    return out


def feature(geom, props, tol=None):
    g = geom.simplify(tol, preserve_topology=True) if tol else geom
    gj = mapping(g)
    return {"type": "Feature", "properties": props, "geometry": _round(gj)}


def _round(gj, nd=5):
    def r(c):
        if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
            return [round(c[0], nd), round(c[1], nd)]
        return [r(x) for x in c]
    return {"type": gj["type"], "coordinates": r(gj["coordinates"])}


def fc(features):
    return {"type": "FeatureCollection", "features": features}


# ----------------------------------------------------------------- build
def build(src: Path) -> tuple[dict, dict]:
    W = src / "wyniki"
    asc, desc, vert = W / "produkty/wznoszaca", W / "produkty/zstepujaca", W / "produkty/pionowy"
    grid = Grid(asc / "los_cum_okres_pary.tif")
    report = {"grid": f"{grid.w}x{grid.h} @ {grid.res:.1f} m (Web Mercator)", "bounds": grid.bounds}

    # --- time series (ascending orbit, LOS): 10 dates; missing pairs filled by the team with the pixel's mean rate
    with rasterio.open(asc / "los_cum_okres_pary.tif") as ds:
        # band descriptions are "20260618 [mm]" - complete, unlike the 'dates' tag which lacks the last date
        dates = [d.split()[0] for d in ds.descriptions]
    cum = grid.warp(asc / "los_cum_okres_pary.tif")
    E = cum.shape[0]
    dates = dates[:E]
    nodata = np.any(np.isnan(cum), axis=0)
    cum[:, nodata] = np.nan
    cum = np.round(cum)                                          # 1 mm precision is plenty (noise ~17 mm)

    coh = grid.warp(asc / "coherence_mean.tif")[0]
    coh_u8 = np.clip(np.round(coh * 250), 0, 250)
    coh_u8[nodata | np.isnan(coh)] = np.nan

    # --- monitoring windows (only their names; used by the risk popup "okna z ruchem")
    mon = json.loads((asc / "monitoring/monitoring.json").read_text(encoding="utf-8"))
    windows = [w["okno"] for w in mon["windows"]]

    # --- noise / threshold (team estimate of LOS noise over the period)
    dec = json.loads((vert / "dekompozycja.json").read_text(encoding="utf-8"))
    years = dec.get("lata", (len(dates) - 1) * 12 / 365.25)
    noise_mm = dec["orbita_wznoszaca"]["szum_los_mm_rok"] * years
    thr = 3 * noise_mm
    lat_mid = float(np.mean([grid.bounds[0][0], grid.bounds[1][0]]))
    px_km2 = (grid.res * math.cos(math.radians(lat_mid))) ** 2 / 1e6
    moving = [round(float(np.sum(np.abs(np.nan_to_num(cum[e])) >= thr)) * px_km2, 2) for e in range(E)]

    # --- anomaly zones: subsidence zones confirmed in both orbits
    gp_risk = W / "infrastruktura/rurociagi_ryzyko.gpkg"
    to_merc = Transformer.from_crs("EPSG:4326", MERC, always_xy=True)
    zones, no_px, never = [], 0, 0
    for k, (g, p) in enumerate(read_layer(gp_risk, "strefy_osiadania")):
        mask = rasterize([(shp_transform(to_merc.transform, g), 1)], out_shape=(grid.h, grid.w), transform=grid.transform,
                         all_touched=True, dtype=np.uint8).astype(bool) & ~nodata
        if not mask.any():
            no_px += 1
            continue
        vals = cum[:, mask]
        peak = -np.percentile(-vals, 95, axis=1)                 # strongest subsidence (5th percentile)
        crossed = np.nonzero(peak <= -thr)[0]
        first = int(crossed[0]) if len(crossed) else None
        if first is None:
            # confirmed in both orbits on the whole-period velocity, but the ascending LOS alone never passes 3 sigma:
            # it counts as detected when the whole record is available, i.e. on the last date
            never += 1
            first = E - 1
        flat = np.flatnonzero(mask)
        best = flat[np.argmin(cum[-1].ravel()[flat])]
        r, c = divmod(int(best), grid.w)
        zones.append({"id": f"z{k + 1}", "series": 0, "kind": "down", "type": "subsidence", "first_index": first,
                      "peak_latlon": [round(float(grid.lat[r, c]), 6), round(float(grid.lon[r, c]), 6)],
                      "peak_series": [round(float(v), 1) for v in peak],
                      "median_series": [round(float(v), 1) for v in np.median(vals, axis=1)],
                      "area_km2": p.get("pow_km2"), "v_up_mm_yr": p.get("v_med"), "pixels": int(mask.sum()),
                      "geometry": _round(mapping(g))})
    # Pins only for the most serious zones (area x subsidence rate); the rest are drawn as outlines,
    # otherwise ~100 pins would cover the whole map
    by_score = sorted(zones, key=lambda z: -(z["area_km2"] or 0) * abs(z["v_up_mm_yr"] or 0))
    for k, z in enumerate(by_score):
        z["pin"] = k < 20
    report.update({"zones_total": len(zones) + no_px, "zones_without_series": no_px, "zones_never_over_3sigma": never,
                   "first_dates": {dates[i]: sum(z["first_index"] == i for z in zones) for i in range(E)},
                   "noise_mm": round(noise_mm, 1), "threshold_mm": round(thr, 1)})

    # ================================================================ extension: what the case-study page shows
    # The page for this case study is built around three results of the analysis (side panel views):
    #   risk  - pipelines with a subsidence risk class
    #   zones - subsidence zones confirmed in both orbits (appear on the timeline)
    #   phase - phase analysis of the Tokal field crop (fringes at 20 m, contours, profiles)
    # Everything else from the results folder is left out on purpose (it cluttered the map).

    # vertical velocity from two orbits: not drawn, only quoted in the point popup
    layers = []
    vel_up = block_reduce(grid.warp(vert / "predkosc_pionowa.tif"), 2)
    layers.append({"key": "vel_up", "enc": "i16", "scale": 1.0, "bands": 1, "down": 2, "unit": "mm/rok",
                   "popup": "Ruch pionowy (2 orbity)", "data": pack(to_i16(vel_up, 1.0))})

    # --- pipelines: whole network (context, merged per pipeline and class) + at-risk fragments
    vectors = []
    groups = {}
    for g, p in read_layer(gp_risk, "odcinki"):
        groups.setdefault((p["obiekt_id"], p["klasa"]), []).append((g, p))
    net = []
    for (oid, klasa), items in groups.items():
        if klasa in ("wysokie", "średnie"):
            continue                                              # drawn by the at-risk layer
        merged = line_merge(unary_union([g for g, _ in items]))
        p0 = items[0][1]
        net.append(feature(merged, {"nazwa": p0["nazwa"], "typ": p0["typ"], "status": p0["status"], "klasa": klasa}, 0.0003))
    vectors.append({"key": "pipes_all", "style": "network", "data": fc(net)})
    risky = [feature(g, p, 0.00005) for g, p in read_layer(gp_risk, "rurociagi_zagrozone")]
    vectors.append({"key": "pipes_risk", "style": "risk", "data": fc(risky)})

    # --- profiles across the Tokal bowl and along the most threatened pipeline
    prof_meta = json.loads((W / "przekroje/przekroje.json").read_text(encoding="utf-8"))
    profs = []
    for g, p in read_layer(W / "przekroje/przekroje.gpkg", "przekroje"):
        name = p["nazwa"]
        rows = list(csv.reader((W / f"przekroje/przekroj_{name}.csv").read_text(encoding="utf-8").splitlines()))
        head, body = rows[0], rows[1:]
        vals = [[None if r[k + 1] in ("", "nan") else round(float(r[k + 1]), 1) for r in body] for k in range(len(head) - 1)]
        m = prof_meta.get(name, {})
        profs.append(feature(g, {"nazwa": name, "dates": head[1:], "distance_m": [round(float(r[0])) for r in body],
                                 "values_mm": vals, "crossings": m.get("przeciecia_infrastruktury", []),
                                 "max_mm": m.get("max_przemieszczenie_mm")}))
    vectors.append({"key": "profiles", "style": "profile", "data": fc(profs)})

    # --- phase analysis crop (Tokal): wrapped fringes + displacement at 20 m, contours per date
    QF = W / "qgis_fazy"
    qpar = json.loads((QF / "parametry.json").read_text(encoding="utf-8"))
    with rasterio.open(QF / "tokal_prazki.tif") as ds:
        fr_crs, fr_bounds, fr_w, fr_h = ds.crs, ds.bounds, ds.width, ds.height
    tr, fw, fh = calculate_default_transform(fr_crs, MERC, fr_w, fr_h, *fr_bounds)
    fgrid = Grid.__new__(Grid)                                    # same helper, own (finer) grid for the crop
    res = abs(tr.a)
    fgrid.res, fgrid.w, fgrid.h, fgrid.x0, fgrid.y1 = res, fw, fh, tr.c, tr.f
    fgrid.transform = rasterio.Affine(res, 0, tr.c, 0, -res, tr.f)
    to_ll = Transformer.from_crs(MERC, "EPSG:4326", always_xy=True)
    w_lon, n_lat = to_ll.transform(tr.c, tr.f)
    e_lon, s_lat = to_ll.transform(tr.c + fw * res, tr.f - fh * res)
    wrapped = fgrid.warp(QF / "tokal_prazki.tif", categorical=True)          # 0..121 mm, nearest keeps the wrap edges
    disp = fgrid.warp(QF / "tokal_przemieszczenie.tif")                      # mm LOS, + toward satellite
    cyc = qpar.get("pol_dlugosci_fali_mm", HALF_WAVELENGTH_MM)
    wrapped_u8 = np.where(np.isnan(wrapped), np.nan, np.clip(np.round(wrapped / cyc * 250), 0, 250))
    tok_dates = [d.replace("-", "") for d in qpar["tokal"]["daty"]]
    kont = [feature(g, {"data": p["data"].replace("-", ""), "poziom_mm": p["poziom_mm"], "etykieta": p["etykieta"]}, 0.00003)
            for g, p in read_layer(QF / "tokal_kontury.gpkg", "kontury")]
    last = disp[-1]
    focus = {
        "key": "tokal", "name": "Złoże Tokal",
        "bounds": [[s_lat, w_lon], [n_lat, e_lon]], "mercator": {"west": tr.c, "north": tr.f, "pixel": res},
        "w": fw, "h": fh, "bands": len(tok_dates), "dates": tok_dates, "ref_date": qpar["tokal"]["data_odniesienia"].replace("-", ""),
        "cycle_mm": cyc, "contour_step_mm": qpar["tokal"]["kontury_co_mm"],
        "wrapped": pack(to_u8(wrapped_u8)), "disp": pack(to_i16(disp, 0.1)),
        "contours": fc(kont), "max_mm": round(float(np.nanmin(last)), 1),
        "pixel_m": 20, "orbit": "orbita wznosząca (tor 62)",
    }

    # --- side-panel texts and numbers (all from the team's summaries)
    summ = json.loads((W / "infrastruktura/rurociagi_podsumowanie.json").read_text(encoding="utf-8"))
    thr_pipe = json.loads((W / "infrastruktura/parametry.json").read_text(encoding="utf-8"))["progi_efektywne"]
    top = [{k: o.get(k) for k in ("obiekt_id", "nazwa", "typ", "status", "srednica_mm", "km_wysokie", "km_średnie",
                                   "v_p95_mm_rok", "gradient_max", "powod_najgorszy")} for o in summ["obiekty_najbardziej_zagrozone"][:8]]
    kmc, cov = summ["km_wg_klasy"], summ["pokrycie_danymi"]
    act = summ["km_wg_statusu_i_klasy"]["czynny"]
    zones_info = summ["strefy_osiadania"]
    pm = prof_meta.get("os_krotka", {})
    views = {
        "default": "risk",
        "risk": {"title": "Zagrożone rurociągi",
                 "lead": "Odcinki sieci (baza OGIM), pod którymi grunt osiada szybko albo nierówno. Klasa wynika z ruchu pionowego "
                         "z dwóch orbit NISAR; ocena co 100 m.",
                 "stats": [[f"{kmc['wysokie']:.1f} km".replace(".", ","), "wysokie zagrożenie"],
                           [f"{kmc['średnie']:.1f} km".replace(".", ","), "średnie zagrożenie"],
                           [str(summ.get("przeciecia_granicy_strefy", 0)), "przecięć granicy strefy osiadania"],
                           [f"{act['wysokie']:.1f} km".replace(".", ","), "czynnych rurociągów w klasie wysokiej"]],
                 "note": f"Sieć ma {summ['km']:,.0f}".replace(",", " ") + f" km; dane NISAR są dla {cov['udzial'] * 100:.0f}% długości "
                         "(reszta: mokradła, woda). "
                         f"Klasa wysoka: osiadanie ≥ {thr_pipe['v_high']:.0f} mm/rok albo nierówne osiadanie ≥ {thr_pipe['g_high']:.0f} mm/100 m/rok.",
                 "top": top},
        "zones": {"title": "Strefy osiadania",
                  "lead": f"{zones_info['liczba']} stref osiadania widocznych w obu orbitach (łącznie "
                          f"{zones_info['pow_km2']:.1f}".replace(".", ",") + " km²). "
                          "Pojawiają się na osi czasu w dniu, w którym ruch przekroczył 3× szum pomiaru.",
                  "note": "Kliknij strefę albo pinezkę, żeby zobaczyć historię ruchu w tym miejscu. Pinezki: 20 stref o największym "
                          "osiadaniu × powierzchni; pozostałe są obrysami."},
        "phase": {"title": "Analiza fazowa: złoże Tokal",
                  "lead": "Wycinek 10 × 8 km nad największą niecką osiadania. Kolorowe pierścienie to prążki interferometryczne: "
                          f"każdy pełny cykl barw to {cyc:.0f} mm ruchu w linii widzenia satelity. Im więcej pierścieni, tym głębsza niecka.",
                  "stats": [[f"{focus['max_mm']:.0f} mm".replace("-", "−"), "najgłębiej w niecce (16.10)"],
                            [f"{qpar['tokal']['kontury_co_mm']:.0f} mm", "odstęp białych konturów"]],
                  "note": "Naciśnij Odtwórz na osi czasu: pierścienie narastają z każdym przelotem (co 12 dni). "
                          f"Przekrój przecina rurociąg {pm.get('przeciecia_infrastruktury', [{}])[0].get('obiekt', '')} – kliknij linię przekroju, "
                          "żeby zobaczyć profil niecki w czasie. Pole wygładzone (~80 m) i interpolowane do 20 m."},
    }
    display = {"coh_min": 0.35, "min_mm": 25}   # hide low-quality pixels and motion within the noise (transparent)

    package = {
        "grid": {"width": grid.w, "height": grid.h, "bounds": grid.bounds,
                 "mercator": {"west": grid.x0, "north": grid.y1, "pixel": grid.res},
                 "pixel_m": round(grid.res * math.cos(math.radians(lat_mid))), "dates": dates, "windows": windows,
                 "series": [{"period": f"{dates[0]}_{dates[-1]}", "start": 0, "end": E - 1, "days": round(years * 365.25),
                             "step_days": 12, "noise_mm": round(noise_mm, 1), "threshold_mm": round(thr, 1),
                             "moving_km2_by_date": moving, "zones": len(zones)}],
                 "orbit": "wznosząca (tor 62, ramka 11), ruch w linii widzenia satelity"},
        "params": {"threshold_rule": "3 x szum LOS okresu (dekompozycja.json)"},
        "payload": {"cum": pack(to_i16(cum, 0.1)), "coh": pack(to_u8(coh_u8))},
        "zones": zones, "pipes": [], "contours": [], "alerts": [],
        "layers": layers, "vectors": vectors, "focus": focus, "views": views, "display": display,
        "config": {"pipelines_label": "OGIM v1.1",
                   "sources": "NISAR L2 GUNW (NASA/ISRO, ASF DAAC) · OGIM v1.1 (EDF/MethaneSAT)"},
    }
    sizes = {k: len(json.dumps(v)) // 1000 for k, v in package.items()}
    sizes["vectors_each"] = {v["key"]: len(json.dumps(v["data"])) // 1000 for v in vectors}
    sizes["focus_each"] = {k: len(json.dumps(v)) // 1000 for k, v in focus.items()}
    report["size_kb"] = sizes
    return package, report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path, help="team results folder (with wyniki/ and 'dane do tabasco/')")
    ap.add_argument("--id", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--region")
    ap.add_argument("--hazard")
    ap.add_argument("--summary")
    ap.add_argument("--order", type=int, default=99)
    ap.add_argument("--dry-run", action="store_true", help="only print the report")
    args = ap.parse_args()

    package, report = build(args.src)
    print(json.dumps(report, indent=1, ensure_ascii=False))
    if args.dry_run:
        return
    b = package["grid"]["bounds"]
    meta = {k: v for k, v in {"name": args.name, "region": args.region, "hazard": args.hazard, "summary": args.summary,
                              "order": args.order, "view": (tuple(b[0]), tuple(b[1]))}.items() if v is not None}
    package["config"].update({k: meta[k] for k in ("name", "region", "hazard", "summary") if k in meta})
    package["config"]["view"] = b
    registry = get_location_registry()
    if registry.get_location(args.id) is None:
        registry.create_location(Location(id=args.id, **meta))
        action = "created"
    else:
        registry.update_location(args.id, LocationUpdate(**meta).model_dump(exclude_unset=True))
        action = "updated"
    content = parse_package(json.dumps(package, ensure_ascii=False).encode("utf-8"), args.id)
    registry.save_data(args.id, content)
    print(f"{args.id}: {action}, data {len(content) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
