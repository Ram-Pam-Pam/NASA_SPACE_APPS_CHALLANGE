// 3D terrain (USGS lidar DTM, 5 m) coloured and optionally deformed by NISAR LOS displacement.
// Each 100 m NISAR pixel is shown as-is (nearest pixel, no interpolation); pixels without data stay gray.
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { LineSegments2 } from 'three/examples/jsm/lines/LineSegments2.js';
import { LineSegmentsGeometry } from 'three/examples/jsm/lines/LineSegmentsGeometry.js';
import { LineMaterial } from 'three/examples/jsm/lines/LineMaterial.js';
import { utm } from './data.js';
import { RDBU } from './colors.js';

const NODATA = -32768;
const REDUCED_MOTION = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

const sharedByStep = new Map();

// Terrain mesh from every `step`-th DEM cell (step 2 = 20 m from the 10 m DEM). Shared by viewers.
function buildShared(D, step = 2) {
  if (sharedByStep.has(step)) return sharedByStep.get(step);
  const { dem } = D;
  const w = Math.floor((dem.w - 1) / step) + 1, h = Math.floor((dem.h - 1) / step) + 1;
  const rx = dem.rx * step, ry = dem.ry * step;
  const elev = dem.elev;
  const W = (w - 1) * rx, H = (h - 1) * ry;
  const n = w * h;
  const pos = new Float32Array(n * 3);
  const valid = new Float32Array(n);
  const cell = new Int32Array(n); // DEM cell of each vertex
  for (let r = 0; r < h; r++)
    for (let c = 0; c < w; c++) {
      const i = r * w + c;
      const ci = r * step * dem.w + c * step;
      cell[i] = ci;
      const e = elev[ci];
      pos[i * 3] = c * rx - W / 2;
      pos[i * 3 + 1] = Number.isNaN(e) ? 0 : e;
      pos[i * 3 + 2] = r * ry - H / 2;
      valid[i] = Number.isNaN(e) ? 0 : 1;
    }
  const index = new Uint32Array((w - 1) * (h - 1) * 6);
  let k = 0;
  for (let r = 0; r < h - 1; r++)
    for (let c = 0; c < w - 1; c++) {
      const a = r * w + c, b = a + 1, cc = a + w, d = a + w + 1;
      index[k++] = a; index[k++] = cc; index[k++] = b;
      index[k++] = b; index[k++] = cc; index[k++] = d;
    }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  geo.setAttribute('aValid', new THREE.BufferAttribute(valid, 1));
  geo.setIndex(new THREE.BufferAttribute(index, 1));
  geo.computeVertexNormals();

  const x0 = dem.left + dem.rx / 2, y0 = dem.top - dem.ry / 2;
  const shared = {
    geo, W, H, w, h, rx, ry, cell, step,
    toLocal(lon, lat) {
      const [x, y] = utm.forward([lon, lat]);
      return [x - x0 - W / 2, y0 - y - H / 2];
    },
    toLonLat(lx, lz) {
      return utm.inverse([lx + W / 2 + x0, y0 - (lz + H / 2)]);
    },
    vertexAt(lx, lz) {
      const c = Math.round((lx + W / 2) / rx), r = Math.round((lz + H / 2) / ry);
      if (c < 0 || r < 0 || c >= w || r >= h) return -1;
      return r * w + c;
    },
    inside(lx, lz) {
      return Math.abs(lx) <= W / 2 && Math.abs(lz) <= H / 2;
    },
  };
  sharedByStep.set(step, shared);
  return shared;
}

