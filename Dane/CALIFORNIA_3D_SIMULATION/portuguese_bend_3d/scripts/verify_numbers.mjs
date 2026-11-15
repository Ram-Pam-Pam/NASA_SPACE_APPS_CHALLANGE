#!/usr/bin/env node
// Independent check of every number shown on the page.
//
// 1. Recomputes all statistics directly from public/data/* and public/dem/* (own code, not src/).
// 2. Serves dist/ (run `npm run build` first), opens it in headless Edge/Chrome (puppeteer-core),
//    and reads every element marked data-num / data-dyn / data-src.
// 3. Compares displayed values with the recomputed ones (to the displayed number of decimals),
//    checks object panels against the source records, and lists any digits on the page that are
//    not tied to a data value.
//
// Usage: node scripts/verify_numbers.mjs [--browser "C:/path/to/msedge.exe"]
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import proj4 from 'proj4';
import puppeteer from 'puppeteer-core';
import { preview } from 'vite';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const P = (...p) => path.join(ROOT, 'public', ...p);
const J = (f) => JSON.parse(fs.readFileSync(P('data', f), 'utf8'));

// ---------------------------------------------------------------------------------------------
// 1. Expected values
// ---------------------------------------------------------------------------------------------
const E = {};
const put = (k, v) => (E[k] = v);

const meta = J('meta.json');
const dates = meta.dates.map((s) => `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6)}`);
const days = meta.dates.map((s) => (Date.UTC(+s.slice(0, 4), +s.slice(4, 6) - 1, +s.slice(6)) - Date.UTC(+meta.dates[0].slice(0, 4), +meta.dates[0].slice(4, 6) - 1, +meta.dates[0].slice(6))) / 864e5);
put('period.start', dates[0]);
put('period.end', dates.at(-1));
put('period.days', meta.period.days);
put('period.nDates', dates.length);
put('period.nPairs', dates.length - 1);
const steps = [];
for (let i = 1; i < days.length; i++) steps.push(days[i] - days[i - 1]);
put('period.n12', steps.filter((s) => s === 12).length);
put('period.n24', steps.filter((s) => s === 24).length);
{
  const first = steps.indexOf(24), lastI = steps.lastIndexOf(24);
  if (first >= 0) { put('period.gapFrom', dates[first]); put('period.gapTo', dates[lastI + 1]); }
}
{
  const parts = meta.stack.split(',').map((s) => s.trim());
  put('orbit.track', parts.find((p) => p.startsWith('track')).split(' ')[1]);
  put('orbit.direction', parts[2]);
  put('orbit.frame', parts.find((p) => p.startsWith('frame')).split(' ')[1]);
}
put('sar.halfWl', meta.half_wavelength_mm);
put('sar.wl', meta.half_wavelength_mm * 2);
put('scale.mm', meta.scale_mm);
put('grid.res', meta.focus.res);
put('noise', meta.noise_mm_yr);

// time series
const tsj = J('timeseries.json');
const raw = Buffer.from(tsj.b64, 'base64');
const TS = new Int16Array(raw.buffer, raw.byteOffset, raw.length / 2);
const [TH, TW, TE] = tsj.shape;
const F = meta.focus;
const at = (row, col, e) => TS[(row * TW + col) * TE + e];
const RAD = 180 / Math.PI;
const pixCenter = (row, col) => {
  const x = F.x0 + (col + 0.5) * F.res, y = F.y1 - (row + 0.5) * F.res;
  return [(x / 6378137) * RAD, (2 * Math.atan(Math.exp(y / 6378137)) - Math.PI / 2) * RAD];
};
let nFull = 0, worst = { v: Infinity };
for (let r = 0; r < TH; r++)
  for (let c = 0; c < TW; c++) {
    let all = true;
    for (let e = 0; e < TE; e++) all = all && at(r, c, e) !== tsj.nodata;
    nFull += all ? 1 : 0;
    const v = at(r, c, TE - 1);
    if (v !== tsj.nodata && v < worst.v) worst = { v, r, c };
  }
