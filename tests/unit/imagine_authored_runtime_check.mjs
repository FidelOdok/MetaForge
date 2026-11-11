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

// -- the Engineer graph: parameters to parts to the form ------------------------------------
function graphDom() {
  const mk = (tag) => {
    const e = {
      tag, children: [], style: { setProperty(k, v) { e.style[k] = v; } }, attrs: {}, handlers: {}, _text: '', open: false,
      clientWidth: 900, clientHeight: 700, value: '', type: '', min: '', max: '', step: '',
      appendChild(c) { e.children.push(c); c.parent = e; return c; },
      setAttribute(k, v) { e.attrs[k] = String(v); if (k === 'class') e.className = String(v); },
      getAttribute(k) { return e.attrs[k]; },
      addEventListener(t, f) { (e.handlers[t] ||= []).push(f); },
      getBoundingClientRect() { return { left: 0, top: 0, width: e.clientWidth, height: e.clientHeight }; },
      setPointerCapture() {}, releasePointerCapture() {},
      fire(t, ev) { (e.handlers[t] || []).forEach((f) => f(Object.assign({ target: e, preventDefault() {}, stopPropagation() {}, pointerId: 1 }, ev))); },
    };
    let cls = '';
    Object.defineProperty(e, 'className', { get() { return cls; }, set(v) { cls = String(v); } });
    e.classList = {
      contains: (c) => cls.split(/\s+/).includes(c),
      add: (c) => { if (!cls.split(/\s+/).includes(c)) cls = (cls + ' ' + c).trim(); },
      remove: (c) => { cls = cls.split(/\s+/).filter((x) => x && x !== c).join(' '); },
      toggle: (c, on) => { const has = cls.split(/\s+/).includes(c); if (on === undefined ? !has : on) { if (!has) cls = (cls + ' ' + c).trim(); } else e.classList.remove(c); },
    };
    Object.defineProperty(e, 'textContent', { get() { return e._text; }, set(v) { e._text = String(v); if (v === '') e.children.length = 0; } });
    return e;
  };
  const doc = { createElement: mk, createElementNS: (ns, tag) => mk(tag), createTextNode: (t) => ({ tag: '#text', _text: t, children: [] }), body: mk('body') };
  const all = (root) => { const out = []; (function w(n) { out.push(n); (n.children || []).forEach(w); })(root); return out; };
  const withClass = (root, c) => all(root).filter((n) => (n.className || '').split(/\s+/).includes(c));
  return { mk, doc, all, withClass };
}
const LAMPFORM = {
  template: 'authored', label: 'Desk lamp', params: { base_r: 55, arm_len: 260, head_r: 50 },
  ranges: { arm_len: [120, 480] },
  authored: { parts: [
    { ...part('base', 'return [g.cylinder(p.base_r, 12, 24)];'), name: 'Base', uses: ['base_r'], body: true },
    { ...part('arm', 'return [g.cylinder(4, p.arm_len, 12)];'), name: 'Arm', uses: ['arm_len'] },
    { ...part('shade', 'return [g.cone(p.head_r, 40, 24)];', { kind: 'primitive' }), name: 'Shade', uses: ['arm_len', 'head_r'] },
  ] },
};

await check('graph_structure_is_built_from_the_forms_parameters_parts_and_uses', async () => {
  const g = FG.buildGraph(LAMPFORM);
  const by = (k) => g.nodes.filter((n) => n.kind === k);
  if (by('param').length !== 3 || by('part').length !== 3 || by('form').length !== 1) return 'nodes ' + JSON.stringify(g.nodes.map((n) => n.id));
  // one wire per name a part uses, and one from each part to the form
  const uses = LAMPFORM.authored.parts.reduce((a, p) => a + p.uses.length, 0);
  if (g.links.length !== uses + 3) return 'links ' + g.links.length + ', wanted ' + (uses + 3);
  const shade = g.nodes.find((n) => n.id === 'part:shade');
  const into = g.links.filter((l) => l.to.node === 'part:shade').map((l) => l.from.node + '>' + l.to.port).sort().join();
  if (into !== 'param:arm_len>arm_len,param:head_r>head_r') return 'shade inputs ' + into;
  if (!g.links.some((l) => l.from.node === 'part:shade' && l.to.node === 'form' && l.type === 'shape')) return 'no shape wire to the form';
  // columns left to right, and nothing on top of anything else
  const x = (k) => by(k)[0].x;
  if (!(x('param') < x('part') && x('part') < x('form'))) return 'columns ' + [x('param'), x('part'), x('form')];
  for (const col of ['param', 'part']) {
    const ns = by(col).slice().sort((a, b) => a.y - b.y);
    for (let i = 1; i < ns.length; i++) if (ns[i].y < ns[i - 1].y + ns[i - 1].h) return col + ' nodes overlap';
  }
  if (!(shade.h > 0 && shade.w > 0)) return 'no size';
  return true;
});

function mountGraph(extra = {}, form = LAMPFORM) {
  const { doc, mk } = graphDom();
  class Group { constructor() { this.children = []; } add(c) { this.children.push(c); } }
  const mats = [];
  const THREE = { Group, Mesh: class { constructor(geometry, material) { this.geometry = geometry; this.material = material; this.userData = {}; } },
    BufferGeometry: class { setAttribute() {} setIndex() {} computeVertexNormals() {} dispose() {} },
    MeshStandardMaterial: class { constructor(o) { this.o = o; mats.push(this); } clone() { return new THREE.MeshStandardMaterial({ ...this.o }); } },
    Float32BufferAttribute: class {} };
  const graph = mk('div'), quick = mk('div');
  const m = FG.mount({ THREE, scene: new Group(), cfg: { form }, document: doc, quick, graph, options: OPTS, ...extra });
  return { m, doc, graph, quick, THREE, mats, all: (r) => graphDom().all.call(null, r) };
}

