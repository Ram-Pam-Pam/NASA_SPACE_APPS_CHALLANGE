import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
import './style.css';
import { loadAll, loadRoadsContext } from './data.js';
import { computeStats, mapDemToTs } from './stats.js';
import { N, text, n, esc } from './format.js';
import { renderStory } from './story.js';
import { TerrainViewer, filledField } from './terrain3d.js';
import { createMap } from './map2d.js';
import { seriesChart, profileCharts, barChart, groupedBarChart } from './charts.js';
import { showPick } from './panel.js';
import { SERIES, INK, rdbuGradientCss } from './colors.js';
import { CLASS_EN, CLASS_ORDER, PROFILE_EN } from './i18n.js';

const STORY_EXAG = 120;
// Observers must stay referenced, otherwise the browser may garbage-collect them before they fire.
const KEPT = [];
const keep = (o) => (KEPT.push(o), o);
const loading = document.getElementById('loading');
const app = document.getElementById('app');

main().catch((err) => {
  console.error(err);
  loading.textContent = `Could not load the data: ${err.message}`;
});

async function main() {
  const D = await loadAll((f) => (loading.textContent = `Loading data… ${Math.round(f * 100)} %`));
  loading.textContent = 'Preparing terrain…';
  await new Promise((r) => setTimeout(r, 0));
  mapDemToTs(D);
  computeStats(D);
  D.demYear = text('dem.year');
  app.innerHTML = renderStory(D);
  loading.hidden = true;

  setupStory(D);
  setupCharts(D);
  setupRiskMap(D);
  setupExplore(D);

  window.__app = {
    numbers: Object.fromEntries([...N].map(([k, v]) => [k, v])),
    pick: (layer, file, index) => {
      const f = D[file].features[index];
      const c = f.geometry.type === 'Point' ? f.geometry.coordinates : firstCoord(f.geometry);
      showPick(document.getElementById('info'), D, { lon: c[0], lat: c[1], feature: { layer, f } });
    },
    ready: true,
  };
  document.body.dataset.ready = '1';
  // Precompute the display-only filled 3D surface while the reader is still at the top of the page.
  (window.requestIdleCallback || setTimeout)(() => filledField(D, true));
}

function firstCoord(g) {
  let c = g.coordinates;
  while (Array.isArray(c[0])) c = c[0];
  return c;
}

// ------------------------------------------------------------------------------------------------
function riskStyle(D, width, extra = {}) {
  const C = D.meta.risk_colors;
  return (f) => {
    const k = f.properties.klasa;
    return { key: k, color: C[k] || C['brak danych'], width, ...extra };
  };
}

function addStandardLayers(v, D, vis = {}) {
  const C = D.meta.risk_colors;
  v.addLayer('zone', D.zone.features, () => ({ key: 'z', color: INK.zone, width: 3.2, order: 3 }), { lift: 6, visible: vis.zone ?? true });
  // One layer per alert date, so alerts appear on the date they were raised.
  const byDate = new Map();
  for (const f of D.alerts.features) {
    const d = f.properties.data;
    if (!byDate.has(d)) byDate.set(d, []);
    byDate.get(d).push(f);
  }
  v.alertDates = [...byDate.keys()].sort();
  v.alertsOn = vis.alerts ?? false;
  for (const [d, fs] of byDate) v.addLayer(`alerts@${d}`, fs, () => ({ key: 'a', color: INK.alert, width: 2.4, dashed: true, order: 2 }), { lift: 5, visible: false });
  syncAlerts(v, D);
  v.addLayer(
    'buildings',
    D.buildings.features,
    (f) => {
      const k = f.properties.klasa;
      return k === 'niskie'
        ? { key: k, color: '#7d878f', width: 1, opacity: 0.8 }
        : { key: k, color: C[k] || C['brak danych'], width: 1.8, order: 2 };
    },
    { lift: 2, visible: vis.buildings ?? true },
  );
  v.addLayer('roadsRisk', D.roadsRisk.features, riskStyle(D, 3.2), { lift: 3, visible: vis.roadsRisk ?? true });
  v.addLayer('pipelines', D.pipelines.features, riskStyle(D, 4.5), { lift: 4, visible: vis.pipelines ?? true });
  v.addLayer('power', D.power.features, riskStyle(D, 2, { dashed: true }), { lift: 8, visible: vis.power ?? true });
  v.addLayer('rail', D.rail.features, riskStyle(D, 2.6, { dashed: true }), { lift: 3, visible: vis.rail ?? true });
  v.addLayer('profiles', D.profilesGeo.features, () => ({ key: 'p', color: '#3b4650', width: 2.2, order: 2 }), { lift: 7, visible: vis.profiles ?? false });
}

