"""Resample a lidar DTM GeoTIFF and export it for the browser.

Equivalent to:  gdal_translate -tr RES RES -r average <input>.tif <output>.tif
(rasterio/GDAL "average" resampling; nodata cells are excluded from the mean).

Current input: nmt_focus_5m.tif, made with PDAL (../nmt_focus.json) from the USGS 3DEP point cloud
USGS_LPC_CA_LosAngeles_2016_LAS_2018: EPT read at 1 m resolution over the NISAR analysis grid,
ground class (2), mean of points per 5 m cell, EPSG:32611.

Outputs:
  public/dem/dem.bin    Int16 little-endian, elevation in decimetres, row-major from the
                        north-west corner, nodata = -32768
  public/dem/dem.json   grid description (CRS, origin, resolution, size, lon/lat bounds, source)
  build_dem/nmt_<res>m.tif   GeoTIFF copy, EPSG:32611, float32 (not shipped)

Usage:  python scripts/prep_dem.py ../nmt_focus_5m.tif 10
"""
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.enums import Resampling

src_path = Path(sys.argv[1] if len(sys.argv) > 1 else "../nmt_focus_5m.tif")
RES = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
SOURCE = (
    "USGS 3DEP lidar point cloud USGS_LPC_CA_LosAngeles_2016_LAS_2018, ground class (2), "
    "EPT read at 1 m resolution, mean per 5 m cell (PDAL)"
)
root = Path(__file__).resolve().parent.parent
out_dir = root / "public" / "dem"
out_dir.mkdir(parents=True, exist_ok=True)

with rasterio.open(src_path) as src:
    w = int(round(src.width * src.res[0] / RES))
    h = int(round(src.height * src.res[1] / RES))
    data = src.read(1, out_shape=(h, w), resampling=Resampling.average, masked=True)
    transform = src.transform * src.transform.scale(src.width / w, src.height / h)
    crs = src.crs
    profile = src.profile.copy()
    src_res = src.res[0]

profile.update(width=w, height=h, transform=transform, dtype="float32", nodata=-9999.0)
tif_dir = root / "build_dem"
tif_dir.mkdir(exist_ok=True)
with rasterio.open(tif_dir / f"nmt_{RES:g}m.tif", "w", **profile) as dst:
    dst.write(data.filled(-9999.0).astype("float32"), 1)

dm = np.where(data.mask, -32768, np.round(data.filled(0) * 10)).astype("<i2")
dm.tofile(out_dir / "dem.bin")

left, top = transform.c, transform.f
right, bottom = left + w * transform.a, top + h * transform.e
tr = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
corners = [tr.transform(x, y) for x, y in [(left, top), (right, top), (right, bottom), (left, bottom)]]
meta = {
    "source": SOURCE,
    "source_file": src_path.name,
    "resampling": f"average, {src_res:g} m -> {RES:g} m",
    "crs": str(crs),
    "res_x_m": transform.a,
    "res_y_m": -transform.e,
    "width": w,
    "height": h,
    "left": left,
    "top": top,
    "right": right,
    "bottom": bottom,
    "lonlat_corners_nw_ne_se_sw": corners,
    "lon_min": min(c[0] for c in corners),
    "lon_max": max(c[0] for c in corners),
    "lat_min": min(c[1] for c in corners),
    "lat_max": max(c[1] for c in corners),
    "encoding": "int16le decimetres, nodata -32768, row 0 = north",
}
(out_dir / "dem.json").write_text(json.dumps(meta, indent=1))
valid = ~data.mask
print(f"{w}x{h} cells, valid {valid.mean():.3f}, elev {float(data.min()):.1f}..{float(data.max()):.1f} m")
