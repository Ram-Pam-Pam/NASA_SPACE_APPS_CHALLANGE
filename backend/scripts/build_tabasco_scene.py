"""Fill the Tabasco 3D/2D model (Dane/model3d_tabasco_mexico/data) with the results of our InSAR analysis.

The model itself (photo + terrain) comes from build_scene3d.py. This script adds, from the team's QGIS
results folder (Dane/TABASCO/tabasco_front), everything the model page (frontend/view3d.html) shows for Tabasco:

  pipelines.geojson   pipelines of the OGIM network with a risk class (wysokie / średnie / niskie / brak danych);
                      the at-risk fragments carry the reason, the ground motion under them and its series
  hazards.geojson     102 subsidence zones confirmed in both orbits
  places.geojson      network facilities (compressor stations, gas plants, batteries, terminal) as labels
  timeline/frame_XX   cumulative ground motion since the first date (red = down, blue = up, clear = under the
                      threshold), one PNG per date, same scheme as build_timeline.py; the 20 most serious
                      zones become numbered markers that appear on the date they first pass the threshold
  phase/frame_XX      phase analysis of the Tokal field: interferometric fringes (one colour cycle = 121 mm)
                      of the 20 m crop, one PNG per date, plus white contours every 20 mm
  extras.json         profile lines with their values for every date, the GNSS station VIL2
  meta.json           "layers" counts, "timeline", "phase", "summary", "extras"

Run from the backend/ folder (needs numpy, rasterio, pyogrio, shapely, pyproj: pip install -r requirements-tools.txt):
    python scripts/build_tabasco_scene.py "C:\\...\\Dane\\TABASCO\\tabasco_front" ..\\Dane\\model3d_tabasco_mexico\\data
"""
import csv
import json
import math
import sys
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.warp import Resampling, calculate_default_transform, reproject
from shapely import line_merge, unary_union
from shapely.geometry import mapping
from shapely.ops import transform as shp_transform

sys.path.insert(0, str(Path(__file__).resolve().parent))
from import_team_results import MERC, Grid, fc, feature, read_layer  # noqa: E402

# --- display parameters
PROFILE_TITLES = {"os_dluga": "Przekrój: oś długa złoża Tokal", "os_krotka": "Przekrój: oś krótka złoża Tokal"}
NOISE_MM = 17.3           # LOS noise over the whole period (team estimate: 52.6 mm/yr x the length of the record)
THRESHOLD_MM = 35.0       # ground-motion changes smaller than this are transparent (about 2 x the measurement noise)
FULL_COLOUR_MM = 100.0    # a change this big (or bigger) gets the strongest colour
COH_MIN = 0.35            # pixels with a lower mean coherence are hidden (unreliable phase)
ANOMALY_COUNT = 20        # most serious subsidence zones get a name and a label
PIPE_MARKERS = 10         # strongest subsidence under the pipelines: numbered markers on the timeline
MARKER_GAP_M = 1500       # markers at least this far apart (neighbouring 100 m segments are one place)
POINT_STEP = 3            # the click-on-map grid is 3 x coarser than the analysis grid (240 m)
HAZARD_COLORS = ["#9b5de5", "#d0b3ff"]   # subsidence zones in violet: edge, centre (red / orange belong to the pipelines)
DOWN_RGB, UP_RGB = (214, 39, 40), (31, 104, 196)
CYCLE_MM = 120.98         # half the radar wavelength: one fringe

MEDIUM = {"sour gas": "Gaz kwaśny", "sour vapors": "Opary kwaśne", "dried": "Gaz osuszony", "oil": "Ropa", "maya": "Ropa (Maya)",
          "oil and gas": "Ropa i gaz", "sweet gas": "Gaz słodki"}


def write_png(path: Path, rgba: np.ndarray) -> None:
    """rgba: [h][w][4] uint8."""
    path.parent.mkdir(parents=True, exist_ok=True)
    h, w = rgba.shape[:2]
    with rasterio.open(path, "w", driver="PNG", width=w, height=h, count=4, dtype="uint8") as dst:
        dst.write(np.moveaxis(rgba, 2, 0))
    for aux in path.parent.glob("*.aux.xml"):
        aux.unlink()