await check('graph_renders_nodes_ports_and_wires_in_the_engines_styles', async () => {
  const { m, graph } = mountGraph();
  await m.render();
  const { all, withClass } = graphDom();
  const nodes = withClass(graph, 'node');
  if (nodes.length !== 7) return 'nodes ' + nodes.length;
  for (const c of ['nh', 'port', 'title', 'dot']) if (!withClass(graph, c).length) return 'no .' + c;
  const wires = all(graph).filter((n) => (n.className || '').split(/\s+/).includes('wire'));
  if (wires.length !== 4 + 3) return 'wires ' + wires.length + ', wanted 7';
  if (!wires.some((w) => (w.className || '').includes('shape'))) return 'no shape-coloured wire';
  if (!wires.every((w) => /^M[\d.\-]+,[\d.\-]+ C/.test(w.attrs.d || ''))) return 'bad path: ' + wires.map((w) => w.attrs.d).slice(0, 2);
  // each wire ends on a port: its start is at a part or parameter's right edge
  const sliders = withClass(graph, 'rng');
  if (sliders.length !== 3) return 'parameter sliders ' + sliders.length;
  if (withClass(graph, 'agx-foot').length < 3) return 'part nodes show no live size';
  return true;
});

await check('graph_slider_and_panel_slider_stay_in_step_and_redraw', async () => {
  const { m, graph, quick } = mountGraph();
  await m.render();
  const { withClass } = graphDom();
  const gs = withClass(graph, 'rng').find((r) => r.attrs['data-param'] === 'arm_len');
  const qs = withClass(quick, 'rng').find((r) => r.attrs['data-param'] === 'arm_len');
  if (!gs || !qs) return 'a slider is missing';
  gs.value = '300'; gs.fire('input', {});
  if (qs.value !== '300') return 'the controls panel did not follow the graph: ' + qs.value;
  const shown = (root) => withClass(root, 'qs').find((q) => q.attrs['data-q'] === 'arm_len').children[0].children[1]._text;
  if (shown(quick) !== '300') return 'panel value ' + shown(quick);
  qs.value = '200'; qs.fire('input', {});
  if (gs.value !== '200') return 'the graph did not follow the panel: ' + gs.value;
  await new Promise((r) => setTimeout(r, 600));
  if (globalThis.FG_AUTHORED.params.arm_len !== 200) return 'not redrawn: ' + globalThis.FG_AUTHORED.params.arm_len;
  return true;
});

await check('selecting_a_part_highlights_its_node_wires_and_mesh', async () => {
  const { m, graph } = mountGraph();
  await m.render();
  const { all, withClass } = graphDom();
  const node = withClass(graph, 'node').find((n) => n.attrs['data-id'] === 'part:shade');
  node.children[0].fire('pointerdown', { clientX: 10, clientY: 10, button: 0 });
  node.children[0].fire('pointerup', { clientX: 10, clientY: 10 });
  if (!node.classList.contains('sel')) return 'the node is not selected';
  const hl = all(graph).filter((n) => (n.className || '').split(/\s+/).includes('hl'));
  if (hl.length !== 3) return 'highlighted wires ' + hl.length + ', wanted its 2 inputs and 1 output';
  if (m.graph.selected() !== 'part:shade') return 'selected() ' + m.graph.selected();
  const meshes = m.group.children.filter((c) => c.userData.part);
  const lit = meshes.filter((c) => c.material.o && c.material.o.emissive);
  if (lit.length !== 1 || lit[0].userData.part !== 'shade') return 'lit meshes ' + lit.map((c) => c.userData.part);
  // another part moves the highlight; clicking the background clears it
  const other = withClass(graph, 'node').find((n) => n.attrs['data-id'] === 'part:base');
  other.children[0].fire('pointerdown', { clientX: 5, clientY: 5, button: 0 }); other.children[0].fire('pointerup', { clientX: 5, clientY: 5 });
  if (node.classList.contains('sel') || !other.classList.contains('sel')) return 'the highlight did not move';
  const canvas = withClass(graph, 'agx-canvas')[0];
  canvas.fire('pointerdown', { target: canvas, clientX: 400, clientY: 400, button: 0 }); canvas.fire('pointerup', { target: canvas, clientX: 400, clientY: 400 });
  return m.graph.selected() === null && m.group.children.every((c) => !(c.material.o && c.material.o.emissive)) ? true : 'the background did not clear it';
});