// Show alerts raised on or before the current date (when the alerts layer is on).
function syncAlerts(v, D) {
  if (!v.alertDates) return;
  const now = D.dates[v.epoch];
  for (const d of v.alertDates) v.setLayerVisible(`alerts@${d}`, v.alertsOn && d <= now);
}

function keyMarkers(D) {
  const [mlon, mlat] = D.ts.center(D.maxPix.col, D.maxPix.row);
  const [slon, slat] = D.ts.center(D.stable.col, D.stable.row);
  return [
    { lon: mlon, lat: mlat, label: 'Largest value (edge pixel)', color: SERIES.max },
    { lon: D.meta.core.lon, lat: D.meta.core.lat, label: 'Landslide core', color: SERIES.core },
    { lon: slon, lat: slat, label: 'Stable reference', color: SERIES.stable },
  ];
}

function legendBar(scaleKey) {
  return `<div class="ramp-wrap">
    <div class="ramp" style="background:${rdbuGradientCss()}"></div>
    <div class="ramp-labels"><span>−${n(scaleKey)} mm</span><span data-ui>0</span><span>+${n(scaleKey)} mm</span></div>
    <div class="ramp-labels sub"><span>away from satellite</span><span>towards</span></div>
    <div class="nodata-key"><i></i> no NISAR data</div>
    <div class="nodata-key"><i class="est"></i> faint: estimated from neighbouring pixels (3D display only)</div>
    <div class="nodata-key"><i class="pale"></i> pale: low-coherence pixel, own series from GUNW</div>
  </div>`;
}

function qualityLegend(mode) {
  if (mode === 'coh')
    return `<div class="ramp-wrap" data-ui>
      <div class="ramp" style="background:linear-gradient(to right,#f7fbff,#6baed6,#08306b)"></div>
      <div class="ramp-labels"><span>0</span><span>0.5</span><span>1</span></div>
      <div class="ramp-labels sub"><span>coherence of the pair ending on the shown date</span></div>
      <div class="nodata-key"><i></i> no data / first date (no pair)</div></div>`;
  return `<div class="legend" data-ui>
    <div class="li"><i class="sw" style="background:#c0601a"></i>component 0: not reliably unwrapped</div>
    <div class="li"><i class="sw" style="background:#c7d6e0"></i>unwrapped component</div>
    <div class="nodata-key"><i></i> no data / first date (no pair)</div></div>`;
}

function dateSpan(D, e) {
  return `<span data-dyn="span" data-epoch="${e}">${D.dates[0]} → ${D.dates[e]} (${D.dayOffsets[e]} days)</span>`;
}