put('grid.fullN', nFull);
put('grid.fullPct', (nFull / (TW * TH)) * 100);
put('max.disp', worst.v);
{
  const [lon, lat] = pixCenter(worst.r, worst.c);
  put('max.lon', lon); put('max.lat', lat);
  put('max.rateDay', Math.abs(worst.v) / meta.period.days);
  const mid = Math.floor((TE - 1) / 2);
  put('max.midDate', dates[mid]);
  put('max.dispMid', at(worst.r, worst.c, mid));
  put('max.rate1', (at(worst.r, worst.c, mid) - at(worst.r, worst.c, 0)) / (days[mid] - days[0]));
  put('max.rate2', (at(worst.r, worst.c, TE - 1) - at(worst.r, worst.c, mid)) / (days[TE - 1] - days[mid]));
}
{
  // second most negative pixel and neighbours of the extreme one
  let second = { v: Infinity };
  for (let r = 0; r < TH; r++)
    for (let c = 0; c < TW; c++) {
      if (r === worst.r && c === worst.c) continue;
      const v = at(r, c, TE - 1);
      if (v !== tsj.nodata && v < second.v) second = { v, r, c };
    }
  put('max2.disp', second.v);
  const [lon2, lat2] = pixCenter(second.r, second.c);
  put('max2.lon', lon2); put('max2.lat', lat2);
  put('max.gap', second.v - worst.v);
  let k = 0;
  for (const [dr, dc] of [[-1,-1],[-1,0],[-1,1],[0,-1],[0,1],[1,-1],[1,0],[1,1]]) {
    const r = worst.r + dr, c = worst.c + dc;
    if (r < 0 || c < 0 || r >= TH || c >= TW) continue;
    let full = true;
    for (let e = 0; e < TE; e++) if (at(r, c, e) === tsj.nodata) full = false;
    if (full) k++;
  }
  put('max.nbFull', k);
}
const extIdx = [];
{
  // GUNW: status, geometry, quality, check of the extreme pixel  (extIdx: see below)
  const g = JSON.parse(fs.readFileSync(P('gunw', 'gunw.json'), 'utf8'));
  const [, GW, GP] = g.shape;
  const u8 = (b) => new Uint8Array(Buffer.from(b, 'base64'));
  const COH = u8(g.coh_b64), CC = u8(g.cc_b64);
  const ib = Buffer.from(g.inc_b64, 'base64');
  const INC = new Int16Array(ib.buffer, ib.byteOffset, ib.length / 2);
  const gi = (r, c, p) => (r * GW + c) * GP + p;
  const colls = [...new Set(g.pairs.map((p) => p.collection))];
  put('gunw.collection', colls.join(', '));
  put('gunw.version', [...new Set(g.pairs.map((p) => p.product_version))].join(', '));
  put('gunw.status', colls.map((c) => c.includes('PROVISIONAL') ? 'PROVISIONAL' : c.includes('BETA') ? 'BETA' : c.includes('VALIDATED') ? 'VALIDATED' : 'not stated').join(', '));
  put('gunw.nPairs', g.pairs.length);
  put('gunw.inc', g.geometry.incidence_deg_mean);
  put('gunw.incMin', g.geometry.incidence_deg_min);
  put('gunw.incMax', g.geometry.incidence_deg_max);
  const e = g.geometry.los_east_mean, nn = g.geometry.los_north_mean;
  put('los.e', e); put('los.n', nn); put('los.u', Math.sqrt(1 - e * e - nn * nn));
  put('los.side', e < 0 ? 'west' : 'east');
  const ch = g.pairs.map((p) => p.coherence_mean_dem);
  const lo = ch.indexOf(Math.min(...ch)), hi = ch.indexOf(Math.max(...ch));
  put('gunw.cohMin', ch[lo]); put('gunw.cohMax', ch[hi]);
  put('gunw.cohMinPair', `${dates[lo]} – ${dates[lo + 1]}`);
  put('gunw.cohMaxPair', `${dates[hi]} – ${dates[hi + 1]}`);
  const r1 = worst.r, c1 = worst.c, r2 = worst.r + 1;
  let both = 0, mx = 0, sum = 0, cmin = 1;
  for (let p = 0; p < GP; p++) {
    const a = CC[gi(r1, c1, p)], b = CC[gi(r2, c1, p)];
    if (a !== 255 && b !== 255 && a > 0 && a === b) both++;
    const da = INC[gi(r1, c1, p)], db = INC[gi(r2, c1, p)];
    if (da !== -32768 && db !== -32768) { mx = Math.max(mx, Math.abs(da - db) / 10); sum += (da - db) / 10; }
    if (COH[gi(r1, c1, p)] !== 255) cmin = Math.min(cmin, COH[gi(r1, c1, p)] / 200);
  }
  put('chk.ccBoth', both); put('chk.maxDiff', mx); put('chk.sumDiff', sum);
  put('chk.halfCycle', meta.half_wavelength_mm / 2); put('chk.cohMin', cmin);
  // extended low-coherence series (independent re-implementation)
  {
    const WAT = u8(g.water_b64);
    const N = TH * TW;
    const isFull = (i) => { for (let e = 0; e < TE; e++) if (TS[i * TE + e] === tsj.nodata) return false; return true; };
    const fullArr = Array.from({ length: N }, (_, i) => isFull(i));
    const med = (arr) => { const q = arr.slice().sort((x, y) => x - y), h = q.length >> 1; return q.length % 2 ? q[h] : (q[h - 1] + q[h]) / 2; };
    const off = [], res = [];
    for (let p = 0; p < GP; p++) {
      const d = [];
      for (let i = 0; i < N; i++) {
        if (!fullArr[i] || INC[i * GP + p] === -32768) continue;
        d.push(INC[i * GP + p] / 10 - (TS[i * TE + p + 1] - TS[i * TE + p]));
      }
      const o = med(d); off.push(o); d.forEach((x) => res.push(x - o));
    }
    let cnt = 0; const cd = [];
    for (let i = 0; i < N; i++) {
      if (WAT[i] !== 0) continue;
      let okp = true;
      for (let p = 0; p < GP; p++) { const c = CC[i * GP + p]; if (INC[i * GP + p] === -32768 || c === 255 || c === 0) { okp = false; break; } }
      if (!okp) continue;
      let sum = 0;
      for (let p = 0; p < GP; p++) sum += INC[i * GP + p] / 10 - off[p];
      if (fullArr[i]) { cd.push(Math.abs(sum - TS[i * TE + TE - 1])); continue; }
      cnt++;
      extIdx.push(i);
    }
    put('ext.n', cnt); put('ext.nCheck', cd.length);
    put('ext.rms', Math.sqrt(res.reduce((a2, x) => a2 + x * x, 0) / res.length));
    put('ext.medCum', med(cd));
  }
  put('chk.southDisp', at(r2, c1, TE - 1));
  put('chk.tsDiff', worst.v - at(r2, c1, TE - 1));
}
put('core.vMedian', meta.core.v_median);
put('core.area', meta.core.area_km2);
put('core.cumMedian', meta.core.cum_median);
put('core.cumP01', meta.core.cum_p01);