await check('graph_pans_zooms_drags_and_fits', async () => {
  const { m, graph } = mountGraph();
  await m.render();
  const { withClass } = graphDom();
  const canvas = withClass(graph, 'agx-canvas')[0], world = withClass(graph, 'agx-world')[0];
  const v0 = { ...m.graph.view };
  canvas.fire('wheel', { clientX: 300, clientY: 300, deltaY: -240 });
  if (!(m.graph.view.k > v0.k)) return 'wheel up did not zoom in: ' + v0.k + ' -> ' + m.graph.view.k;
  for (let i = 0; i < 40; i++) canvas.fire('wheel', { clientX: 300, clientY: 300, deltaY: 400 });
  if (m.graph.view.k < 0.3 - 1e-9) return 'zoomed out past the limit: ' + m.graph.view.k;
  for (let i = 0; i < 60; i++) canvas.fire('wheel', { clientX: 300, clientY: 300, deltaY: -400 });
  if (m.graph.view.k > 1.6 + 1e-9) return 'zoomed in past the limit: ' + m.graph.view.k;
  const before = { x: m.graph.view.x, y: m.graph.view.y };
  canvas.fire('pointerdown', { target: canvas, clientX: 100, clientY: 100, button: 0 });
  canvas.fire('pointermove', { clientX: 160, clientY: 130 });
  canvas.fire('pointerup', { clientX: 160, clientY: 130 });
  if (Math.round(m.graph.view.x - before.x) !== 60 || Math.round(m.graph.view.y - before.y) !== 30) return 'pan ' + JSON.stringify([m.graph.view.x - before.x, m.graph.view.y - before.y]);
  if (!/translate\(.*\) scale\(/.test(world.style.transform || '')) return 'the world was not transformed: ' + world.style.transform;
  // dragging a node by its header moves it and its wires follow
  const node = withClass(graph, 'node').find((n) => n.attrs['data-id'] === 'part:arm');
  const x0 = m.graph.nodeAt('part:arm').x;
  node.children[0].fire('pointerdown', { clientX: 10, clientY: 10, button: 0 });
  node.children[0].fire('pointermove', { clientX: 70, clientY: 10 });
  node.children[0].fire('pointerup', { clientX: 70, clientY: 10 });
  if (!(m.graph.nodeAt('part:arm').x > x0)) return 'the node did not move';
  // Fit puts every node inside the canvas
  m.graph.fit();
  const g = m.graph, k = g.view.k;
  for (const n of g.nodes) {
    const x = n.x * k + g.view.x, y = n.y * k + g.view.y;
    if (x < -1 || y < -1 || x + n.w * k > 900 + 1 || y + n.h * k > 700 + 1) return 'Fit left ' + n.id + ' outside: ' + [x, y, n.w * k, n.h * k];
  }
  return true;
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

// -- the panel in the engine's own markup (so it looks and behaves like the other studios) -----
await check('quick_panel_uses_the_engines_own_markup_and_still_redraws', async () => {
  const mk = (tag) => {
    const e = { tag, children: [], style: { setProperty() {} }, className: '', attrs: {}, handlers: {}, _text: '', open: false,
      appendChild(c) { e.children.push(c); return c; },
      setAttribute(k, v) { e.attrs[k] = v; }, addEventListener(t, f) { e.handlers[t] = f; } };
    Object.defineProperty(e, 'textContent', { get() { return e._text; }, set(v) { e._text = String(v); if (v === '') e.children.length = 0; } });
    return e;
  };
  const doc = { createElement: mk, createTextNode: (t) => ({ tag: '#text', _text: t, children: [] }), body: mk('body') };
  class Group { constructor() { this.children = []; } add(c) { this.children.push(c); } }
  const THREE = { Group, Mesh: class { constructor() { this.userData = {}; } },
    BufferGeometry: class { setAttribute() {} setIndex() {} computeVertexNormals() {} dispose() {} },
    MeshStandardMaterial: class {}, Float32BufferAttribute: class {} };
  const cfg = { form: { template: 'authored', label: 'Phone stand', params: { base_w: 90, tilt: 55 }, ranges: { tilt: [40, 80] },
    authored: { parts: [part('base', 'return [g.box(p.base_w, 8, 20)];'), part('rest', 'return [g.box(p.base_w, p.tilt, 3)];')] } } };
  const quick = mk('div');
  const m = FG.mount({ THREE, scene: new Group(), cfg, document: doc, quick, options: OPTS });
  await m.render();
  const all = [];
  (function walk(n) { all.push(n); (n.children || []).forEach(walk); })(quick);
  const byClass = (c) => all.filter((n) => (n.className || '').split(' ').includes(c));
  const needs = { qintro: 1, pp: 2, ppb: 2, qgrp: 1, qs: 2, qt: 2, rng: 2 };
  for (const [c, n] of Object.entries(needs)) if (byClass(c).length < n) return 'class ' + c + ' x' + byClass(c).length + ', wanted ' + n;
  if (all.some((n) => (n.className || '').includes('fg-authored-panel'))) return 'the floating panel was used';
  const summary = all.find((n) => n.tag === 'summary');
  if (!summary || !JSON.stringify(summary, (k, v) => (k === 'handlers' ? undefined : v)).includes('Phone stand')) return 'the form is not named in the summary';
  const rows = byClass('qs');
  const keys = rows.slice(0, 2).map((r) => r.attrs['data-q']);
  if (keys.join() !== 'base_w,tilt') return 'rows ' + keys;
  const label = (r) => r.children[0].children[0]._text;
  if (label(rows[0]) !== 'Base w' || label(rows[1]) !== 'Tilt') return 'labels ' + label(rows[0]) + ' / ' + label(rows[1]);
  const input = rows[1].children[1];
  if (input.className !== 'rng' || input.type !== 'range' || +input.min > 40 || +input.max < 80) return 'slider ' + input.className + input.min + input.max;
  const value = rows[1].children[0].children[1];
  if (value._text !== '55') return 'value ' + value._text;
  input.value = '70'; input.handlers.input();
  if (value._text !== '70') return 'the value did not follow the slider: ' + value._text;
  await new Promise((r) => setTimeout(r, 600));
  if (globalThis.FG_AUTHORED.params.tilt !== 70) return 'the form was not redrawn: ' + JSON.stringify(globalThis.FG_AUTHORED.params);
  const partRows = all.filter((n) => n.attrs && n.attrs['data-part']);
  if (partRows.map((n) => n.attrs['data-part']).join() !== 'base,rest') return 'parts list: ' + partRows.map((n) => n.attrs['data-part']);
  return true;
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


// -- the components panel: each real part, how far it is trusted, and what has no part -----

const COMP_CFG = () => ({
  functions: ['e_th', 'e_press'],
  form: { authored: { parts: [
    { id: 'th', kind: 'component', component_ref: 'th-a' },
    { id: 'body', kind: 'generated' },
  ] } },
  _context: { parts: [
    { id: 'th-a', role: 'fn:e_th', name: 'Temp sensor', mpn: 'T-1', manufacturer: 'Acme',
      datasheet: 'https://x.test/t.pdf', retrieved: '2026-11-10', interfaces: [{ id: 'io' }],
      geometry: { envelope: [3, 3, 1], fidelity: 'exact', size_basis: 'read', fidelity_basis: 'from the model' } },
    { id: 'led-a', role: 'fn:e_led', name: 'Status LED', mpn: 'L-1', manufacturer: 'Acme',
      geometry: { envelope: [2, 2, 1], fidelity: 'approximation', size_basis: 'typical', fidelity_basis: 'typical class size' } },
    { id: 'gone', role: 'other', name: 'Excluded part', excluded: true },
  ] },
});

await check('components_describe_each_part_its_readiness_and_the_functions_without_a_part', async () => {
  const d = FG.describeComponents(COMP_CFG());
  if (d.rows.length !== 2) return 'rows ' + d.rows.length + ' (an excluded part must be left out)';
  const th = d.rows.find((r) => r.id === 'th-a'), led = d.rows.find((r) => r.id === 'led-a');
  const all = (r) => [r.steps.defined, r.steps.size, r.steps.sourced, r.steps.drawn].join();
  if (all(th) !== 'true,true,true,true' || th.state !== 'pass') return 'th ' + all(th) + ' ' + th.state;
  if (all(led) !== 'true,false,false,false' || led.state !== 'warn') return 'led ' + all(led) + ' ' + led.state;
  if (JSON.stringify(d.missing) !== '["e_press"]') return 'missing ' + JSON.stringify(d.missing);
  if (th.fidelity !== 'exact' || led.sizeBasis !== 'typical') return 'labels ' + th.fidelity + ' ' + led.sizeBasis;
  const h = d.headline;
  return h.includes('2 parts') && h.includes('1 with a real size') && h.includes('1 drawn in the form') ? true : h;
});

await check('components_a_guess_never_counts_as_a_real_size', async () => {
  const size = (geometry) => FG.describeComponents({ _context: { parts: [{ id: 'p', role: 'other', geometry }] } }).rows[0].steps.size;
  const got = [
    size({ envelope: [1, 1, 1] }),
    size({ envelope: [1, 1, 1], size_basis: 'read' }),
    size({ envelope: [1, 1, 1], size_basis: 'typical' }),
    size({ envelope: [1, 1, 1], size_basis: 'generic' }),
    size({ envelope: [1, 1, 1], fidelity: 'placeholder' }),
    size({}),
  ].join();
  return got === 'true,true,false,false,false,false' ? true : got;
});

await check('components_a_dummy_link_is_not_a_source', async () => {
  const sourced = (datasheet) => FG.describeComponents({ _context: { parts: [
    { id: 'p', role: 'other', mpn: 'M', manufacturer: 'A', datasheet, retrieved: '2026-11-10',
      geometry: { envelope: [1, 1, 1] } }] } }).rows[0].steps.sourced;
  const got = [
    sourced('https://www.mouser.com/datasheet/x.pdf'),
    sourced('https://example.invalid/no-datasheet-read'),
    sourced('http://x.INVALID./a'),
    sourced('https://user@host.invalid:8080/a'),
    sourced('datasheet page 12'),
    sourced(''),
  ].join();
  return got === 'true,false,false,false,true,false' ? true : got;
});

await check('components_card_renders_rows_and_missing_functions_and_is_absent_when_empty', async () => {
  const el = () => {
    const e = { children: [], style: {}, className: '', textContent: '', id: '',
      appendChild(c) { e.children.push(c); return c; }, insertBefore(c) { e.children.unshift(c); return c; },
      setAttribute() {}, addEventListener() {}, get firstChild() { return e.children[0] || null; } };
    return e;
  };
  const doc = { createElement: el, createTextNode: (t) => ({ textContent: t }) };
  const host = el();
  const card = FG.renderComponents(doc, host, FG.describeComponents(COMP_CFG()));
  const all = [];
  (function walk(n) { if (!n) return; if (n.textContent) all.push(n.textContent); (n.children || []).forEach(walk); })(host);
  const text = all.join(' | ');
  if (!card || card.id !== 'authComp') return 'card ' + (card && card.id);
  for (const want of ['Components', 'Temp sensor', 'T-1', 'Status LED', 'e_press', 'no part', 'typical']) {
    if (!text.includes(want)) return 'missing "' + want + '" in ' + text.slice(0, 300);
  }
  const empty = el();
  const none = FG.renderComponents(doc, empty, FG.describeComponents({}));
  return none === null && empty.children.length === 0 ? true : 'an empty config drew a card';
});


// -- what is measured from the drawn shapes: overhang, balance and overlaps ---------------------

const P = (id, kind, code) => ({ id, name: id, kind, code, uses: [] });
const measured = async (...parts) => (await FG.run(parts, {}, OPTS)).measured;
const closeTo = (a, b, tol) => Math.abs(a - b) <= (tol === undefined ? 0.01 : tol);

await check('measure_a_standing_box_has_no_overhang_and_tips_at_45_degrees', async () => {
  const m = await measured(P('box', 'generated', 'return [g.box(10, 10, 10).at(0, 5, 0)];'));
  if (!m) return 'nothing measured';
  if (m.overhang_area !== 0 || m.overhang_share !== 0) return 'overhang ' + m.overhang_area;
  if (!closeTo(m.com[1], 5) || !closeTo(m.com[0], 0) || !closeTo(m.com[2], 0)) return 'com ' + JSON.stringify(m.com);
  if (!m.support || !closeTo(m.support.margin_mm, 5) || !closeTo(m.support.tip_angle_deg, 45, 0.05)) return 'support ' + JSON.stringify(m.support);
  return closeTo(m.total_area, 600) ? true : 'area ' + m.total_area;
});

await check('measure_a_block_hanging_off_a_small_base_would_tip_over', async () => {
  const m = await measured(
    P('base', 'generated', 'return [g.box(2, 2, 2).at(0, 1, 0)];'),
    P('block', 'generated', 'return [g.box(10, 10, 10).at(6, 7, 0)];'));
  if (!m || !m.support) return 'no support ' + JSON.stringify(m);
  if (!(m.support.margin_mm < 0) || !(m.support.tip_angle_deg < 0)) return 'stable? ' + JSON.stringify(m.support);
  return m.com[0] > 5 ? true : 'com x ' + m.com[0];
});

await check('measure_a_mushroom_overhang_counts_the_whole_underside_as_an_upper_bound', async () => {
  const m = await measured(
    P('stem', 'generated', 'return [g.box(2, 10, 2).at(0, 5, 0)];'),
    P('disc', 'generated', 'return [g.box(10, 2, 10).at(0, 11, 0)];'));
  if (!m) return 'nothing measured';
  if (!closeTo(m.overhang_area, 100)) return 'overhang ' + m.overhang_area;
  if (!(m.overhang_share > 0.25 && m.overhang_share < 0.3)) return 'share ' + m.overhang_share;
  return m.notes.some((n) => n.includes('upper bound')) ? true : 'no note about the upper bound: ' + JSON.stringify(m.notes);
});

await check('measure_a_form_with_a_point_at_the_bottom_has_no_base_to_stand_on', async () => {
  const m = await measured(P('cone', 'generated', 'return [g.lathe([[0, 0], [10, 20], [0, 20]], 24)];'));
  if (!m) return 'nothing measured';
  return m.support === null && m.notes.some((n) => n.includes('no flat base')) ? true : 'support ' + JSON.stringify(m.support) + ' ' + JSON.stringify(m.notes);
});

await check('measure_overlapping_components_are_listed_with_their_volume_and_generated_parts_are_not', async () => {
  const m = await measured(
    P('a', 'component', 'return [g.box(10, 10, 10).at(0, 5, 0)];'),
    P('b', 'component', 'return [g.box(10, 10, 10).at(5, 5, 0)];'),
    P('c', 'generated', 'return [g.box(10, 10, 10).at(2, 5, 0)];'));
  if (!m || m.overlaps.length !== 1) return 'overlaps ' + JSON.stringify(m && m.overlaps);
  const o = m.overlaps[0];
  return o.a === 'a' && o.b === 'b' && closeTo(o.volume_mm3, 500, 0.5) ? true : JSON.stringify(o);
});

await check('measure_overhang_starts_at_45_degrees_from_straight_down', async () => {
  // A 10 x 1 x 10 slab tilted about z, clear of the floor. Its underside has area 100.
  // At 30 degrees the underside is within 45 of straight down: it counts (100). At 60 it
  // is not, and only the 10 mm^2 edge that now looks down is counted.
  const slab = (deg) => P('slab', 'generated', 'return [g.box(10, 1, 10).rot(0, 0, ' + deg + ').at(0, 20, 0)];');
  const flat = (await measured(slab(30))).overhang_area, steep = (await measured(slab(60))).overhang_area;
  return closeTo(flat, 100, 0.5) && closeTo(steep, 10, 0.5) ? true : 'at 30: ' + flat + ' at 60: ' + steep;
});

await check('measure_says_what_it_did_not_measure', async () => {
  const m = await measured(P('box', 'generated', 'return [g.box(10, 10, 10).at(0, 5, 0)];'));
  const text = (m.notes || []).join(' ');
  return ['wall thickness', 'counts overlapping', 'uniform density'].every((w) => text.includes(w)) ? true : text;
});

await check('describe_shows_the_measurements_and_says_when_a_form_would_tip_over', async () => {
  const base = { status: 'ready', parts: [{ id: 'a', name: 'a', kind: 'generated', triangles: 12, volume: 1000 }],
    bbox: { min: [0, 0, 0], max: [1, 2, 3] }, triangles: 12, errors: [] };
  const ok = FG.describe({ ...base, measured: { overhang_share: 0.274, overhang_area: 50, total_area: 180, com: [0, 5, 0],
    support: { margin_mm: 5, tip_angle_deg: 45 }, overlaps: [], notes: [] } }, null);
  const bad = FG.describe({ ...base, measured: { overhang_share: 0, overhang_area: 0, total_area: 180, com: [6, 7, 0],
    support: { margin_mm: -4.9, tip_angle_deg: -35.5 }, overlaps: [{ a: 'x', b: 'y', volume_mm3: 500 }], notes: [] } }, null);
  const facts = (d) => Object.fromEntries(d.facts);
  const f = facts(ok), g2 = facts(bad);
  if (f['Overhang (upper bound)'] !== '27% of the surface') return JSON.stringify(f);
  if (f['Tips over at'] !== '45°') return JSON.stringify(f);
  if (!/would tip over/i.test(g2['Balance'] || '')) return JSON.stringify(g2);
  if (!/1 pair/.test(g2['Components overlap'] || '')) return JSON.stringify(g2);
  return facts(FG.describe(base, null))['Tips over at'] === undefined ? true : 'a form with no measurements showed some';
});

await check('screening_rules_report_only_what_was_measured', async () => {
  const base = { status: 'ready', parts: [{ id: 'a', name: 'a', kind: 'generated', triangles: 12, volume: 1000 }],
    bbox: { min: [0, 0, 0], max: [1, 2, 3] }, triangles: 12, errors: [] };
  const m = (support, overlaps = []) => ({ overhang_share: 0.1, overhang_area: 10, total_area: 100, com: [0, 5, 0], support, overlaps, notes: [] });
  const by = (rules, id) => rules.find((r) => r.id === id);
  const ok = FG.screeningRules({ ...base, measured: m({ margin_mm: 40, tip_angle_deg: 22 }) });
  if (!Array.isArray(ok)) return 'no rules for a measured form: ' + JSON.stringify(ok);
  if (by(ok, 'form.balance').st !== 'pass') return JSON.stringify(by(ok, 'form.balance'));
  if (by(ok, 'form.component_overlap').st !== 'pass') return JSON.stringify(by(ok, 'form.component_overlap'));
  if (by(ok, 'form.draws').st !== 'pass') return JSON.stringify(by(ok, 'form.draws'));
  const enc = by(ok, 'form.enclosure_checks');
  if (!enc || enc.st !== 'na' || !/not run/i.test(enc.label)) return 'the enclosure checks must be listed as not run: ' + JSON.stringify(enc);
  if (ok.some((r) => ['wall', 'draft', 'cost', 'fit'].some((w) => r.id.includes(w)))) return 'a rule claims an enclosure check';
  const low = FG.screeningRules({ ...base, measured: m({ margin_mm: 4, tip_angle_deg: 10 }) });
  if (by(low, 'form.balance').st !== 'warn') return 'under 15 degrees should warn: ' + JSON.stringify(by(low, 'form.balance'));
  const tip = FG.screeningRules({ ...base, measured: m({ margin_mm: -4, tip_angle_deg: -20 }) });
  if (by(tip, 'form.balance').st !== 'fail') return 'a centre of mass outside the base should fail';
  const flat = FG.screeningRules({ ...base, measured: m(null) });
  if (by(flat, 'form.balance').st !== 'na') return 'no flat base should be not assessed';
  const ov = FG.screeningRules({ ...base, measured: m({ margin_mm: 40, tip_angle_deg: 22 }, [{ a: 'x', b: 'y', volume_mm3: 5 }]) });
  if (by(ov, 'form.component_overlap').st !== 'warn') return 'overlapping components should warn';
  const bad = FG.screeningRules({ ...base, errors: ['part x failed'], measured: m({ margin_mm: 40, tip_angle_deg: 22 }) });
  if (by(bad, 'form.draws').st !== 'fail') return 'a part that failed to draw should fail';
  if (FG.screeningRules({ status: 'pending', parts: [], errors: [], measured: null }) !== null) return 'nothing measured must give no screening';
  return FG.screeningRules(null) === null ? true : 'no summary must give no screening';
});

await check('the_summary_offers_a_button_for_a_person_to_say_they_have_seen_the_form', async () => {
  const el = () => {
    const e = { children: [], style: {}, className: '', textContent: '', id: '', attrs: {}, handlers: {}, disabled: false,
      appendChild(c) { e.children.push(c); return c; }, insertBefore(c) { e.children.unshift(c); return c; },
      setAttribute(k, v) { e.attrs[k] = v; }, addEventListener(t, f) { e.handlers[t] = f; }, get firstChild() { return e.children[0] || null; } };
    return e;
  };
  const doc = { createElement: el, createTextNode: (t) => ({ textContent: t }) };
  const base = FG.describe({ status: 'ready', parts: [{ id: 'a', name: 'a', kind: 'generated', triangles: 12, volume: 1000 }],
    bbox: { min: [0, 0, 0], max: [1, 2, 3] }, triangles: 12, errors: [] }, null);
  const find = (n, pred, out = []) => { if (!n) return out; if (pred(n)) out.push(n); (n.children || []).forEach((c) => find(c, pred, out)); return out; };
  // No hook: no button, so a plain summary is unchanged.
  const none = el(); FG.renderSummary(doc, none, base);
  if (find(none, (n) => n.attrs && n.attrs['data-form-seen'] !== undefined).length) return 'a button appeared with no hook';
  let calls = 0;
  const host = el(); FG.renderSummary(doc, host, { ...base, onSeen: () => { calls++; }, seen: false });
  const btn = find(host, (n) => n.attrs && n.attrs['data-form-seen'] !== undefined)[0];
  if (!btn) return 'no button for a person to say they have seen the form';
  if (!/seen this form/i.test(btn.textContent) || btn.disabled) return 'unexpected unseen button: ' + btn.textContent;
  btn.handlers.click();
  if (calls !== 1) return 'a click should call the hook once, got ' + calls;
  const done = el(); FG.renderSummary(doc, done, { ...base, onSeen: () => { calls++; }, seen: true });
  const b2 = find(done, (n) => n.attrs && n.attrs['data-form-seen'] !== undefined)[0];
  return b2 && b2.disabled && /seen/i.test(b2.textContent) ? true : 'a seen form should show a disabled, recorded button';
});

// Each part is drawn in its own material (MetaForge, 2026-11-11). The page hands the runtime
// the engine's process, material and colour tables; a part's make block picks from them,
// so each part is drawn as the process and material it is made with.
const LOOKS = {
  process: 'fdm', finish: 'asp',
  processes: {
    fdm: { finishes: [{ id: 'asp', name: 'As printed', rough: 0.9 }, { id: 'sanded', name: 'Sanded and primed', rough: 0.35 }] },
    cnc: { metal: true, finishes: [{ id: 'asm', name: 'As machined', rough: 0.32 }, { id: 'anod', name: 'Bead blast and anodise', rough: 0.5 }] },
  },
  materials: {
    fdm: [{ id: 'petg', name: 'PETG', density: 1.27 }, { id: 'asa', name: 'ASA', density: 1.07 }],
    cnc: [{ id: 'al6061', name: 'Aluminium 6061-T6', density: 2.7, metal: true }],
  },
  colors: [{ id: 'arctic', name: 'Arctic white', hex: '#E4E6E8' }, { id: 'graphite', name: 'Graphite', hex: '#30343B' }],
};
const DRESSED = {
  ...LAMPFORM,
  authored: { parts: [
    { ...LAMPFORM.authored.parts[0], make: { material: 'petg', finish: 'sanded', color: 'graphite', source: 'proposed', why: 'a heavy, dark base' } },
    { ...LAMPFORM.authored.parts[1], make: { process: 'cnc', material: 'al6061', finish: 'anod', color: '#A7ABB0', source: 'person' } },
    { ...LAMPFORM.authored.parts[2], make: { material: 'asa', color: 'arctic', source: 'proposed' } },
  ] },
};

await check('a_part_look_comes_from_its_own_process_material_finish_and_colour', () => {
  const al = FG.lookOf(DRESSED.authored.parts[1].make, LOOKS);
  if (!al || al.hex !== '#A7ABB0' || al.metal !== 0.9 || al.rough !== 0.5) return 'aluminium arm: ' + JSON.stringify(al);
  if (al.material !== 'Aluminium 6061-T6' || al.finish !== 'Bead blast and anodise' || al.density !== 2.7 || al.proposed) return 'arm labels: ' + JSON.stringify(al);
  const base = FG.lookOf(DRESSED.authored.parts[0].make, LOOKS);
  if (!base || base.hex !== '#30343B' || base.metal !== 0 || base.rough !== 0.35 || !base.proposed) return 'PETG base: ' + JSON.stringify(base);
  // No finish: the product's own finish when the part shares its process.
  const shade = FG.lookOf(DRESSED.authored.parts[2].make, LOOKS);
  if (!shade || shade.rough !== 0.9 || shade.material !== 'ASA') return 'ASA shade: ' + JSON.stringify(shade);
  if (FG.lookOf(null, LOOKS) !== null || FG.lookOf(DRESSED.authored.parts[0].make, null) !== null) return 'no make block or no tables must give null';
  return true;
});

await check('each_part_is_drawn_in_its_own_material', async () => {
  const { m } = mountGraph({ looks: () => LOOKS, cfg: { form: DRESSED } }, DRESSED);
  await m.render();
  const byPart = {};
  m.group.children.forEach((mesh) => { byPart[mesh.userData.part] = mesh.material; });
  const want = { base: ['#30343B', 0], arm: ['#A7ABB0', 0.9], shade: ['#E4E6E8', 0] };
  for (const [id, [hex, metal]] of Object.entries(want)) {
    const mat = byPart[id];
    if (!mat) return 'no mesh for ' + id;
    if (mat.o.color !== hex || mat.o.metalness !== metal) return id + ' drawn as ' + JSON.stringify(mat.o);
  }
  if (byPart.base === byPart.shade) return 'two parts share one material';
  return true;
});

await check('a_part_with_no_make_block_keeps_the_product_look', async () => {
  const cmf = () => ({ hex: '#123456', rough: 0.5, metal: 0, cc: 0 });
  const form = { ...DRESSED, authored: { parts: [LAMPFORM.authored.parts[0], DRESSED.authored.parts[1], LAMPFORM.authored.parts[2]] } };
  const { m } = mountGraph({ looks: () => LOOKS, cmf, cfg: { form } }, form);
  await m.render();
  const by = {};
  m.group.children.forEach((mesh) => { by[mesh.userData.part] = mesh.material.o; });
  if (by.base.color !== '#123456') return 'base should take the product colour, got ' + by.base.color;
  if (by.arm.color !== '#A7ABB0') return 'arm should keep its own, got ' + by.arm.color;
  return true;
});

await check('describe_sums_the_mass_from_each_parts_own_material', () => {
  const sm = { status: 'ready', bbox: { min: [0, 0, 0], max: [10, 10, 10] }, triangles: 36, errors: [],
    parts: [{ id: 'base', name: 'Base', kind: 'generated', triangles: 12, volume: 10000 }, { id: 'arm', name: 'Arm', kind: 'generated', triangles: 12, volume: 10000 }] };
  const dens = { base: { name: 'PETG', density: 1.27 }, arm: { name: 'Aluminium 6061-T6', density: 2.7 } };
  const d = FG.describe(sm, { name: 'PETG', density: 1.27 }, (id) => dens[id] || null);
  const row = d.facts.find((f) => /^Mass if solid/.test(f[0]));
  if (!row) return 'no mass row: ' + JSON.stringify(d.facts);
  // 10 cm3 x 1.27 + 10 cm3 x 2.7 = 39.7 g, rounded to 40
  return row[0] === "Mass if solid, each part in its own material" && row[1] === '40 g' ? true : 'mass row ' + JSON.stringify(row);
});

// Behaviours (MetaForge, 2026-11-11): the studio is a low-fidelity twin, so a form says how
// its parts move, light up and switch, and a person can drive each behaviour.
const apply = (m, p) => [m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12], m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13], m[2] * p[0] + m[6] * p[1] + m[10] * p[2] + m[14]];
const close3 = (a, b) => a.every((v, i) => Math.abs(v - b[i]) < 1e-6);
const BEHAVES = [
  { id: 'tilt', name: 'Tilt the arm', kind: 'rotate', parts: ['arm', 'shade'], axis: [0, 0, 1], origin: [0, 12, 0], range: [-90, 90] },
  { id: 'raise', name: 'Raise', kind: 'slide', parts: ['shade'], axis: [0, 2, 0], range: [0, 30] },
  { id: 'light', name: 'Light', kind: 'glow', parts: ['shade'], color: '#FFD27A' },
  { id: 'base', name: 'Base shown', kind: 'toggle', parts: ['base'], default: true },
];
const BEHAVED = { ...LAMPFORM, authored: { ...LAMPFORM.authored, behaviours: BEHAVES } };

await check('a_behaviour_moves_its_parts_about_its_own_axis_and_origin', () => {
  const r = FG.behaviourMatrix(BEHAVES, 'arm', { tilt: 90 });
  if (!r) return 'no matrix for the arm';
  // 90 degrees about z through (0, 12, 0): (1, 12, 0) goes to (0, 13, 0)
  if (!close3(apply(r, [1, 12, 0]), [0, 13, 0])) return 'rotate: ' + apply(r, [1, 12, 0]);
  const s = FG.behaviourMatrix(BEHAVES, 'shade', { tilt: 0, raise: 20 });
  if (!close3(apply(s, [0, 0, 0]), [0, 20, 0])) return 'slide along a normalised axis: ' + apply(s, [0, 0, 0]);
  if (FG.behaviourMatrix(BEHAVES, 'base', { tilt: 90 }) !== null) return 'a part with no motion has no matrix';
  // out of range is held to the range
  const c = FG.behaviourMatrix(BEHAVES, 'arm', { tilt: 400 });
  const at90 = FG.behaviourMatrix(BEHAVES, 'arm', { tilt: 90 });
  return close3(c, at90) ? true : 'a value outside the range is not clamped';
});

await check('a_person_drives_each_behaviour_in_the_studio', async () => {
  const { m, quick } = mountGraph({ cfg: { form: BEHAVED } }, BEHAVED);
  await m.render();
  const { withClass } = graphDom();
  const controls = withClass(quick, 'fg-behave');
  if (controls.length !== 4) return 'behaviour controls ' + controls.length;
  if (!m.setBehaviour || !m.behaviours) return 'no behaviour api';
  m.setBehaviour('tilt', 90);
  const arm = m.group.children.find((x) => x.userData.part === 'arm');
  if (!arm.userData.behaviourMatrix || !close3(apply(arm.userData.behaviourMatrix, [1, 12, 0]), [0, 13, 0])) return 'the arm did not turn';
  m.setBehaviour('light', true);
  const shade = m.group.children.find((x) => x.userData.part === 'shade');
  if (!shade.material.o.emissive || shade.material.o.emissive !== '#FFD27A') return 'the shade did not light: ' + JSON.stringify(shade.material.o);
  m.setBehaviour('base', false);
  const base = m.group.children.find((x) => x.userData.part === 'base');
  if (base.visible !== false) return 'the base was not switched off';
  if (m.behaviours().tilt !== 90 || m.behaviours().light !== true) return 'state ' + JSON.stringify(m.behaviours());
  await m.render();
  const again = m.group.children.find((x) => x.userData.part === 'arm');
  return again.userData.behaviourMatrix ? true : 'a redraw lost the behaviour';
});

// Live component specs (MetaForge, 2026-11-11): a component part shows its real part's
// specs, from the part record, with where each came from.
const GNSS_REC = {
  id: 'gnss-a', role: 'fn:e_gnss', name: 'GNSS antenna module', mpn: 'SAM-M10Q', manufacturer: 'u-blox',
  datasheet: 'https://example.com/sam-m10q.pdf', retrieved: '2026-11-11',
  cost: { unit: 9.8, qty: 250, source: 'https://distributor.example/sam-m10q' }, mass: 1.8,
  interfaces: [{ id: 'vcc', bus: 'pwr', dir: 'in', v: [2.7, 3.6], imax_mA: 40 }, { id: 'uart', bus: 'uart', dir: 'bi' }],
  geometry: { envelope: [15.5, 15.5, 6.3], source: 'product summary', size_basis: 'read', fidelity: 'approximation' },
};
const SPECFORM = { ...LAMPFORM, authored: { parts: [...LAMPFORM.authored.parts,
  { id: 'gnss', name: 'GNSS', kind: 'component', component_ref: 'gnss-a', code: 'return [g.box(15.5, 6.3, 15.5)];', uses: [] }] } };

await check('a_component_part_reads_its_specs_from_its_part_record', () => {
  const sp = FG.specsOf({ _context: { parts: [GNSS_REC] } }, 'gnss-a');
  if (!sp) return 'no specs';
  const want = { maker: 'u-blox', mpn: 'SAM-M10Q', size: '15.5 × 15.5 × 6.3 mm', power: '2.7–3.6 V, up to 40 mA', buses: 'UART', price: '9.80 each at 250', mass: '1.8 g', read: '2026-11-11' };
  for (const [k, v] of Object.entries(want)) if (sp[k] !== v) return k + ': ' + JSON.stringify(sp[k]);
  if (FG.specsOf({ _context: { parts: [GNSS_REC] } }, 'nope') !== null) return 'an unknown ref should give null';
  const ph = FG.specsOf({ _context: { parts: [{ id: 'p', geometry: { envelope: [10, 10, 5], fidelity: 'placeholder' } }] } }, 'p');
  if (ph.size !== '10 × 10 × 5 mm (placeholder size)') return 'a placeholder size must say so: ' + ph.size;
  const bare = FG.specsOf({ _context: { parts: [{ id: 'x', name: 'X' }] } }, 'x');
  return bare && bare.price === '' && bare.mpn === '' ? true : 'missing values must be empty, never invented: ' + JSON.stringify(bare);
});

await check('the_parts_list_and_the_3d_hover_show_a_components_specs', async () => {
  const { m, quick } = mountGraph({ cfg: { form: SPECFORM, _context: { parts: [GNSS_REC] } } }, SPECFORM);
  await m.render();
  const { withClass } = graphDom();
  const lines = withClass(quick, 'fg-part-spec');
  if (lines.length !== 1) return 'spec lines ' + lines.length;
  const text = String(lines[0].textContent || '');
  if (!/SAM-M10Q/.test(text) || !/9\.80 each at 250/.test(text)) return 'spec line ' + text.slice(0, 300);
  if (!m.partInfo) return 'no partInfo';
  const info = m.partInfo('gnss');
  if (!info || info.title !== 'GNSS' || !info.lines.some((l) => /u-blox SAM-M10Q/.test(l))) return 'hover ' + JSON.stringify(info);
  const made = m.partInfo('base');
  return made && made.title === 'Base' ? true : 'a made part also has hover info: ' + JSON.stringify(made);
});

// One next step (MetaForge, 2026-11-11): the single most important thing for the person to
// decide, in plain words, with buttons; never a list of gates.
await check('the_next_step_is_the_most_important_open_decision_with_buttons', () => {
  const base = {
    assumptions: [{ id: 'a-size', text: 'Case about 40 mm across' }, { id: 'a-ip', text: 'No water resistance' }],
    confirmed: { 'a-size': true },
    ledger: [{ text: 'fitness smart watch', status: 'mapped' }, { text: 'see-through case' }],
    ledgerState: {},
    missingParts: [{ fn: 'e_hr', name: 'Heart-rate sensor' }],
    proposed: [{ part: 'case', name: 'Watch case', line: 'FDM printing · PLA · Sanded' }],
  };
  let n = FG.nextStep(base);
  if (n.kind !== 'assumption' || n.id !== 'a-ip' || !/No water resistance/.test(n.detail)) return 'first: ' + JSON.stringify(n);
  if (n.remaining !== 4) return 'remaining ' + n.remaining;
  if (!n.actions.some((a) => a.id === 'confirm') || !n.actions.some((a) => a.ask)) return 'assumption buttons ' + JSON.stringify(n.actions);
  n = FG.nextStep({ ...base, confirmed: { 'a-size': true, 'a-ip': true } });
  if (n.kind !== 'ledger' || n.index !== 1 || !n.actions.some((a) => a.id === 'mapped')) return 'second: ' + JSON.stringify(n);
  n = FG.nextStep({ ...base, confirmed: { 'a-size': true, 'a-ip': true }, ledgerState: { l1: 'custom' } });
  if (n.kind !== 'part' || !/Heart-rate sensor/.test(n.title) || !/Heart-rate sensor/.test(n.actions[0].ask)) return 'third: ' + JSON.stringify(n);
  n = FG.nextStep({ ...base, confirmed: { 'a-size': true, 'a-ip': true }, ledgerState: { l1: 'custom' }, missingParts: [] });
  if (n.kind !== 'make' || !/Watch case/.test(n.title) || !/PLA/.test(n.detail)) return 'fourth: ' + JSON.stringify(n);
  n = FG.nextStep({ assumptions: [], confirmed: {}, ledger: [], ledgerState: {}, missingParts: [], proposed: [] });
  return n.kind === 'done' && n.remaining === 0 && n.actions.length === 0 ? true : 'done: ' + JSON.stringify(n);
});

// Running cost (MetaForge, 2026-11-11): an estimate per unit from each part's material and the
// components' recorded prices, every line with its basis, and what is not in it said plainly.
await check('the_running_cost_adds_materials_and_recorded_prices_and_says_what_it_leaves_out', () => {
  const parts = [
    { id: 'case', name: 'Watch case', kind: 'generated', volume: 10000 },
    { id: 'bezel', name: 'Bezel', kind: 'generated', volume: 2000 },
    { id: 'gnss', name: 'GNSS', kind: 'component', volume: 1500 },
    { id: 'ble', name: 'BLE module', kind: 'component', volume: 300 },
    { id: 'lcd', name: 'Display', kind: 'component', volume: 500 },
  ];
  const look = { case: { material: 'PLA', density: 1.24, costKg: 20 } };
  const product = { name: 'PETG', density: 1.27, costKg: 22 };
  const specs = { gnss: { priceUnit: 9.8, currency: 'GBP', price: '9.80 each at 250', priceSource: 'distributor' }, ble: { priceUnit: 6.5, currency: '', price: '6.50 each' }, lcd: { priceUnit: null } };
  const e = FG.estimateCost(parts, (id) => look[id] || null, (id) => specs[id] || null, product);
  // case: 10 cm3 x 1.24 = 12.4 g x £20/kg = £0.248; bezel (product PETG): 2 cm3 x 1.27 = 2.54 g x £22/kg = £0.0559; gnss £9.80
  if (Math.abs(e.total - (0.248 + 0.05588 + 9.8)) > 1e-6) return 'total ' + e.total;
  const caseLine = e.lines.find((l) => l.name === 'Watch case');
  if (!caseLine || !/12 g of PLA at £20\/kg/.test(caseLine.basis)) return 'case basis ' + JSON.stringify(caseLine);
  if (!e.lines.some((l) => l.name === 'GNSS' && Math.abs(l.amount - 9.8) < 1e-9)) return 'gnss line';
  const ble = e.missing.find((m) => m.name === 'BLE module'), lcd = e.missing.find((m) => m.name === 'Display');
  if (!ble || !/currency/.test(ble.why) || !lcd || !/no recorded price/.test(lcd.why)) return 'missing ' + JSON.stringify(e.missing);
  if (!/not included/.test(e.note) || !/solid/.test(e.note)) return 'note ' + e.note;
  const none = FG.estimateCost([{ id: 'x', name: 'X', kind: 'generated', volume: 1000 }], () => null, () => null, null);
  return none.total === 0 && none.lines.length === 0 && none.missing.length === 1 ? true : 'nothing known: ' + JSON.stringify(none);
});

console.log(results.map((r) => JSON.stringify(r)).join('\n'));
process.exit(0);