// ------------------------------------------------------------------------------------------------
function setupStory(D) {
  const el = document.getElementById('viewer-story');
  const v = new TerrainViewer(el, D, {
    zoom: false,
    exag: 0,
    surface: 'raw',
    label: '3D model of the Portuguese Bend terrain coloured by NISAR line-of-sight displacement',
  });
  addStandardLayers(v, D, { zone: false, buildings: false, roadsRisk: false, pipelines: false, power: false, rail: false });
  const setScale = (key) => {
    v.setScale(key === 'scale.mm' ? D.meta.scale_mm : D.scale3d);
    document.getElementById('story-legend').innerHTML = legendBar(key);
  };
  setScale('scale.mm');
  const dateEl = document.getElementById('story-date');
  const spanEl = document.getElementById('story-span');
  const exagEl = document.getElementById('story-exag');
  document.getElementById('story-exag-text').textContent = `×${STORY_EXAG}`;
  const setE = (e) => {
    v.setEpoch(e);
    syncAlerts(v, D);
    dateEl.innerHTML = `<span data-dyn="date" data-epoch="${e}">${D.dates[e]}</span>`;
    spanEl.innerHTML = dateSpan(D, e);
  };
  setE(D.ts.E - 1);

  let timer = null;
  const play = () => {
    clearInterval(timer);
    let e = 0;
    setE(0);
    timer = setInterval(() => {
      e++;
      if (e >= D.ts.E) { clearInterval(timer); return; }
      setE(e);
    }, 550);
  };
  document.getElementById('story-replay').addEventListener('click', play);

  const states = {
    overview: () => { setScale('scale.mm'); clearInterval(timer); v.setSmooth(false); v.view('overview'); setLayers({}); v.setExag(0); v.setMarkers([]); setE(D.ts.E - 1); },
    zone: () => { setScale('scale.mm'); clearInterval(timer); v.setSmooth(false); v.view('overview'); setLayers({ zone: true }); v.setExag(0); v.setMarkers([]); },
    core: () => { setScale('scale.3d'); clearInterval(timer); v.setSmooth(false); v.view('core'); setLayers({ zone: true, buildings: true, roadsRisk: true, pipelines: true }); v.setExag(0); },
    play: () => { setScale('scale.mm'); v.setSmooth(false); v.view('overview'); setLayers({ zone: true, alerts: true }); v.setExag(0); v.setMarkers(keyMarkers(D)); play(); },
    deform: () => { setScale('scale.3d'); v.setSmooth(true); v.view('core'); setLayers({ zone: true, alerts: true }); v.setExag(STORY_EXAG); v.setMarkers(keyMarkers(D)); play(); },
  };
  const setLayers = (on) => {
    for (const id of ['zone', 'buildings', 'roadsRisk', 'pipelines', 'power', 'rail', 'profiles']) v.setLayerVisible(id, !!on[id]);
    v.alertsOn = !!on.alerts;
    syncAlerts(v, D);
  };
  const shownExag = () => {
    exagEl.hidden = v.exag === 0;
    exagEl.textContent = `Displacement drawn along the line of sight ×${v.exag} on a smoothed display surface (terrain true scale)`;
  };
  let current = null;
  const io = keep(new IntersectionObserver(
    (entries) => {
      for (const en of entries) {
        if (!en.isIntersecting) continue;
        const s = en.target.dataset.step;
        if (s === current) continue;
        current = s;
        document.querySelectorAll('.step').forEach((x) => x.classList.toggle('active', x === en.target));
        states[s]();
        shownExag();
      }
    },
    { rootMargin: '-45% 0px -45% 0px' },
  ));
  document.querySelectorAll('.step').forEach((s) => io.observe(s));
}

