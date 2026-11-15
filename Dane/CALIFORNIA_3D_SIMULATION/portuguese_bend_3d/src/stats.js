// Every number displayed on the page is computed here from the loaded data files.
// scripts/verify_numbers.mjs re-implements these computations independently.
import { setNum, setStr } from './format.js';
import { utm, merc } from './data.js';

export const SLOPE_BINS = [0, 5, 10, 15, 20, 25, 90];
export const TYPE_KEYS = {
  'droga główna': 'majorRoad',
  'droga lokalna': 'localRoad',
  gazociąg: 'gas',
  kolej: 'rail',
  'kolej nieczynna': 'railDisused',
  'linia energetyczna': 'power',
};
export const TOP_N = 8;

export function median(a) {
  if (!a.length) return null;
  const s = [...a].sort((x, y) => x - y);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

// Map every 5 m DEM cell centre to its 100 m NISAR pixel (nearest pixel, no interpolation).
export function mapDemToTs(D) {
  const { dem, ts } = D;
  const idx = new Int32Array(dem.w * dem.h).fill(-1);
  // Fractional grid position relative to pixel centres (for optional display smoothing in 3D).
  const fx = new Float32Array(dem.w * dem.h), fy = new Float32Array(dem.w * dem.h);
  for (let r = 0; r < dem.h; r++) {
    const y = dem.top - (r + 0.5) * dem.ry;
    for (let c = 0; c < dem.w; c++) {
      const x = dem.left + (c + 0.5) * dem.rx;
      const [lon, lat] = utm.inverse([x, y]);
      const [mx, my] = merc(lon, lat);
      const gx = (mx - ts.x0) / ts.res, gy = (ts.y1 - my) / ts.res;
      const col = Math.floor(gx);
      const row = Math.floor(gy);
      fx[r * dem.w + c] = gx - 0.5;
      fy[r * dem.w + c] = gy - 0.5;
      if (col >= 0 && row >= 0 && col < ts.w && row < ts.h) idx[r * dem.w + c] = row * ts.w + col;
    }
  }
  dem.tsIdx = idx;
  dem.tsFx = fx;
  dem.tsFy = fy;
}

export function slopeDeg(dem, r, c) {
  const { w, h, elev, rx, ry } = dem;
  if (r < 1 || c < 1 || r >= h - 1 || c >= w - 1) return NaN;
  const i = r * w + c;
  const dzdx = (elev[i + 1] - elev[i - 1]) / (2 * rx);
  const dzdy = (elev[i - w] - elev[i + w]) / (2 * ry);
  return (Math.atan(Math.sqrt(dzdx * dzdx + dzdy * dzdy)) * 180) / Math.PI;
}

// Low-coherence pixels that the project time series leaves out, but where every GUNW pair is
// unwrapped (connected component >= 1) and not water: their series is built here by chaining the
// per-pair GUNW increments after removing, for each pair, the median offset to the project series
// (pixels with a full project series). Used only for the 3D display and the object panel; it never
// enters statistics or risk classes.
export function buildExtended(D) {
  const { ts, gunw: G } = D;
  const n = ts.w * ts.h, E = ts.E, P = G.P;
  const full = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    let ok = 1;
    for (let e = 0; e < E; e++) if (ts.arr[i * E + e] === ts.nodata) { ok = 0; break; }
    full[i] = ok;
  }
  const offsets = [], resid = [];
  for (let p = 0; p < P; p++) {
    const r = [];
    for (let i = 0; i < n; i++) {
      if (!full[i]) continue;
      const g = G.incArr[i * P + p];
      if (g === -32768) continue;
      r.push(g / 10 - (ts.arr[i * E + p + 1] - ts.arr[i * E + p]));
    }
    const o = median(r);
    offsets.push(o);
    for (const x of r) resid.push(x - o);
  }
  const arr = new Int16Array(ts.arr);
  const low = new Uint8Array(n);
  const cumDiff = [];
  let count = 0;
  for (let i = 0; i < n; i++) {
    let usable = G.water[i] === 0;
    for (let p = 0; usable && p < P; p++) {
      const c = G.ccArr[i * P + p];
      if (G.incArr[i * P + p] === -32768 || c === 255 || c < 1) usable = false;
    }
    if (!usable) continue;
    let cum = 0;
    const s = [0];
    for (let p = 0; p < P; p++) { cum += G.incArr[i * P + p] / 10 - offsets[p]; s.push(cum); }
    if (full[i]) { cumDiff.push(Math.abs(cum - ts.arr[i * E + E - 1])); continue; }
    for (let e = 0; e < E; e++) arr[i * E + e] = Math.round(s[e]);
    low[i] = 1;
    count++;
  }
  D.tsx = { arr, low, offsets, count };
  let nDem = 0;
  for (let i = 0; i < n; i++) {
    if (!low[i]) continue;
    const [lon, lat] = ts.center(i % ts.w, Math.floor(i / ts.w));
    if (D.dem.inside(lon, lat)) nDem++;
  }
  setNum('ext.nDem', nDem);
  const rms = Math.sqrt(resid.reduce((a, x) => a + x * x, 0) / resid.length);
  setNum('ext.n', count);
  setNum('ext.rms', rms, 1);
  setNum('ext.medCum', median(cumDiff), 1);
  setNum('ext.nCheck', cumDiff.length);
}

