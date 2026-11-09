// Checks for the authored-form runtime (spec 23.6, IMG-FORM-002 and 003).
//
// The runtime is api_gateway/imagine/engine/authored.js. In the page it runs each
// part's code in a throwaway Web Worker; here the same worker source runs in a
// node worker thread, so the harness, the shape builder and the output checks are
// the real ones. Prints one JSON line per check: {"name", "ok", "detail"}.
//
// Run by tests/unit/test_imagine_authored_runtime.py.

import { createRequire } from 'node:module';
import { Worker } from 'node:worker_threads';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const FG = require(path.resolve(here, '../../api_gateway/imagine/engine/authored.js'));

// node's worker has no `self` and takes messages from parentPort, so the test
// supplies the two lines that differ. Everything else is the shipped source.
const NODE_PRE =
  "const { parentPort: __pp } = require('worker_threads');" +
  'const __post = (m) => __pp.postMessage(m);' +
  'const __listen = (f) => __pp.on("message", f);';
const NODE_POST = '__main(__PARTS, __post, __listen);';

function makeWorker(source) {
  const w = new Worker(source, { eval: true });
  const handle = {
    postMessage: (m) => w.postMessage(m),
    terminate: () => w.terminate(),
    onmessage: null,
    onerror: null,
  };
  w.on('message', (data) => handle.onmessage && handle.onmessage({ data }));
  w.on('error', (err) => handle.onerror && handle.onerror({ message: String(err && err.message) }));
  return handle;
}

const OPTS = { makeWorker, pre: NODE_PRE, post: NODE_POST };
const part = (id, code, extra = {}) => ({ id, name: id, kind: 'generated', code, ...extra });
const results = [];
function record(name, ok, detail = '') {
  results.push({ name, ok: !!ok, detail: String(detail) });
}
async function check(name, fn) {
  try {
    const out = await fn();
    record(name, out === undefined ? true : out === true, out === true || out === undefined ? '' : out);
  } catch (e) {
    record(name, false, e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e);
  }
}
const near = (a, b, tol = 1e-3) => Math.abs(a - b) <= tol;

// Two helper modes, used by tests that keep the agent's skill honest against this runtime:
//   --methods          prints the shape builder's method names
//   --form FILE.json   runs {"params", "parts"} and prints what each part drew
const argv = process.argv.slice(2);
if (argv[0] === '--methods') {
  const r = await FG.run([part('p', "throw new Error(Object.keys(g).sort().join(','));")], {}, OPTS);
  console.log(JSON.stringify({ methods: r.parts[0].error.split(',') }));
  process.exit(0);
}
if (argv[0] === '--form') {
  const fs = await import('node:fs');
  const spec = JSON.parse(fs.readFileSync(argv[1], 'utf8'));
  const r = await FG.run(spec.parts, spec.params || {}, OPTS);
  const span = (b) => b && [b.max[0] - b.min[0], b.max[2] - b.min[2], b.max[1] - b.min[1]];
  console.log(JSON.stringify({
    parts: r.parts.map((p) => ({ id: p.id, kind: p.kind, triangles: p.triangles, error: p.error || null, size: span(p.bbox) })),
    size: span(r.bbox),
    errors: r.errors,
  }));
  process.exit(0);
}

// -- the lamp ----------------------------------------------------------------
const LAMP = [
  part('base', "return [g.cylinder(p.base_r, p.base_h).at(0, p.base_h / 2, 0).name('base')];"),
  part('arm', "return [g.cylinder(4, p.arm_len).at(0, p.base_h + p.arm_len / 2, 0).name('arm')];"),
  part('head', "return [g.cone(p.head_r, p.head_h).at(0, p.base_h + p.arm_len + p.head_h / 2, 0).name('head')];"),
];
const LAMP_P = { base_r: 55, base_h: 12, arm_len: 260, head_r: 45, head_h: 60 };

