// Minimal use of the 3D terrain model without the story page.
// Run `npm run dev` and open http://localhost:5173/examples/minimal.html
import { loadAll } from '../src/data.js';
import { mapDemToTs, computeStats } from '../src/stats.js';
import { TerrainViewer } from '../src/terrain3d.js';

const status = document.getElementById('status');

// 1. Load and prepare the data (DEM <-> NISAR grid mapping, LOS vector, GUNW series).
const D = await loadAll((f) => (status.textContent = `Loading ${Math.round(f * 100)} %`));
mapDemToTs(D);
computeStats(D);

// 2. Create the viewer.
const viewer = new TerrainViewer(document.getElementById('viewer'), D, {
  exag: 120, // displacement exaggeration along the line of sight (0 = off)
  step: 2, // mesh from every 2nd DEM cell (20 m); 1 = full 10 m
  surface: 'filled', // 'filled' | 'surface' | 'blend' | 'raw'
  onPick: ({ lon, lat, feature }) => console.log('clicked', lon, lat, feature?.layer, feature?.f.properties),
});

// 3. Overlays: any GeoJSON lines or polygons; style(feature) returns colour and width.
const C = D.meta.risk_colors;
viewer.addLayer('zone', D.zone.features, () => ({ key: 'z', color: '#1d232a', width: 3, order: 3 }), { lift: 6 });
viewer.addLayer('roads', D.roadsRisk.features, (f) => ({ key: f.properties.klasa, color: C[f.properties.klasa], width: 3 }));

// 4. Markers with HTML labels.
viewer.setMarkers([{ lon: D.meta.core.lon, lat: D.meta.core.lat, label: 'Landslide core', color: '#c0601a' }]);
viewer.view('core');

// 5. Controls.
const epoch = document.getElementById('epoch');
const date = document.getElementById('date');
const show = () => (date.textContent = D.dates[+epoch.value]);
epoch.max = D.ts.E - 1;
epoch.addEventListener('input', () => { viewer.setEpoch(+epoch.value); show(); });
document.getElementById('exag').addEventListener('input', (e) => viewer.setExag(+e.target.value));
document.getElementById('markers').addEventListener('change', (e) => viewer.setMarkersVisible(e.target.checked));
show();
status.textContent = 'Ready';
