"""Import a case study from the team's QGIS results folder (e.g. Dane/TABASCO/tabasco_front).

The folder holds raw analysis results: GeoTIFFs in UTM (both orbits, vertical decomposition,
per-pair interferograms, monitoring), GeoPackages (pipelines with risk classes, zones, alerts,
profiles) and CSV/JSON (profiles over time, GNSS). This script reprojects everything to one
Web Mercator grid and writes the data package the page draws:

  core (same as build_location.py data.js, so the existing page works as is)
    grid, payload.cum / coh / vwin, zones (with peak per date and first date), contours
  extensions (optional keys, only used when present)
    layers   - extra rasters: whole-period results, monitoring classes, per-pair phase
    vectors  - GeoJSON layers: pipelines, facilities, water anomalies, alerts, profiles, GNSS

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

    # --- monitoring windows (velocity in each 36-day window, change classes)
    mon = json.loads((asc / "monitoring/monitoring.json").read_text(encoding="utf-8"))
    windows = [w["okno"] for w in mon["windows"]]
    vwin = grid.warp(asc / "monitoring/predkosc_okna.tif")
    classes = grid.warp(asc / "monitoring/klasy_zmian.tif", categorical=True)

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

    # --- contours of the ascending displacement (every 50 mm, at the window end dates)
    contours = [feature(g, {"data": p["data"][:10], "poziom_mm": p["poziom_mm"]}, 0.0002)
                for g, p in read_layer(asc / "monitoring/strefy.gpkg", "kontury")]

    # ================================================================ extension: extra rasters
    D2 = 2                                                       # whole-period / window / pair layers at 2x pixel
    layers = []

    def add_layer(key, arr, **meta):
        enc = meta.pop("enc", "i16")
        scale = meta.pop("scale", 1.0)
        data = to_i16(arr, scale) if enc == "i16" else to_u8(arr)
        layers.append({"key": key, "enc": enc, "scale": scale, "bands": int(arr.shape[0]), "down": meta.pop("down", 1),
                       "data": pack(data), **meta})

    def d2(a, how="mean"):
        return block_reduce(a, D2, how)

    add_layer("vel_up", d2(grid.warp(vert / "predkosc_pionowa.tif")), down=D2, group="Wynik za cały okres (obie orbity)",
              label="Ruch pionowy [mm/rok]", unit="mm/rok", ramp="div", range=200, popup="Ruch pionowy (2 orbity)",
              ends=["osiadanie", "wypiętrzanie"], note="z dwóch orbit, 18.06–16.10.2026; niepewność ok. ±57 mm/rok")
    add_layer("disp_up", d2(grid.warp(vert / "osiadanie_okres.tif")), down=D2, group="Wynik za cały okres (obie orbity)",
              label="Przemieszczenie pionowe [mm]", unit="mm", ramp="div", range=80, popup="Przemieszczenie pionowe",
              ends=["osiadanie", "wypiętrzanie"], note="suma za 18.06–16.10.2026, z dwóch orbit")
    add_layer("vel_east", d2(grid.warp(vert / "predkosc_wschodnia.tif")), down=D2, group="Wynik za cały okres (obie orbity)",
              label="Ruch wschód–zachód [mm/rok]", unit="mm/rok", ramp="puor", range=150, popup="Ruch wschód–zachód",
              ends=["na zachód", "na wschód"], note="tylko tam, gdzie są obie orbity")
    add_layer("vel_asc", d2(grid.warp(asc / "los_velocity_okres_pary.tif")), down=D2, group="Wynik za cały okres (obie orbity)",
              label="Prędkość LOS – orbita wznosząca [mm/rok]", unit="mm/rok", ramp="div", range=150,
              ends=["od satelity", "do satelity"], note="tor 62, ramka 11; ruch w linii widzenia")
    add_layer("vel_desc", d2(grid.warp(desc / "los_velocity_okres_pary.tif")), down=D2, group="Wynik za cały okres (obie orbity)",
              label="Prędkość LOS – orbita zstępująca [mm/rok]", unit="mm/rok", ramp="div", range=150,
              ends=["od satelity", "do satelity"], note="tor 156, ramka 79; ruch w linii widzenia")
    add_layer("orbits", d2(grid.warp(vert / "liczba_orbit.tif", categorical=True), "mode"), down=D2, enc="u8",
              group="Jakość i tło", label="Liczba orbit z danymi", ramp="cat",
              classes={"0": ["brak danych", "#00000000"], "1": ["jedna orbita (zakładamy ruch pionowy)", "#fdae61"],
                       "2": ["obie orbity (ruch pionowy i wschód–zachód)", "#2c7bb6"]},
              note="pewność rozkładu ruchu na składowe")
    lc_path = src / "dane do tabasco/pokrycie_terenu/esa_worldcover_2021_tabasco.tif"
    lc = d2(grid.warp(lc_path, categorical=True), "mode")
    lc[lc == 0] = np.nan
    add_layer("landcover", lc, down=D2, enc="u8", group="Jakość i tło", label="Pokrycie terenu (ESA WorldCover)", ramp="cat",
              classes={"10": ["las", "#5f8f4e"], "20": ["krzewy", "#c9b16b"], "30": ["trawy, pastwiska", "#d8d39a"],
                       "40": ["uprawy", "#e3b7d8"], "50": ["zabudowa", "#c96a5f"], "60": ["teren odkryty", "#bdbdbd"],
                       "80": ["woda", "#7fb2d9"], "90": ["mokradła", "#79bdb9"], "95": ["namorzyny", "#6fbf95"]},
              note="woda, mokradła i namorzyny są zamaskowane: radar mierzy tam poziom wody, nie grunt")
    add_layer("vwin", d2(vwin), down=D2, time="window", group="Oś czasu (orbita wznosząca)",
              label="Tempo ruchu w oknach 36 dni [mm/rok]", unit="mm/rok", ramp="div", range=250,
              ends=["od satelity", "do satelity"], note="prędkość z 3 kolejnych par kończących się w wybranej dacie")
    add_layer("classes", d2(classes, "mode"), down=D2, enc="u8", time="window", group="Oś czasu (orbita wznosząca)",
              label="Wykrywanie zmian (okna 36 dni)", ramp="cat",
              classes={"0": ["brak istotnego ruchu", "#9aa3ab55"], "1": ["ruch stały", "#e3a21a"],
                       "2": ["przyspieszenie", "#d7301f"], "3": ["nowa strefa ruchu", "#7b3294"], "4": ["spowolnienie", "#2166ac"]},
              note="każde okno: 3 kolejne pary porównane z poprzednimi 72 dniami")

    # per-pair interferometric phase: the raw 12-day interferograms (with ionosphere and atmosphere), wrapped
    pair_files = sorted((W / "wycinki_gunw/obszar_aoi").glob("062A_011_*.tif"))
    pairs, pair_names = [], []
    for f in pair_files:
        ph = grid.warp(f, bands=[1])[0]
        cohp = grid.warp(f, bands=[2])[0]
        water = grid.warp(f, bands=[11], categorical=True)[0]
        ph[(water == 1) | (cohp < 0.15)] = np.nan
        z = np.exp(1j * ph.astype(np.complex64))                  # average phasors, then take the angle
        re, im = d2(np.real(z)[None])[0], d2(np.imag(z)[None])[0]
        wrapped = (np.angle(re + 1j * im) % (2 * np.pi)) / (2 * np.pi) * 254
        wrapped[np.isnan(re)] = np.nan
        pairs.append(wrapped)
        a, b = f.stem.split("_")[-2:]
        pair_names.append(f"{a}-{b}")
    add_layer("pairs", np.stack(pairs), down=D2, enc="u8", time="pair", group="Oś czasu (orbita wznosząca)",
              label="Interferogram pary 12-dniowej (surowa faza)", ramp="cyclic", pairs=pair_names,
              note="faza prosto z satelity: ruch + jonosfera + atmosfera; 1 cykl barw = 121 mm")

    # ================================================================ extension: vectors
    risk_col = {"wysokie": "#c62a22", "średnie": "#e3871f", "niskie": "#4a5560", "brak danych": "#98a3ab"}
    vectors = []

    # whole pipeline network: 100 m segments merged per pipeline and risk class
    segs = read_layer(gp_risk, "odcinki")
    groups = {}
    for g, p in segs:
        groups.setdefault((p["obiekt_id"], p["klasa"]), []).append((g, p))
    net = []
    for (oid, klasa), items in groups.items():
        merged = line_merge(unary_union([g for g, _ in items]))
        p0 = items[0][1]
        net.append(feature(merged, {"nazwa": p0["nazwa"], "typ": p0["typ"], "status": p0["status"],
                                    "srednica_mm": p0["srednica_mm"], "klasa": klasa,
                                    "km": round(sum(p.get("dl_m") or 0 for _, p in items) / 1000, 2)}, 0.00015))
    vectors.append({"key": "pipes_all", "label": "Sieć rurociągów (OGIM)", "kind": "line", "on": False,
                    "style": "risk", "data": fc(net)})
    risky = [feature(g, p, 0.00005) for g, p in read_layer(gp_risk, "rurociagi_zagrozone")]
    vectors.append({"key": "pipes_risk", "label": "Rurociągi zagrożone osiadaniem", "kind": "line", "on": True,
                    "style": "risk", "data": fc(risky)})
    vectors.append({"key": "facilities", "label": "Obiekty sieci (tłocznie, baterie, terminal)", "kind": "point", "on": False,
                    "style": "risk", "data": fc([feature(g, p) for g, p in read_layer(gp_risk, "obiekty_sieci")])})
    vectors.append({"key": "water", "label": "Pozorne wypiętrzanie – prawdopodobnie woda", "kind": "polygon", "on": False,
                    "style": "water", "data": fc([feature(g, p, 0.0001) for g, p in read_layer(gp_risk, "anomalie_wody")])})
    alerts = [feature(g, {**p, "data": (p.get("data") or "")[:10]}, 0.0001) for g, p in read_layer(asc / "monitoring/strefy.gpkg", "alerty")]
    vectors.append({"key": "alerts", "label": "Alerty monitoringu (w wybranym oknie)", "kind": "polygon", "on": True,
                    "style": "alert", "time": "date", "data": fc(alerts)})

    # profiles: lines + values for every date + crossings with infrastructure
    prof_meta = json.loads((W / "przekroje/przekroje.json").read_text(encoding="utf-8"))
    profs = []
    for g, p in read_layer(W / "przekroje/przekroje.gpkg", "przekroje"):
        name = p["nazwa"]
        rows = list(csv.reader((W / f"przekroje/przekroj_{name}.csv").read_text(encoding="utf-8").splitlines()))
        head, body = rows[0], rows[1:]
        dist = [round(float(r[0])) for r in body]
        vals = [[None if r[k + 1] in ("", "nan") else round(float(r[k + 1]), 1) for r in body] for k in range(len(head) - 1)]
        m = prof_meta.get(name, {})
        profs.append(feature(g, {"nazwa": name, "dates": head[1:], "distance_m": dist, "values_mm": vals,
                                 "crossings": m.get("przeciecia_infrastruktury", []), "max_mm": m.get("max_przemieszczenie_mm")}))
    vectors.append({"key": "profiles", "label": "Przekroje przez nieckę (kliknij linię)", "kind": "line", "on": True,
                    "style": "profile", "data": fc(profs)})

    # GNSS VIL2: comparison + vertical series (weekly means to keep it light)
    gn = json.loads((W / "gnss/vil2_porownanie.json").read_text(encoding="utf-8"))
    rows = list(csv.DictReader((W / "gnss/vil2_enu.csv").read_text(encoding="utf-8").splitlines()))
    yrs = np.array([float(r["rok"]) for r in rows])
    up = np.array([float(r["pion_mm"]) for r in rows])
    bins = np.floor(yrs * 52).astype(int)
    series = [[round(float(yrs[bins == b].mean()), 3), round(float(up[bins == b].mean()), 1)] for b in np.unique(bins)]
    gnss = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(gn["lon"], 6), round(gn["lat"], 6)]},
            "properties": {**{k: gn[k] for k in ("stacja", "zrodlo", "wniosek", "niepewnosc_nisar_pionowa_mm_rok")},
                           "tempo": gn["tempo_mm_rok"], "nisar": gn["nisar_w_miejscu_stacji_mm_rok"], "series_up": series}}
    vectors.append({"key": "gnss", "label": "Stacja GNSS VIL2 (kontrola pomiaru)", "kind": "point", "on": True,
                    "style": "gnss", "data": fc([gnss])})

    package = {
        "grid": {"width": grid.w, "height": grid.h, "bounds": grid.bounds,
                 "mercator": {"west": grid.x0, "north": grid.y1, "pixel": grid.res},
                 "pixel_m": round(grid.res * math.cos(math.radians(lat_mid))), "dates": dates, "windows": windows,
                 "fringe_cycle_mm": HALF_WAVELENGTH_MM, "contour_step_mm": 50,
                 "series": [{"period": f"{dates[0]}_{dates[-1]}", "start": 0, "end": E - 1, "days": round(years * 365.25),
                             "step_days": 12, "noise_mm": round(noise_mm, 1), "threshold_mm": round(thr, 1),
                             "moving_km2_by_date": moving, "zones": len(zones)}],
                 "orbit": "wznosząca (tor 62, ramka 11), ruch w linii widzenia satelity"},
        "params": {"threshold_rule": "3 x szum LOS okresu (dekompozycja.json)"},
        "payload": {"cum": pack(to_i16(cum, 0.1)), "coh": pack(to_u8(coh_u8))},
        "zones": zones, "pipes": [], "contours": contours, "alerts": [],
        "layers": layers, "vectors": vectors,
        "config": {"pipelines_label": "OGIM v1.1",
                   "sources": "NISAR L2 GUNW (NASA/ISRO, ASF DAAC) · OGIM v1.1 (EDF/MethaneSAT) · ESA WorldCover 2021 (CC-BY 4.0) · GNSS VIL2: Nevada Geodetic Laboratory"},
    }
    sizes = {k: len(json.dumps(v)) // 1000 for k, v in package.items()}
    sizes["layers_each"] = {l["key"]: len(l["data"]) // 1000 for l in layers}
    sizes["vectors_each"] = {v["key"]: len(json.dumps(v["data"])) // 1000 for v in vectors}
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