// DEM
const dm = JSON.parse(fs.readFileSync(P('dem', 'dem.json'), 'utf8'));
const demRaw = fs.readFileSync(P('dem', 'dem.bin'));
const DEM = new Int16Array(demRaw.buffer, demRaw.byteOffset, demRaw.length / 2);
const elevOf = (i) => (DEM[i] === -32768 ? NaN : DEM[i] / 10);
let dValid = 0, dMax = -Infinity;
for (let i = 0; i < DEM.length; i++) if (DEM[i] !== -32768) { dValid++; dMax = Math.max(dMax, DEM[i] / 10); }
put('dem.wKm', (dm.right - dm.left) / 1000);
put('dem.hKm', (dm.top - dm.bottom) / 1000);
put('dem.res', dm.res_x_m);
put('dem.validPct', (dValid / DEM.length) * 100);
put('dem.elevMax', dMax);
put('dem.year', dm.source.match(/LosAngeles_(\d{4})/)[1]);
const inDem = (lon, lat) => lon >= dm.lon_min && lon <= dm.lon_max && lat >= dm.lat_min && lat <= dm.lat_max;
put('ext.nDem', extIdx.filter((i) => inDem(...pixCenter(Math.floor(i / TW), i % TW))).length);

{
  let best = null, peakAll = 0;
  for (let r = 0; r < TH; r++)
    for (let c = 0; c < TW; c++) {
      const [lon, lat] = pixCenter(r, c);
      if (!inDem(lon, lat)) continue;
      let peak = 0, ok = true;
      for (let e = 0; e < TE; e++) {
        const v = at(r, c, e);
        if (v === tsj.nodata) { ok = false; break; }
        peak = Math.max(peak, Math.abs(v));
      }
      if (!ok) continue;
      peakAll = Math.max(peakAll, peak);
      if (best === null || peak < best.peak) best = { r, c, peak };
    }
  put('stable.maxAbs', best.peak);
  const [lon, lat] = pixCenter(best.r, best.c);
  put('stable.lon', lon); put('stable.lat', lat);
  put('scale.3d', Math.ceil(peakAll / 100) * 100);
}