// Displacement fields on the 100 m grid as Float32 (NaN = no data), cached per source.
//  raw: project series (+ low-coherence GUNW series when ext)
//  surface: display-only smooth surface = Gaussian-weighted mean of measured pixels
//           (sigma 1 pixel, radius 2 pixels); a cell gets a value only where the measured
//           pixels around it carry at least 40 % of the full kernel weight, so small gaps
//           close and large gaps stay empty.
const fieldCache = new Map();
export function displacementField(D, { ext, surface }) {
  const key = `${ext}-${surface}`;
  if (fieldCache.has(key)) return fieldCache.get(key);
  const { ts } = D;
  const src = ext ? D.tsx.arr : ts.arr;
  const { w, h, E } = ts;
  const raw = new Float32Array(w * h * E);
  for (let i = 0; i < raw.length; i++) raw[i] = src[i] === ts.nodata ? NaN : src[i];
  if (!surface) { fieldCache.set(key, raw); return raw; }
  const R = 2, sig = 1;
  const kw = [];
  let ksum = 0;
  for (let dy = -R; dy <= R; dy++)
    for (let dx = -R; dx <= R; dx++) {
      const g = Math.exp(-(dx * dx + dy * dy) / (2 * sig * sig));
      kw.push([dx, dy, g]);
      ksum += g;
    }
  const out = new Float32Array(w * h * E).fill(NaN);
  for (let r = 0; r < h; r++)
    for (let c = 0; c < w; c++)
      for (let e = 0; e < E; e++) {
        let s = 0, ws = 0;
        for (const [dx, dy, g] of kw) {
          const rr = r + dy, cc = c + dx;
          if (rr < 0 || cc < 0 || rr >= h || cc >= w) continue;
          const v = raw[(rr * w + cc) * E + e];
          if (Number.isNaN(v)) continue;
          s += v * g;
          ws += g;
        }
        if (ws >= 0.4 * ksum) out[(r * w + c) * E + e] = s / ws;
      }
  fieldCache.set(key, out);
  return out;
}

// Display-only gap filling (requested for the 3D view): starting from the smooth surface, every
// empty grid cell repeatedly takes the mean of its filled 8-neighbours until no gaps remain.
// Returns the filled field and a mask of the estimated cells (1 = estimated, 0 = measured).
export function filledField(D, ext) {
  const key = `${ext}-filled`;
  if (fieldCache.has(key)) return fieldCache.get(key);
  const base = displacementField(D, { ext, surface: true });
  const { w, h, E } = D.ts;
  const F = new Float32Array(base);
  const est = new Uint8Array(w * h);
  for (let i = 0; i < w * h; i++) if (Number.isNaN(F[i * E + E - 1])) est[i] = 1;
  for (let e = 0; e < E; e++) {
    for (let iter = 0; iter < 400; iter++) {
      const upd = [];
      for (let r = 0; r < h; r++)
        for (let c = 0; c < w; c++) {
          const i = r * w + c;
          if (!Number.isNaN(F[i * E + e])) continue;
          let s = 0, n = 0;
          for (let dr = -1; dr <= 1; dr++)
            for (let dc = -1; dc <= 1; dc++) {
              if (!dr && !dc) continue;
              const rr = r + dr, cc = c + dc;
              if (rr < 0 || cc < 0 || rr >= h || cc >= w) continue;
              const v = F[(rr * w + cc) * E + e];
              if (!Number.isNaN(v)) { s += v; n++; }
            }
          if (n) upd.push(i, s / n);
        }
      if (!upd.length) break;
      for (let k = 0; k < upd.length; k += 2) F[upd[k] * E + e] = upd[k + 1];
    }
  }
  const res = { F, est };
  fieldCache.set(key, res);
  return res;
}

const vert = /* glsl */ `
attribute float aDisp;
attribute float aValid;
attribute float aQ;
attribute float aLow;
uniform float uExag;
uniform vec3 uLos;
varying float vDisp;
varying float vHas;
varying float vValid;
varying float vQ;
varying float vLow;
varying vec3 vN;
varying vec3 vPos;
void main() {
  vLow = aLow;
  vec3 p = position;
  float has = aDisp > -30000.0 ? 1.0 : 0.0;
  // LOS displacement drawn along the ground-to-satellite unit vector (positive = towards the satellite).
  p += uLos * (has * aDisp * 0.001 * uExag);
  vDisp = has * aDisp;
  vQ = aQ;
  vHas = has;
  vValid = aValid;
  vN = normal;
  vPos = p;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
}`;