await check('lamp_parts_draw_with_the_expected_bounding_boxes', async () => {
  const r = await FG.run(LAMP, LAMP_P, OPTS);
  const by = Object.fromEntries(r.parts.map((x) => [x.id, x]));
  if (r.parts.some((x) => x.error)) return 'errors: ' + JSON.stringify(r.parts.map((x) => x.error));
  const b = by.base.bbox;
  if (!near(b.min[0], -55) || !near(b.max[0], 55) || !near(b.min[1], 0) || !near(b.max[1], 12)) {
    return 'base bbox ' + JSON.stringify(b);
  }
  if (!(by.arm.triangles > 0 && by.head.triangles > 0)) return 'no triangles';
  const all = r.bbox;
  if (!near(all.max[1], 12 + 260 + 60)) return 'overall height ' + all.max[1];
  return true;
});

await check('a_parameter_change_changes_the_geometry_with_no_model_call', async () => {
  const a = await FG.run(LAMP, LAMP_P, OPTS);
  const b = await FG.run(LAMP, { ...LAMP_P, arm_len: 400 }, OPTS);
  return near(b.bbox.max[1] - a.bbox.max[1], 140) ? true : 'height ' + a.bbox.max[1] + ' -> ' + b.bbox.max[1];
});

await check('the_same_input_gives_the_same_output', async () => {
  const a = await FG.run(LAMP, LAMP_P, OPTS);
  const b = await FG.run(LAMP, LAMP_P, OPTS);
  return JSON.stringify(a) === JSON.stringify(b) ? true : 'outputs differ';
});

// -- the sandbox -------------------------------------------------------------
const FORBIDDEN_GLOBALS = [
  'fetch', 'XMLHttpRequest', 'WebSocket', 'EventSource', 'importScripts', 'indexedDB',
  'caches', 'navigator', 'postMessage', 'eval', 'setTimeout', 'setInterval', 'queueMicrotask',
  'BroadcastChannel', 'SharedWorker', 'Worker',
];
for (const name of FORBIDDEN_GLOBALS) {
  await check(`sandbox_removes_${name}`, async () => {
    const code = `if (typeof ${name} !== 'undefined') throw new Error('${name} is reachable'); return [g.box(1, 1, 1)];`;
    const r = await FG.run([part('p', code)], {}, OPTS);
    return r.parts[0].error ? r.parts[0].error : true;
  });
}

await check('sandbox_this_is_undefined_in_strict_code', async () => {
  const r = await FG.run([part('p', "if (this !== undefined) throw new Error('this is bound'); return [g.box(1,1,1)];")], {}, OPTS);
  return r.parts[0].error ? r.parts[0].error : true;
});

await check('sandbox_parameters_are_frozen', async () => {
  const r = await FG.run([part('p', 'p.x = 1; return [g.box(1,1,1)];')], { x: 0 }, OPTS);
  return r.parts[0].error ? true : 'a parameter was writable';
});

await check('sandbox_builder_offers_only_the_documented_methods', async () => {
  const r = await FG.run(
    [part('p', "throw new Error(Object.keys(g).sort().join(','));")], {}, OPTS);
  const want = ['box', 'cone', 'cylinder', 'extrude', 'frustum', 'lathe', 'sphere', 'torus', 'tube'].join(',');
  return r.parts[0].error && r.parts[0].error.includes(want) && r.parts[0].error.split(',').length === 9
    ? true : 'got ' + r.parts[0].error;
});

await check('an_endless_loop_is_stopped_and_named', async () => {
  const t0 = Date.now();
  const r = await FG.run([part('spin', 'for (;;) {}'), part('ok', 'return [g.box(1,1,1)];')], {}, { ...OPTS, timeMs: 400 });
  const ms = Date.now() - t0;
  const bad = r.parts.find((x) => x.id === 'spin');
  const good = r.parts.find((x) => x.id === 'ok');
  if (ms > 4000) return 'took ' + ms + 'ms';
  if (!bad.error || !/time/i.test(bad.error)) return 'spin error: ' + bad.error;
  return good.error ? 'the good part failed: ' + good.error : true;
});

await check('a_busy_page_does_not_count_against_the_part', async () => {
  // The page's own thread can be blocked for seconds while it loads and solves. The limit
  // is on the part's running time, so a quick part that finishes meanwhile must not time out.
  // The order in which a blocked thread reads its queue is not fixed, so try several times.
  const failures = [];
  for (let round = 0; round < 4; round++) {
    const pending = FG.run([part('quick', 'return [g.box(1, 1, 1)];')], {}, { ...OPTS, timeMs: 100 });
    const until = Date.now() + 1200;
    while (Date.now() < until) { /* the page is busy */ }
    const r = await pending;
    if (r.parts[0].error) failures.push('round ' + round + ': ' + r.parts[0].error);
  }
  return failures.length ? failures.join(' | ') : true;
});

