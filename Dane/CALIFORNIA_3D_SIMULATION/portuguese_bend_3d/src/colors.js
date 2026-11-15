// ColorBrewer RdBu (11 classes). Negative (away from satellite) = red, positive (towards) = blue,
// matching the raster convention in data/README.md.
export const RDBU = [
  '#67001f', '#b2182b', '#d6604d', '#f4a582', '#fddbc7', '#f7f7f7',
  '#d1e5f0', '#92c5de', '#4393c3', '#2166ac', '#053061',
];

export function hexToRgb(h) {
  const v = parseInt(h.slice(1), 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}

export function rdbu(t) {
  const x = Math.max(-1, Math.min(1, t));
  const f = (x + 1) * 5;
  const i = Math.min(9, Math.floor(f));
  const a = hexToRgb(RDBU[i]), b = hexToRgb(RDBU[i + 1]);
  const k = f - i;
  return a.map((v, j) => Math.round(v + (b[j] - v) * k));
}

export function rdbuGradientCss() {
  return `linear-gradient(to right, ${RDBU.join(', ')})`;
}

// Series colours for point time series (validated: CVD-separated; the stable reference is gray + dashed).
export const SERIES = { max: '#1f6f9a', core: '#c0601a', stable: '#6a6f75' };

// Overlay colours that are not risk classes.
export const INK = {
  zone: '#1d232a',
  alert: '#6b3fa0',
  profile: '#1d232a',
  context: '#9aa3ab',
  demExtent: '#1d232a',
};