// ------------------------------------------------------------------------------------------------
function setupCharts(D) {
  const last = D.ts.E - 1;
  const core = D.ts.pixOf(D.meta.core.lon, D.meta.core.lat);
  seriesChart(document.getElementById('chart-points'), D, [
    { label: 'Largest value (edge pixel)', color: SERIES.max, values: D.ts.series(D.maxPix.col, D.maxPix.row) },
    { label: 'Landslide core (meta.core)', color: SERIES.core, values: D.ts.series(core.col, core.row) },
    { label: 'Stable reference', color: SERIES.stable, values: D.ts.series(D.stable.col, D.stable.row), dashed: true },
  ]);

  // Profiles
  const sel = document.getElementById('profile-select');
  const slider = document.getElementById('profile-date');
  const out = document.getElementById('profile-date-out');
  let prof = null;
  const draw = () => {
    if (prof) prof.destroy();
    const i = +sel.value;
    prof = profileCharts(document.getElementById('chart-prof-elev'), document.getElementById('chart-prof-disp'), D, i, +slider.value);
    document.querySelectorAll('.profile-text').forEach((p) => (p.hidden = +p.dataset.profile !== i));
  };
  const setDate = () => {
    const e = +slider.value;
    out.innerHTML = `<span data-dyn="date" data-epoch="${e}">${D.dates[e]}</span>`;
    slider.setAttribute('aria-valuetext', D.dates[e]);
    prof && prof.setEpoch(e);
  };
  sel.addEventListener('change', draw);
  slider.addEventListener('input', setDate);
  draw();
  setDate();

  // Buildings by distance (cumulative)
  const dist = D.sumBuildings.w_odleglosci_od_strefy_aktywnej;
  const distLabel = (k) => {
    const m = +/do_(\d+)_m/.exec(k)[1];
    return m === 0 ? 'inside zone' : m >= 1000 ? `≤ ${m / 1000} km` : `≤ ${m} m`;
  };
  barChart(document.getElementById('chart-dist'), {
    labels: Object.keys(dist).map(distLabel),
    values: Object.values(dist),
    yTitle: 'Buildings (cumulative)',
    xTitle: 'Distance from active zone',
    tooltip: (it) => `${it.parsed.y.toLocaleString('en-US')} buildings`,
  });

  // Slope
  barChart(document.getElementById('chart-slope'), {
    labels: D.slopeBins.map((b) => `${b.lo}–${b.hi}°`),
    values: D.slopeBins.map((b) => b.med),
    yTitle: `Median |LOS displacement| on ${D.dates[last]} (mm)`,
    xTitle: 'Mean terrain slope in the 100 m pixel',
    tooltip: (it) => {
      const b = D.slopeBins[it.dataIndex];
      return `${b.med === null ? 'no data' : b.med + ' mm'} (${b.n} pixels)`;
    },
  });

  // Monitoring windows
  const mon = D.monitoring;
  const lbl = mon.map((w) => `${w.data.slice(4, 6)}-${w.data.slice(6, 8)}`);
  groupedBarChart(document.getElementById('chart-mon'), {
    labels: lbl,
    xTitle: `Window end date (MM-DD, ${mon[0].data.slice(0, 4)})`,
    yTitle: 'Area (km²)',
    sets: [
      { label: 'Moving area', color: '#9aa3ab', values: mon.map((w) => w.ruch_km2) },
      { label: 'New moving area', color: '#6b3fa0', values: mon.map((w) => w['nowa strefa ruchu']) },
      { label: 'Deceleration', color: '#1f6f9a', values: mon.map((w) => w.spowolnienie) },
      { label: 'Acceleration', color: '#c0601a', values: mon.map((w) => w.przyspieszenie) },
    ],
  });
}

// ------------------------------------------------------------------------------------------------
function riskLegendHtml(D, { lines = true } = {}) {
  const C = D.meta.risk_colors;
  return `<div class="legend">
    <div class="legend-title">Risk / exposure class (our analysis)</div>
    ${CLASS_ORDER.map((k) => `<div class="li"><i class="sw" style="background:${C[k]}"></i>${CLASS_EN[k]}</div>`).join('')}
    ${lines ? `<div class="legend-title">Line style</div>
    <div class="li"><i class="ln solid thick"></i>Pipeline</div>
    <div class="li"><i class="ln solid"></i>Road</div>
    <div class="li"><i class="ln dashed"></i>Railway</div>
    <div class="li"><i class="ln dotted"></i>Power line</div>
    <div class="li"><i class="ln" style="border-color:${INK.zone};border-width:3px"></i>Active zone</div>
    <div class="li"><i class="ln dashed" style="border-color:${INK.alert}"></i>Alert polygon</div>` : ''}
  </div>`;
}

function setupRiskMap(D) {
  const wrap = document.querySelector('#s-risk .map-wrap');
  const panel = document.createElement('div');
  panel.className = 'panel map-panel';
  panel.tabIndex = -1;
  panel.hidden = true;
  panel.dataset.verify = 'panel';
  wrap.appendChild(panel);
  document.getElementById('risk-legend').innerHTML = legendBar('scale.mm') + riskLegendHtml(D);
  const io = keep(new IntersectionObserver(([en]) => {
    if (!en.isIntersecting) return;
    io.disconnect();
    createMap(document.getElementById('map-risk'), D, {
      interactive: false,
      layers: { rail: true, power: true },
      onPick: (p) => showPick(panel, D, p),
    });
  }, { rootMargin: '300px' }));
  io.observe(wrap);
}