export function computeStats(D) {
  const { meta, ts, dem } = D;
  const last = ts.E - 1;

  // --- period and acquisition geometry -------------------------------------------
  setStr('period.start', D.dates[0]);
  setStr('period.end', D.dates[D.dates.length - 1]);
  setNum('period.days', meta.period.days);
  setNum('period.nDates', meta.dates.length);
  setNum('period.nPairs', meta.dates.length - 1);
  const gaps = D.dayOffsets.slice(1).map((d, i) => d - D.dayOffsets[i]);
  setNum('period.n12', gaps.filter((g) => g === 12).length);
  setNum('period.n24', gaps.filter((g) => g === 24).length);
  const g24 = gaps.map((g, i) => (g === 24 ? i : -1)).filter((i) => i >= 0);
  if (g24.length) {
    setStr('period.gapFrom', D.dates[g24[0]]);
    setStr('period.gapTo', D.dates[g24[g24.length - 1] + 1]);
  }
  const m = /track (\d+), (\w+), frame (\d+)/.exec(meta.stack);
  setStr('orbit.track', m[1]);
  setStr('orbit.direction', m[2]);
  setStr('orbit.frame', m[3]);
  setNum('sar.halfWl', meta.half_wavelength_mm);
  setNum('sar.wl', 2 * meta.half_wavelength_mm);
  setNum('scale.mm', meta.scale_mm);
  setNum('grid.res', meta.focus.res);
  setNum('noise', meta.noise_mm_yr, 1);

  // --- time-series grid --------------------------------------------------------------
  let full = 0, minV = Infinity, minAt = null;
  for (let row = 0; row < ts.h; row++)
    for (let col = 0; col < ts.w; col++) {
      let ok = true;
      for (let e = 0; e < ts.E; e++) if (ts.value(col, row, e) === null) { ok = false; break; }
      if (ok) full++;
      const v = ts.value(col, row, last);
      if (v !== null && v < minV) { minV = v; minAt = { col, row }; }
    }
  setNum('grid.fullN', full);
  setNum('grid.fullPct', (100 * full) / (ts.w * ts.h), 1);
  setNum('max.disp', minV);
  const [mlon, mlat] = ts.center(minAt.col, minAt.row);
  setNum('max.lon', mlon, 4);
  setNum('max.lat', mlat, 4);
  setNum('max.rateDay', Math.abs(minV) / meta.period.days, 2);
  const mid = Math.floor(last / 2);
  const ser = ts.series(minAt.col, minAt.row);
  setStr('max.midDate', D.dates[mid]);
  setNum('max.rate1', (ser[mid] - ser[0]) / (D.dayOffsets[mid] - D.dayOffsets[0]), 2);
  setNum('max.rate2', (ser[last] - ser[mid]) / (D.dayOffsets[last] - D.dayOffsets[mid]), 2);
  setNum('max.dispMid', ser[mid]);

  // Second most negative pixel and the neighbourhood of the extreme pixel (outlier context).
  let min2 = Infinity, min2At = null;
  for (let row = 0; row < ts.h; row++)
    for (let col = 0; col < ts.w; col++) {
      if (row === minAt.row && col === minAt.col) continue;
      const v = ts.value(col, row, last);
      if (v !== null && v < min2) { min2 = v; min2At = { col, row }; }
    }
  setNum('max2.disp', min2);
  const [m2lon, m2lat] = ts.center(min2At.col, min2At.row);
  setNum('max2.lon', m2lon, 4);
  setNum('max2.lat', m2lat, 4);
  setNum('max.gap', min2 - minV);
  let nbFull = 0;
  for (let dr = -1; dr <= 1; dr++)
    for (let dc = -1; dc <= 1; dc++) {
      if (!dr && !dc) continue;
      const r = minAt.row + dr, c = minAt.col + dc;
      if (r < 0 || c < 0 || r >= ts.h || c >= ts.w) continue;
      if (ts.series(c, r).every((v) => v !== null)) nbFull++;
    }
  setNum('max.nbFull', nbFull);
  // Is the gap to the next pixel within ±25 % of one fringe cycle (typical unwrapping-error size)?
  D.gapNearCycle = Math.abs(min2 - minV - meta.half_wavelength_mm) <= 0.25 * meta.half_wavelength_mm;

  // --- GUNW products: status, geometry, quality, check of the extreme pixel ------------------
  const G = D.gunw;
  const colls = [...new Set(G.pairs.map((p) => p.collection))];
  const vers = [...new Set(G.pairs.map((p) => p.product_version))];
  setStr('gunw.collection', colls.join(', '));
  setStr('gunw.version', vers.join(', '));
  setStr('gunw.status', colls.map((c) => (/_(PROVISIONAL|BETA|VALIDATED|BETA_V\d)/.exec(c) || [, 'not stated'])[1]).join(', '));
  setNum('gunw.nPairs', G.pairs.length);
  setNum('gunw.inc', G.geometry.incidence_deg_mean, 1);
  setNum('gunw.incMin', G.geometry.incidence_deg_min, 1);
  setNum('gunw.incMax', G.geometry.incidence_deg_max, 1);
  const le = G.geometry.los_east_mean, ln = G.geometry.los_north_mean;
  const lu = Math.sqrt(Math.max(0, 1 - le * le - ln * ln));
  setNum('los.e', le, 2);
  setNum('los.n', ln, 2);
  setNum('los.u', lu, 2);
  setStr('los.side', le < 0 ? 'west' : 'east');
  D.losVec = [le, lu, ln];
  const cohs = G.pairs.map((p) => p.coherence_mean_dem);
  const iLo = cohs.indexOf(Math.min(...cohs)), iHi = cohs.indexOf(Math.max(...cohs));
  setNum('gunw.cohMin', cohs[iLo], 2);
  setNum('gunw.cohMax', cohs[iHi], 2);
  setStr('gunw.cohMinPair', `${D.dates[iLo]} – ${D.dates[iLo + 1]}`);
  setStr('gunw.cohMaxPair', `${D.dates[iHi]} – ${D.dates[iHi + 1]}`);

  // Extreme pixel vs. its southern neighbour in the GUNW pairs.
  const s = { col: minAt.col, row: minAt.row + 1 };
  let ccBoth = 0, maxDiff = 0, sumDiff = 0, cohMin = Infinity;
  for (let p = 0; p < G.P; p++) {
    const a = G.cc(minAt.col, minAt.row, p), b = G.cc(s.col, s.row, p);
    if (a !== null && b !== null && a > 0 && a === b) ccBoth++;
    const da = G.inc(minAt.col, minAt.row, p), db = G.inc(s.col, s.row, p);
    if (da !== null && db !== null) {
      maxDiff = Math.max(maxDiff, Math.abs(da - db));
      sumDiff += da - db;
    }
    const c = G.coh(minAt.col, minAt.row, p);
    if (c !== null) cohMin = Math.min(cohMin, c);
  }
  setNum('chk.ccBoth', ccBoth);
  setNum('chk.maxDiff', maxDiff, 1);
  setNum('chk.sumDiff', sumDiff, 0);
  setNum('chk.halfCycle', meta.half_wavelength_mm / 2, 1);
  setNum('chk.cohMin', cohMin, 2);
  buildExtended(D);
  setNum('chk.southDisp', ts.value(s.col, s.row, last));
  setNum('chk.tsDiff', minV - ts.value(s.col, s.row, last));
  D.chkOk = ccBoth === G.P && maxDiff < meta.half_wavelength_mm / 2;

  // --- landslide core (meta.core) ----------------------------------------------------
  setNum('core.vMedian', meta.core.v_median);
  setNum('core.area', meta.core.area_km2, 2);
  setNum('core.cumMedian', meta.core.cum_median);
  setNum('core.cumP01', meta.core.cum_p01);

  // --- DEM -------------------------------------------------------------------------------
  setNum('dem.wKm', (dem.right - dem.left) / 1000, 2);
  setNum('dem.hKm', (dem.top - dem.bottom) / 1000, 2);
  setNum('dem.res', dem.rx, 0);
  setNum('dem.validPct', 100 * dem.validFrac, 1);
  setNum('dem.elevMax', dem.max, 1);
  setStr('dem.year', /_(\d{4})_LAS/.exec(dem.source)[1]);

  // Stable reference: pixel inside the DEM with a full series and the smallest peak |value|.
  let best = null;
  let inDemMaxAbs = 0;
  for (let row = 0; row < ts.h; row++)
    for (let col = 0; col < ts.w; col++) {
      const [lon, lat] = ts.center(col, row);
      if (!dem.inside(lon, lat)) continue;
      const s = ts.series(col, row);
      if (s.some((v) => v === null)) continue;
      const pk = Math.max(...s.map(Math.abs));
      if (pk > inDemMaxAbs) inDemMaxAbs = pk;
      if (!best || pk < best.pk) best = { col, row, pk };
    }
  D.stable = best;
  D.maxPix = minAt;
  setNum('stable.maxAbs', best.pk);
  const [slon, slat] = ts.center(best.col, best.row);
  setNum('stable.lon', slon, 4);
  setNum('stable.lat', slat, 4);
  setNum('scale.3d', Math.ceil(inDemMaxAbs / 100) * 100);
  D.scale3d = Math.ceil(inDemMaxAbs / 100) * 100;

  // --- active zone ---------------------------------------------------------------------
  const zf = D.zone.features[0];
  setNum('az.area', zf.properties.pow_km2, 1);
  const parts = zf.geometry.coordinates;
  setNum('az.parts', parts.length);
  setNum(
    'az.partsInDem',
    parts.filter((p) => {
      const ring = p[0];
      const lon = ring.reduce((s, c) => s + c[0], 0) / ring.length;
      const lat = ring.reduce((s, c) => s + c[1], 0) / ring.length;
      return dem.inside(lon, lat);
    }).length,
  );

  // --- threatened elements -------------------------------------------------------------
  const thr = D.threatened.features;
  const inDem = thr.filter((f) => dem.inside(...f.geometry.coordinates));
  setNum('thr.n', thr.length);
  setNum('inDem.thr', inDem.length);
  setNum('inDem.bHigh', inDem.filter((f) => f.properties.kategoria === 'budynek' && f.properties.klasa === 'wysokie').length);
  const prio = thr.filter((f) => f.properties.klasa === 'wysokie' && f.properties.okna_ruch >= 2);
  setNum('prio.n', prio.length);
  setNum('prio.bld', prio.filter((f) => f.properties.kategoria === 'budynek').length);
  setNum('prio.infra', prio.filter((f) => f.properties.kategoria === 'infrastruktura').length);

  const sb = D.sumBuildings;
  setNum('b.total', sb.liczba_budynkow);
  setNum('b.high', sb.wg_klasy['wysokie']);
  setNum('b.med', sb.wg_klasy['średnie']);
  setNum('b.inZone', sb.w_aktywnej_strefie);
  const dist = sb.w_odleglosci_od_strefy_aktywnej;
  for (const k of Object.keys(dist)) setNum(`b.${k}`, dist[k]);
  setNum('b.confHigh', sb.potwierdzone_w_monitoringu['wysokie']);
  setNum('b.confMed', sb.potwierdzone_w_monitoringu['średnie']);
  setNum('b.windows', sb.liczba_okien_monitoringu);
  for (const [k, v] of Object.entries(sb.progi)) setNum(`th.${k}`, v, k === 'v_med' ? 1 : 0);

  const st = D.sumThreatened;
  setNum('infra.nObjects', st.liczba_obiektow_infrastruktury);
  setNum('infra.confirmed', st.potwierdzone_w_monitoringu.obiekty_infrastruktury);
  setNum('b.confirmed', st.potwierdzone_w_monitoringu.budynki);
  for (const [t, v] of Object.entries(st.infrastruktura_km)) {
    const k = TYPE_KEYS[t];
    setNum(`infra.${k}.high`, v['wysokie'], 2);
    setNum(`infra.${k}.med`, v['średnie'], 2);
    const all = meta.infra[t];
    if (all) {
      setNum(`infra.${k}.low`, all['niskie'], 2);
      setNum(`infra.${k}.nodata`, all['brak danych'], 2);
    }
  }
  setNum('roads.high', st.infrastruktura_km['droga główna']['wysokie'] + st.infrastruktura_km['droga lokalna']['wysokie'], 2);
  setNum('roads.med', st.infrastruktura_km['droga główna']['średnie'] + st.infrastruktura_km['droga lokalna']['średnie'], 2);
  setNum('pipe.minDist', meta.pipelines.min_dist_m);

  // Top infrastructure in the high class, ranked by |v|.
  const top = thr
    .filter((f) => f.properties.kategoria === 'infrastruktura' && f.properties.klasa === 'wysokie')
    .sort((a, b) => Math.abs(b.properties.v ?? 0) - Math.abs(a.properties.v ?? 0))
    .slice(0, TOP_N);
  D.top = top;
  top.forEach((f, i) => {
    const p = f.properties;
    setNum(`top.${i}.v`, p.v, 0);
    setNum(`top.${i}.grad`, p.gradient, 0);
    setNum(`top.${i}.len`, p.dlugosc_m, 0);
    setNum(`top.${i}.dist`, p.odl_m, 0);
    setNum(`top.${i}.win`, p.okna_ruch, 0);
  });

  // --- alerts and monitoring -------------------------------------------------------------
  const al = D.alerts.features.map((f) => f.properties);
  setNum('alerts.n', al.length);
  setNum('alerts.new', al.filter((a) => a.typ === 'nowa strefa ruchu').length);
  setNum('alerts.decel', al.filter((a) => a.typ === 'spowolnienie').length);
  setNum('alerts.accel', al.filter((a) => a.typ === 'przyspieszenie').length);
  const adates = al.map((a) => a.data).sort();
  setStr('alerts.first', adates[0]);
  setStr('alerts.last', adates[adates.length - 1]);

  const mon = D.monitoring;
  setNum('mon.n', mon.length);
  const [ws, we] = mon[0].okno.split('-');
  setNum('mon.pairs', meta.dates.indexOf(we) - meta.dates.indexOf(ws));
  setNum('mon.spinup', mon.filter((w) => w.baza !== 'trailing').length);
  setNum('mon.accelSum', mon.reduce((s, w) => s + w.przyspieszenie, 0), 1);
  const lw = mon[mon.length - 1];
  setStr('mon.lastDate', `${lw.data.slice(0, 4)}-${lw.data.slice(4, 6)}-${lw.data.slice(6, 8)}`);
  setNum('mon.lastDecel', lw.spowolnienie, 2);
  setNum('mon.lastNew', lw['nowa strefa ruchu'], 2);
  setNum('mon.lastMoving', lw.ruch_km2, 2);
  const firstAlertWin = mon.find((w) => w['nowa strefa ruchu'] + w.spowolnienie + w.przyspieszenie > 0);
  setStr('mon.firstAlert', `${firstAlertWin.data.slice(0, 4)}-${firstAlertWin.data.slice(4, 6)}-${firstAlertWin.data.slice(6, 8)}`);
  setNum('mon.noiseMin', Math.min(...mon.map((w) => w.szum_mm_rok)), 1);
  setNum('mon.noiseMax', Math.max(...mon.map((w) => w.szum_mm_rok)), 1);

  // --- slope vs. displacement (DEM x NISAR) ----------------------------------------------
  const sum = new Float64Array(ts.w * ts.h), cnt = new Int32Array(ts.w * ts.h), tot = new Int32Array(ts.w * ts.h);
  for (let r = 0; r < dem.h; r++)
    for (let c = 0; c < dem.w; c++) {
      const i = r * dem.w + c;
      const p = dem.tsIdx[i];
      if (p < 0) continue;
      tot[p]++;
      const s = slopeDeg(dem, r, c);
      if (Number.isNaN(s)) continue;
      sum[p] += s;
      cnt[p]++;
    }
  // A 100 m Web Mercator pixel is res * cos(lat) metres wide on the ground.
  const latC = (meta.focus.bounds[0][0] + meta.focus.bounds[1][0]) / 2;
  const minCells = (0.5 * (ts.res * Math.cos((latC * Math.PI) / 180)) ** 2) / (dem.rx * dem.ry);
  const bins = SLOPE_BINS.slice(0, -1).map(() => []);
  let nPix = 0;
  for (let p = 0; p < ts.w * ts.h; p++) {
    if (cnt[p] < minCells) continue; // at least half of the pixel's ground area covered by valid DEM cells
    const v = ts.arr[p * ts.E + last];
    if (v === ts.nodata) continue;
    const s = sum[p] / cnt[p];
    const b = SLOPE_BINS.findIndex((lo, k) => k < SLOPE_BINS.length - 1 && s >= lo && s < SLOPE_BINS[k + 1]);
    if (b < 0) continue;
    bins[b].push(Math.abs(v));
    nPix++;
  }
  D.slopeBins = bins.map((a, i) => ({ lo: SLOPE_BINS[i], hi: SLOPE_BINS[i + 1], n: a.length, med: median(a) }));
  setNum('slope.nPix', nPix);
  D.slopeBins.forEach((b, i) => {
    setNum(`slope.${i}.n`, b.n);
    setNum(`slope.${i}.med`, b.med);
  });

  const withMed = D.slopeBins.filter((b) => b.med !== null);
  const hiBin = withMed.reduce((a, b) => (b.med > a.med ? b : a));
  const loBin = withMed.reduce((a, b) => (b.med < a.med ? b : a));
  setNum('slope.maxLo', hiBin.lo);
  setNum('slope.maxHi', hiBin.hi);
  setNum('slope.maxMed', hiBin.med);
  setNum('slope.minLo', loBin.lo);
  setNum('slope.minHi', loBin.hi);
  setNum('slope.minMed', loBin.med);

  // Elements inside the active zone whose signed velocity is positive (see Limitations).
  const inZone = thr.filter((f) => f.properties.odl_m === 0);
  setNum('zone.thrN', inZone.length);
  setNum('zone.vPos', inZone.filter((f) => (f.properties.v ?? 0) > 0).length);

  // --- profiles ----------------------------------------------------------------------------
  D.profiles.forEach((p, i) => {
    setNum(`prof.${i}.len`, p.distance_m[p.distance_m.length - 1]);
    const vals = p.values_mm[last].filter((v) => v !== null).map(Math.abs);
    setNum(`prof.${i}.maxAbs`, vals.length ? Math.max(...vals) : null);
    setNum(`prof.${i}.nCross`, p.crossings.length);
  });

  // Labels of the legend bins shown in charts.
  SLOPE_BINS.forEach((b, i) => setNum(`slope.edge.${i}`, b));
}
