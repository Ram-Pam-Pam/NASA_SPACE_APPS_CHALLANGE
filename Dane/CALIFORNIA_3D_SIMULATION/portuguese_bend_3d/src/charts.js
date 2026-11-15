import Chart from 'chart.js/auto';
import { fmt } from './format.js';
import { utm } from './data.js';
import { PROFILE_EN } from './i18n.js';

Chart.defaults.font.family = "'Inter', system-ui, sans-serif";
Chart.defaults.font.size = 12;
Chart.defaults.locale = 'en-US';
Chart.defaults.color = '#4a5560';
Chart.defaults.borderColor = '#e3e6e8';
Chart.defaults.animation = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? false : { duration: 300 };

const AXIS_TITLE = { display: true, color: '#4a5560', font: { size: 12, weight: 500 } };

function dateAxis(D) {
  return {
    type: 'linear',
    min: 0,
    max: D.dayOffsets[D.dayOffsets.length - 1],
    afterBuildTicks: (ax) => (ax.ticks = D.dayOffsets.map((value) => ({ value }))),
    ticks: {
      callback: (v) => {
        const i = D.dayOffsets.indexOf(v);
        return i >= 0 ? D.dates[i].slice(5) : '';
      },
      maxRotation: 0,
      autoSkip: true,
      autoSkipPadding: 8,
    },
    grid: { display: false },
    title: { ...AXIS_TITLE, text: `Acquisition date (MM-DD), ${D.dates[0].slice(0, 4)}–${D.dates[D.dates.length - 1].slice(0, 4)}` },
  };
}

// Point time series. series: [{label, color, values (mm|null), dashed}]
export function seriesChart(canvas, D, series, { height, animate = true } = {}) {
  if (height) canvas.parentElement.style.height = `${height}px`;
  const datasets = series.map((s) => ({
    label: s.label,
    data: s.values.map((v, i) => ({ x: D.dayOffsets[i], y: v })),
    borderColor: s.color,
    backgroundColor: s.color,
    borderWidth: 2,
    borderDash: s.dashed ? [6, 4] : [],
    pointRadius: 3,
    pointHoverRadius: 5,
    spanGaps: false,
  }));
  return new Chart(canvas, {
    type: 'line',
    data: { datasets },
    options: {
      maintainAspectRatio: false,
      ...(animate ? {} : { animation: false }),
      interaction: { mode: 'nearest', axis: 'x', intersect: false },
      plugins: {
        legend: { display: series.length > 1, position: 'bottom', labels: { boxWidth: 18, boxHeight: 2 } },
        tooltip: {
          callbacks: {
            title: (items) => D.dates[D.dayOffsets.indexOf(items[0].parsed.x)],
            label: (it) => `${it.dataset.label}: ${it.parsed.y === null ? 'no data' : fmt(it.parsed.y) + ' mm'}`,
          },
        },
      },
      scales: {
        x: dateAxis(D),
        y: { title: { ...AXIS_TITLE, text: 'LOS displacement since first date (mm)' } },
      },
    },
  });
}

// Positions along a profile line at given distances (ground metres, UTM 11N).
export function profilePoints(coords, distances) {
  const pts = coords.map(([lon, lat]) => utm.forward([lon, lat]));
  const cum = [0];
  for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
  return distances.map((d) => {
    let k = 1;
    while (k < cum.length - 1 && cum[k] < d) k++;
    const t = Math.min(1, Math.max(0, (d - cum[k - 1]) / (cum[k] - cum[k - 1] || 1)));
    return [pts[k - 1][0] + (pts[k][0] - pts[k - 1][0]) * t, pts[k - 1][1] + (pts[k][1] - pts[k - 1][1]) * t];
  });
}