// active zone
const zone = J('active_zone.geojson').features[0];
put('az.area', zone.properties.pow_km2);
put('az.parts', zone.geometry.coordinates.length);
put('az.partsInDem', zone.geometry.coordinates.filter((poly) => {
  const ring = poly[0];
  const mx = ring.reduce((a, p) => a + p[0], 0) / ring.length, my = ring.reduce((a, p) => a + p[1], 0) / ring.length;
  return inDem(mx, my);
}).length);

// threatened
const thr = J('threatened.geojson').features.map((f) => ({ ...f.properties, lon: f.geometry.coordinates[0], lat: f.geometry.coordinates[1] }));
put('thr.n', thr.length);
put('inDem.thr', thr.filter((t) => inDem(t.lon, t.lat)).length);
put('inDem.bHigh', thr.filter((t) => inDem(t.lon, t.lat) && t.kategoria === 'budynek' && t.klasa === 'wysokie').length);
const prio = thr.filter((t) => t.klasa === 'wysokie' && t.okna_ruch >= 2);
put('prio.n', prio.length);
put('prio.bld', prio.filter((t) => t.kategoria === 'budynek').length);
put('prio.infra', prio.filter((t) => t.kategoria !== 'budynek').length);
const zoneThr = thr.filter((t) => t.odl_m === 0);
put('zone.thrN', zoneThr.length);
put('zone.vPos', zoneThr.filter((t) => t.v !== null && t.v > 0).length);

const sb = J('summary_buildings.json');
put('b.total', sb.liczba_budynkow);
put('b.high', sb.wg_klasy.wysokie);
put('b.med', sb.wg_klasy['średnie']);
put('b.inZone', sb.w_aktywnej_strefie);
for (const [k, v] of Object.entries(sb.w_odleglosci_od_strefy_aktywnej)) put(`b.${k}`, v);
put('b.confHigh', sb.potwierdzone_w_monitoringu.wysokie);
put('b.confMed', sb.potwierdzone_w_monitoringu['średnie']);
put('b.windows', sb.liczba_okien_monitoringu);
for (const [k, v] of Object.entries(sb.progi)) put(`th.${k}`, v);