await check('output_over_the_triangle_budget_is_refused_naming_the_part', async () => {
  const code = 'const out = []; for (let i = 0; i < 400; i++) out.push(g.sphere(5, 40).at(i, 0, 0)); return out;';
  const r = await FG.run([part('heavy', code, { max_triangles: 5000 })], {}, OPTS);
  return r.parts[0].error && /triangle/i.test(r.parts[0].error) ? true : 'error: ' + r.parts[0].error;
});

await check('a_throwing_part_is_reported_and_the_others_still_draw', async () => {
  const r = await FG.run(
    [part('bad', "throw new Error('boom');"), part('ok', 'return [g.box(2,2,2)];')], {}, OPTS);
  const bad = r.parts.find((x) => x.id === 'bad');
  const good = r.parts.find((x) => x.id === 'ok');
  return bad.error && bad.error.includes('boom') && !good.error && good.triangles === 12 ? true : JSON.stringify(r.parts);
});

await check('a_syntax_error_in_one_part_does_not_take_down_the_others', async () => {
  const r = await FG.run(
    [part('bad', 'return [g.box(1,1,1)'), part('ok', 'return [g.box(2,2,2)];')], {}, OPTS);
  const bad = r.parts.find((x) => x.id === 'bad');
  const good = r.parts.find((x) => x.id === 'ok');
  return bad.error && !good.error ? true : JSON.stringify(r.parts);
});

await check('code_that_closes_its_function_early_cannot_run_before_the_sandbox', async () => {
  // A part that escapes its own function can still run code while the page builds the
  // list of parts. That list is built after the sandbox has removed fetch, so the escape
  // sees the sandbox. If the order were wrong, __seen would be 'function'.
  const code =
    "if (globalThis.__seen !== 'undefined') throw new Error('escape saw fetch: ' + globalThis.__seen);" +
    ' return [g.box(1, 1, 1)]; }, __x: (globalThis.__seen = typeof fetch, 1), "q": function (p, g) { return [];';
  const r = await FG.run([part('p', code)], {}, OPTS);
  return r.parts[0].error ? r.parts[0].error : r.parts[0].triangles === 12 ? true : 'triangles ' + r.parts[0].triangles;
});

await check('returning_something_that_is_not_a_list_of_shapes_is_an_error', async () => {
  const r = await FG.run([part('p', "return 'hello';"), part('q', 'return [1, 2];')], {}, OPTS);
  return r.parts.every((x) => x.error && /shape/i.test(x.error)) ? true : JSON.stringify(r.parts);
});

// -- the page does not trust what a worker sends back --------------------------
await check('sanitize_refuses_bad_numbers_bad_indices_and_oversized_output', () => {
  const ok = { name: 'a', positions: [0, 0, 0, 1, 0, 0, 0, 1, 0], indices: [0, 1, 2], flat: true };
  const lim = { maxTriangles: 10 };
  const bad = [
    { ...ok, positions: [0, 0, NaN, 1, 0, 0, 0, 1, 0] },
    { ...ok, positions: [0, 0, 0, 1e12, 0, 0, 0, 1, 0] },
    { ...ok, indices: [0, 1, 3] },
    { ...ok, indices: [0, 1] },
    { ...ok, positions: [0, 0, 0, 1, 0] },
    { ...ok, indices: Array(33).fill(0) },
    'nope',
    null,
  ];
  if (FG.sanitize([ok], lim).error) return 'a good shape was refused: ' + FG.sanitize([ok], lim).error;
  for (const b of bad) {
    const out = FG.sanitize([b], lim);
    if (!out.error) return 'accepted ' + JSON.stringify(b).slice(0, 80);
  }
  return true;
});

// -- the builder -------------------------------------------------------------
async function one(code, params = {}) {
  const r = await FG.run([part('p', code)], params, OPTS);
  return r.parts[0];
}

