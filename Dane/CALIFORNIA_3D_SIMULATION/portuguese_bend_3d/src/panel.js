// Info panel for a clicked location / object: attributes from the data file + NISAR time series.
import { rec, esc, fmt } from './format.js';
import { CLASS_EN, typeEn, CATEGORY_EN, ALERT_EN, CONFIDENCE_EN, STRUCTURE_EN, PROFILE_EN, reasonEn } from './i18n.js';
import { seriesChart, barChart } from './charts.js';
import { SERIES } from './colors.js';

const FILE = {
  buildings: 'buildings_risk.geojson',
  threatened: 'threatened.geojson',
  pipelines: 'pipelines.geojson',
  power: 'power.geojson',
  rail: 'rail.geojson',
  roadsRisk: 'roads_risk.geojson',
  alerts: 'alerts.geojson',
  zone: 'active_zone.geojson',
  profiles: 'profiles.geojson',
};
const LAYER_EN = {
  buildings: 'Building', threatened: 'Threatened element', pipelines: 'Pipeline', power: 'Power line',
  rail: 'Railway', roadsRisk: 'Road', alerts: 'Change-detection alert', zone: 'Active zone', profiles: 'Profile line',
};

const num = (v) => (v === null || v === undefined || v === '' || v === 'null' ? null : Number(v));
const yes = (v) => v === true || v === 'true';

function attrRows(layer, p) {
  const src = FILE[layer];
  const R = [];
  const add = (label, field, value, d, unit = '') => R.push([label, rec(src, field, value, d) + (unit && value !== null && value !== undefined && value !== '' ? ` ${unit}` : '')]);
  const cls = (k) => `<span class="cls-chip" style="--c:var(--risk-${['wysokie', 'średnie', 'niskie'].indexOf(k)})"></span>`;
  const klasa = p.klasa ? `${cls(p.klasa)}${rec(src, 'klasa', CLASS_EN[p.klasa] || p.klasa)}` : null;
  if (layer === 'buildings') {
    add('Name', 'nazwa', p.nazwa || '—');
    add('Building type (OSM)', 'typ_budynku', typeEn(p.typ_budynku));
    if (klasa) R.push(['Exposure class', klasa]);
    add('Reason', 'powod', reasonEn(p.powod));
    add('Ground velocity (max |v| of 5 footprint points)', 'v', num(p.v), 1, 'mm/yr');
    add('Displacement over the period (centroid)', 'przem_mm', num(p.przem_mm), 1, 'mm');
    add('Differential motion', 'gradient', num(p.gradient), 1, 'mm/100 m/yr');
    add('Distance from active zone', 'odl_m', num(p.odl_m), 0, 'm');
    add('Monitoring windows with motion', 'okna_ruch', num(p.okna_ruch), 0);
    add('Data confidence', 'pewnosc', CONFIDENCE_EN[p.pewnosc] || p.pewnosc || 'no data');
    add('Footprint area', 'pow_m2', num(p.pow_m2), 0, 'm²');
    add('OSM id', 'osm_id', p.osm_id);
  } else if (layer === 'threatened') {
    add('Category', 'kategoria', CATEGORY_EN[p.kategoria] || p.kategoria);
    add('Type', 'typ', typeEn(p.typ));
    add('Name', 'nazwa', p.nazwa || '—');
    if (klasa) R.push(['Class', klasa]);
    add('Reason', 'powod', reasonEn(p.powod));
    add('Ground velocity', 'v', num(p.v), 1, 'mm/yr');
    add('Differential motion', 'gradient', num(p.gradient), 1, 'mm/100 m/yr');
    if (num(p.dlugosc_m) !== null) add('Length in this class', 'dlugosc_m', num(p.dlugosc_m), 0, 'm');
    if (num(p.pow_m2) !== null) add('Footprint area', 'pow_m2', num(p.pow_m2), 0, 'm²');
    add('Distance from active zone', 'odl_m', num(p.odl_m), 0, 'm');
    add('Monitoring windows with motion', 'okna_ruch', num(p.okna_ruch), 0);
    add('Data confidence', 'pewnosc', CONFIDENCE_EN[p.pewnosc] || p.pewnosc || 'no data');
    add('Object id', 'obiekt_id', p.obiekt_id);
  } else if (['pipelines', 'power', 'rail', 'roadsRisk'].includes(layer)) {
    add('Type', 'typ', typeEn(p.typ));
    add('Subtype (source value)', 'podtyp', p.podtyp || '—');
    add('Name', 'nazwa', p.nazwa || '—');
    add('Operator', 'operator', p.operator || '—');
    if (p.napiecie_kv) add('Voltage', 'napiecie_kv', p.napiecie_kv, 0, 'kV');
    if (p.uzycie) add('Use', 'uzycie', p.uzycie);
    if (p.obiekt_inz) add('Structure', 'obiekt_inz', STRUCTURE_EN[p.obiekt_inz] || p.obiekt_inz);
    if (klasa) R.push(['Risk class', klasa]);
    add('Reason', 'powod', reasonEn(p.powod));
    add('Velocity (95th percentile in corridor, signed)', 'v', num(p.v), 1, 'mm/yr');
    add('Max differential motion', 'gradient', num(p.gradient), 1, 'mm/100 m/yr');
    add('Length of this segment', 'km', num(p.km), 2, 'km');
    add('Distance from active zone', 'odl_m', num(p.odl_m), 0, 'm');
    add('Data confidence', 'pewnosc', CONFIDENCE_EN[p.pewnosc] || p.pewnosc || 'no data');
    add('Route geometry', 'schem', yes(p.schem) ? 'schematic (assessed in ±300 m corridor)' : 'mapped');
    add('Source / id', 'obiekt_id', `${p.zrodlo || ''} ${p.obiekt_id || ''}`.trim());
  } else if (layer === 'alerts') {
    add('Alert type', 'typ', ALERT_EN[p.typ] || p.typ);
    add('Monitoring window', 'okno', p.okno);
    add('Alert date', 'data', p.data);
    add('Area', 'pow_km2', num(p.pow_km2), 2, 'km²');
    add('Velocity in window', 'v_mm_rok', num(p.v_mm_rok), 1, 'mm/yr');
  } else if (layer === 'zone') {
    add('Area', 'pow_km2', num(p.pow_km2), 1, 'km²');
  } else if (layer === 'profiles') {
    add('Profile', 'nazwa', PROFILE_EN[p.nazwa] || p.nazwa);
  }
  return R;
}