const st = J('summary_threatened.json');
put('infra.nObjects', st.liczba_obiektow_infrastruktury);
put('infra.confirmed', st.potwierdzone_w_monitoringu.obiekty_infrastruktury);
put('b.confirmed', st.potwierdzone_w_monitoringu.budynki);
const TK = { 'droga główna': 'majorRoad', 'droga lokalna': 'localRoad', gazociąg: 'gas', kolej: 'rail', 'kolej nieczynna': 'railDisused', 'linia energetyczna': 'power' };
for (const [t, v] of Object.entries(st.infrastruktura_km)) {
  put(`infra.${TK[t]}.high`, v.wysokie);
  put(`infra.${TK[t]}.med`, v['średnie']);
  if (meta.infra[t]) { put(`infra.${TK[t]}.low`, meta.infra[t].niskie); put(`infra.${TK[t]}.nodata`, meta.infra[t]['brak danych']); }
}
put('roads.high', st.infrastruktura_km['droga główna'].wysokie + st.infrastruktura_km['droga lokalna'].wysokie);
put('roads.med', st.infrastruktura_km['droga główna']['średnie'] + st.infrastruktura_km['droga lokalna']['średnie']);
put('pipe.minDist', meta.pipelines.min_dist_m);

const topInfra = thr
  .filter((t) => t.kategoria === 'infrastruktura' && t.klasa === 'wysokie')
  .map((t, i) => ({ t, i }))
  .sort((a, b) => Math.abs(b.t.v ?? 0) - Math.abs(a.t.v ?? 0) || a.i - b.i)
  .slice(0, 8);
topInfra.forEach(({ t }, i) => {
  put(`top.${i}.v`, t.v); put(`top.${i}.grad`, t.gradient); put(`top.${i}.len`, t.dlugosc_m);
  put(`top.${i}.dist`, t.odl_m); put(`top.${i}.win`, t.okna_ruch);
});

// alerts & monitoring
const alerts = J('alerts.geojson').features.map((f) => f.properties);
put('alerts.n', alerts.length);
put('alerts.new', alerts.filter((a) => a.typ === 'nowa strefa ruchu').length);
put('alerts.decel', alerts.filter((a) => a.typ === 'spowolnienie').length);
put('alerts.accel', alerts.filter((a) => a.typ === 'przyspieszenie').length);
put('alerts.first', alerts.map((a) => a.data).sort()[0]);
put('alerts.last', alerts.map((a) => a.data).sort().at(-1));
const mon = J('monitoring.json');
const isoD = (s) => `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6)}`;
put('mon.n', mon.length);
{
  const [a, b] = mon[0].okno.split('-');
  put('mon.pairs', meta.dates.indexOf(b) - meta.dates.indexOf(a));
}
put('mon.spinup', mon.filter((w) => w.baza.startsWith('brak')).length);
put('mon.accelSum', mon.reduce((a, w) => a + w.przyspieszenie, 0));
put('mon.lastDate', isoD(mon.at(-1).data));
put('mon.lastDecel', mon.at(-1).spowolnienie);
put('mon.lastNew', mon.at(-1)['nowa strefa ruchu']);
put('mon.lastMoving', mon.at(-1).ruch_km2);
put('mon.firstAlert', isoD(mon.find((w) => w.przyspieszenie > 0 || w.spowolnienie > 0 || w['nowa strefa ruchu'] > 0).data));
put('mon.noiseMin', Math.min(...mon.map((w) => w.szum_mm_rok)));
put('mon.noiseMax', Math.max(...mon.map((w) => w.szum_mm_rok)));