await check('box_has_twelve_triangles_and_cylinder_and_sphere_are_closed_shapes', async () => {
  const b = await one('return [g.box(10, 2, 4)];');
  if (b.triangles !== 12) return 'box ' + b.triangles;
  if (!near(b.bbox.max[0] - b.bbox.min[0], 10) || !near(b.bbox.max[2] - b.bbox.min[2], 4)) return JSON.stringify(b.bbox);
  const c = await one('return [g.cylinder(5, 10, 16)];');
  if (c.triangles !== 16 * 4) return 'cylinder ' + c.triangles;
  const s = await one('return [g.sphere(5, 12)];');
  return near(s.bbox.max[1], 5, 0.05) && s.triangles > 100 ? true : JSON.stringify([s.triangles, s.bbox]);
});

await check('primitives_are_wound_outward_and_have_the_right_size', async () => {
  const vol = async (code) => (await one(code)).volume;
  const cases = [
    ['box', 'return [g.box(10, 2, 4)];', 80, 0.01],
    ['cylinder', 'return [g.cylinder(5, 10, 96)];', Math.PI * 25 * 10, 0.01],
    ['sphere', 'return [g.sphere(5, 48)];', (4 / 3) * Math.PI * 125, 0.03],
    ['torus', 'return [g.torus(10, 2, 64, 32)];', 2 * Math.PI * Math.PI * 10 * 4, 0.03],
    ['cone', 'return [g.cone(6, 12, 96)];', (Math.PI * 36 * 12) / 3, 0.01],
    ['lathe_up', 'return [g.lathe([[0,0],[5,0],[5,10],[0,10]], 96)];', Math.PI * 25 * 10, 0.01],
    ['lathe_down', 'return [g.lathe([[0,10],[5,10],[5,0],[0,0]], 96)];', Math.PI * 25 * 10, 0.01],
    ['extrude', 'return [g.extrude([[0,0],[10,0],[10,4],[4,4],[4,10],[0,10]], 3)];', 64 * 3, 0.001],
    ['extrude_reversed', 'return [g.extrude([[0,10],[4,10],[4,4],[10,4],[10,0],[0,0]], 3)];', 64 * 3, 0.001],
    ['tube', 'return [g.tube([[0,0,0],[0,20,0]], 2, 64)];', Math.PI * 4 * 20, 0.01],
  ];
  for (const [name, code, want, tol] of cases) {
    const v = await vol(code);
    if (!(typeof v === 'number') || Math.abs(v - want) > want * tol) return name + ': volume ' + v + ', want ' + want;
  }
  return true;
});

await check('rotation_is_in_degrees_about_x_then_y_then_z', async () => {
  const b = await one('return [g.box(10, 2, 2).rot(0, 0, 90)];');
  return near(b.bbox.max[0], 1, 0.01) && near(b.bbox.max[1], 5, 0.01) ? true : JSON.stringify(b.bbox);
});

await check('extrude_triangulates_a_concave_outline', async () => {
  const L = '[[0,0],[10,0],[10,4],[4,4],[4,10],[0,10]]';
  const b = await one(`return [g.extrude(${L}, 3)];`);
  const n = 6;
  return b.triangles === 2 * (n - 2) + 2 * n ? true : 'triangles ' + b.triangles;
});

await check('lathe_and_torus_and_tube_and_frustum_build', async () => {
  const l = await one('return [g.lathe([[0,0],[5,0],[5,10],[2,12],[0,12]], 20)];');
  const t = await one('return [g.torus(10, 2, 24, 12)];');
  const u = await one('return [g.tube([[0,0,0],[0,20,0],[10,30,0]], 1.5, 8)];');
  const f = await one('return [g.frustum(6, 3, 10, 16)];');
  const bad = [l, t, u, f].filter((x) => x.error || !(x.triangles > 0));
  return bad.length ? JSON.stringify(bad) : true;
});

await check('a_builder_call_with_a_bad_argument_says_which_one', async () => {
  const b = await one('return [g.cylinder(-3, 10)];');
  return b.error && /cylinder/.test(b.error) && /radius|positive/.test(b.error) ? true : 'error: ' + b.error;
});