export function showPick(panel, D, pick) {
  if (panel._chart) { panel._chart.destroy(); panel._chart = null; }
  if (panel._chart2) { panel._chart2.destroy(); panel._chart2 = null; }
  const { lon, lat, feature } = pick;
  const title = feature ? LAYER_EN[feature.layer] || 'Object' : 'Location';
  const rows = feature ? attrRows(feature.layer, feature.f.properties) : [];
  const pix = D.ts.pixOf(lon, lat);
  let tsHtml = '';
  let series = null;
  if (!pix) {
    tsHtml = '<p class="note">Outside the NISAR analysis grid: no time series available here.</p>';
  } else {
    series = D.ts.series(pix.col, pix.row);
    let missing = series.filter((v) => v === null).length;
    const [clon, clat] = D.ts.center(pix.col, pix.row);
    let head = `<p class="note">NISAR pixel ${D.meta.focus.res} m × ${D.meta.focus.res} m, centre ${clat.toFixed(5)}° N, ${Math.abs(clon).toFixed(5)}° W (row ${pix.row}, column ${pix.col}).</p>`;
    const pi = pix.row * D.ts.w + pix.col;
    if (missing && D.tsx.low[pi]) {
      series = Array.from({ length: D.ts.E }, (_, e) => D.tsx.arr[pi * D.ts.E + e]);
      missing = 0;
      head += '<p class="note"><strong>Low-coherence pixel</strong>, not in the project time series. This series was built by us from the GUNW pairs (all unwrapped), referenced to the project series. Treat it with caution; it is not used in any statistic or risk class.</p>';
    }
    if (missing === series.length) {
      tsHtml = head + '<p class="note">No valid NISAR data for this pixel (water or loss of coherence).</p>';
      series = null;
    } else {
      tsHtml = head + (missing ? `<p class="note">${missing} of ${series.length} dates without data (shown as gaps).</p>` : '') + '<div class="chart-box small"><canvas aria-label="LOS displacement time series for the selected pixel"></canvas></div>';
    }
  }
  panel.innerHTML = `
    <div class="panel-head">
      <h3>${esc(title)}</h3>
      <button type="button" class="btn-close" aria-label="Close details">×</button>
    </div>
    <p class="coords">Clicked at ${lat.toFixed(5)}° N, ${Math.abs(lon).toFixed(5)}° W</p>
    ${rows.length ? `<table class="attr"><tbody>${rows.map(([k, v]) => `<tr><th scope="row">${esc(k)}</th><td>${v}</td></tr>`).join('')}</tbody></table><p class="src">Source: data/${esc(FILE[feature.layer])} (our analysis on NISAR data)</p>` : ''}
    <h4>LOS displacement at this location (NISAR measurement)</h4>
    ${tsHtml}`;
  panel.hidden = false;
  panel.querySelector('.btn-close').addEventListener('click', () => {
    panel.hidden = true;
  });
  if (series) {
    panel._chart = seriesChart(panel.querySelector('canvas'), D, [{ label: 'This pixel', color: SERIES.max, values: series }], { animate: false });
  }
  // GUNW quality of each pair at this pixel.
  if (pix) {
    const G = D.gunw;
    const coh = [], cc0 = [];
    for (let p = 0; p < G.P; p++) {
      coh.push(G.coh(pix.col, pix.row, p));
      if (G.cc(pix.col, pix.row, p) === 0) cc0.push(D.dates[p + 1]);
    }
    if (coh.some((c) => c !== null)) {
      const box = document.createElement('div');
      box.innerHTML = `<h4>Signal quality per pair (GUNW)</h4>
        <p class="note">${cc0.length ? `Not reliably unwrapped (connected component 0) in pairs ending ${cc0.join(', ')}.` : 'Unwrapped (connected component ≥ 1) in every pair.'}</p>
        <div class="chart-box small"><canvas aria-label="Coherence of each interferometric pair at the selected pixel"></canvas></div>`;
      panel.appendChild(box);
      panel._chart2 = barChart(box.querySelector('canvas'), {
        labels: D.dates.slice(1).map((d) => d.slice(5)),
        values: coh,
        color: '#6baed6',
        yTitle: 'Coherence (0–1)',
        xTitle: 'Pair end date (MM-DD)',
        tooltip: (it) => `coherence ${it.parsed.y === null ? 'no data' : it.parsed.y.toFixed(2)}`,
        animate: false,
      });
    }
  }
  panel.focus();
}

export { fmt };
