#!/usr/bin/env node
// Screenshots of every section of the built page (dist/) in headless Edge/Chrome.
// Usage: node scripts/screenshots.mjs [width] [height]   -> screenshots/*.png
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';
import { preview } from 'vite';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const OUT = path.join(ROOT, 'screenshots');
fs.mkdirSync(OUT, { recursive: true });
const W = +(process.argv[2] || 1440), H = +(process.argv[3] || 900);
const exe = [
  process.env.BROWSER_PATH,
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium',
].find((p) => p && fs.existsSync(p));

const server = await preview({ root: ROOT, preview: { port: 4180, strictPort: false, open: false }, logLevel: 'silent' });
const browser = await puppeteer.launch({
  executablePath: exe, headless: true,
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
  defaultViewport: { width: W, height: H },
});
const page = await browser.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
page.on('response', (r) => r.status() >= 400 && errors.push(`HTTP ${r.status()} ${r.url()}`));
await page.goto(server.resolvedUrls.local[0], { waitUntil: 'networkidle2', timeout: 120000 });
await page.waitForSelector('body[data-ready="1"]', { timeout: 120000 });
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

async function shot(name, selector, block = 'start', ms = 1500) {
  await page.evaluate((s, b) => document.querySelector(s).scrollIntoView({ block: b, behavior: 'instant' }), selector, block);
  await wait(ms);
  await page.screenshot({ path: path.join(OUT, `${name}.png`) });
  console.log('saved', name);
}

await shot('01-problem', '#s-problem');
await shot('02-nisar', '#s-nisar');
await shot('03a-place-overview', '[data-step="overview"]', 'center', 2500);
await shot('03b-place-zone', '[data-step="zone"]', 'center', 2000);
await shot('03c-place-core', '[data-step="core"]', 'center', 2500);
await shot('04a-motion-play', '[data-step="play"]', 'center', 4000);
await shot('04b-motion-deform', '[data-step="deform"]', 'center', 2500);
await shot('04c-motion-points', '#chart-points', 'center');
await shot('04d-profiles', '#chart-prof-elev', 'center');
await shot('05a-risk', '#s-risk');
await shot('05b-risk-map', '#map-risk', 'center', 6000);
await shot('05c-risk-tables', '#chart-dist', 'center');
await shot('06-why', '#s-why');
await shot('07-response', '#s-response');
await page.evaluate(() => document.querySelector('#explore').scrollIntoView({ block: 'start', behavior: 'instant' }));
await wait(3000);
await page.evaluate(() => window.__app.pick('buildings', 'buildings', 1411));
await shot('08a-explore-3d', '#explore', 'start', 2500);
await page.evaluate(() => {
  document.getElementById('info').hidden = true;
  const s = document.getElementById('ex-mode');
  s.value = 'coh';
  s.dispatchEvent(new Event('change'));
  const x = document.getElementById('ex-exag');
  x.value = 300;
  x.dispatchEvent(new Event('input'));
});
await wait(2000);
await page.screenshot({ path: path.join(OUT, '08a2-explore-3d-coherence.png') });
console.log('saved 08a2-explore-3d-coherence');
await page.evaluate(() => {
  const s = document.getElementById('ex-mode');
  s.value = 'disp';
  s.dispatchEvent(new Event('change'));
  document.querySelector('[data-view="core"]').click();
});
await wait(2500);
await page.screenshot({ path: path.join(OUT, '08a4-explore-3d-deform-core.png') });
console.log('saved 08a4-explore-3d-deform-core');
await page.evaluate(() => window.__app.pick('buildings', 'buildings', 1411));
await wait(1500);
await page.evaluate(() => document.getElementById('info').scrollTo(0, 9999));
await page.screenshot({ path: path.join(OUT, '08a3-explore-3d-panel-quality.png') });
console.log('saved 08a3-explore-3d-panel-quality');
await page.evaluate(() => document.getElementById('tab-map').click());
await wait(6000);
await page.screenshot({ path: path.join(OUT, '08b-explore-map.png') });
console.log('saved 08b-explore-map');
await shot('09-limits', '#s-limits');
await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
await wait(800);
await page.screenshot({ path: path.join(OUT, '10-sources.png') });
console.log('saved 10-sources');
if (errors.length) console.log('Console errors:\n  ' + errors.join('\n  '));
await browser.close();
await new Promise((r) => server.httpServer.close(r));
