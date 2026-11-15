# Handoff: 3D ground-motion model (Portuguese Bend, NISAR)

This folder is a complete static web project: a story page for NASA Space Apps 2026 plus a reusable
3D terrain model that shows NISAR line-of-sight (LOS) ground displacement over time. This note is for
reusing the model in another application. General setup and data sources: [README.md](README.md).

## Quick start

```bash
npm install
npm run dev
```

- Full page: http://localhost:5173/
- **Minimal example, only the 3D model:** http://localhost:5173/examples/minimal.html
  (code: [examples/minimal.js](examples/minimal.js), about 40 lines)

Requirements: Node.js 18+. No backend, no API keys. `npm run build` writes a static site to `dist/`.

## What to take into another app

| File | Reuse | Role |
|---|---|---|
| `src/terrain3d.js` | **yes** | `TerrainViewer`: terrain, displacement colour and deformation along LOS, display surfaces, overlays, markers, picking, camera presets |
| `src/data.js` | **yes** | Loads and decodes all data files (`loadAll`), projections (`utm`, `merc`) |
| `src/stats.js` | **yes** | `mapDemToTs` (DEM ↔ NISAR grid) and `computeStats` (also prepares `D.losVec`, `D.tsx`, `D.scale3d`, which the viewer needs) |
| `src/colors.js`, `src/format.js`, `src/i18n.js` | yes | Colour ramps, number formatting, translation of the Polish field values |
| `src/map2d.js`, `src/charts.js`, `src/panel.js` | optional | MapLibre regional map, Chart.js charts, object detail panel |
| `src/main.js`, `src/story.js`, `src/style.css` | no | Page-specific: story text, scrollytelling, controls |
| `public/data`, `public/dem`, `public/gunw` | **yes** | All data the model needs (about 16 MB) |

Dependencies of the model: `three`, `proj4` (map and charts add `maplibre-gl`, `chart.js`).
If the other app is not built with Vite, replace `import.meta.env.BASE_URL` in `src/data.js` with the
URL where `public/` is served.

## Minimal use

```js
import { loadAll } from './src/data.js';
import { mapDemToTs, computeStats } from './src/stats.js';
import { TerrainViewer } from './src/terrain3d.js';

const D = await loadAll();          // fetches data/*, dem/*, gunw/*
mapDemToTs(D);                      // required before computeStats and the viewer
computeStats(D);                    // required: sets D.losVec, D.tsx, D.scale3d, ...

const v = new TerrainViewer(document.getElementById('viewer'), D, { exag: 120 });
v.addLayer('zone', D.zone.features, () => ({ key: 'z', color: '#1d232a', width: 3 }));
v.setEpoch(5);                      // show acquisition date D.dates[5]
```

The container must have a size and `position: relative`; copy the label/compass CSS from
`examples/minimal.html` (or `src/style.css`).

## TerrainViewer API

Constructor `new TerrainViewer(container, D, options)`:

| Option | Default | Meaning |
|---|---|---|
| `exag` | `0` | Exaggeration of displacement drawn along the LOS vector (0 = no deformation, colour only) |
| `scale` | `D.scale3d` | Colour scale ± mm (RdBu, red = away from satellite) |
| `step` | `2` | Mesh from every n-th DEM cell: 2 = 20 m (fast), 1 = 10 m (needs a strong GPU) |
| `surface` | `'filled'` | `'filled'` smooth surface, gaps estimated from neighbours · `'surface'` smooth, big gaps empty · `'blend'` bilinear between measured pixels · `'raw'` 100 m pixels |
| `useExt` | `true` | Include low-coherence pixels with our own series built from GUNW |
| `zoom` | `true` | Mouse-wheel zoom (disable inside scrolling pages) |
| `onPick` | – | Click callback `({ lon, lat, feature: { layer, f } \| null })` |
| `label` | – | Accessible label of the canvas |

Methods:

| Method | Effect |
|---|---|
| `setEpoch(i)` | Show date `D.dates[i]` (displacement since `D.dates[0]`) |
| `setExag(x)`, `setScale(mm)` | Exaggeration, colour scale |
| `setSurface(mode)`, `setUseExt(bool)`, `setDetail(step)` | Display surface, low-coherence pixels, mesh detail |
| `setMode('disp' \| 'coh' \| 'cc')` | Colour by displacement, pair coherence or connected component (GUNW) |
| `setShowDisp(bool)` | Colour on/off (gray hillshade only) |
| `addLayer(id, features, style, { lift, visible, pickable })` | Drape GeoJSON lines/polygons; `style(f)` → `{ key, color, width, opacity, dashed, order }` or `null` to skip |
| `setLayerVisible(id, bool)` | Toggle a layer |
| `setMarkers([{ lon, lat, label, color }])`, `setMarkersVisible(bool)` | HTML labels pinned to the terrain |
| `view('overview' \| 'core' \| 'top')` | Animated camera presets |

Exported helpers: `displacementField(D, { ext, surface })` and `filledField(D, ext)` return the
display fields on the 100 m grid (Float32Array `[row][col][date]`, `NaN` = no data).

## Data the model uses

| File | Content |
|---|---|
| `public/data/meta.json` | Dates, grid of the time series (`focus`: EPSG:3857, `x0`, `y1`, `res` = 100 m, `w`, `h`), colours, product and stack info |
| `public/data/timeseries.json` | LOS displacement per 100 m pixel and date: Int16 mm, base64, shape `[h, w, 14]`, nodata −32768; positive = towards the satellite |
| `public/dem/dem.bin` + `dem.json` | Lidar terrain 10 m, EPSG:32611, Int16 decimetres, nodata −32768 (sea) |
| `public/gunw/gunw.json` | Per-pair coherence, connected components, LOS increments, water mask on the same 100 m grid; LOS unit vector and incidence; granule ids, collection (PROVISIONAL), version |
| `public/data/*.geojson` | Overlays: active zone, alerts (with `data` = date raised), buildings, roads, rail, power, pipelines, profiles (EPSG:4326, Polish field values, see `public/data/README.md`) |

Coordinates inside the viewer: metres in UTM 11N, centred on the DEM; three.js `x` = east, `y` = up,
`z` = south. `viewer.S.toLocal(lon, lat)` and `viewer.S.toLonLat(x, z)` convert.

## Rules this project follows (keep them if you reuse the numbers)

- Every number on the page is computed from the data files; `npm run build && npm run verify`
  recomputes all of them independently and compares them with the rendered page.
- Measurements, our analysis and external context are labelled separately.
- 3D display surfaces (`'filled'`, `'surface'`, `useExt`) are **for display only**. Gap filling and
  low-coherence series are estimates; statistics and risk classes use only the project time series.
- GUNW products are **PROVISIONAL** (collection `NISAR_L2_GUNW_PROVISIONAL_V1`, version 1.0.11);
  values are LOS only, one descending track, 12/24-day pairs.

## Regenerating data

Inputs (not in this package, sent separately if needed): `nmt_focus_5m.tif` (made with PDAL from
`sources/nmt_focus.json`) and `gunw_aoi.zip` (NISAR GUNW subsets).

```bash
python scripts/prep_dem.py ../nmt_focus_5m.tif 10
python scripts/prep_gunw.py ../gunw_pb/gunw_aoi/portuguese_bend_california
```

Python needs `rasterio`, `numpy`, `pyproj`.
