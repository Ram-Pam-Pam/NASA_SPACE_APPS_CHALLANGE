// Regional 2D map (MapLibre, OpenStreetMap raster tiles) with the NISAR displacement rasters
// exported in data/img (EPSG:3857 PNG, transparent = no data) and the risk layers.
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { dataUrl } from './data.js';
import { INK } from './colors.js';

const pad = (i) => String(i).padStart(2, '0');
// MapLibre loads images in a worker, so the URL must be absolute.
const imgUrl = (e) => new URL(dataUrl(`img/cum_${pad(e)}.png`), window.location.href).href;

export function classColorExpr(meta) {
  const c = meta.risk_colors;
  return ['match', ['get', 'klasa'], 'wysokie', c['wysokie'], 'średnie', c['średnie'], 'niskie', c['niskie'], c['brak danych']];
}

export function createMap(container, D, { onPick, epoch, interactive = true, layers = {} } = {}) {
  const [[S, W], [Nn, E]] = D.meta.overlay_bounds;
  const [[fs, fw], [fn, fe]] = D.meta.focus.bounds;
  const map = new maplibregl.Map({
    container,
    style: {
      version: 8,
      sources: {
        osm: {
          type: 'raster',
          tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
          tileSize: 256,
          maxzoom: 19,
          attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        },
      },
      layers: [{ id: 'osm', type: 'raster', source: 'osm', paint: { 'raster-saturation': -1, 'raster-opacity': 0.55 } }],
    },
    bounds: [[fw, fs], [fe, fn]],
    fitBoundsOptions: { padding: 10 },
    attributionControl: { compact: false, customAttribution: 'NISAR L2 GUNW (NASA/ISRO, ASF DAAC) · OGIM' },
    cooperativeGestures: !interactive,
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
  map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left');

  const api = {
    map,
    epoch: epoch ?? D.ts.E - 1,
    ready: new Promise((res) => map.on('load', res)),
    setEpoch(e) {
      api.epoch = e;
      const src = map.getSource('disp');
      if (src) src.updateImage({ url: imgUrl(e), coordinates: corners });
    },
    setVisible(id, on) {
      for (const l of LAYER_IDS[id] || []) if (map.getLayer(l)) map.setLayoutProperty(l, 'visibility', on ? 'visible' : 'none');
    },
    setAlertDate(iso) {
      api.alertDate = iso;
      const f = ['<=', ['get', 'data'], iso];
      for (const l of ['alerts-fill', 'alerts-line']) if (map.getLayer(l)) map.setFilter(l, f);
    },
    setOpacity(o) {
      if (map.getLayer('disp')) map.setPaintProperty('disp', 'raster-opacity', o);
    },
  };
  const corners = [[W, Nn], [E, Nn], [E, S], [W, S]];

  const LAYER_IDS = {
    disp: ['disp'],
    zone: ['zone-line'],
    alerts: ['alerts-fill', 'alerts-line'],
    demExtent: ['dem-extent'],
    roadsRisk: ['roads-risk'],
    rail: ['rail'],
    power: ['power'],
    pipelines: ['pipelines'],
    buildings: ['buildings-fill'],
    threatened: ['threatened'],
  };
  const vis = (id, def) => ((layers[id] ?? def) ? 'visible' : 'none');

  map.on('load', () => {
    const color = classColorExpr(D.meta);
    map.addSource('disp', { type: 'image', url: imgUrl(api.epoch), coordinates: corners });
    map.addLayer({ id: 'disp', type: 'raster', source: 'disp', paint: { 'raster-opacity': 0.8, 'raster-resampling': 'nearest', 'raster-fade-duration': 0 }, layout: { visibility: vis('disp', true) } });

    const dem = D.dem;
    map.addSource('dem-extent', {
      type: 'geojson',
      data: { type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: [...dem.lonlat_corners_nw_ne_se_sw, dem.lonlat_corners_nw_ne_se_sw[0]] } },
    });
    map.addLayer({ id: 'dem-extent', type: 'line', source: 'dem-extent', paint: { 'line-color': INK.demExtent, 'line-width': 1.5, 'line-dasharray': [3, 2] }, layout: { visibility: vis('demExtent', true) } });

    map.addSource('alerts', { type: 'geojson', data: D.alerts });
    map.addLayer({ id: 'alerts-fill', type: 'fill', source: 'alerts', paint: { 'fill-color': INK.alert, 'fill-opacity': 0.18 }, layout: { visibility: vis('alerts', true) } });
    map.addLayer({ id: 'alerts-line', type: 'line', source: 'alerts', paint: { 'line-color': INK.alert, 'line-width': 1.5 }, layout: { visibility: vis('alerts', true) } });
    if (api.alertDate) api.setAlertDate(api.alertDate);

    map.addSource('zone', { type: 'geojson', data: D.zone });
    map.addLayer({ id: 'zone-line', type: 'line', source: 'zone', paint: { 'line-color': INK.zone, 'line-width': 2.2 }, layout: { visibility: vis('zone', true) } });

    const lineLayer = (id, data, width, dash) => {
      map.addSource(id, { type: 'geojson', data });
      map.addLayer({
        id, type: 'line', source: id,
        paint: { 'line-color': color, 'line-width': width, ...(dash ? { 'line-dasharray': dash } : {}) },
        layout: { visibility: vis(id === 'roads-risk' ? 'roadsRisk' : id, true), 'line-cap': 'butt' },
      });
    };
    lineLayer('roads-risk', D.roadsRisk, 2.5);
    lineLayer('rail', D.rail, 2, [2, 1.5]);
    lineLayer('power', D.power, 1.6, [0.6, 1.2]);
    lineLayer('pipelines', D.pipelines, 3.2);

    map.addSource('buildings', { type: 'geojson', data: D.buildings });
    map.addLayer({
      id: 'buildings-fill', type: 'fill', source: 'buildings', minzoom: 14,
      paint: { 'fill-color': color, 'fill-opacity': ['match', ['get', 'klasa'], 'niskie', 0.25, 0.85], 'fill-outline-color': '#1d232a' },
      layout: { visibility: vis('buildings', true) },
    });
    map.addSource('threatened', { type: 'geojson', data: D.threatened });
    map.addLayer({
      id: 'threatened', type: 'circle', source: 'threatened', maxzoom: 14,
      filter: ['==', ['get', 'kategoria'], 'budynek'],
      paint: { 'circle-color': color, 'circle-radius': 2.6, 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 0.6 },
      layout: { visibility: vis('threatened', true) },
    });

    const pickLayers = ['buildings-fill', 'threatened', 'pipelines', 'power', 'rail', 'roads-risk', 'alerts-fill'];
    const layerName = { 'buildings-fill': 'buildings', threatened: 'threatened', pipelines: 'pipelines', power: 'power', rail: 'rail', 'roads-risk': 'roadsRisk', 'alerts-fill': 'alerts' };
    map.on('click', (e) => {
      const box = [[e.point.x - 4, e.point.y - 4], [e.point.x + 4, e.point.y + 4]];
      const hits = map.queryRenderedFeatures(box, { layers: pickLayers.filter((l) => map.getLayoutProperty(l, 'visibility') !== 'none') });
      const h = hits[0];
      onPick && onPick({ lon: e.lngLat.lng, lat: e.lngLat.lat, feature: h ? { layer: layerName[h.layer.id], f: { properties: h.properties } } : null });
    });
    for (const l of pickLayers) {
      map.on('mouseenter', l, () => (map.getCanvas().style.cursor = 'pointer'));
      map.on('mouseleave', l, () => (map.getCanvas().style.cursor = ''));
    }
  });
  return api;
}