const frag = /* glsl */ `
uniform vec3 uStops[11];
uniform float uScale;
uniform float uShowDisp;
uniform vec3 uLight;
uniform float uMode;
varying float vDisp;
varying float vHas;
varying float vValid;
varying float vQ;
varying vec3 vN;
varying vec3 vPos;
varying float vLow;
uniform float uExag;
vec3 blues(float t) {
  vec3 a = vec3(0.969, 0.984, 1.0), b = vec3(0.420, 0.682, 0.839), c = vec3(0.031, 0.188, 0.420);
  t = clamp(t, 0.0, 1.0);
  return t < 0.5 ? mix(a, b, t * 2.0) : mix(b, c, t * 2.0 - 1.0);
}
vec3 rdbu(float t) {
  float f = (clamp(t, -1.0, 1.0) + 1.0) * 5.0;
  int i = int(min(floor(f), 9.0));
  return mix(uStops[i], uStops[i + 1], f - float(i));
}
void main() {
  vec3 nrm = normalize(vN);
  float shade = clamp(dot(nrm, normalize(uLight)), 0.0, 1.0);
  float lit = 0.38 + 0.62 * shade;
  vec3 base = vValid > 0.5 ? vec3(0.84, 0.84, 0.82) : vec3(0.80, 0.85, 0.88);
  // With displacement drawn, land without a NISAR series is cut out instead of left standing.
  if (uExag > 0.0 && vValid > 0.99 && vHas < 0.99) discard;
  if (uMode < 0.5) {
    if (vValid > 0.99 && vHas > 0.99 && uShowDisp > 0.5) {
      base = rdbu(vDisp / uScale);
      float pale = vLow > 1.5 ? 0.62 : 0.45 * clamp(vLow, 0.0, 1.0);
      base = mix(base, vec3(0.80, 0.80, 0.78), pale);
    }
  } else if (uMode < 1.5) {
    if (vValid > 0.99 && vQ > -0.5) base = blues(vQ);
  } else {
    if (vValid > 0.99 && vQ > -0.5) base = vQ < 0.5 ? vec3(0.753, 0.376, 0.102) : vec3(0.78, 0.84, 0.88);
  }
  gl_FragColor = vec4(base * lit, 1.0);
}`;

export class TerrainViewer {
  constructor(container, D, opts = {}) {
    this.D = D;
    this.S = buildShared(D, opts.step ?? 2);
    this.container = container;
    this.epoch = D.ts.E - 1;
    this.exag = opts.exag ?? 0;
    this.scale = opts.scale ?? D.scale3d;
    this.showDisp = true;
    this.layers = new Map();
    this.markers = [];
    this.onPick = opts.onPick || null;
    this.dirty = true;

    const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0xf4f5f3, 1);
    container.appendChild(renderer.domElement);
    renderer.domElement.setAttribute('role', 'img');
    renderer.domElement.setAttribute('aria-label', opts.label || '3D terrain model');
    renderer.domElement.tabIndex = 0;
    this.renderer = renderer;

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(40, 1, 10, 60000);
    this.controls = new OrbitControls(this.camera, renderer.domElement);
    this.controls.enableZoom = opts.zoom !== false;
    this.controls.maxPolarAngle = Math.PI * 0.48;
    this.controls.minDistance = 300;
    this.controls.maxDistance = 14000;
    this.controls.listenToKeyEvents(renderer.domElement);
    this.controls.addEventListener('change', () => (this.dirty = true));

    const g = this.makeGeometry();
    this.useExt = opts.useExt ?? true;
    // 'surface' (smooth display surface) | 'blend' (between measured pixels) | 'raw' (100 m pixels)
    this.surf = opts.surface ?? 'filled';
    this.mode = 'disp';
    const [le, lu, ln] = D.losVec;
    this.los = new THREE.Vector3(le, lu, -ln); // three.js: x = east, y = up, z = south
    this.uniforms = {
      uMode: { value: 0 },
      uLos: { value: this.los },
      uExag: { value: this.exag },
      uScale: { value: this.scale },
      uShowDisp: { value: 1 },
      // Raw sRGB values: this shader writes gl_FragColor without colour-space conversion.
      uStops: { value: RDBU.map((c) => new THREE.Color(c).convertLinearToSRGB()) },
      uLight: { value: new THREE.Vector3(-1, 1.4, -0.6) },
    };
    const mat = new THREE.ShaderMaterial({ vertexShader: vert, fragmentShader: frag, uniforms: this.uniforms, extensions: { derivatives: true } });
    this.mesh = new THREE.Mesh(g, mat);
    this.mesh.frustumCulled = false;
    this.scene.add(this.mesh);