def merc_y(lat: float) -> float:
    return 6378137.0 * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def to_lat_rows(arr: np.ndarray, grid: Grid) -> np.ndarray:
    """Source rows are equal steps of Web Mercator y; the scene wants equal steps of latitude."""
    h = arr.shape[-2]
    (s_lat, _), (n_lat, _) = grid.bounds
    lats = n_lat - (np.arange(h) + 0.5) / h * (n_lat - s_lat)
    y = np.array([merc_y(v) for v in lats])
    rows = np.clip(((merc_y(n_lat) - y) / (merc_y(n_lat) - merc_y(s_lat)) * h).astype(int), 0, h - 1)
    return arr[..., rows, :]


def hue_rgb(frac: np.ndarray) -> np.ndarray:
    """Fringe colour: cyclic hue, 0 = cyan (the team's convention); vectorised HSL (s=.85, l=.55) -> RGB uint8."""
    h = (180 + frac * 360) % 360
    s, l = 0.85, 0.55
    a = s * min(l, 1 - l)
    out = []
    for n in (0, 8, 4):
        k = (n + h / 30) % 12
        out.append(255 * (l - a * np.clip(np.minimum(k - 3, 9 - k), -1, 1)))
    return np.stack(out, axis=-1).round().astype(np.uint8)


