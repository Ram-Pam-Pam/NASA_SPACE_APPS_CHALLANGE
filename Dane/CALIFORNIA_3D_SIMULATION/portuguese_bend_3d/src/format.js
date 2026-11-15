// Number formatting and the registry that ties every displayed number to a data key.
// Every number shown on the page is rendered via n(key) so scripts/verify_numbers.mjs
// can recompute it independently and compare it with the DOM.

export function fmt(v, d = 0) {
  if (v === null || v === undefined || Number.isNaN(v)) return 'no data';
  const s = Math.abs(v).toLocaleString('en-US', {
    minimumFractionDigits: d,
    maximumFractionDigits: d,
  });
  const isZero = Number(s.replace(/,/g, '')) === 0;
  return (v < 0 && !isZero ? '−' : '') + s;
}

export const N = new Map();

export function setNum(key, v, d = 0) {
  N.set(key, { v, d });
}
export function setStr(key, v) {
  N.set(key, { v, d: 's' });
}

export function text(key) {
  const e = N.get(key);
  if (!e) throw new Error(`Unknown number key: ${key}`);
  return e.d === 's' ? String(e.v) : fmt(e.v, e.d);
}

// HTML span for a registered value.
export function n(key) {
  return `<span class="num" data-num="${key}">${esc(text(key))}</span>`;
}

export function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// Values from a single data record (panels, tables built from a feature): they are
// shown with data-src so the verifier can trace them back to the file and field.
export function rec(src, field, v, d = 0) {
  const t = v === null || v === undefined || v === '' ? 'no data' : typeof v === 'number' ? fmt(v, d) : v;
  return `<span class="num" data-src="${esc(src)}" data-field="${esc(field)}">${esc(t)}</span>`;
}
