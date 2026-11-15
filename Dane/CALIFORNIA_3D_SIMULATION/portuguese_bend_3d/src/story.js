// Page content. All numbers come from the registry (format.js) filled by stats.js.
import { n, esc, text, rec } from './format.js';
import { CLASS_EN, typeEn, PROFILE_EN, reasonEn } from './i18n.js';
import { TYPE_KEYS } from './stats.js';

const tag = (kind) =>
  ({
    m: '<span class="tag tag-m">NISAR measurement</span>',
    a: '<span class="tag tag-a">Our analysis</span>',
    x: '<span class="tag tag-x">External context</span>',
  })[kind];

const ext = (href, label) => `<a href="${href}" target="_blank" rel="noopener">${label}</a>`;

function kpi(key, unit, label, kind) {
  return `<div class="kpi"><div class="kpi-val">${n(key)}<span class="unit">${unit}</span></div><div class="kpi-label">${label}</div>${tag(kind)}</div>`;
}

export function renderStory(D) {
  const profileOptions = D.profiles
    .map((p, i) => `<option value="${i}">${esc(PROFILE_EN[p.name] || p.name)}</option>`)
    .join('');
  const profileTexts = D.profiles
    .map(
      (p, i) => `<p class="profile-text" data-profile="${i}" ${i ? 'hidden' : ''}>
      Length ${n(`prof.${i}.len`)} m. Largest |LOS displacement| along the line on ${n('period.end')}: ${n(`prof.${i}.maxAbs`)} mm.
      Crossings with infrastructure in a medium or high class (dotted vertical lines): ${n(`prof.${i}.nCross`)}.</p>`,
    )
    .join('');

  const infraRows = Object.entries(TYPE_KEYS)
    .map(([pl, k]) => {
      const has = (s) => {
        try { text(`infra.${k}.${s}`); return true; } catch { return false; }
      };
      const cell = (s) => (has(s) ? n(`infra.${k}.${s}`) : '<span class="muted">not reported</span>');
      return `<tr><th scope="row">${esc(typeEn(pl))}</th><td>${cell('high')}</td><td>${cell('med')}</td><td>${cell('low')}</td><td>${cell('nodata')}</td></tr>`;
    })
    .join('');

  const topRows = D.top
    .map((f, i) => {
      const p = f.properties;
      return `<tr><td>${esc(p.nazwa || '—')}</td><td>${esc(typeEn(p.typ))}</td><td class="r">${n(`top.${i}.v`)}</td><td class="r">${n(`top.${i}.grad`)}</td><td class="r">${n(`top.${i}.len`)}</td><td class="r">${n(`top.${i}.dist`)}</td><td class="r">${n(`top.${i}.win`)}</td><td>${rec('threatened.geojson', 'powod', reasonEn(p.powod))}</td></tr>`;
    })
    .join('');

  return `
<section id="s-problem" class="sec hero">
  <div class="wrap">
    <p class="eyebrow">NASA Space Apps Challenge 2026 · Dancing with the SARs</p>
    <h1>Infrastructure on moving ground: Portuguese Bend, California</h1>
    <p class="lede">Between ${n('period.start')} and ${n('period.end')} (${n('period.days')} days), NISAR measured up to
    ${n('max.disp')} mm of line-of-sight displacement in a single ${n('grid.res')}&nbsp;m pixel at the edge of the moving area on the
    Palos Verdes Peninsula (${n('max.rateDay')} mm per day); the next-largest pixel reached ${n('max2.disp')} mm. Ground moving millimetres per day is hard to notice from one day to the next,
    but it accumulates under roads, pipes and houses. This page shows where the ground moved, how fast, and what stands on it.</p>
    <div class="kpis">
      ${kpi('max.disp', 'mm', `Largest LOS displacement, ${n('period.start')} to ${n('period.end')}, one edge pixel; next largest ${n('max2.disp')} mm`, 'm')}
      ${kpi('az.area', 'km²', 'Active landslide zone', 'a')}
      ${kpi('b.high', '', 'Buildings in the high exposure class', 'a')}
      ${kpi('b.med', '', 'Buildings in the medium exposure class', 'a')}
      ${kpi('roads.high', 'km', 'Roads in the high risk class', 'a')}
      ${kpi('infra.gas.high', 'km', 'Gas pipelines in the high risk class', 'a')}
    </div>
    <p class="fine">Every number on this page is computed in your browser from the project data files.
    Labels mark what each statement rests on: ${tag('m')} ${tag('a')} ${tag('x')}</p>
  </div>
</section>

<section id="s-nisar" class="sec">
  <div class="wrap narrow">
    <h2><span class="secno">2</span>What NISAR measures</h2>
    <p>${tag('x')} NISAR is a joint NASA–ISRO synthetic aperture radar mission (${ext('https://nisar.jpl.nasa.gov/', 'NASA JPL mission site')}).
    Radar works day and night and through clouds. We use its L-band Level-2 Geocoded Unwrapped Interferogram products (GUNW),
    distributed by the ${ext('https://asf.alaska.edu/', 'Alaska Satellite Facility DAAC')}.</p>
    <p>${tag('m')} Interferometry compares the phase of two radar acquisitions of the same ground. A phase change means the distance
    between the ground and the satellite changed. One full colour cycle of a wrapped interferogram corresponds to ${n('sar.halfWl')} mm
    of motion, half of the ${n('sar.wl')} mm radar wavelength.</p>
    <dl class="defs">
      <dt>Line of sight (LOS)</dt>
      <dd>The radar measures only the part of the motion directed along the line between the ground and the satellite.
      Positive values: towards the satellite. Negative values: away from it. Motion perpendicular to that line is not measured.</dd>
      <dt>Viewing geometry</dt>
      <dd>${tag('m')} The radar looks from the ${n('los.side')} at ${n('gunw.inc')}° from vertical (${n('gunw.incMin')}–${n('gunw.incMax')}° across the 3D model).
      The line-of-sight unit vector from the ground to the satellite is east ${n('los.e')}, north ${n('los.n')}, up ${n('los.u')} (GUNW layers).
      Ground moving down or towards the ${D.losVec[0] < 0 ? 'east' : 'west'} (away from the satellite) appears as negative values.</dd>
      <dt>Products</dt>
      <dd>${tag('m')} ${n('gunw.nPairs')} GUNW pairs from the ASF collection ${n('gunw.collection')}, product version ${n('gunw.version')}:
      status <strong>${n('gunw.status')}</strong>. Mean coherence over the 3D model area ranged from ${n('gunw.cohMin')} (pair ${n('gunw.cohMinPair')})
      to ${n('gunw.cohMax')} (pair ${n('gunw.cohMaxPair')}).</dd>
      <dt>Our stack</dt>
      <dd>Track ${n('orbit.track')}, frame ${n('orbit.frame')}, ${n('orbit.direction')} orbit. ${n('period.nDates')} acquisitions from
      ${n('period.start')} to ${n('period.end')}, chained as ${n('period.nPairs')} consecutive pairs: ${n('period.n12')} pairs span 12 days
      and ${n('period.n24')} span 24 days (between ${n('period.gapFrom')} and ${n('period.gapTo')}).
      Displacement is given relative to the first date, on a ${n('grid.res')} m grid.</dd>
    </dl>
  </div>
</section>

<section id="s-place" class="sec scrolly-sec">
  <div class="wrap">
    <h2><span class="secno">3</span>The place</h2>
  </div>
  <div class="scrolly">
    <div class="scrolly-graphic" aria-label="3D terrain model used by the story">
      <div id="viewer-story" class="viewer"></div>
      <div class="viewer-hud">
        <div class="hud-date" aria-live="polite"><span class="hud-label">Date</span> <strong id="story-date"></strong> <span id="story-span" class="muted"></span></div>
        <div id="story-legend" class="legend-bar"></div>
        <div id="story-exag" class="hud-exag" hidden data-ui></div>
      </div>
    </div>
    <div class="scrolly-steps">
      <div class="step" data-step="overview">
        <p>${tag('x')} The terrain is the USGS 3DEP lidar ground surface from ${n('dem.year')}, averaged to ${n('dem.res')} m cells,
        covering ${n('dem.wKm')} × ${n('dem.hKm')} km: the whole NISAR analysis grid. Heights are true scale.</p>
        <p>${tag('m')} Colour is NISAR LOS displacement on ${n('period.end')}: red moved away from the satellite, blue towards it,
        on a ±${n('scale.mm')} mm scale (stronger values are saturated). Gray terrain has no valid NISAR data.</p>
      </div>
      <div class="step" data-step="zone">
        <p>${tag('a')} The black outline is the active zone: ground moving faster than ${n('th.v_high')} mm/yr.
        It covers ${n('az.area')} km² in ${n('az.parts')} parts; ${n('az.partsInDem')} of them lie inside this model.</p>
        <p>${tag('a')} In the landslide core (${n('core.area')} km²) the median velocity was ${n('core.vMedian')} mm/yr and the median
        displacement over the period ${n('core.cumMedian')} mm.</p>
      </div>
      <div class="step" data-step="core">
        <p>${tag('a')} ${n('inDem.thr')} of the ${n('thr.n')} threatened elements lie inside the model extent, including
        ${n('inDem.bHigh')} of the ${n('b.high')} buildings in the high exposure class. Building outlines and roads are coloured by class.</p>
        <p>${tag('m')} In this close-up the colour scale is ±${n('scale.3d')} mm, the full range inside the model, so the core is not saturated.</p>
      </div>
      <div class="step" data-step="play" id="s-motion">
        <h2 class="step-h"><span class="secno">4</span>Motion over time</h2>
        <p>${tag('m')} The model now steps through all ${n('period.nDates')} dates. Each frame is one acquisition; nothing is
        interpolated between dates. Colour scale is fixed for all dates. ${tag('a')} Purple dashed outlines are change-detection alerts;
        each appears on the date it was raised.</p>
        <button type="button" class="btn" id="story-replay">Replay animation</button>
      </div>
      <div class="step" data-step="deform">
        <p>${tag('m')} The same time series, now moving the terrain: each point is shifted along the radar line of sight
        (from the ${n('los.side')}, ${n('gunw.inc')}° incidence) by its measured displacement, exaggerated <strong id="story-exag-text" data-ui></strong>.
        Where values are negative, the surface moves away from the satellite as the dates advance.</p>
        <p>${tag('a')} For display the motion is drawn as a smooth surface: each point takes a Gaussian-weighted mean of the measured
        ${n('grid.res')} m pixels around it, up to two pixels away. Remaining gaps on land are estimated from neighbouring pixels (faint colours), only for this view.
        Inside the landslide the radar signal often decorrelates and the project series leaves those pixels out; where every GUNW pair is still
        unwrapped we built their series ourselves (pale colours, ${n('ext.nDem')} pixels in the model). Only the line-of-sight component of the motion is known.</p>
      </div>
    </div>
  </div>
  <div class="wrap">
    <div class="grid-2">
      <div>
        <h3>Three pixels compared</h3>
        <p>${tag('m')} Largest displacement: pixel centred at ${n('max.lat')}° N, ${n('max.lon')}°, ${n('max.disp')} mm by ${n('period.end')}.
        Its mean rate was ${n('max.rate1')} mm/day from ${n('period.start')} to ${n('max.midDate')} and ${n('max.rate2')} mm/day from
        ${n('max.midDate')} to ${n('period.end')}.</p>
        <p>${tag('a')} This is an edge pixel: only ${n('max.nbFull')} of its 8 neighbours have a full series. The next-largest pixel, directly south
        (${n('max2.lat')}° N, ${n('max2.lon')}°), reached ${n('max2.disp')} mm, ${n('max.gap')} mm less.</p>
        <p>${tag('m')} Check against the GUNW products: both pixels are in the same unwrapped connected component in ${n('chk.ccBoth')} of ${n('gunw.nPairs')} pairs,
        the difference between them never exceeds ${n('chk.maxDiff')} mm in one pair (half a fringe cycle is ${n('chk.halfCycle')} mm), and the per-pair
        differences add up to ${n('chk.sumDiff')} mm (time series: ${n('chk.tsDiff')} mm). Lowest pair coherence at the pixel: ${n('chk.cohMin')}.
        ${D.chkOk ? 'There is no sign of a phase-unwrapping error: the value is supported by the source data.' : 'At least one check fails, so a phase-unwrapping error cannot be ruled out.'}</p>
        <p>${tag('m')} Stable reference: the pixel in the model extent with the smallest peak displacement
        (${n('stable.lat')}° N, ${n('stable.lon')}°) stayed within ±${n('stable.maxAbs')} mm on all dates.</p>
      </div>
      <div class="chart-box"><canvas id="chart-points" role="img" aria-label="LOS displacement over time at three pixels: largest displacement, landslide core and stable reference"></canvas></div>
    </div>

    <h3 class="mt">Cross-sections</h3>
    <div class="profile-ctl">
      <label>Profile <select id="profile-select">${profileOptions}</select></label>
      <label class="grow">Date <input type="range" id="profile-date" min="0" max="${D.ts.E - 1}" value="${D.ts.E - 1}" step="1"></label>
      <output id="profile-date-out" class="mono"></output>
    </div>
    ${profileTexts}
    <p class="fine">${tag('x')} Elevation: lidar DTM ${n('dem.year')}, gaps outside the DTM extent. ${tag('m')} Displacement: NISAR, sampled every 20 m along the line by the project processing; gaps = no data.</p>
    <div class="chart-box short"><canvas id="chart-prof-elev" role="img" aria-label="Terrain elevation along the selected profile"></canvas></div>
    <div class="chart-box short"><canvas id="chart-prof-disp" role="img" aria-label="LOS displacement along the selected profile"></canvas></div>
  </div>
</section>

<section id="s-risk" class="sec">
  <div class="wrap">
    <h2><span class="secno">5</span>What is at risk</h2>
    <p class="narrow">${tag('a')} Classes describe ground motion under an object, not the condition of the structure.
    Medium: velocity at least ${n('th.v_med')} mm/yr or differential motion at least ${n('th.g_med')} mm/100 m/yr in a coherent moving area.
    High: at least ${n('th.v_high')} mm/yr or ${n('th.g_high')} mm/100 m/yr, or an alert in the latest monitoring window.
    Pipelines and rail use stricter thresholds. Background velocity noise in this period: ${n('noise')} mm/yr.</p>
    <div class="kpis">
      <div class="kpi"><div class="kpi-val">${n('b.high')}</div><div class="kpi-label">buildings, high class (of ${n('b.total')} assessed)</div></div>
      <div class="kpi"><div class="kpi-val">${n('b.med')}</div><div class="kpi-label">buildings, medium class</div></div>
      <div class="kpi"><div class="kpi-val">${n('b.inZone')}</div><div class="kpi-label">buildings inside the active zone</div></div>
      <div class="kpi"><div class="kpi-val">${n('b.confirmed')}</div><div class="kpi-label">threatened buildings with motion in ≥ 2 monitoring windows</div></div>
      <div class="kpi"><div class="kpi-val">${n('infra.nObjects')}</div><div class="kpi-label">threatened infrastructure segments (${n('infra.confirmed')} with motion in ≥ 2 windows)</div></div>
      <div class="kpi"><div class="kpi-val">${n('alerts.n')}</div><div class="kpi-label">change-detection alerts (${n('alerts.new')} new moving areas, ${n('alerts.decel')} decelerations)</div></div>
    </div>

    <div class="map-wrap">
      <div id="map-risk" class="map" role="region" aria-label="Map of threatened elements, alerts and active zone"></div>
      <div class="map-legend" id="risk-legend"></div>
    </div>
    <p class="fine">${tag('m')} Raster: cumulative LOS displacement on ${n('period.end')}, fixed scale ±${n('scale.mm')} mm (values beyond are saturated).
    ${tag('a')} Points: threatened buildings (footprints at higher zoom). Purple: alerts. Dashed frame: extent of the 3D model. Click any object for details.</p>

    <div class="grid-2 mt">
      <div>
        <h3>Infrastructure length by class (km)</h3>
        <table class="data">
          <thead><tr><th scope="col">Type</th><th scope="col">High</th><th scope="col">Medium</th><th scope="col">Low</th><th scope="col">No data</th></tr></thead>
          <tbody>${infraRows}</tbody>
        </table>
        <p class="fine">${tag('a')} Lengths of 100 m segments in each class. “No data”: too few reliable NISAR pixels (water, loss of coherence) — unknown, not safe.</p>
      </div>
      <div>
        <h3>Buildings by distance from the active zone</h3>
        <div class="chart-box"><canvas id="chart-dist" role="img" aria-label="Number of buildings within given distances from the active zone"></canvas></div>
        <p class="fine">${tag('a')} All buildings, counted cumulatively: inside the zone ${n('b.do_0_m')}, within 100 m ${n('b.do_100_m')},
        250 m ${n('b.do_250_m')}, 500 m ${n('b.do_500_m')}, 1 km ${n('b.do_1000_m')}.</p>
      </div>
    </div>

    <h3 class="mt">High-class infrastructure with the fastest ground motion</h3>
    <div class="table-scroll">
      <table class="data">
        <thead><tr><th scope="col">Name</th><th scope="col">Type</th><th scope="col" class="r">v (mm/yr)</th><th scope="col" class="r">Differential (mm/100 m/yr)</th><th scope="col" class="r">Length (m)</th><th scope="col" class="r">Distance to zone (m)</th><th scope="col" class="r">Windows with motion</th><th scope="col">Reason</th></tr></thead>
        <tbody>${topRows}</tbody>
      </table>
    </div>
    <p class="fine">${tag('a')} From threatened.geojson, ranked by |v|. Full list: <a href="data/threatened.csv" download>threatened.csv</a>.</p>
  </div>
</section>

<section id="s-why" class="sec">
  <div class="wrap">
    <h2><span class="secno">6</span>Why here: what the data show</h2>
    <p class="narrow">${tag('a')} These are associations computed from the data, not a causal analysis. Geology, groundwater and
    rainfall are not in our data and are not discussed here.</p>
    <div class="grid-2">
      <div>
        <h3>Displacement and terrain slope</h3>
        <div class="chart-box"><canvas id="chart-slope" role="img" aria-label="Median absolute LOS displacement by terrain slope class"></canvas></div>
      </div>
      <div class="pad-top">
        <p>${tag('a')} For ${n('slope.nPix')} NISAR pixels inside the lidar model we averaged the slope of the ${n('dem.res')} m terrain cells
        and grouped pixels by slope. The median |LOS displacement| on ${n('period.end')} per slope class ranges from ${n('slope.minMed')} mm
        (${n('slope.minLo')}–${n('slope.minHi')}°) to ${n('slope.maxMed')} mm (first reached at ${n('slope.maxLo')}–${n('slope.maxHi')}°).
        ${D.slopeBins.at(-1).med < D.slopeBins[0].med ? 'The steepest class has a lower median than the flattest class, so in this area larger motion is not tied to steeper ground.' : 'The steepest class has a higher median than the flattest class.'}
        See the table for every class.</p>
        <p class="fine">Caveats: LOS sensitivity depends on the slope direction relative to the radar; the terrain is from ${n('dem.year')};
        pixels need at least half of their area covered by the DTM.</p>
        <table class="data compact">
          <thead><tr><th scope="col">Slope (°)</th><th scope="col" class="r">Pixels</th><th scope="col" class="r">Median |LOS| (mm)</th></tr></thead>
          <tbody>${D.slopeBins.map((b, i) => `<tr><td>${n(`slope.edge.${i}`)}–${n(`slope.edge.${i + 1}`)}</td><td class="r">${n(`slope.${i}.n`)}</td><td class="r">${n(`slope.${i}.med`)}</td></tr>`).join('')}</tbody>
        </table>
      </div>
    </div>
    <p class="mt narrow">${tag('a')} Exposure is concentrated near the active zone: ${n('b.do_0_m')} buildings stand inside it and
    ${n('b.do_1000_m')} within 1 km (chart in section 5). Type matters through the thresholds: pipelines and rail are classified more
    strictly than roads and buildings, because they tolerate less differential motion.</p>
  </div>
</section>

<section id="s-response" class="sec">
  <div class="wrap">
    <h2><span class="secno">7</span>What follows for operators</h2>
    <div class="grid-2">
      <div>
        <p>${tag('a')} Change detection runs over windows of ${n('mon.pairs')} consecutive pairs (${n('mon.n')} windows). The first
        ${n('mon.spinup')} windows only build the baseline. The first alerts appear in the window ending ${n('mon.firstAlert')}.</p>
        <p>${tag('a')} No window flagged acceleration (total ${n('mon.accelSum')} km²). In the last window, ending ${n('mon.lastDate')},
        ${n('mon.lastDecel')} km² were flagged as decelerating, ${n('mon.lastNew')} km² as new moving areas, and ${n('mon.lastMoving')} km²
        were moving in total. Window noise ranged from ${n('mon.noiseMin')} to ${n('mon.noiseMax')} mm/yr.</p>
      </div>
      <div class="chart-box"><canvas id="chart-mon" role="img" aria-label="Areas flagged per monitoring window"></canvas></div>
    </div>
    <h3 class="mt">How the outputs can be used</h3>
    <ol class="uses">
      <li><strong>Prioritise inspection.</strong> ${n('prio.n')} elements are in the high class and moved in at least 2 windows:
      ${n('prio.bld')} buildings and ${n('prio.infra')} infrastructure segments. They are the first candidates for field checks.</li>
      <li><strong>Update with every acquisition.</strong> Each new pair extends the series; the latest window is compared with the trailing baseline,
      and new moving areas, accelerations and decelerations are flagged as polygons.</li>
      <li><strong>Treat “no data” as unknown.</strong> For example, ${n('infra.rail.nodata')} km of railway and ${n('infra.majorRoad.nodata')} km of
      major roads have too few reliable pixels to be assessed.</li>
      <li><strong>Confirm on the ground.</strong> The classes are a screening tool based on LOS motion. Structural decisions need surveys,
      GNSS or inclinometers.</li>
    </ol>
  </div>
</section>

<section id="explore" class="sec explore">
  <div class="wrap">
    <h2>Explore the data</h2>
    <div class="tabs" role="tablist" aria-label="Exploration views">
      <button role="tab" id="tab-3d" aria-selected="true" aria-controls="pane-3d">3D terrain model</button>
      <button role="tab" id="tab-map" aria-selected="false" aria-controls="pane-map" tabindex="-1">Regional map</button>
    </div>
  </div>
  <div class="explore-body">
    <aside class="ctl" aria-label="Controls">
      <fieldset>
        <legend>Time</legend>
        <div class="date-row"><button type="button" class="btn" id="ex-play" aria-pressed="false">Play</button>
          <output id="ex-date" class="mono" aria-live="polite"></output></div>
        <input type="range" id="ex-epoch" min="0" max="${D.ts.E - 1}" value="${D.ts.E - 1}" step="1" aria-label="Acquisition date">
        <p class="fine" id="ex-span"></p>
      </fieldset>
      <fieldset class="only-3d">
        <legend>Displacement in 3D</legend>
        <label>Colour shows
          <select id="ex-mode">
            <option value="disp">LOS displacement since the first date (NISAR)</option>
            <option value="coh">Coherence of the pair ending on this date (GUNW)</option>
            <option value="cc">Unwrapping: connected component (GUNW)</option>
          </select></label>
        <label>Surface
          <select id="ex-surface">
            <option value="filled" selected>Smooth surface, gaps estimated from neighbours (display only)</option>
            <option value="surface">Smooth surface, large gaps left empty</option>
            <option value="blend">Blended between measured pixels</option>
            <option value="raw">Raw ${text('grid.res')} m pixels</option>
          </select></label>
        <label class="row"><input type="checkbox" id="ex-detail"> High-detail terrain (every DEM cell; needs a strong GPU)</label>
        <label class="row"><input type="checkbox" id="ex-ext" checked> Include low-coherence pixels (own series from GUNW, pale)</label>
        <label>Colour scale
          <select id="ex-scale">
            <option value="m" selected>±${text('scale.mm')} mm (same as map rasters)</option>
            <option value="d">±${text('scale.3d')} mm (full range in model)</option>
          </select></label>
        <label>Displacement drawn along the line of sight, exaggerated <output id="ex-exag-out" class="mono" data-ui></output>
          <input type="range" id="ex-exag" min="0" max="1000" step="50" value="0"></label>
        <div class="btn-row"><button type="button" class="btn" data-view="overview">Overview</button><button type="button" class="btn" data-view="core">Core</button><button type="button" class="btn" data-view="top">Top</button></div>
      </fieldset>
      <fieldset>
        <legend>Layers</legend>
        <div id="ex-layers"></div>
      </fieldset>
      <div id="ex-legend"></div>
    </aside>
    <div class="stage">
      <div id="pane-3d" role="tabpanel" aria-labelledby="tab-3d" class="pane"><div id="viewer-explore" class="viewer"></div></div>
      <div id="pane-map" role="tabpanel" aria-labelledby="tab-map" class="pane" hidden><div id="map-explore" class="map"></div></div>
      <div class="panel" id="info" tabindex="-1" hidden data-verify="panel"></div>
      <p class="hint">Click terrain or an object for details and a time series. Drag to rotate, right-drag to pan, scroll to zoom; arrow keys pan when the model has focus.</p>
    </div>
  </div>
</section>

<section id="s-limits" class="sec">
  <div class="wrap narrow">
    <h2><span class="secno">8</span>Limitations, methods and sources</h2>
    <h3>Limitations</h3>
    <ul class="limits">
      <li><strong>Line of sight only.</strong> Values are motion along the radar line of sight. Vertical and horizontal motion cannot be separated with one viewing geometry.</li>
      <li><strong>One orbit.</strong> All data come from track ${n('orbit.track')} (${n('orbit.direction')}), frame ${n('orbit.frame')}. No ascending track was used.
      Incidence ${n('gunw.inc')}° (GUNW).</li>
      <li><strong>Time sampling.</strong> ${n('period.n12')} pairs span 12 days and ${n('period.n24')} span 24 days. Motion between dates is not shown and is not interpolated.
      The analysis covers ${n('period.days')} days only; velocities in mm/yr extrapolate this period and include the rainy season.</li>
      <li><strong>Product status.</strong> All ${n('gunw.nPairs')} GUNW pairs come from the collection ${n('gunw.collection')} (product version ${n('gunw.version')}),
      i.e. status ${n('gunw.status')}. Provisional products can change when NASA/ISRO reprocess them; treat absolute values with care.</li>
      <li><strong>Spatial resolution and gaps.</strong> NISAR values are on a ${n('grid.res')} m grid; a full series exists for ${n('grid.fullPct')} % of grid cells.
      Cells without data are shown as gray or transparent and never filled.</li>
      <li><strong>Extreme pixel.</strong> The largest value, ${n('max.disp')} mm, comes from one pixel at the edge of the moving area with
      ${n('max.nbFull')} of 8 neighbours fully measured; the next-largest pixel reached ${n('max2.disp')} mm. ${D.chkOk ? 'The GUNW connected components and per-pair differences show no unwrapping error (see Three pixels compared), but' : 'A phase-unwrapping error cannot be ruled out, and'}
      a single pixel is a weak basis for a headline. Core statistics (median ${n('core.cumMedian')} mm) are not affected by it.</li>
      <li><strong>Terrain from ${n('dem.year')}.</strong> The lidar surface predates the measurements; the landslide has moved since. ${n('dem.validPct')} % of the model extent has lidar ground elevation.</li>
      <li><strong>Low-coherence pixels in 3D.</strong> ${n('ext.n')} pixels outside the project series, with every GUNW pair unwrapped and no water, are shown
      in 3D with a series we chained from the per-pair GUNW increments, after removing each pair's median offset to the project series.
      On the ${n('ext.nCheck')} pixels where both exist, the per-pair difference has an RMS of ${n('ext.rms')} mm and the cumulative values differ by a median of ${n('ext.medCum')} mm.
      These pixels are not used in any statistic or risk class.</li>
      <li><strong>Exaggeration and smoothing in 3D.</strong> Terrain heights are true scale. Displacement can be drawn along the line of sight, exaggerated by the factor shown on screen.
      The default 3D surface is smoothed for display (Gaussian-weighted mean of measured pixels within two pixels), and the remaining gaps on land are
      estimated by repeatedly averaging neighbouring pixels; estimated areas are drawn faint. These estimates are not measurements. The surface with gaps or the raw ${n('grid.res')} m pixels can be selected in Explore. The 3D view does not show the true direction of motion. Statistics and risk classes use the unsmoothed project data only.</li>
      <li><strong>Model extent.</strong> The 3D model covers ${n('dem.wKm')} × ${n('dem.hKm')} km; ${n('az.partsInDem')} of the ${n('az.parts')} parts of the active zone lie inside it. The regional map covers the full area.</li>
      <li><strong>Classes are our analysis.</strong> Thresholds are project choices. For line objects, v is the signed 95th percentile in a corridor, so it can be positive inside the zone:
      ${n('zone.vPos')} of ${n('zone.thrN')} threatened elements in the active zone have v &gt; 0.</li>
      <li><strong>Context data.</strong> Building, road and rail layers come from OpenStreetMap and pipelines from OGIM; their completeness is not verified.</li>
    </ul>
    <h3>Methods</h3>
    <ol class="methods">
      <li>NISAR L2 GUNW pairs for track ${n('orbit.track')}, frame ${n('orbit.frame')}, ${n('period.start')} to ${n('period.end')}, subset to the area of interest and chained into a displacement series (project scripts).</li>
      <li>Velocity, differential motion (gradient), active zone and change-detection windows computed from the series (project scripts, exported to data/).</li>
      <li>Infrastructure split into 100 m segments and classified by thresholds; buildings classified from five footprint points.</li>
      <li>Per-pair coherence, connected components and line-of-sight geometry read from the GUNW subsets and sampled at the centres of the ${n('grid.res')} m grid (<code>scripts/prep_gunw.py</code>).</li>
      <li>Lidar ground points (class 2, read at 1 m resolution) gridded to 5 m (mean per cell) with PDAL and averaged to ${n('dem.res')} m for this page; the model covers the whole NISAR analysis grid.</li>
      <li>Statistics on this page are computed in the browser; <code>scripts/verify_numbers.mjs</code> recomputes them independently and compares them with the rendered page.</li>
    </ol>
    <h3>Data sources</h3>
    <ul class="sources">
      <li>NISAR L2 GUNW, NASA/ISRO — ${ext('https://asf.alaska.edu/', 'ASF DAAC')}, ${ext('https://nisar.jpl.nasa.gov/', 'NISAR mission')}</li>
      <li>USGS 3DEP lidar, USGS_LPC_CA_LosAngeles_2016_LAS_2018 — ${ext('https://www.usgs.gov/3d-elevation-program', 'USGS 3D Elevation Program')}, ${ext('https://s3-us-west-2.amazonaws.com/usgs-lidar-public/USGS_LPC_CA_LosAngeles_2016_LAS_2018/ept.json', 'point cloud (EPT)')}</li>
      <li>Buildings, roads, railways, power lines — © ${ext('https://www.openstreetmap.org/copyright', 'OpenStreetMap contributors')} (ODbL); map tiles © OpenStreetMap</li>
      <li>Gas and oil pipelines — OGIM (Oil and Gas Infrastructure Mapping database)</li>
      <li>Challenge — ${ext('https://www.spaceappschallenge.org/2026/challenges/dancing-with-the-sars/', 'NASA Space Apps 2026: Dancing with the SARs')}</li>
      <li>Software — three.js, MapLibre GL JS, Chart.js, proj4js</li>
    </ul>
  </div>
</section>
<footer class="site-footer"><div class="wrap">Open-source project for NASA Space Apps Challenge 2026. Data: NASA/ISRO NISAR via ASF DAAC · USGS 3DEP · © OpenStreetMap contributors · OGIM.</div></footer>
`;
}

export { CLASS_EN };
