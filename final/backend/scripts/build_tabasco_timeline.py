"""Build separate A062 and D156 cumulative LOS timelines for the Tabasco 2D scene.

Usage:
    python3 backend/scripts/build_tabasco_timeline.py <processed Tabasco output> <scene data folder>

The input folder must contain A062_cube.npz, D156_cube.npz, and products.json.
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
from osgeo import gdal, osr

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_timeline import clusters  # noqa: E402
from pipeline_anomalies import pipeline_anomalies  # noqa: E402

gdal.UseExceptions()

DOWN_RGB = (214, 39, 40)
UP_RGB = (31, 104, 196)
FULL_COLOUR_MM = 200.0
PIXEL_SIZE_M = 80.0
ANOMALY_COUNT = 3
SERIES = (
    ("A062", "A062 · wznosząca", "wznosząca"),
    ("D156", "D156 · zstępująca", "zstępująca"),
)


def geographic_bounds(x: np.ndarray, y: np.ndarray, epsg: int) -> tuple[float, float, float, float]:
    source = osr.SpatialReference()
    source.ImportFromEPSG(epsg)
    source.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    target = osr.SpatialReference()
    target.ImportFromEPSG(4326)
    target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    transform = osr.CoordinateTransformation(source, target)

    left, right = float(x[0] - PIXEL_SIZE_M / 2), float(x[-1] + PIXEL_SIZE_M / 2)
    bottom, top = float(y[-1] - PIXEL_SIZE_M / 2), float(y[0] + PIXEL_SIZE_M / 2)
    edge = np.linspace(0, 1, 33)
    points = (
        [(left + t * (right - left), bottom) for t in edge]
        + [(left + t * (right - left), top) for t in edge]
        + [(left, bottom + t * (top - bottom)) for t in edge]
        + [(right, bottom + t * (top - bottom)) for t in edge]
    )
    transformed = [transform.TransformPoint(px, py) for px, py in points]
    return (
        min(p[0] for p in transformed),
        min(p[1] for p in transformed),
        max(p[0] for p in transformed),
        max(p[1] for p in transformed),
    )


def warp_cube(cube: np.ndarray, x: np.ndarray, y: np.ndarray, epsg: int,
              view_bounds: tuple[float, float, float, float]) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    height, width = cube.shape[1:]
    source_srs = osr.SpatialReference()
    source_srs.ImportFromEPSG(epsg)
    source_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    source = gdal.GetDriverByName("MEM").Create("", width, height, cube.shape[0], gdal.GDT_Float32)
    source.SetProjection(source_srs.ExportToWkt())
    source.SetGeoTransform((float(x[0] - PIXEL_SIZE_M / 2), PIXEL_SIZE_M, 0,
                           float(y[0] + PIXEL_SIZE_M / 2), 0, -PIXEL_SIZE_M))
    for band, frame in enumerate(cube, start=1):
        source.GetRasterBand(band).WriteArray(frame)

    data_bounds = geographic_bounds(x, y, epsg)
    west = max(data_bounds[0], view_bounds[0])
    south = max(data_bounds[1], view_bounds[1])
    east = min(data_bounds[2], view_bounds[2])
    north = min(data_bounds[3], view_bounds[3])
    if west >= east or south >= north:
        raise ValueError("Tabasco displacement cube does not overlap the scene bounds")
    bounds = (west, south, east, north)
    mid_lat = math.radians((south + north) / 2)
    out_width = math.ceil((east - west) * 111_320 * math.cos(mid_lat) / PIXEL_SIZE_M)
    out_height = math.ceil((north - south) * 110_540 / PIXEL_SIZE_M)
    warped = gdal.Warp(
        "",
        source,
        format="MEM",
        dstSRS="EPSG:4326",
        outputBounds=bounds,
        width=out_width,
        height=out_height,
        resampleAlg="near",
        srcNodata=float("nan"),
        dstNodata=float("nan"),
        outputType=gdal.GDT_Float32,
        multithread=True,
    )
    if warped is None:
        raise RuntimeError("GDAL could not reproject the Tabasco displacement cube")
    data = warped.ReadAsArray()
    if data.ndim == 2:
        data = data[np.newaxis, ...]
    return data, bounds


def smooth_for_display(mm: np.ndarray) -> np.ndarray:
    """Light 3 x 3 (1-2-1) smoothing that skips missing pixels: softer edges and less pixel speckle, like the
    Portuguese Bend frames (which come from the team's presentation maps). Display only - the anomalies and
    the click panel use the measured values."""
    finite = np.isfinite(mm)
    val = np.where(finite, mm, 0.0)
    acc = np.zeros_like(val)
    wsum = np.zeros_like(val)
    pv, pw = np.pad(val, 1), np.pad(finite.astype(np.float64), 1)
    h, w = mm.shape
    for dy, ky in ((0, 1), (1, 2), (2, 1)):
        for dx, kx in ((0, 1), (1, 2), (2, 1)):
            acc += ky * kx * pv[dy:dy + h, dx:dx + w]
            wsum += ky * kx * pw[dy:dy + h, dx:dx + w]
    with np.errstate(invalid="ignore", divide="ignore"):
        out = acc / wsum
    out[~finite] = np.nan  # no colour where nothing was measured
    return out


DISPLAY_UPSAMPLE = 1   # >1 draws the frames on a finer grid; 1 keeps the 80 m pixels, which the GPU blends softly (like PB)
EDGE_FEATHER = 0.6     # clear pixels next to a patch get this share of the blurred patch opacity: a soft rim, like PB


def upsample(mm: np.ndarray, k: int) -> np.ndarray:
    """Bilinear k x upsampling that leaves missing pixels out of the interpolation."""
    h, w = mm.shape
    src = gdal.GetDriverByName("MEM").Create("", w, h, 1, gdal.GDT_Float32)
    src.SetGeoTransform((0, 1, 0, 0, 0, -1))
    band = src.GetRasterBand(1)
    band.SetNoDataValue(float("nan"))
    band.WriteArray(mm.astype(np.float32))
    out = gdal.Translate("", src, format="MEM", width=w * k, height=h * k, resampleAlg="bilinear")
    return out.ReadAsArray().astype(np.float64)


def write_frame(path: Path, mm: np.ndarray, threshold: float) -> None:
    mm = smooth_for_display(smooth_for_display(mm))  # two passes ~ a soft 5 x 5 blur
    if DISPLAY_UPSAMPLE > 1:
        mm = upsample(mm, DISPLAY_UPSAMPLE)
    height, width = mm.shape
    magnitude = np.abs(mm)
    strength = np.clip((magnitude - threshold) / max(FULL_COLOUR_MM - threshold, 1), 0, 1)
    visible = np.isfinite(mm) & (magnitude >= threshold)
    alpha = np.where(visible, 0.35 + 0.55 * strength, 0)
    # soft rim: the opacity of the patch fades out over about one pixel outside it (display only)
    rim = smooth_for_display(np.where(np.isfinite(mm), alpha, 0.0))
    alpha = np.where(visible, alpha, EDGE_FEATHER * np.nan_to_num(rim))
    down = np.nan_to_num(mm) < 0
    # A clear pixel takes the colour of its strongest visible neighbour: the GPU blends neighbouring pixels,
    # and a clear "blue" pixel next to a red one made a pink rim around every red patch.
    best = np.where(visible, alpha, -1.0)
    pa, pd = np.pad(best, 1, constant_values=-1.0), np.pad(down, 1)
    edge_down, edge_best = down.copy(), best.copy()
    for dy in range(3):
        for dx in range(3):
            a, d = pa[dy:dy + height, dx:dx + width], pd[dy:dy + height, dx:dx + width]
            take = ~visible & (a > edge_best)
            edge_best = np.where(take, a, edge_best)
            edge_down = np.where(take, d, edge_down)
    down = np.where(visible, down, edge_down)
    rgba = np.zeros((4, height, width), dtype=np.uint8)
    for band in range(3):
        rgba[band] = np.where(down, DOWN_RGB[band], UP_RGB[band])
    rgba[3] = np.round(alpha * 255).astype(np.uint8)
    mem = gdal.GetDriverByName("MEM").Create("", width, height, 4, gdal.GDT_Byte)
    for band in range(4):
        mem.GetRasterBand(band + 1).WriteArray(rgba[band])
    if gdal.GetDriverByName("PNG").CreateCopy(str(path), mem) is None:
        raise RuntimeError(f"Could not write timeline frame: {path}")


def anomaly_list(frames: list[np.ndarray], dates: list[str], bounds: tuple[float, float, float, float], threshold: float) -> list[dict]:
    west, south, east, north = bounds
    height, width = frames[-1].shape
    metres_per_pixel = ((east - west) / width * 111_320 * math.cos(math.radians((north + south) / 2))) \
        * ((north - south) / height * 110_540)
    anomalies = []
    for cluster in clusters(frames[-1], threshold)[:ANOMALY_COUNT]:
        ys, xs, sign = cluster["ys"], cluster["xs"], cluster["sign"]
        values = frames[-1][ys, xs] * sign
        weights = values - threshold + 1
        cy = float((ys * weights).sum() / weights.sum())
        cx = float((xs * weights).sum() / weights.sum())
        first = next(
            (dates[i] for i, frame in enumerate(frames)
             if np.mean(np.nan_to_num(frame[ys, xs]) * sign >= threshold) >= 0.25),
            dates[-1],
        )
        peak = float(values.max())
        anomalies.append({
            "rank": len(anomalies) + 1,
            "direction": "down" if sign < 0 else "up",
            "lon": round(west + (cx + 0.5) / width * (east - west), 6),
            "lat": round(north - (cy + 0.5) / height * (north - south), 6),
            "area_ha": round(len(ys) * metres_per_pixel / 1e4),
            "peak_mm": round(peak),
            "peak_saturated": peak >= FULL_COLOUR_MM - 1,
            "median_mm": round(float(np.median(values))),
            "first_date": first,
            "series_mm": [
                round(float(np.nanmedian(frame[ys, xs]))) if np.isfinite(frame[ys, xs]).any() else 0
                for frame in frames
            ],
        })
    return anomalies


def build_series(source_dir: Path, scene_dir: Path, segment: dict, stack: str, label: str, orbit: str,
                 view_bounds: tuple[float, float, float, float]) -> dict:
    with np.load(source_dir / f"{stack}_cube.npz", allow_pickle=False) as archive:
        cube = archive["cum"]
        reliable = archive["reliable"]
        x, y = archive["x"], archive["y"]
        epsg = int(archive["epsg"])
        dates = [str(d).replace("-", "") for d in archive["dates"]]

    if cube.shape[0] != len(dates) or cube.shape[1:] != (len(y), len(x)) or reliable.shape != cube.shape[1:]:
        raise ValueError(f"{stack}: cube dimensions do not match its date and coordinate arrays")
    cube[:, ~reliable] = np.nan
    if not np.isfinite(cube).any():
        raise ValueError(f"{stack}: displacement cube has no finite measurements")

    frames, bounds = warp_cube(cube, x, y, epsg, view_bounds)
    pipes_file = scene_dir / "pipelines.geojson"
    pipes = json.loads(pipes_file.read_text(encoding="utf-8"))["features"] if pipes_file.exists() else []
    noise = float(segment["background_noise_mm"])
    threshold = math.ceil(noise * 2)
    series_id = stack.lower()
    out_dir = scene_dir / "timeline" / series_id
    out_dir.mkdir(parents=True, exist_ok=True)

    frame_arrays = []
    for i, date in enumerate(dates):
        mm = frames[i]
        frame_arrays.append(mm)
        write_frame(out_dir / f"frame_{i:02d}.png", mm, threshold)

    return {
        "id": series_id,
        "label": label,
        "dir": f"timeline/{series_id}",
        "files": [f"frame_{i:02d}.png" for i in range(len(dates))],
        "dates": dates,
        "bbox": list(bounds),
        "threshold_mm": threshold,
        "full_colour_mm": FULL_COLOUR_MM,
        # with pipelines in the scene the anomalies are the strong ground motion AT the pipes (possible risk to them)
        "anomalies": (pipeline_anomalies(frame_arrays, dates, bounds, threshold, pipes) if pipes
                      else anomaly_list(frame_arrays, dates, bounds, threshold)),
        "anomaly_kind": "pipeline" if pipes else "cluster",
        "source": f"NISAR L2 GUNW · {stack} ({orbit})",
        "note": (
            "Kumulatywne przemieszczenie w linii widzenia satelity (LOS) od pierwszej akwizycji, nie ruch pionowy. "
            f"Czerwony = od satelity, niebieski = w stronę satelity; zmiany poniżej {threshold} mm są przezroczyste. "
            f"Próg przezroczystości to 2x szacowany szum tła ({noise:g} mm). "
            "Pokazano tylko wiarygodne piksele z przetworzonego szeregu. Daty to rzeczywiste akwizycje; "
            "wartości między pomiarami nie są interpolowane. To sygnał do wstępnej oceny, a nie diagnoza "
            "stanu infrastruktury."
        ),
    }


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    source_dir, scene_dir = map(Path, sys.argv[1:])
    metadata_path = scene_dir / "meta.json"
    products = json.loads((source_dir / "products.json").read_text(encoding="utf-8"))
    segments = {segment["stack"]: segment for segment in products["segments"]}
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    view_bounds = tuple(metadata["bbox"])
    timelines = [
        build_series(source_dir, scene_dir, segments[stack], stack, label, orbit, view_bounds)
        for stack, label, orbit in SERIES
    ]
    metadata["timeline"] = {"series": timelines}
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for timeline in timelines:
        print(f"{timeline['id']}: {len(timeline['dates'])} dates, {len(timeline['anomalies'])} anomalies")


if __name__ == "__main__":
    main()