def build(src: Path, scene: Path) -> None:
    W = src / "wyniki"
    asc, vert = W / "produkty/wznoszaca", W / "produkty/pionowy"
    meta_path = scene / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    gp_risk = W / "infrastruktura/rurociagi_ryzyko.gpkg"

    # ------------------------------------------------------------------ ground motion (ascending orbit, LOS)
    grid = Grid(asc / "los_cum_okres_pary.tif")
    with rasterio.open(asc / "los_cum_okres_pary.tif") as ds:
        dates = [d.split()[0] for d in ds.descriptions]       # "20260618 [mm]" -> complete list of dates
    cum = grid.warp(asc / "los_cum_okres_pary.tif")            # [E][h][w] mm, NaN = no series
    E = cum.shape[0]
    coh = grid.warp(asc / "coherence_mean.tif")[0]
    valid = ~np.any(np.isnan(cum), axis=0) & (coh >= COH_MIN)
    cum[:, ~valid] = np.nan
    (s_lat, w_lon), (n_lat, e_lon) = grid.bounds
    print(f"grid {grid.w}x{grid.h}, {E} dates, valid pixels {valid.mean():.0%}")

    # ------------------------------------------------------------------ timeline frames
    out_dir = scene / "timeline"
    for e in range(E):
        mm = to_lat_rows(cum[e], grid)
        mag = np.abs(mm)
        strength = np.clip((mag - THRESHOLD_MM) / (FULL_COLOUR_MM - THRESHOLD_MM), 0, 1)
        alpha = np.where(np.isfinite(mm) & (mag >= THRESHOLD_MM), 0.35 + 0.55 * strength, 0)
        rgba = np.zeros(mm.shape + (4,), dtype=np.uint8)
        rgba[..., :3] = np.where((mm < 0)[..., None], np.array(DOWN_RGB), np.array(UP_RGB))
        rgba[..., 3] = np.round(alpha * 255)
        write_png(out_dir / f"frame_{e:02d}.png", rgba)
    print(f"timeline frames -> {out_dir}")

    # ------------------------------------------------------------------ subsidence zones -> hazards + anomalies
    to_merc = Transformer.from_crs("EPSG:4326", MERC, always_xy=True)
    zones = []
    for k, (g, p) in enumerate(read_layer(gp_risk, "strefy_osiadania")):
        mask = rasterize([(shp_transform(to_merc.transform, g), 1)], out_shape=(grid.h, grid.w), transform=grid.transform,
                         all_touched=True, dtype=np.uint8).astype(bool) & valid
        zone = {"k": k, "geom": g, "area_ha": round((p.get("pow_km2") or 0) * 100), "v": p.get("v_med"), "mask": mask}
        if mask.any():
            vals = cum[:, mask]
            peak = np.percentile(vals, 5, axis=1)               # strongest subsidence in the zone (robust)
            # the zone "appears" on the first date from which it STAYS past the threshold; a one-off jump in a
            # single interferogram does not count (such outliers would make zones pop up after 12 days)
            over = peak <= -THRESHOLD_MM
            stays = [e for e in range(E) if over[e:].all()]
            flat = np.flatnonzero(mask)
            r, c = divmod(int(flat[np.argmin(cum[-1].ravel()[flat])]), grid.w)
            zone.update(first=stays[0] if stays else E - 1, peak=peak, median=np.median(vals, axis=1),
                        lat=float(grid.lat[r, c]), lon=float(grid.lon[r, c]))
        zones.append(zone)
    scored = sorted((z for z in zones if "peak" in z), key=lambda z: -(z["area_ha"] or 0) * abs(z["v"] or 0))
    top = {z["k"]: i + 1 for i, z in enumerate(scored[:ANOMALY_COUNT])}
    # name the biggest ones; the field's own name is known from the team's README (Tokal)
    tokal = scored[0] if scored else None
    hazards = []
    for z in zones:
        rank = top.get(z["k"])
        v = z["v"]
        props = {"kind": "subsidence", "source": "NISAR L2 GUNW, dwie orbity (wznosząca i zstępująca)",
                 "note": f"Strefa osiadania potwierdzona w obu orbitach: ok. {z['area_ha']} ha"
                         + (f", ruch pionowy {v:.0f} mm/rok (mediana strefy)" if v is not None else "")}
        if rank:
            props["name"] = "Niecka osiadania Tokal" if z is tokal else f"Strefa osiadania {rank}"
            props["rank"] = rank
        else:
            props["name"] = "Strefa osiadania"
        props["label"] = False                                    # no zone labels on the map (15.11: the map is about the pipelines); name stays in the tooltip
        hazards.append({"type": "Feature", "properties": props, "geometry": mapping(z["geom"])})
    (scene / "hazards.geojson").write_text(json.dumps(fc([feature(z["geom"], h["properties"]) for z, h in zip(zones, hazards)]), ensure_ascii=False), encoding="utf-8")
    anomalies = []
    for z in sorted((z for z in scored if z["k"] in top), key=lambda z: top[z["k"]]):
        anomalies.append({
            "rank": top[z["k"]], "direction": "down", "lon": round(z["lon"], 6), "lat": round(z["lat"], 6),
            "area_ha": z["area_ha"], "peak_mm": round(float(-z["peak"][-1])), "peak_saturated": False,
            "median_mm": round(float(z["median"][-1])), "first_date": dates[z["first"]],
            "series_mm": [round(float(v)) for v in z["median"]],
            "note": f"Ruch pionowy z dwóch orbit: {z['v']:.0f} mm/rok" if z["v"] is not None else None,
        })
    print(f"hazards: {len(zones)} zones")

    # ------------------------------------------------------------------ pipelines
    segs = read_layer(gp_risk, "odcinki")
    groups = {}
    for g, p in segs:
        groups.setdefault((p["obiekt_id"], p["klasa"]), []).append((g, p))
    feats = []
    for (oid, klasa), items in groups.items():
        if klasa in ("wysokie", "średnie"):
            continue                                              # drawn from the at-risk layer below
        merged = line_merge(unary_union([g for g, _ in items]))
        p0 = items[0][1]
        feats.append(feature(merged, {"name": p0["nazwa"], "kind": p0["typ"], "status": p0["status"], "medium": MEDIUM.get(p0["podtyp"], p0["podtyp"]),
                                      "diameter_in": round((p0["srednica_mm"] or 0) / 25.4) or None, "risk": klasa, "flat": True}, 0.0003))
    n_net = len(feats)
    risky = []
    for g, p in read_layer(gp_risk, "rurociagi_zagrozone"):
        # ground motion under the fragment: the pixel (within the corridor) that moved most by the last date
        mask = rasterize([(shp_transform(to_merc.transform, g), 1)], out_shape=(grid.h, grid.w), transform=grid.transform, all_touched=True, dtype=np.uint8).astype(bool) & valid
        series = None
        if mask.any():
            flat = np.flatnonzero(mask)
            r, c = divmod(int(flat[np.argmin(cum[-1].ravel()[flat])]), grid.w)
            series = [round(float(v)) for v in cum[:, r, c]]
        risky.append(feature(g, {"name": p["nazwa"], "kind": p["typ"], "status": p["status"], "medium": MEDIUM.get(p["podtyp"], p["podtyp"]),
                                 "diameter_in": round((p["srednica_mm"] or 0) / 25.4) or None, "risk": p["klasa"], "reason": p["powod"],
                                 "v": p["v"], "gradient": p["gradient"], "przem_mm": p["przem_mm"], "length_m": p["dlugosc_m"],
                                 "dist_m": p["odl_m"], "windows": p["okna_ruch"], "confidence": p["pewnosc"], "series_mm": series}, 0.00005))
    (scene / "pipelines.geojson").write_text(json.dumps(fc(feats + risky), ensure_ascii=False), encoding="utf-8")
    print(f"pipelines: {n_net} network + {len(risky)} at risk")

    # ------------------------------------------------------------------ timeline markers = strongest subsidence UNDER the pipelines
    # (not the strongest anomalies anywhere): at-risk segments by vertical rate, one marker per place
    anomalies, taken = [], []
    for f, (g, p) in sorted(zip(risky, read_layer(gp_risk, "rurociagi_zagrozone")), key=lambda t: t[1][1]["v"] if t[1][1]["v"] is not None else 0):
        series = f["properties"].get("series_mm")
        if p["v"] is None or p["v"] >= 0 or len(anomalies) >= PIPE_MARKERS or not series or min(series) > -NOISE_MM:
            continue                                              # only places where the timeline shows the drop too
        mid = g.interpolate(0.5, normalized=True)
        if any(math.hypot((mid.x - x) * 105_000, (mid.y - y) * 110_540) < MARKER_GAP_M for x, y in taken):
            continue
        taken.append((mid.x, mid.y))
        stays = [e for e in range(E) if all(v is not None and v <= -NOISE_MM for v in series[e:])]
        anomalies.append({
            "rank": len(anomalies) + 1, "direction": "down", "lon": round(mid.x, 6), "lat": round(mid.y, 6),
            "title": f"Rurociąg: {p['nazwa']}", "risk": p["klasa"], "v": round(p["v"]),
            "peak_mm": round(-min(v for v in series if v is not None)) if any(v is not None for v in series) else None,
            "first_date": dates[stays[0]] if stays else dates[-1], "series_mm": series,
            "note": f"Ruch pionowy gruntu pod rurą: {p['v']:.0f} mm/rok (dwie orbity) · zagrożenie {p['klasa']}",
        })
    print(f"markers: {len(anomalies)} strongest subsidence points on pipelines; first dates "
          + str({dates[i]: sum(1 for a in anomalies if a['first_date'] == dates[i]) for i in range(E) if any(a['first_date'] == dates[i] for a in anomalies)}))

    # ------------------------------------------------------------------ click on the map: local values on a coarser grid
    # LOS series of the ascending orbit (what the timeline shows) + vertical rate, its uncertainty and data confidence
    vel, unc = grid.warp(vert / "predkosc_pionowa.tif")[0], grid.warp(vert / "niepewnosc_pionowa.tif")[0]
    conf = grid.warp(vert / "pewnosc_danych.tif", categorical=True)[0]
    F = POINT_STEP
    hh, ww = (grid.h // F) * F, (grid.w // F) * F

    def coarse(a):
        a = to_lat_rows(a, grid)[..., :hh, :ww]
        a = a.reshape(a.shape[:-2] + (hh // F, F, ww // F, F))
        with np.errstate(all="ignore"):
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                return np.nanmedian(a, axis=(-3, -1))

    enc = lambda a: np.where(np.isfinite(a), np.clip(np.round(a), -32767, 32767), -32768).astype("<i2")  # noqa: E731
    blocks = [enc(coarse(cum)), enc(coarse(vel)), enc(coarse(unc)), enc(coarse(np.where(np.isfinite(conf), conf, np.nan)))]
    (scene / "points.bin").write_bytes(b"".join(b.tobytes() for b in blocks))
    tb = json.loads((W / "infrastruktura/parametry.json").read_text(encoding="utf-8"))
    points = {"file": "points.bin", "width": ww // F, "height": hh // F,
              "bbox": [w_lon, n_lat - (n_lat - s_lat) * hh / grid.h, w_lon + (e_lon - w_lon) * ww / grid.w, n_lat],  # the crop keeps the north-west corner
              "layout": "int16 LE, -32768 = no data: los_mm[dates][h][w] (cumulative, ascending orbit), v_up[h][w] mm/yr, v_up_sigma[h][w] mm/yr, confidence[h][w] (0 none, 1 partial / one orbit, 2 full series, both orbits); rows north -> south in equal latitude steps",
              "pixel_m": round(grid.res * F), "v_noise": tb["szum_tla"]["v_mm_rok"], "v_mid": tb["progi_efektywne"]["v_med"], "v_high": tb["progi_efektywne"]["v_high"]}
    print(f"points: {points['width']}x{points['height']} cells of {points['pixel_m']} m")

    # ------------------------------------------------------------------ facilities as labels
    places = []
    for g, p in read_layer(gp_risk, "obiekty_sieci"):
        places.append({"type": "Feature", "properties": {"kind": "business", "name": f"{p['typ'].capitalize()}: {p['nazwa'].title()}"},
                       "geometry": {"type": "Point", "coordinates": [round(g.x, 5), round(g.y, 5)]}})
    (scene / "places.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": places}, ensure_ascii=False), encoding="utf-8")

    # ------------------------------------------------------------------ phase analysis: the Tokal crop
    QF = W / "qgis_fazy"
    qpar = json.loads((QF / "parametry.json").read_text(encoding="utf-8"))["tokal"]
    with rasterio.open(QF / "tokal_prazki.tif") as ds:
        tr, fw, fh = calculate_default_transform(ds.crs, "EPSG:4326", ds.width, ds.height, *ds.bounds)
        wrapped = np.full((ds.count, fh, fw), np.nan, dtype=np.float32)
        for b in range(ds.count):
            reproject(ds.read(b + 1), wrapped[b], src_transform=ds.transform, src_crs=ds.crs, dst_transform=tr, dst_crs="EPSG:4326",
                      src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.nearest)   # nearest keeps the wrap edges crisp
    c_w, c_n = tr.c, tr.f
    c_e, c_s = c_w + fw * tr.a, c_n + fh * tr.e
    phase_dir = scene / "phase"
    ref_valid = np.isfinite(wrapped[0])
    # reference date: nothing has moved yet = the zero colour everywhere there is data
    frames = [np.where(ref_valid, 0.0, np.nan)] + [wrapped[b] / CYCLE_MM for b in range(wrapped.shape[0])]
    for e, fr in enumerate(frames):
        ok = np.isfinite(fr)
        rgba = np.zeros(fr.shape + (4,), dtype=np.uint8)
        rgba[..., :3] = hue_rgb(np.where(ok, np.clip(fr, 0, 0.9999), 0))
        rgba[..., 3] = np.where(ok, 240, 0)
        write_png(phase_dir / f"frame_{e:02d}.png", rgba)
    cont = []
    for g, p in read_layer(QF / "tokal_kontury.gpkg", "kontury"):       # read_layer already gives lon/lat
        cont.append(feature(g, {"date": p["data"].replace("-", ""), "mm": p["poziom_mm"], "label": p["etykieta"]}, 0.00003))
    (phase_dir / "contours.geojson").write_text(json.dumps(fc(cont), ensure_ascii=False), encoding="utf-8")
    tokal_dates = [d.replace("-", "") for d in qpar["daty"]]
    assert tokal_dates == dates[1:], (tokal_dates, dates)
    depth = float(np.nanmin(np.asarray(rasterio.open(QF / "tokal_przemieszczenie.tif").read(9))))
    print(f"phase: {len(frames)} frames {fw}x{fh}, {len(cont)} contour lines, deepest {depth:.0f} mm")

    # ------------------------------------------------------------------ profiles and the GNSS station
    prof_meta = json.loads((W / "przekroje/przekroje.json").read_text(encoding="utf-8"))
    profiles = []
    for g, p in read_layer(W / "przekroje/przekroje.gpkg", "przekroje"):
        name = p["nazwa"]
        rows = list(csv.reader((W / f"przekroje/przekroj_{name}.csv").read_text(encoding="utf-8").splitlines()))
        head, body = rows[0], rows[1:]
        m = prof_meta.get(name, {})
        profiles.append({"name": name, "title": PROFILE_TITLES.get(name, "Przekrój wzdłuż rurociągu " + name.replace("_", " ")), "coords": [[round(x, 5), round(y, 5)] for x, y in g.coords],
                         "dates": head[1:], "distance_m": [round(float(r[0])) for r in body],
                         "values_mm": [[None if r[k + 1] in ("", "nan") else round(float(r[k + 1]), 1) for r in body] for k in range(len(head) - 1)],
                         "crossings": m.get("przeciecia_infrastruktury", [])})
    gn = json.loads((W / "gnss/vil2_porownanie.json").read_text(encoding="utf-8"))
    rows = list(csv.DictReader((W / "gnss/vil2_enu.csv").read_text(encoding="utf-8").splitlines()))
    yrs = np.array([float(r["rok"]) for r in rows])
    up = np.array([float(r["pion_mm"]) for r in rows])
    bins = np.floor(yrs * 52).astype(int)
    series = [[round(float(yrs[bins == b].mean()), 3), round(float(up[bins == b].mean()), 1)] for b in np.unique(bins)]
    stations = [{"name": gn["stacja"], "lon": round(gn["lon"], 6), "lat": round(gn["lat"], 6), "source": gn["zrodlo"], "conclusion": gn["wniosek"],
                 "rate": gn["tempo_mm_rok"], "nisar": gn["nisar_w_miejscu_stacji_mm_rok"], "nisar_sigma": gn["niepewnosc_nisar_pionowa_mm_rok"], "series": series}]
    (scene / "extras.json").write_text(json.dumps({"profiles": profiles, "stations": stations}, ensure_ascii=False), encoding="utf-8")

    # ------------------------------------------------------------------ summary block for the layer panel
    summ = json.loads((W / "infrastruktura/rurociagi_podsumowanie.json").read_text(encoding="utf-8"))
    thr = json.loads((W / "infrastruktura/parametry.json").read_text(encoding="utf-8"))["progi_efektywne"]
    kmc, cov, act = summ["km_wg_klasy"], summ["pokrycie_danymi"], summ["km_wg_statusu_i_klasy"]["czynny"]
    nf = lambda v, d=1: f"{v:.{d}f}".replace(".", ",")  # noqa: E731
    summary = {
        "title": "Wyniki analizy: osiadanie nad złożem Tokal",
        "stats": [[f"{nf(kmc['wysokie'])} km", "rurociągów o wysokim zagrożeniu"], [f"{nf(kmc['średnie'])} km", "o średnim zagrożeniu"],
                  [str(summ.get("przeciecia_granicy_strefy", 0)), "przecięć granicy strefy osiadania"], [f"{nf(act['wysokie'])} km", "czynnych rurociągów w klasie wysokiej"]],
        "legend": [["#e03131", "Rurociąg – wysokie zagrożenie"], ["#f08c00", "Rurociąg – średnie zagrożenie"], ["#37b24d", "Rurociąg – niskie zagrożenie"], ["#8a96a3", "Sieć rurociągów (w tym bez danych: mokradła, woda)"]],
        "notes": [f"Klasa wynika z ruchu pionowego gruntu pod rurociągiem (dwie orbity NISAR, ocena co 100 m); wysoka: osiadanie ≥ {thr['v_high']:.0f} mm/rok albo nierówne ≥ {thr['g_high']:.0f} mm/100 m/rok.",
                  f"Sieć OGIM: {summ['km']:,.0f} km".replace(",", " ") + f"; dane NISAR dla {cov['udzial'] * 100:.0f}% długości (reszta: mokradła, woda, utrata spójności).",
                  "Ruch na osi czasu i w analizie fazowej jest w linii widzenia orbity wznoszącej, nie pionowy."],
    }

    # ------------------------------------------------------------------ meta.json
    meta["built_tabasco_results"] = "2026-11-15"
    meta["layers"]["pipelines"]["count"] = n_net + len(risky)
    meta["layers"]["hazards"]["count"] = len(zones)
    meta["layers"]["places"]["count"] = len(places)
    meta["timeline"] = {
        "dir": "timeline", "files": [f"frame_{e:02d}.png" for e in range(E)], "dates": dates, "bbox": [w_lon, s_lat, e_lon, n_lat],
        "threshold_mm": THRESHOLD_MM, "noise_mm": NOISE_MM, "points": points,
        "anomaly_label": "największe osiadania pod rurociągami", "full_colour_mm": FULL_COLOUR_MM, "anomalies": anomalies, "source": "NISAR L2 GUNW, orbita wznosząca (tor 62, ramka 11)",
        "note": "Cumulative ground motion since the first date, in the satellite's line of sight. Red = down, blue = up, clear = less than threshold_mm.",
        "phase": {"title": "Analiza fazowa: złoże Tokal", "dir": "phase", "files": [f"frame_{e:02d}.png" for e in range(len(frames))], "bbox": [c_w, c_s, c_e, c_n],
                  "cycle_mm": CYCLE_MM, "contour_step_mm": qpar["kontury_co_mm"], "contours": "phase/contours.geojson", "ref_date": dates[0],
                  "deepest_mm": round(depth), "pixel_m": 20},
    }
    meta["summary"] = summary
    meta["extras"] = {"file": "extras.json"}
    meta["hazard_colors"] = HAZARD_COLORS
    meta["place_label_m"] = 14000         # village names only from closer than 14 km (a 100 km box has hundreds)
    meta["hide_empty_layers"] = True  # the panel lists only what this location has (no "brak" rows)
    # the 3D view opens on the analysed field, not on the whole 100 km box
    meta["home"] = {"lon": round((c_w + c_e) / 2, 5), "lat": round((c_s + c_n) / 2, 5), "dist_m": 16000, "polar_deg": 52}
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print("meta.json updated")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    build(Path(sys.argv[1]), Path(sys.argv[2]))