// Two stacked charts sharing the distance axis: terrain elevation and LOS displacement.
export function profileCharts(elevCanvas, dispCanvas, D, idx, epoch) {
  const p = D.profiles[idx];
  const geo = D.profilesGeo.features.find((f) => f.properties.nazwa === p.name);
  const pts = profilePoints(geo.geometry.coordinates, p.distance_m);
  const elev = pts.map(([x, y]) => {
    const e = D.dem.elevAtUTM(x, y);
    return Number.isNaN(e) ? null : e;
  });
  const xmax = p.distance_m[p.distance_m.length - 1];
  const xScale = (title) => ({
    type: 'linear', min: 0, max: xmax,
    title: { ...AXIS_TITLE, display: title, text: 'Distance along profile (m)' },
    grid: { color: '#eef0f1' },
  });
  const crossings = p.crossings || [];
  const crossPlugin = {
    id: 'crossings',
    afterDatasetsDraw(chart) {
      const { ctx, chartArea, scales } = chart;
      ctx.save();
      ctx.strokeStyle = '#1d232a';
      ctx.setLineDash([2, 3]);
      ctx.lineWidth = 1;
      for (const c of crossings) {
        const x = scales.x.getPixelForValue(c.odleglosc_m);
        ctx.beginPath();
        ctx.moveTo(x, chartArea.top);
        ctx.lineTo(x, chartArea.bottom);
        ctx.stroke();
      }
      ctx.restore();
    },
  };
  const elevChart = new Chart(elevCanvas, {
    type: 'line',
    data: {
      datasets: [{
        label: `Terrain elevation, lidar DTM ${D.demYear} (m)`,
        data: p.distance_m.map((d, i) => ({ x: d, y: elev[i] })),
        borderColor: '#5b6670', backgroundColor: 'rgba(91,102,112,0.12)', fill: 'origin',
        borderWidth: 1.5, pointRadius: 0, spanGaps: false,
      }],
    },
    options: {
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { mode: 'index', intersect: false, callbacks: {
          title: (it) => `${fmt(it[0].parsed.x)} m`,
          label: (it) => (it.parsed.y === null ? 'Elevation: no data (outside DTM)' : `Elevation: ${fmt(it.parsed.y, 1)} m`),
        } },
      },
      scales: { x: xScale(false), y: { afterFit: (s) => (s.width = 64), title: { ...AXIS_TITLE, text: 'Elevation (m)' } } },
    },
    plugins: [crossPlugin],
  });
  const dispChart = new Chart(dispCanvas, {
    type: 'line',
    data: {
      datasets: [{
        label: 'LOS displacement (mm)',
        data: p.distance_m.map((d, i) => ({ x: d, y: p.values_mm[epoch][i] })),
        borderColor: '#1f6f9a', backgroundColor: '#1f6f9a', borderWidth: 2, pointRadius: 0, spanGaps: false,
      }],
    },
    options: {
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { mode: 'index', intersect: false, callbacks: {
          title: (it) => `${fmt(it[0].parsed.x)} m`,
          label: (it) => (it.parsed.y === null ? 'No data' : `LOS displacement: ${fmt(it.parsed.y)} mm`),
        } },
      },
      scales: { x: xScale(true), y: { afterFit: (s) => (s.width = 64), title: { ...AXIS_TITLE, text: 'LOS displacement (mm)' } } },
    },
    plugins: [crossPlugin],
  });
  return {
    name: PROFILE_EN[p.name] || p.name,
    setEpoch(e) {
      dispChart.data.datasets[0].data = p.distance_m.map((d, i) => ({ x: d, y: p.values_mm[e][i] }));
      dispChart.update('none');
    },
    destroy() { elevChart.destroy(); dispChart.destroy(); },
  };
}

export function barChart(canvas, { labels, values, color = '#1f6f9a', yTitle, xTitle, tooltip, animate = true }) {
  return new Chart(canvas, {
    type: 'bar',
    data: { labels, datasets: [{ data: values, backgroundColor: color, borderRadius: 4, borderSkipped: 'bottom', maxBarThickness: 48 }] },
    options: {
      maintainAspectRatio: false,
      ...(animate ? {} : { animation: false }),
      plugins: { legend: { display: false }, tooltip: tooltip ? { callbacks: { label: tooltip } } : {} },
      scales: {
        x: { grid: { display: false }, title: { ...AXIS_TITLE, display: !!xTitle, text: xTitle } },
        y: { beginAtZero: true, title: { ...AXIS_TITLE, text: yTitle } },
      },
    },
  });
}

export function groupedBarChart(canvas, { labels, sets, yTitle, xTitle }) {
  return new Chart(canvas, {
    type: 'bar',
    data: {
      labels,
      datasets: sets.map((s) => ({ label: s.label, data: s.values, backgroundColor: s.color, borderRadius: 3, borderSkipped: 'bottom', maxBarThickness: 22 })),
    },
    options: {
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom', labels: { boxWidth: 12 } },
        tooltip: { callbacks: { label: (it) => `${it.dataset.label}: ${fmt(it.parsed.y, 2)} km²` } },
      },
      scales: {
        x: { grid: { display: false }, title: { ...AXIS_TITLE, display: !!xTitle, text: xTitle } },
        y: { beginAtZero: true, title: { ...AXIS_TITLE, text: yTitle } },
      },
    },
  });
}