// slope vs displacement
{
  proj4.defs('UTM11', '+proj=utm +zone=11 +datum=WGS84 +units=m +no_defs');
  const inv = proj4('UTM11', 'EPSG:4326');
  const W = dm.width, H = dm.height, rx = dm.res_x_m, ry = dm.res_y_m;
  const acc = new Map();
  for (let r = 1; r < H - 1; r++)
    for (let c = 1; c < W - 1; c++) {
      const [lon, lat] = inv.forward([dm.left + (c + 0.5) * rx, dm.top - (r + 0.5) * ry]);
      const mx = (lon / RAD) * 6378137, my = Math.log(Math.tan(Math.PI / 4 + lat / RAD / 2)) * 6378137;
      const col = Math.floor((mx - F.x0) / F.res), row = Math.floor((F.y1 - my) / F.res);
      if (col < 0 || row < 0 || col >= TW || row >= TH) continue;
      const i = r * W + c;
      const gx = (elevOf(i + 1) - elevOf(i - 1)) / (2 * rx);
      const gy = (elevOf(i + W) - elevOf(i - W)) / (2 * ry);
      const s = Math.atan(Math.hypot(gx, gy)) * RAD;
      if (Number.isNaN(s)) continue;
      const key = row * TW + col;
      const a = acc.get(key) || { s: 0, n: 0 };
      a.s += s; a.n++;
      acc.set(key, a);
    }
  const edges = [0, 5, 10, 15, 20, 25, 90];
  const phi = ((F.bounds[0][0] + F.bounds[1][0]) / 2) / RAD;
  const ground = F.res * Math.cos(phi);
  const need = (ground * ground) / 2 / (rx * ry);
  const groups = edges.slice(1).map(() => []);
  let total = 0;
  for (const [key, a] of acc) {
    if (a.n < need) continue;
    const v = TS[key * TE + TE - 1];
    if (v === tsj.nodata) continue;
    const mean = a.s / a.n;
    for (let b = 0; b < groups.length; b++)
      if (mean >= edges[b] && mean < edges[b + 1]) { groups[b].push(Math.abs(v)); total++; break; }
  }
  const med = (arr) => {
    if (!arr.length) return null;
    const s = arr.slice().sort((x, y) => x - y), h = Math.floor(s.length / 2);
    return s.length % 2 ? s[h] : (s[h - 1] + s[h]) / 2;
  };
  put('slope.nPix', total);
  const meds = groups.map(med);
  groups.forEach((g, i) => { put(`slope.${i}.n`, g.length); put(`slope.${i}.med`, meds[i]); });
  edges.forEach((e, i) => put(`slope.edge.${i}`, e));
  const valid = meds.map((m, i) => ({ m, i })).filter((x) => x.m !== null);
  const hi = valid.reduce((a, b) => (b.m > a.m ? b : a)), lo = valid.reduce((a, b) => (b.m < a.m ? b : a));
  put('slope.maxLo', edges[hi.i]); put('slope.maxHi', edges[hi.i + 1]); put('slope.maxMed', hi.m);
  put('slope.minLo', edges[lo.i]); put('slope.minHi', edges[lo.i + 1]); put('slope.minMed', lo.m);
}

// profiles
J('profiles.json').forEach((p, i) => {
  put(`prof.${i}.len`, p.distance_m.at(-1));
  const vals = p.values_mm.at(-1).filter((v) => v !== null).map(Math.abs);
  put(`prof.${i}.maxAbs`, vals.length ? Math.max(...vals) : null);
  put(`prof.${i}.nCross`, p.crossings.length);
});

// ---------------------------------------------------------------------------------------------
// 2. Render the built page
// ---------------------------------------------------------------------------------------------
function findBrowser() {
  const i = process.argv.indexOf('--browser');
  if (i > 0) return process.argv[i + 1];
  if (process.env.BROWSER_PATH) return process.env.BROWSER_PATH;
  const c = [
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ];
  return c.find((p) => fs.existsSync(p));
}