// -- what the page says about a form: measured facts, and what was not assessed ----
await check('describe_states_what_was_measured_and_what_was_not_assessed', async () => {
  const r = await FG.run(LAMP, LAMP_P, OPTS);
  const summary = { status: 'ready', parts: r.parts, bbox: r.bbox, triangles: r.triangles, errors: [] };
  const d = FG.describe(summary, { name: 'PETG', density: 1.27 });
  if (d.headline !== 'Form drawn') return 'headline ' + d.headline;
  if (d.dims.indexOf('110 \u00d7 110 \u00d7 332 mm') !== 0) return 'dims ' + d.dims;
  const facts = Object.fromEntries(d.facts);
  if (facts['Parts'] !== '3' || facts['Triangles'] !== '240') return 'facts ' + JSON.stringify(d.facts);
  if (!/g$/.test(facts['Mass if solid in PETG'] || '')) return 'no mass: ' + JSON.stringify(d.facts);
  if (d.notAssessed.length < 6) return 'only ' + d.notAssessed.length + ' not assessed';
  if (d.notAssessed.some((n) => !n.label || !n.why)) return 'an item has no reason';
  const text = JSON.stringify(d).toLowerCase();
  if (/\bpass(es|ed)?\b|\bready\b/.test(text.replace('form drawn', ''))) return 'it claims a pass: ' + text.slice(0, 200);
  return true;
});

await check('describe_says_a_form_was_not_or_only_partly_drawn_and_why', async () => {
  const bad = await FG.run([part('bad', "throw new Error('boom');"), part('ok', 'return [g.box(2, 2, 2)];')], {}, OPTS);
  const partly = FG.describe({ status: 'error', parts: bad.parts, bbox: bad.bbox, triangles: bad.triangles, errors: bad.errors }, null);
  if (partly.headline !== 'Form partly drawn') return 'partly: ' + partly.headline;
  if (!partly.problems.some((x) => x.includes('boom'))) return 'no reason: ' + JSON.stringify(partly.problems);
  if (partly.dims !== '') return 'a partly drawn form gave a size: ' + partly.dims;
  const none = await FG.run([part('bad', "throw new Error('boom');")], {}, OPTS);
  const nothing = FG.describe({ status: 'error', parts: none.parts, bbox: none.bbox, triangles: 0, errors: none.errors }, null);
  return nothing.headline === 'Form not drawn' && nothing.facts.length === 0 ? true : JSON.stringify(nothing);
});

await check('summary_block_renders_the_description_into_the_page', async () => {
  const el = () => {
    const e = { children: [], style: {}, className: '', textContent: '', id: '',
      appendChild(c) { e.children.push(c); return c; }, insertBefore(c) { e.children.unshift(c); return c; },
      setAttribute() {}, addEventListener() {}, get firstChild() { return e.children[0] || null; } };
    return e;
  };
  const doc = { createElement: el, createTextNode: (t) => ({ textContent: t }) };
  const host = el();
  const d = FG.describe({ status: 'ready', parts: (await FG.run(LAMP, LAMP_P, OPTS)).parts, bbox: { min: [0,0,0], max: [1,2,3] }, triangles: 240, errors: [] }, null);
  FG.renderSummary(doc, host, d);
  const all = [];
  (function walk(n) { if (!n) return; if (n.textContent) all.push(n.textContent); (n.children || []).forEach(walk); })(host);
  const text = all.join(' | ');
  const block = host.children[0];
  return block && block.id === 'authBlk' && text.includes('Form drawn') && text.includes('not assessed') && text.includes(d.notAssessed[0].label)
    ? true : 'block ' + (block && block.id) + ' text ' + text.slice(0, 200);
});

