// Data loading and geometry helpers. All values come from files in public/data and public/dem.
import proj4 from 'proj4';

proj4.defs('EPSG:32611', '+proj=utm +zone=11 +datum=WGS84 +units=m +no_defs +type=crs');
export const utm = proj4('EPSG:4326', 'EPSG:32611'); // .forward([lon,lat]) / .inverse([x,y])

const R = 6378137;
export const merc = (lon, lat) => [
  (R * lon * Math.PI) / 180,
  R * Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360)),
];
export const unmerc = (x, y) => [
  ((x / R) * 180) / Math.PI,
  ((2 * Math.atan(Math.exp(y / R)) - Math.PI / 2) * 180) / Math.PI,
];

export const iso = (s) => `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}`;
const toDate = (s) => Date.UTC(+s.slice(0, 4), +s.slice(4, 6) - 1, +s.slice(6, 8));

const BASE = import.meta.env.BASE_URL;
export const dataUrl = (p) => `${BASE}data/${p}`;

async function getJSON(path) {
  const r = await fetch(`${BASE}${path}`);
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}
async function getBin(path) {
  const r = await fetch(`${BASE}${path}`);
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.arrayBuffer();
}

const FILES = {
  meta: 'data/meta.json',
  timeseries: 'data/timeseries.json',
  profiles: 'data/profiles.json',
  profilesGeo: 'data/profiles.geojson',
  zone: 'data/active_zone.geojson',
  alerts: 'data/alerts.geojson',
  buildings: 'data/buildings_risk.geojson',
  roadsRisk: 'data/roads_risk.geojson',
  rail: 'data/rail.geojson',
  power: 'data/power.geojson',
  pipelines: 'data/pipelines.geojson',
  threatened: 'data/threatened.geojson',
  monitoring: 'data/monitoring.json',
  sumBuildings: 'data/summary_buildings.json',
  sumThreatened: 'data/summary_threatened.json',
  sumRail: 'data/summary_rail.json',
  sumPower: 'data/summary_power.json',
  demMeta: 'dem/dem.json',
  gunwRaw: 'gunw/gunw.json',
};

function b64(s) {
  const bin = atob(s);
  const u8 = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
  return u8;
}

// Per-pair GUNW quality layers sampled on the 100 m grid (see scripts/prep_gunw.py).
function decodeGunw(g) {
  const [h, w, P] = g.shape;
  const coh = b64(g.coh_b64), cc = b64(g.cc_b64);
  const inc = new Int16Array(b64(g.inc_b64).buffer);
  const water = b64(g.water_b64);
  const at = (col, row, p) => (row * w + col) * P + p;
  return {
    ...g, h, w, P, cohArr: coh, ccArr: cc, incArr: inc, water,
    coh: (col, row, p) => (coh[at(col, row, p)] === 255 ? null : coh[at(col, row, p)] / 200),
    cc: (col, row, p) => (cc[at(col, row, p)] === 255 ? null : cc[at(col, row, p)]),
    inc: (col, row, p) => (inc[at(col, row, p)] === -32768 ? null : inc[at(col, row, p)] / 10),
  };
}

export async function loadAll(onProgress) {
  const keys = Object.keys(FILES);
  let done = 0;
  const total = keys.length + 1;
  const tick = () => onProgress && onProgress(++done / total);
  const vals = await Promise.all(keys.map((k) => getJSON(FILES[k]).then((v) => (tick(), v))));
  const D = Object.fromEntries(keys.map((k, i) => [k, vals[i]]));
  const demBuf = await getBin('dem/dem.bin');
  tick();

  D.dates = D.meta.dates.map(iso);
  const t0 = toDate(D.meta.dates[0]);
  D.dayOffsets = D.meta.dates.map((s) => Math.round((toDate(s) - t0) / 86400000));
  D.ts = decodeTimeseries(D.timeseries, D.meta.focus);
  delete D.timeseries;
  D.dem = decodeDem(demBuf, D.demMeta);
  D.gunw = decodeGunw(D.gunwRaw);
  delete D.gunwRaw;
  return D;
}

// Lazily loaded large context layer (roads background).
let roadsContextPromise = null;
export function loadRoadsContext() {
  if (!roadsContextPromise) roadsContextPromise = getJSON('data/roads_context.geojson');
  return roadsContextPromise;
}

function decodeTimeseries(ts, F) {
  const bin = atob(ts.b64);
  const u8 = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
  const arr = new Int16Array(u8.buffer);
  const [h, w, E] = ts.shape;
  const nodata = ts.nodata;
  return {
    h, w, E, arr, nodata, x0: F.x0, y1: F.y1, res: F.res,
    pixOf(lon, lat) {
      const [x, y] = merc(lon, lat);
      const col = Math.floor((x - F.x0) / F.res);
      const row = Math.floor((F.y1 - y) / F.res);
      if (col < 0 || row < 0 || col >= w || row >= h) return null;
      return { col, row };
    },
    center(col, row) {
      return unmerc(F.x0 + (col + 0.5) * F.res, F.y1 - (row + 0.5) * F.res);
    },
    value(col, row, e) {
      const v = arr[(row * w + col) * E + e];
      return v === nodata ? null : v;
    },
    series(col, row) {
      const out = [];
      for (let e = 0; e < E; e++) out.push(this.value(col, row, e));
      return out;
    },
  };
}

function decodeDem(buf, m) {
  const i16 = new Int16Array(buf);
  const n = m.width * m.height;
  const elev = new Float32Array(n);
  let max = -Infinity, valid = 0;
  for (let i = 0; i < n; i++) {
    if (i16[i] === -32768) elev[i] = NaN;
    else {
      elev[i] = i16[i] / 10;
      valid++;
      if (elev[i] > max) max = elev[i];
    }
  }
  return {
    ...m,
    w: m.width, h: m.height, rx: m.res_x_m, ry: m.res_y_m,
    elev, max, validFrac: valid / n,
    inside(lon, lat) {
      return lon >= m.lon_min && lon <= m.lon_max && lat >= m.lat_min && lat <= m.lat_max;
    },
    // Nearest 5 m cell for UTM coordinates; NaN outside / nodata.
    elevAtUTM(x, y) {
      const c = Math.floor((x - m.left) / m.res_x_m);
      const r = Math.floor((m.top - y) / m.res_y_m);
      if (c < 0 || r < 0 || c >= m.width || r >= m.height) return NaN;
      return elev[r * m.width + c];
    },
  };
}