if (!fs.existsSync(path.join(ROOT, 'dist', 'index.html'))) {
  console.error('dist/ not found: run `npm run build` first.');
  process.exit(2);
}
const server = await preview({ root: ROOT, preview: { port: 4179, strictPort: false, open: false }, logLevel: 'silent' });
const url = server.resolvedUrls.local[0];
const browser = await puppeteer.launch({
  executablePath: findBrowser(),
  headless: true,
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--window-size=1440,900'],
  defaultViewport: { width: 1440, height: 900 },
});
const page = await browser.newPage();
const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(String(e)));
await page.goto(url, { waitUntil: 'networkidle2', timeout: 120000 });
await page.waitForSelector('body[data-ready="1"]', { timeout: 120000 });

const shown = await page.$$eval('[data-num]', (els) => els.map((e) => ({ key: e.dataset.num, text: e.textContent.trim() })));
const dyn = await page.$$eval('[data-dyn]', (els) => els.map((e) => ({ kind: e.dataset.dyn, epoch: +e.dataset.epoch, text: e.textContent.trim() })));
const uiKeys = await page.evaluate(() => Object.keys(window.__app.numbers));
const options = await page.$$eval('#ex-scale option', (els) => els.map((e) => e.textContent));

// Object panels: open a few records from different layers and read the rendered attributes.
const featuresOf = (f) => J(f).features;
const samples = [];
const pickFirst = (layer, file, file2, pred) => {
  const fs_ = featuresOf(file2);
  const i = fs_.findIndex(pred);
  if (i >= 0) samples.push({ layer, file, file2, i });
};
const fileKey = { 'buildings_risk.geojson': 'buildings', 'threatened.geojson': 'threatened', 'pipelines.geojson': 'pipelines', 'roads_risk.geojson': 'roadsRisk', 'alerts.geojson': 'alerts', 'power.geojson': 'power', 'rail.geojson': 'rail' };
pickFirst('buildings', 'buildings', 'buildings_risk.geojson', (f) => f.properties.klasa === 'wysokie');
pickFirst('threatened', 'threatened', 'threatened.geojson', () => true);
pickFirst('pipelines', 'pipelines', 'pipelines.geojson', (f) => f.properties.klasa === 'wysokie');
pickFirst('roadsRisk', 'roadsRisk', 'roads_risk.geojson', () => true);
pickFirst('alerts', 'alerts', 'alerts.geojson', () => true);
pickFirst('power', 'power', 'power.geojson', (f) => f.properties.klasa === 'średnie');
const panelReads = [];
for (const s of samples) {
  await page.evaluate((s) => window.__app.pick(s.layer, s.file, s.i), s);
  const vals = await page.$$eval('#info [data-src]', (els) => els.map((e) => ({ src: e.dataset.src, field: e.dataset.field, text: e.textContent.trim() })));
  panelReads.push({ s, vals });
}
await page.evaluate(() => (document.getElementById('info').hidden = true));

// Digits on the page that are not tied to a data value.
const loose = await page.evaluate(() => {
  const skip = (el) => el.closest('[data-num],[data-src],[data-dyn],[data-ui],[data-verify],script,style,canvas,select,.maplibregl-control-container,.marker-label');
  const out = [];
  const walker = document.createTreeWalker(document.getElementById('app'), NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const n = walker.currentNode;
    if (!/\d/.test(n.textContent) || skip(n.parentElement)) continue;
    out.push(n.textContent.replace(/\s+/g, ' ').trim());
  }
  return out;
});
await browser.close();
await new Promise((r) => server.httpServer.close(r));

// ---------------------------------------------------------------------------------------------
// 3. Compare
// ---------------------------------------------------------------------------------------------
const fails = [], passes = [];
const parse = (t) => (t === 'no data' ? null : Number(t.replace(/\u2212/g, '-').replace(/,/g, '')));
const decimals = (t) => (t.includes('.') ? t.split('.')[1].length : 0);
function check(label, expected, text) {
  if (typeof expected === 'string') {
    (expected === text ? passes : fails).push(`${label}: shown "${text}", expected "${expected}"`);
    return;
  }
  const v = parse(text);
  if (expected === null || expected === undefined) {
    (v === null ? passes : fails).push(`${label}: shown "${text}", expected no data`);
    return;
  }
  const tol = 0.5 * 10 ** -decimals(text) + 1e-9;
  (v !== null && Math.abs(v - expected) <= tol ? passes : fails).push(`${label}: shown "${text}", recomputed ${expected}`);
}

