# Ground motion early warning — Portuguese Bend (NISAR)

Static web page for NASA Space Apps Challenge 2026, *Dancing with the SARs*. It shows NISAR L-band
line-of-sight (LOS) ground displacement at the Portuguese Bend landslide (Palos Verdes, California).
The page combines:

1. a 3D terrain model with the animated NISAR displacement time series,
2. a scroll-driven story: what moves, where, how fast, what is at risk and why,
3. an explore mode for clicking through the data in 3D and on a regional map.

Every number on the page is computed in the browser from the files in `public/data` and `public/dem`.
`scripts/verify_numbers.mjs` recomputes them independently and checks them against the rendered page.

## Run

Requires Node.js 18+.

```bash
npm install
npm run dev        # development server
npm run build      # static site in dist/
npm run verify     # after build: recompute all displayed numbers and compare with the page
node scripts/screenshots.mjs   # after build: screenshots of every section in screenshots/
```

`verify` and `screenshots` use `puppeteer-core` with an installed Edge or Chrome. If neither is
found automatically, set `BROWSER_PATH` to the browser executable.

### GitHub Pages

The build uses a relative base (`./`), so `dist/` can be published from any sub-path, for example
with the `peaceiris/actions-gh-pages` action or by pushing `dist/` to a `gh-pages` branch.

## Data

| Path | Content | Source |
|---|---|---|
| `public/data/meta.json`, `timeseries.json` | Dates, grid, LOS displacement for every 100 m pixel and date | NISAR L2 GUNW (NASA/ISRO, ASF DAAC), track 013 descending, frame 071; project processing |
| `public/data/*_risk.geojson`, `rail`, `power`, `pipelines`, `threatened.*`, `summary_*.json` | Risk / exposure classes of buildings and infrastructure | Our analysis of NISAR data; geometry from OpenStreetMap (ODbL) and OGIM |
| `public/data/active_zone.geojson`, `alerts.geojson`, `monitoring.json` | Active zone and change-detection alerts | Our analysis of NISAR data |
| `public/data/profiles.*` | Cross-section lines and displacement along them | Our analysis of NISAR data |
| `public/data/img/cum_*.png` | Displacement rasters for the regional map (EPSG:3857, ±150 mm) | NISAR, project export |
| `public/gunw/gunw.json` | Per-pair coherence, connected components and LOS increments on the 100 m grid; LOS geometry; granule ids, collection (status) and product version | NISAR L2 GUNW subsets (`gunw_aoi.zip`), extracted with `scripts/prep_gunw.py` |
| `public/dem/dem.bin`, `dem.json` | Terrain, 10 m, whole NISAR analysis grid | USGS 3DEP lidar `USGS_LPC_CA_LosAngeles_2016_LAS_2018`, ground class, PDAL mean 5 m grid (`../nmt_focus.json`) averaged to 10 m |
| Map tiles | Background map | © OpenStreetMap contributors |

`public/data/README.md` (in Polish) documents all fields of the data export. Field values in the data
are Polish; the page translates them for display (`src/i18n.js`).

The 5 m terrain is produced from the 1 m lidar DTM with:

```bash
python scripts/prep_dem.py ../nmt_focus_5m.tif 10
```

GUNW quality layers and geometry:

```bash
python scripts/prep_gunw.py ../gunw_pb/gunw_aoi/portuguese_bend_california
```

This is equivalent to `gdal_translate -tr 10 10 -r average nmt_focus_5m.tif nmt_10m.tif`, run with
rasterio. The script writes the browser files to `public/dem/` and a GeoTIFF copy to `build_dem/`,
which is not shipped.

## Rules followed

- No hand-typed statistics. Text is generated from data through `src/stats.js` and `src/format.js`
  (`n(key)` renders a value with a `data-num` attribute).
- In 3D, displacement can be drawn along the line-of-sight vector from GUNW and blended between measured pixel centres for display (toggle); quality layers (coherence, connected components) can be shown per date.
- Missing data is shown as missing: gray in 3D, transparent on the map, gaps in charts. There is no
  interpolation between dates or pixels. In 3D each 100 m NISAR pixel is shown as a block.
- Each statement is tagged as *NISAR measurement*, *Our analysis* or *External context*.
- Known limitations are listed on the page: LOS only, one track, 12/24-day sampling, product status
  not recorded in metadata, 2016 terrain, exaggeration in 3D, partial model extent.

## Code

| File | Role |
|---|---|
| `src/data.js` | Loading, time-series and DEM decoding, projections |
| `src/stats.js` | All displayed statistics |
| `src/story.js` | Page text (story sections, limitations, sources) |
| `src/terrain3d.js` | three.js terrain viewer (displacement colour, exaggeration, overlays, picking) |
| `src/map2d.js` | MapLibre regional map |
| `src/charts.js`, `src/panel.js` | Chart.js charts and the object detail panel |
| `scripts/verify_numbers.mjs` | Independent recomputation and comparison with the rendered page |