// ------------------------------------------------------------------------------------------------
function setupExplore(D) {
  const panel = document.getElementById('info');
  const st = { epoch: D.ts.E - 1, tab: '3d', v: null, m: null };
  const onPick = (p) => showPick(panel, D, p);

  const LAYERS = [
    ['markers', 'Markers (largest value, core, stable reference)', true],
    ['zone', 'Active zone', true],
    ['alerts', 'Change-detection alerts', true],
    ['buildings', 'Buildings', true],
    ['roadsRisk', 'Roads (medium/high)', true],
    ['pipelines', 'Pipelines', true],
    ['power', 'Power lines', true],
    ['rail', 'Railways', true],
    ['profiles', 'Profile lines (3D)', false],
    ['roadsContext', 'Road network, background (3D)', false],
    ['disp', 'Displacement raster (map)', true],
    ['demExtent', '3D model extent (map)', true],
  ];
  const layerBox = document.getElementById('ex-layers');
  layerBox.innerHTML = LAYERS.map(([id, label, on]) => `<label class="row"><input type="checkbox" data-layer="${id}" ${on ? 'checked' : ''}> ${esc(label)}</label>`).join('');
  const layerOn = (id) => layerBox.querySelector(`[data-layer="${id}"]`).checked;
  document.getElementById('ex-legend').innerHTML = `<div id="ex-ramp"></div>` + riskLegendHtml(D);

  const dateOut = document.getElementById('ex-date');
  const spanOut = document.getElementById('ex-span');
  const slider = document.getElementById('ex-epoch');
  const scaleSel = document.getElementById('ex-scale');
  const exag = document.getElementById('ex-exag');
  const exagOut = document.getElementById('ex-exag-out');

  const ramp = () => {
    const mode = document.getElementById('ex-mode').value;
    const key = st.tab === 'map' || scaleSel.value === 'm' ? 'scale.mm' : 'scale.3d';
    document.getElementById('ex-ramp').innerHTML = st.tab === '3d' && mode !== 'disp' ? qualityLegend(mode) : legendBar(key);
  };
  const setEpoch = (e) => {
    st.epoch = e;
    slider.value = e;
    slider.setAttribute('aria-valuetext', D.dates[e]);
    dateOut.innerHTML = `<span data-dyn="date" data-epoch="${e}">${D.dates[e]}</span>`;
    spanOut.innerHTML = `Displacement since the first date: ${dateSpan(D, e)}`;
    if (st.v) { st.v.setEpoch(e); syncAlerts(st.v, D); }
    st.m && st.m.setEpoch(e);
    st.m && st.m.setAlertDate(D.dates[e]);
  };
  const setExag = () => {
    const x = +exag.value;
    exagOut.textContent = x === 0 ? 'off' : `×${x}`;
    exagOut.dataset.ui = '';
    exag.setAttribute('aria-valuetext', x === 0 ? 'off' : `times ${x}`);
    st.v && st.v.setExag(x);
  };

  const ensure3d = () => {
    if (st.v) return;
    st.v = new TerrainViewer(document.getElementById('viewer-explore'), D, {
      exag: +exag.value,
      onPick,
      label: 'Interactive 3D terrain model coloured by NISAR line-of-sight displacement; click for details',
    });
    const vis = Object.fromEntries(LAYERS.map(([id]) => [id, layerOn(id)]));
    addStandardLayers(st.v, D, vis);
    st.v.setMarkers(keyMarkers(D));
    st.v.setMarkersVisible(layerOn('markers'));
    st.v.setEpoch(st.epoch);
    st.v.setScale(scaleSel.value === 'm' ? D.meta.scale_mm : D.scale3d);
    st.v.setSurface(document.getElementById('ex-surface').value);
    st.v.setDetail(document.getElementById('ex-detail').checked ? 1 : 2);
    st.v.setUseExt(document.getElementById('ex-ext').checked);
    st.v.setMode(document.getElementById('ex-mode').value);
    if (layerOn('roadsContext')) addRoadsContext();
  };
  let ctxAdded = false;
  const addRoadsContext = async () => {
    if (ctxAdded || !st.v) return;
    ctxAdded = true;
    const rc = await loadRoadsContext();
    st.v.addLayer('roadsContext', rc.features, () => ({ key: 'c', color: INK.context, width: 1, order: 0 }), { lift: 2, pickable: false });
  };
  const ensureMap = () => {
    if (st.m) return;
    const vis = Object.fromEntries(LAYERS.map(([id]) => [id, layerOn(id)]));
    st.m = createMap(document.getElementById('map-explore'), D, { epoch: st.epoch, onPick, layers: { ...vis, threatened: vis.buildings } });
    st.m.setAlertDate(D.dates[st.epoch]);
  };

  // Lazy start when the section is reached.
  const io = keep(new IntersectionObserver(([en]) => {
    if (en.isIntersecting) { io.disconnect(); ensure3d(); }
  }, { rootMargin: '200px' }));
  io.observe(document.getElementById('explore'));

  slider.addEventListener('input', () => setEpoch(+slider.value));
  exag.addEventListener('input', setExag);
  scaleSel.addEventListener('change', () => {
    st.v && st.v.setScale(scaleSel.value === 'm' ? D.meta.scale_mm : D.scale3d);
    ramp();
  });
  document.getElementById('ex-mode').addEventListener('change', (e) => {
    st.v && st.v.setMode(e.target.value);
    ramp();
  });
  document.getElementById('ex-surface').addEventListener('change', (e) => st.v && st.v.setSurface(e.target.value));
  document.getElementById('ex-detail').addEventListener('change', (e) => st.v && st.v.setDetail(e.target.checked ? 1 : 2));
  document.getElementById('ex-ext').addEventListener('change', (e) => st.v && st.v.setUseExt(e.target.checked));
  document.querySelectorAll('[data-view]').forEach((b) => b.addEventListener('click', () => st.v && st.v.view(b.dataset.view)));
  layerBox.addEventListener('change', (e) => {
    const id = e.target.dataset.layer;
    const on = e.target.checked;
    if (id === 'roadsContext' && on) addRoadsContext();
    if (id === 'markers') { st.v && st.v.setMarkersVisible(on); return; }
    if (id === 'alerts' && st.v) { st.v.alertsOn = on; syncAlerts(st.v, D); }
    else st.v && st.v.setLayerVisible(id, on);
    if (st.m) {
      st.m.setVisible(id, on);
      if (id === 'buildings') st.m.setVisible('threatened', on);
    }
  });

  let timer = null;
  const playBtn = document.getElementById('ex-play');
  playBtn.addEventListener('click', () => {
    if (timer) {
      clearInterval(timer);
      timer = null;
      playBtn.textContent = 'Play';
      playBtn.setAttribute('aria-pressed', 'false');
      return;
    }
    playBtn.textContent = 'Pause';
    playBtn.setAttribute('aria-pressed', 'true');
    if (st.epoch >= D.ts.E - 1) setEpoch(0);
    timer = setInterval(() => {
      if (st.epoch >= D.ts.E - 1) {
        clearInterval(timer);
        timer = null;
        playBtn.textContent = 'Play';
        playBtn.setAttribute('aria-pressed', 'false');
        return;
      }
      setEpoch(st.epoch + 1);
    }, 600);
  });

  // Tabs (keyboard: arrow keys switch)
  const tabs = [document.getElementById('tab-3d'), document.getElementById('tab-map')];
  const selectTab = (which) => {
    st.tab = which;
    tabs.forEach((t) => {
      const on = t.id === `tab-${which}`;
      t.setAttribute('aria-selected', on);
      t.tabIndex = on ? 0 : -1;
    });
    document.getElementById('pane-3d').hidden = which !== '3d';
    document.getElementById('pane-map').hidden = which !== 'map';
    document.querySelectorAll('.only-3d').forEach((x) => (x.hidden = which !== '3d'));
    if (which === 'map') { ensureMap(); setTimeout(() => st.m.map.resize(), 0); } else ensure3d();
    ramp();
  };
  tabs.forEach((t, i) =>
    t.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        const nt = tabs[(i + 1) % 2];
        nt.focus();
        selectTab(nt.id.slice(4));
      }
    }),
  );
  tabs[0].addEventListener('click', () => selectTab('3d'));
  tabs[1].addEventListener('click', () => selectTab('map'));

  setEpoch(st.epoch);
  setExag();
  ramp();
}

export { PROFILE_EN };