// -- mount: the page side, with a minimal fake DOM and three.js ------------------
await check('sliders_can_land_exactly_on_the_declared_values_and_on_whole_numbers', async () => {
  const el = () => {
    const e = { children: [], style: {}, className: '', textContent: '', attrs: {},
      appendChild(c) { e.children.push(c); return c; }, setAttribute(k, v) { e.attrs[k] = v; }, addEventListener() {} };
    return e;
  };
  const fakeDoc = { createElement: el, createTextNode: (t) => ({ text: t }), body: el() };
  class Group { constructor() { this.children = []; } add(c) { this.children.push(c); } }
  const THREE = { Group, Mesh: class {}, BufferGeometry: class { setAttribute() {} setIndex() {} computeVertexNormals() {} dispose() {} },
    MeshStandardMaterial: class {}, Float32BufferAttribute: class {} };
  const cfg = { form: { template: 'authored', params: { base_r: 55, base_h: 12, arm_len: 260, lean: -3.5 },
    ranges: { arm_len: [120, 480], lean: [-80, 80] },
    authored: { parts: [part('b', 'return [g.box(1, 1, 1)];')] } } };
  const m = FG.mount({ THREE, scene: new Group(), cfg, document: fakeDoc, host: fakeDoc.body, options: OPTS });
  await m.render();
  const rows = m.panel.children[1].children;
  const bad = [];
  rows.forEach((row) => {
    const input = row.children[2];
    const key = input.attrs['data-param'];
    const lo = +input.min, hi = +input.max, step = +input.step;
    const want = cfg.form.params[key];
    const k = (want - lo) / step;
    if (Math.abs(k - Math.round(k)) > 1e-6) bad.push(key + ': ' + want + ' is not on the grid ' + lo + ' + n * ' + step);
    for (const whole of [Math.ceil(lo) + 1, Math.floor(hi) - 1]) {
      const j = (whole - lo) / step;
      if (Math.abs(j - Math.round(j)) > 1e-6) bad.push(key + ': whole number ' + whole + ' is not reachable (step ' + step + ', min ' + lo + ')');
    }
  });
  return bad.length ? bad.join(' | ') : true;
});

await check('mount_draws_publishes_a_summary_and_redraws_on_a_parameter_change', async () => {
  const el = () => {
    const e = {
      children: [], style: {}, className: '', textContent: '', dataset: {}, value: '',
      appendChild(c) { e.children.push(c); return c; },
      setAttribute() {}, addEventListener(type, fn) { (e.handlers ||= {})[type] = fn; },
    };
    return e;
  };
  const fakeDoc = { createElement: el, createTextNode: (t) => ({ text: t }), body: el() };
  class Group { constructor() { this.children = []; } add(c) { this.children.push(c); } }
  class Mesh { constructor(geometry, material) { this.geometry = geometry; this.material = material; this.userData = {}; } }
  class BufferGeometry {
    constructor() { this.attrs = {}; }
    setAttribute(k, v) { this.attrs[k] = v; } setIndex(i) { this.index = i; } computeVertexNormals() {} dispose() {}
  }
  const THREE = {
    Group, Mesh, BufferGeometry,
    MeshStandardMaterial: class { constructor(o) { this.o = o; } },
    Float32BufferAttribute: class { constructor(a, n) { this.array = a; this.n = n; } },
  };
  const scene = new Group();
  const draws = [];
  const cfg = { form: { template: 'authored', params: { w: 10 }, ranges: { w: [5, 20] },
    authored: { parts: [part('box', 'return [g.box(p.w, 4, 4)];')] } } };
  const m = FG.mount({ THREE, scene, cfg, document: fakeDoc, host: fakeDoc.body, options: OPTS, onDraw: (r) => draws.push(r) });
  await m.render();
  const a = globalThis.FG_AUTHORED;
  if (!a || a.status !== 'ready') return 'status ' + (a && a.status) + ' ' + JSON.stringify(a && a.errors);
  if (m.group.children.length !== 1 || a.parts[0].triangles !== 12) return 'drawn ' + m.group.children.length + ' meshes';
  if (!near(a.bbox.max[0] - a.bbox.min[0], 10)) return 'width ' + JSON.stringify(a.bbox);
  const input = m.panel.children[1].children[0].children[2];
  input.value = '16'; input.handlers.input();
  await new Promise((r) => setTimeout(r, 600));
  const b = globalThis.FG_AUTHORED;
  return near(b.bbox.max[0] - b.bbox.min[0], 16) && draws.length >= 2 ? true : 'after change ' + JSON.stringify(b.bbox) + ' draws ' + draws.length;
});

console.log(results.map((r) => JSON.stringify(r)).join('\n'));
process.exit(0);