for (const { key, text } of shown) {
  if (!(key in E)) fails.push(`${key}: shown "${text}" but no independent computation exists`);
  else check(key, E[key], text);
}
for (const k of uiKeys) if (!(k in E)) fails.push(`${k}: computed by the page but not verified`);
for (const d of dyn) {
  const exp = d.kind === 'date' ? dates[d.epoch] : `${dates[0]} → ${dates[d.epoch]} (${days[d.epoch]} days)`;
  check(`dynamic ${d.kind} @${d.epoch}`, exp, d.text);
}
for (const key of ['scale.3d', 'scale.mm']) {
  const ok = options.some((o) => o.includes(`±${E[key]} mm`));
  (ok ? passes : fails).push(`a colour-scale option shows ±${E[key]} mm`);
}

const NUMERIC = new Set(['v', 'gradient', 'przem_mm', 'odl_m', 'okna_ruch', 'pow_m2', 'km', 'dlugosc_m', 'pow_km2', 'v_mm_rok']);
const RAWSTR = new Set(['okno', 'data', 'osm_id', 'nazwa', 'operator', 'podtyp', 'uzycie']);
for (const { s, vals } of panelReads) {
  const props = J(s.file2).features[s.i].properties;
  for (const { src, field, text } of vals) {
    const label = `panel ${s.file2}#${s.i}.${field}`;
    if (src !== s.file2) { fails.push(`${label}: panel cites ${src}`); continue; }
    if (NUMERIC.has(field)) check(label, props[field] ?? null, text);
    else if (RAWSTR.has(field)) check(label, props[field] || '—', text);
  }
}

const allowed = [];
const unexplained = [];
// Method constants stated in data/README.md (sampling steps, segment length, window rule) and labels.
const CONST_OK = [/^(12|24) days$/, /\b2 windows\b/, /≥ 2/, /100 m segments/, /\b20 m\b/, /\b1 m\b/, /95th percentile/, /Space Apps (Challenge )?2026/, /\bL2\b|Level-2/, /\b3D\b/, /class 2\b/, /1 km/, /250 m|500 m|within 100 m/, /12 days|24 days/, /3DEP/, /mm\/100 m\/yr/, /v > 0/, /USGS_LPC_CA_LosAngeles_2016/, /three\.js|proj4js/, /of (its )?8 neighbours/, /^4$|^[2-8]$/];
for (const t of loose) (CONST_OK.some((re) => re.test(t)) ? allowed : unexplained).push(t);

console.log(`\nVerified values: ${passes.length} OK, ${fails.length} FAILED`);
for (const f of fails) console.log('  FAIL', f);
console.log(`\nObject panels checked: ${panelReads.map((p) => `${p.s.file2}#${p.s.i} (${p.vals.length} fields)`).join(', ')}`);
console.log(`\nText containing digits outside data-bound elements: ${loose.length}`);
for (const t of allowed) console.log('  constant/label:', JSON.stringify(t.slice(0, 120)));
for (const t of unexplained) console.log('  UNEXPLAINED:', JSON.stringify(t.slice(0, 160)));
if (pageErrors.length) console.log('\nPage errors:\n  ' + pageErrors.join('\n  '));
const bad = fails.length + unexplained.length + pageErrors.length;
console.log(bad ? `\nRESULT: FAILED (${bad} issues)` : '\nRESULT: all displayed numbers match the data files');
process.exit(bad ? 1 : 0);