    this.labelLayer = document.createElement('div');
    this.labelLayer.className = 'label-layer';
    container.appendChild(this.labelLayer);

    this.compass = document.createElement('div');
    this.compass.className = 'compass';
    this.compass.setAttribute('aria-hidden', 'true');
    this.compass.innerHTML = '<span>N</span><i></i>';
    container.appendChild(this.compass);

    this.setEpoch(this.epoch);
    this.resize();
    this.view('overview', true);

    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(container);

    renderer.domElement.addEventListener('pointerdown', (e) => (this._down = [e.clientX, e.clientY]));
    renderer.domElement.addEventListener('pointerup', (e) => {
      if (!this._down) return;
      const moved = Math.hypot(e.clientX - this._down[0], e.clientY - this._down[1]);
      this._down = null;
      if (moved < 5) this.handleClick(e);
    });

    this.visible = true;
    this.io = new IntersectionObserver(([en]) => {
      this.visible = en.isIntersecting;
      if (this.visible) this.dirty = true;
    });
    this.io.observe(container);
    const loop = () => {
      requestAnimationFrame(loop);
      if (this._anim) this._anim();
      if (this.visible && this.dirty) this.render();
    };
    loop();
  }

  // Terrain mesh: shared geometry buffers, per-viewer displacement attributes.
  makeGeometry() {
    const g = new THREE.BufferGeometry();
    g.setIndex(this.S.geo.index);
    g.setAttribute('position', this.S.geo.attributes.position);
    // Own copy of the normals: recomputed for the deformed surface when displacement is drawn.
    this.normals = new Float32Array(this.S.geo.attributes.normal.array);
    g.setAttribute('normal', new THREE.BufferAttribute(this.normals, 3));
    g.setAttribute('aValid', this.S.geo.attributes.aValid);
    const n = this.S.w * this.S.h;
    this.disp = new Float32Array(n);
    g.setAttribute('aDisp', new THREE.BufferAttribute(this.disp, 1));
    this.q = new Float32Array(n).fill(-1);
    g.setAttribute('aQ', new THREE.BufferAttribute(this.q, 1));
    this.low = new Float32Array(n);
    g.setAttribute('aLow', new THREE.BufferAttribute(this.low, 1));
    return g;
  }

  // Mesh detail: 1 = every DEM cell (10 m), 2 = every second cell (20 m).
  setDetail(step) {
    if (this.S.step === step) return;
    this.S = buildShared(this.D, step);
    const old = this.mesh.geometry;
    this.mesh.geometry = this.makeGeometry();
    old.dispose();
    this.setEpoch(this.epoch);
  }

  resize() {
    const w = this.container.clientWidth, h = this.container.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    for (const L of this.layers.values()) for (const o of L.objects) o.material.resolution.set(w, h);
    this.dirty = true;
  }

  render() {
    this.dirty = false;
    this.renderer.render(this.scene, this.camera);
    this.updateLabels();
    const az = this.controls.getAzimuthalAngle();
    this.compass.style.transform = `rotate(${az}rad)`;
  }

  // ----- displacement ------------------------------------------------------------------------
  setEpoch(e) {
    this.epoch = e;
    const { ts, dem, gunw } = this.D;
    const idx = dem.tsIdx, fx = dem.tsFx, fy = dem.tsFy, cell = this.S.cell;
    const filled = this.surf === 'filled' ? filledField(this.D, this.useExt) : null;
    const F = filled ? filled.F : displacementField(this.D, { ext: this.useExt, surface: this.surf === 'surface' });
    const landV = this.S.geo.attributes.aValid.array;
    const E = ts.E, W = ts.w, H = ts.h;
    const LOW = this.D.tsx.low;
    const mode = this.surf === 'filled' ? 'surface' : this.surf;
    for (let i = 0; i < this.disp.length; i++) {
      const ci = cell[i];
      const p = idx[ci];
      this.low[i] = p < 0 ? 0 : filled && filled.est[p] ? 2 : this.useExt ? LOW[p] : 0;
      if (filled && !landV[i]) { this.disp[i] = NODATA; continue; } // the sea is never moved
      if (p < 0) { this.disp[i] = NODATA; continue; }
      const near = F[p * E + e];
      if (mode === 'raw') { this.disp[i] = Number.isNaN(near) ? NODATA : near; continue; }
      const c0 = Math.floor(fx[ci]), r0 = Math.floor(fy[ci]);
      if (c0 < 0 || r0 < 0 || c0 + 1 >= W || r0 + 1 >= H) { this.disp[i] = Number.isNaN(near) ? NODATA : near; continue; }
      const tx = fx[ci] - c0, ty = fy[ci] - r0;
      const k = (r0 * W + c0) * E + e;
      const vs = [F[k], F[k + E], F[k + W * E], F[k + W * E + E]];
      const ws = [(1 - tx) * (1 - ty), tx * (1 - ty), (1 - tx) * ty, tx * ty];
      let sv = 0, sw = 0, nValid = 0;
      for (let j = 0; j < 4; j++) if (!Number.isNaN(vs[j])) { sv += vs[j] * ws[j]; sw += ws[j]; nValid++; }
      if (mode === 'blend') {
        this.disp[i] = nValid === 4 ? sv : Number.isNaN(near) ? NODATA : near;
      } else {
        // smooth surface: bilinear over the corners that have a value (needs half of the weight)
        this.disp[i] = sw >= 0.5 ? sv / sw : NODATA;
      }
    }
    // Quality of the pair that ends on this date (coherence or connected component), nearest pixel.
    const pr = e - 1;
    for (let i = 0; i < this.q.length; i++) {
      const p = idx[cell[i]];
      if (p < 0 || pr < 0 || this.mode === 'disp') { this.q[i] = -1; continue; }
      const k = p * gunw.P + pr;
      if (this.mode === 'coh') this.q[i] = gunw.cohArr[k] === 255 ? -1 : gunw.cohArr[k] / 200;
      else this.q[i] = gunw.ccArr[k] === 255 ? -1 : gunw.ccArr[k];
    }
    this.mesh.geometry.attributes.aDisp.needsUpdate = true;
    this.mesh.geometry.attributes.aQ.needsUpdate = true;
    this.mesh.geometry.attributes.aLow.needsUpdate = true;
    this.updateNormals();
    this.updateOverlayHeights();
    this.dirty = true;
  }

  // Smooth per-vertex normals of the displaced surface (central differences on the mesh grid).
  updateNormals() {
    const { w, h } = this.S;
    const base = this.S.geo.attributes.position.array;
    const N = this.normals;
    if (!this.exag) {
      N.set(this.S.geo.attributes.normal.array);
    } else {
      const k = 0.001 * this.exag, lx = this.los.x, ly = this.los.y, lz = this.los.z;
      const n = w * h;
      if (!this._dp || this._dp.length !== n * 3) this._dp = new Float32Array(n * 3);
      const P = this._dp, d = this.disp;
      for (let i = 0, j = 0; i < n; i++, j += 3) {
        const s = d[i] > -30000 ? d[i] * k : 0;
        P[j] = base[j] + lx * s; P[j + 1] = base[j + 1] + ly * s; P[j + 2] = base[j + 2] + lz * s;
      }
      for (let r = 0; r < h; r++)
        for (let c = 0; c < w; c++) {
          const i = r * w + c;
          const l = (c > 0 ? i - 1 : i) * 3, rr = (c < w - 1 ? i + 1 : i) * 3;
          const u = (r > 0 ? i - w : i) * 3, dn = (r < h - 1 ? i + w : i) * 3;
          const tx0 = P[rr] - P[l], tx1 = P[rr + 1] - P[l + 1], tx2 = P[rr + 2] - P[l + 2];
          const tz0 = P[dn] - P[u], tz1 = P[dn + 1] - P[u + 1], tz2 = P[dn + 2] - P[u + 2];
          const nx = tz1 * tx2 - tz2 * tx1, ny = tz2 * tx0 - tz0 * tx2, nz = tz0 * tx1 - tz1 * tx0;
          const len = Math.sqrt(nx * nx + ny * ny + nz * nz) || 1;
          N[i * 3] = nx / len; N[i * 3 + 1] = ny / len; N[i * 3 + 2] = nz / len;
        }
    }
    this.mesh.geometry.attributes.normal.needsUpdate = true;
  }
  setUseExt(on) {
    this.useExt = on;
    this.setEpoch(this.epoch);
  }
  setSurface(m) {
    this.surf = m;
    this.setEpoch(this.epoch);
  }
  setSmooth(on) {
    this.setSurface(on ? 'filled' : 'raw');
  }
  // 'disp' | 'coh' | 'cc'
  setMode(m) {
    this.mode = m;
    this.uniforms.uMode.value = m === 'disp' ? 0 : m === 'coh' ? 1 : 2;
    this.setEpoch(this.epoch);
  }
  setExag(x) {
    this.exag = x;
    this.uniforms.uExag.value = x;
    this.updateNormals();
    this.updateOverlayHeights();
    this.dirty = true;
  }
  setScale(s) {
    this.scale = s;
    this.uniforms.uScale.value = s;
    this.dirty = true;
  }
  setShowDisp(on) {
    this.showDisp = on;
    this.uniforms.uShowDisp.value = on ? 1 : 0;
    this.dirty = true;
  }

  // Displaced surface position near (lx, lz): [x, y, z] of the nearest vertex offset along LOS.
  surfaceAt(lx, lz) {
    const i = this.S.vertexAt(lx, lz);
    if (i < 0) return [lx, 0, lz];
    const pos = this.S.geo.attributes.position.array;
    const d = this.disp[i];
    const s = d > -30000 ? d * 0.001 * this.exag : 0;
    return [lx + this.los.x * s, pos[i * 3 + 1] + this.los.y * s, lz + this.los.z * s];
  }
  surfaceY(lx, lz) {
    return this.surfaceAt(lx, lz)[1];
  }

  // ----- overlays --------------------------------------------------------------------------
  // features: GeoJSON features; style(f) -> {key, color, width, opacity, dashed} or null to skip.
  addLayer(id, features, style, { lift = 3, visible = true, pickable = true } = {}) {
    const groups = new Map();
    const pickItems = [];
    const S = this.S;
    const STEP = 8;
    for (const f of features) {
      const st = style(f);
      if (!st) continue;
      const geom = f.geometry;
      const rings =
        geom.type === 'LineString' ? [geom.coordinates]
        : geom.type === 'MultiLineString' ? geom.coordinates
        : geom.type === 'Polygon' ? geom.coordinates
        : geom.type === 'MultiPolygon' ? geom.coordinates.flat()
        : [];
      let used = false;
      const localRings = [];
      for (const ring of rings) {
        const loc = ring.map(([lon, lat]) => S.toLocal(lon, lat));
        localRings.push(loc);
        for (let i = 0; i < loc.length - 1; i++) {
          const [ax, az] = loc[i], [bx, bz] = loc[i + 1];
          if (!S.inside(ax, az) || !S.inside(bx, bz)) continue;
          used = true;
          let g = groups.get(st.key);
          if (!g) groups.set(st.key, (g = { st, pts: [] }));
          const len = Math.hypot(bx - ax, bz - az);
          const n = Math.max(1, Math.ceil(len / STEP));
          for (let k = 0; k < n; k++) {
            const t0 = k / n, t1 = (k + 1) / n;
            g.pts.push(ax + (bx - ax) * t0, az + (bz - az) * t0, ax + (bx - ax) * t1, az + (bz - az) * t1);
          }
        }
      }
      if (used && pickable) pickItems.push({ f, rings: localRings, polygon: geom.type.includes('Polygon') });
    }
    const objects = [];
    for (const { st, pts } of groups.values()) {
      const geo = new LineSegmentsGeometry();
      const mat = new LineMaterial({
        color: st.color,
        linewidth: st.width,
        transparent: (st.opacity ?? 1) < 1,
        opacity: st.opacity ?? 1,
        dashed: !!st.dashed,
        dashSize: 20,
        gapSize: 14,
      });
      mat.resolution.set(this.container.clientWidth || 800, this.container.clientHeight || 600);
      const obj = new LineSegments2(geo, mat);
      obj.userData = { pts: new Float32Array(pts), lift, dashed: !!st.dashed };
      obj.frustumCulled = false;
      obj.renderOrder = st.order ?? 1;
      obj.visible = visible;
      this.scene.add(obj);
      objects.push(obj);
    }
    const L = { id, objects, pickItems, visible, count: pickItems.length };
    this.layers.set(id, L);
    this.updateLayerHeights(L);
    this.dirty = true;
    return L.count;
  }

  updateLayerHeights(L) {
    for (const o of L.objects) {
      const p = o.userData.pts;
      const out = new Float32Array((p.length / 2) * 3);
      for (let i = 0, j = 0; i < p.length; i += 2, j += 3) {
        const [sx, sy, sz] = this.surfaceAt(p[i], p[i + 1]);
        out[j] = sx;
        out[j + 1] = sy + o.userData.lift;
        out[j + 2] = sz;
      }
      o.geometry.setPositions(out);
      if (o.userData.dashed) o.computeLineDistances();
    }
  }
  updateOverlayHeights() {
    if (!this.layers) return;
    for (const L of this.layers.values()) this.updateLayerHeights(L);
    for (const m of this.markers) [m.sx, m.y, m.sz] = this.surfaceAt(m.x, m.z);
  }
  setLayerVisible(id, on) {
    const L = this.layers.get(id);
    if (!L) return;
    L.visible = on;
    for (const o of L.objects) o.visible = on;
    this.dirty = true;
  }

  // ----- markers with HTML labels -----------------------------------------------------------
  setMarkers(list) {
    this.labelLayer.innerHTML = '';
    this.markers = list.map((m) => {
      const [x, z] = this.S.toLocal(m.lon, m.lat);
      const el = document.createElement('div');
      el.className = 'marker-label';
      el.style.setProperty('--c', m.color);
      el.textContent = m.label;
      this.labelLayer.appendChild(el);
      const [sx, y, sz] = this.surfaceAt(x, z);
      return { ...m, x, z, sx, y, sz, el };
    });
    this.dirty = true;
  }
  setMarkersVisible(on) {
    this.labelLayer.hidden = !on;
    this.dirty = true;
  }
  updateLabels() {
    const w = this.container.clientWidth, h = this.container.clientHeight;
    const v = new THREE.Vector3();
    for (const m of this.markers) {
      v.set(m.sx, m.y + 40, m.sz).project(this.camera);
      const vis = v.z < 1 && Math.abs(v.x) < 1.1 && Math.abs(v.y) < 1.1;
      m.el.style.display = vis ? '' : 'none';
      m.el.style.transform = `translate(${((v.x + 1) / 2) * w}px, ${((1 - v.y) / 2) * h}px)`;
    }
  }

  // ----- camera ---------------------------------------------------------------------------------
  // Distance at which the whole model extent fits the current viewport.
  fitDist() {
    const vf = (this.camera.fov * Math.PI) / 180;
    const hf = 2 * Math.atan(Math.tan(vf / 2) * this.camera.aspect);
    return Math.max(this.S.W / 2 / Math.tan(hf / 2), this.S.H / 2 / Math.tan(vf / 2));
  }

  view(name, instant = false) {
    const D = this.D;
    let target, dist, polar, az;
    if (name === 'core') {
      const [x, z] = this.S.toLocal(D.meta.core.lon, D.meta.core.lat);
      target = new THREE.Vector3(x, 80, z);
      dist = 2600; polar = 0.95; az = -0.35;
    } else if (name === 'top') {
      target = new THREE.Vector3(0, 0, 0);
      dist = this.fitDist() * 1.08; polar = 0.05; az = 0;
    } else {
      target = new THREE.Vector3(0, 60, 0);
      dist = this.fitDist() * 1.2; polar = 0.92; az = -0.3;
    }
    const pos = new THREE.Vector3().setFromSphericalCoords(dist, polar, az).add(target);
    if (instant || REDUCED_MOTION) {
      this.camera.position.copy(pos);
      this.controls.target.copy(target);
      this.controls.update();
      this.dirty = true;
      return;
    }
    const p0 = this.camera.position.clone(), t0 = this.controls.target.clone();
    const start = performance.now(), dur = 900;
    this._anim = () => {
      const k = Math.min(1, (performance.now() - start) / dur);
      const e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
      this.camera.position.lerpVectors(p0, pos, e);
      this.controls.target.lerpVectors(t0, target, e);
      this.controls.update();
      this.dirty = true;
      if (k >= 1) this._anim = null;
    };
  }

  // ----- picking ----------------------------------------------------------------------------------
  handleClick(ev) {
    if (!this.onPick) return;
    const rect = this.renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(((ev.clientX - rect.left) / rect.width) * 2 - 1, -((ev.clientY - rect.top) / rect.height) * 2 + 1);
    const rc = new THREE.Raycaster();
    rc.setFromCamera(ndc, this.camera);
    const hit = this.marchRay(rc.ray);
    if (!hit) return;
    const [lon, lat] = this.S.toLonLat(hit.x, hit.z);
    const tol = Math.max(10, this.camera.position.distanceTo(hit) * 0.006);
    this.onPick({ lon, lat, feature: this.findFeature(hit.x, hit.z, tol) });
  }
  marchRay(ray) {
    const p = new THREE.Vector3();
    let prev = null;
    for (let t = 0; t < 30000; t += 4) {
      ray.at(t, p);
      if (!this.S.inside(p.x, p.z)) { prev = null; continue; }
      const s = this.surfaceY(p.x, p.z);
      if (p.y <= s) return prev ? prev.lerp(p, 0.5) : p.clone();
      prev = p.clone();
    }
    return null;
  }
  findFeature(x, z, tol) {
    const order = ['buildings', 'pipelines', 'power', 'rail', 'roadsRisk', 'profiles', 'alerts', 'zone'];
    for (const id of order) {
      const L = this.layers.get(id);
      if (!L || !L.visible) continue;
      let best = null, bestD = tol;
      for (const it of L.pickItems) {
        if (it.polygon && id !== 'zone' && id !== 'alerts') {
          if (pointInRings(x, z, it.rings)) return { layer: id, f: it.f };
        }
        if (it.polygon && (id === 'zone' || id === 'alerts')) {
          if (pointInRings(x, z, it.rings)) return { layer: id, f: it.f };
          continue;
        }
        for (const r of it.rings)
          for (let i = 0; i < r.length - 1; i++) {
            const d = segDist(x, z, r[i], r[i + 1]);
            if (d < bestD) { bestD = d; best = it.f; }
          }
      }
      if (best) return { layer: id, f: best };
    }
    return null;
  }
}

function pointInRings(x, z, rings) {
  let inside = false;
  for (const r of rings)
    for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
      const [xi, zi] = r[i], [xj, zj] = r[j];
      if (zi > z !== zj > z && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi) inside = !inside;
    }
  return inside;
}
function segDist(x, z, a, b) {
  const dx = b[0] - a[0], dz = b[1] - a[1];
  const l2 = dx * dx + dz * dz;
  let t = l2 ? ((x - a[0]) * dx + (z - a[1]) * dz) / l2 : 0;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(x - a[0] - t * dx, z - a[1] - t * dz);
}
