/* Agent-authored form runtime (spec 23.6, IMG-FORM-002, 003, 006, 007).

   A product form can be "authored": a list of parts, each carrying the body of one
   function, build(p, g). p holds the declared parameters (form.params); g is the
   shape builder below. The function returns a list of shapes, which the page draws.
   A parameter change runs the functions again. No model is involved after the code
   is written.

   The code is an agent's, so it never runs on the page. Each part runs in its own
   throwaway Web Worker that is built from text:

     1. the harness removes the network, storage, timers and messaging from the
        worker's scope;
     2. only then is the agent's code placed in the source, as the body of a function
        (never passed to eval, so the page's CSP, which forbids eval, is unchanged);
     3. the page terminates the worker after a time limit, and never trusts what it
        sends back: sanitize() checks every number, index and count.

   One worker per part means a syntax error, a crash or an endless loop belongs to
   one named part and does not stop the others.

   The module also runs under node, which is how the tests exercise the real worker
   source. In the page it is loaded before engine.js and exposes window.FGAuthored. */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.FGAuthored = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // The page (or node) global; the wrapper's own `root` is not visible in here.
  var GLOBAL = typeof globalThis !== 'undefined' ? globalThis : window;

  var LIMITS = {
    timeMs: 3000,
    startMs: 20000,
    maxPartTriangles: 20000,
    maxTotalTriangles: 100000,
    maxCoord: 100000,
  };

  // Removed from the worker's scope before any agent code is placed in its source.
  var HARDEN_NAMES = [
    'fetch', 'XMLHttpRequest', 'WebSocket', 'EventSource', 'importScripts', 'indexedDB',
    'caches', 'navigator', 'postMessage', 'eval', 'setTimeout', 'setInterval', 'queueMicrotask',
    'BroadcastChannel', 'SharedWorker', 'Worker', 'requestAnimationFrame', 'close',
  ];

  /* ---- worker side. These two functions are turned into text with toString(), so
          they must not use anything from outside themselves. ---- */

  function harden(names) {
    for (var i = 0; i < names.length; i++) {
      var n = names[i];
      try {
        Object.defineProperty(globalThis, n, { value: undefined, writable: false, configurable: false });
      } catch (e) {
        try { globalThis[n] = undefined; } catch (e2) { /* not removable here */ }
      }
    }
  }

  function workerMain(PARTS, post, listen) {
    'use strict';
    var made = new WeakSet();

    function fail(msg) { throw new Error(msg); }
    function isNum(v) { return typeof v === 'number' && isFinite(v); }
    function num(v, what, fn) { if (!isNum(v)) fail('g.' + fn + ': ' + what + ' must be a number'); return v; }
    function pos(v, what, fn) { num(v, what, fn); if (v <= 0) fail('g.' + fn + ': ' + what + ' must be a positive number'); return v; }
    function nonneg(v, what, fn) { num(v, what, fn); if (v < 0) fail('g.' + fn + ': ' + what + ' must not be negative'); return v; }
    function segs(v, def, fn, lo) {
      if (v === undefined) return def;
      if (!isNum(v) || Math.floor(v) !== v || v < (lo || 3) || v > 96) fail('g.' + fn + ': segments must be a whole number from ' + (lo || 3) + ' to 96');
      return v;
    }

    var state = { tris: 0, max: 20000 };
    function charge(n, fn) {
      state.tris += n;
      if (state.tris > state.max) fail('g.' + fn + ': this part would make more than ' + state.max + ' triangles; use fewer segments or fewer shapes');
    }

    function volume(pos, idx) {
      var v = 0;
      for (var i = 0; i < idx.length; i += 3) {
        var a = idx[i] * 3, b = idx[i + 1] * 3, c = idx[i + 2] * 3;
        v += (pos[a] * (pos[b + 1] * pos[c + 2] - pos[b + 2] * pos[c + 1])
            - pos[a + 1] * (pos[b] * pos[c + 2] - pos[b + 2] * pos[c])
            + pos[a + 2] * (pos[b] * pos[c + 1] - pos[b + 1] * pos[c])) / 6;
      }
      return v;
    }

    function shape(posn, idx) {
      var s = { pos: posn, idx: idx, label: '' };
      s.at = function (x, y, z) {
        num(x, 'x', 'at'); num(y, 'y', 'at'); num(z, 'z', 'at');
        for (var i = 0; i < s.pos.length; i += 3) { s.pos[i] += x; s.pos[i + 1] += y; s.pos[i + 2] += z; }
        return s;
      };
      s.scale = function (x, y, z) {
        if (y === undefined && z === undefined) { y = x; z = x; }
        num(x, 'x', 'scale'); num(y, 'y', 'scale'); num(z, 'z', 'scale');
        for (var i = 0; i < s.pos.length; i += 3) { s.pos[i] *= x; s.pos[i + 1] *= y; s.pos[i + 2] *= z; }
        if (x * y * z < 0) { for (var j = 0; j < s.idx.length; j += 3) { var t = s.idx[j + 1]; s.idx[j + 1] = s.idx[j + 2]; s.idx[j + 2] = t; } }
        return s;
      };
      s.rot = function (rx, ry, rz) {
        num(rx, 'x', 'rot'); num(ry, 'y', 'rot'); num(rz, 'z', 'rot');
        var D = Math.PI / 180, cx = Math.cos(rx * D), sx = Math.sin(rx * D), cy = Math.cos(ry * D), sy = Math.sin(ry * D), cz = Math.cos(rz * D), sz = Math.sin(rz * D);
        for (var i = 0; i < s.pos.length; i += 3) {
          var x = s.pos[i], y = s.pos[i + 1], z = s.pos[i + 2], t;
          t = y * cx - z * sx; z = y * sx + z * cx; y = t;            // about x
          t = x * cy + z * sy; z = -x * sy + z * cy; x = t;           // about y
          t = x * cz - y * sz; y = x * sz + y * cz; x = t;            // about z
          s.pos[i] = x; s.pos[i + 1] = y; s.pos[i + 2] = z;
        }
        return s;
      };
      s.name = function (n) { s.label = String(n).slice(0, 60); return s; };
      made.add(s);
      return s;
    }

    // Faces the same way as the rest of the shape if the volume is negative.
    function orient(posn, idx) {
      if (volume(posn, idx) < 0) { for (var i = 0; i < idx.length; i += 3) { var t = idx[i + 1]; idx[i + 1] = idx[i + 2]; idx[i + 2] = t; } }
    }

    // A surface of revolution about Y. profile is [[radius, y], ...]. An open profile is
    // closed with a flat cap at each end that has a radius; a closed one wraps round.
    function revolve(profile, seg, closed, fn) {
      var m = profile.length, P = [], I = [], k, i;
      var ringTris = (closed ? m : m - 1) * seg * 2;
      charge(ringTris + (closed ? 0 : 2 * seg), fn);
      for (k = 0; k < m; k++) {
        for (i = 0; i < seg; i++) {
          var a = (2 * Math.PI * i) / seg, r = profile[k][0];
          P.push(r * Math.cos(a), profile[k][1], r * Math.sin(a));
        }
      }
      var rows = closed ? m : m - 1;
      for (k = 0; k < rows; k++) {
        var b = k * seg, t = ((k + 1) % m) * seg;
        for (i = 0; i < seg; i++) {
          var i2 = (i + 1) % seg;
          I.push(b + i, t + i, b + i2, b + i2, t + i, t + i2);
        }
      }
      if (!closed) {
        [[0, false], [m - 1, true]].forEach(function (e) {
          var ring = e[0] * seg, r = profile[e[0]][0];
          if (r <= 1e-9) return;
          var c = P.length / 3;
          P.push(0, profile[e[0]][1], 0);
          var base = P.length / 3;
          for (var j = 0; j < seg; j++) {
            var aa = (2 * Math.PI * j) / seg;
            P.push(r * Math.cos(aa), profile[e[0]][1], r * Math.sin(aa));
          }
          for (var q = 0; q < seg; q++) {
            var q2 = (q + 1) % seg;
            if (e[1]) I.push(c, base + q2, base + q); else I.push(c, base + q, base + q2);
          }
          void ring;
        });
      }
      orient(P, I);
      return shape(P, I);
    }

    function earClip(pts, fn) {
      var n = pts.length, idx = [], i, area = 0;
      for (i = 0; i < n; i++) idx.push(i);
      for (i = 0; i < n; i++) { var j = (i + 1) % n; area += pts[i][0] * pts[j][1] - pts[j][0] * pts[i][1]; }
      if (area === 0) fail('g.' + fn + ': the outline has no area');
      if (area < 0) idx.reverse();
      function cross(a, b, c) { return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]); }
      function inside(p, a, b, c) { return cross(a, b, p) >= 0 && cross(b, c, p) >= 0 && cross(c, a, p) >= 0; }
      var out = [], guard = 0;
      while (idx.length > 3 && guard++ < n * n) {
        var clipped = false;
        for (var k = 0; k < idx.length; k++) {
          var a = idx[(k + idx.length - 1) % idx.length], b = idx[k], c = idx[(k + 1) % idx.length];
          if (cross(pts[a], pts[b], pts[c]) <= 1e-12) continue;
          var hit = false;
          for (var q = 0; q < idx.length; q++) {
            var v = idx[q];
            if (v === a || v === b || v === c) continue;
            if (inside(pts[v], pts[a], pts[b], pts[c])) { hit = true; break; }
          }
          if (hit) continue;
          out.push([a, b, c]); idx.splice(k, 1); clipped = true; break;
        }
        if (!clipped) fail('g.' + fn + ': the outline crosses itself or repeats a point');
      }
      out.push([idx[0], idx[1], idx[2]]);
      return { tris: out, ccw: area > 0 };
    }

    var g = {
      box: function (w, h, d) {
        pos(w, 'width', 'box'); pos(h, 'height', 'box'); pos(d, 'depth', 'box'); charge(12, 'box');
        var x = w / 2, y = h / 2, z = d / 2, P = [], I = [];
        var faces = [
          [[x, -y, -z], [x, y, -z], [x, y, z], [x, -y, z]], [[-x, -y, z], [-x, y, z], [-x, y, -z], [-x, -y, -z]],
          [[-x, y, -z], [-x, y, z], [x, y, z], [x, y, -z]], [[-x, -y, z], [-x, -y, -z], [x, -y, -z], [x, -y, z]],
          [[-x, -y, z], [x, -y, z], [x, y, z], [-x, y, z]], [[x, -y, -z], [-x, -y, -z], [-x, y, -z], [x, y, -z]],
        ];
        faces.forEach(function (f) {
          var o = P.length / 3;
          f.forEach(function (v) { P.push(v[0], v[1], v[2]); });
          I.push(o, o + 1, o + 2, o, o + 2, o + 3);
        });
        orient(P, I);
        return shape(P, I);
      },
      frustum: function (r0, r1, h, seg) {
        nonneg(r0, 'bottom radius', 'frustum'); nonneg(r1, 'top radius', 'frustum'); pos(h, 'height', 'frustum');
        if (r0 === 0 && r1 === 0) fail('g.frustum: at least one radius must be positive');
        seg = segs(seg, 24, 'frustum');
        return revolve([[r0, -h / 2], [r1, h / 2]], seg, false, 'frustum');
      },
      cylinder: function (r, h, seg) {
        pos(r, 'radius', 'cylinder'); pos(h, 'height', 'cylinder');
        seg = segs(seg, 24, 'cylinder');
        return revolve([[r, -h / 2], [r, h / 2]], seg, false, 'cylinder');
      },
      cone: function (r, h, seg) {
        pos(r, 'radius', 'cone'); pos(h, 'height', 'cone');
        seg = segs(seg, 24, 'cone');
        return revolve([[r, -h / 2], [0, h / 2]], seg, false, 'cone');
      },
      sphere: function (r, seg) {
        pos(r, 'radius', 'sphere');
        seg = segs(seg, 16, 'sphere');
        var lat = Math.max(2, Math.ceil(seg / 2)), prof = [], k;
        for (k = 0; k <= lat; k++) {
          var phi = -Math.PI / 2 + (Math.PI * k) / lat;
          prof.push([k === 0 || k === lat ? 0 : r * Math.cos(phi), r * Math.sin(phi)]);
        }
        return revolve(prof, seg, false, 'sphere');
      },
      torus: function (R, r, segR, segr) {
        pos(R, 'major radius', 'torus'); pos(r, 'minor radius', 'torus');
        if (r >= R) fail('g.torus: the minor radius must be smaller than the major radius');
        segR = segs(segR, 24, 'torus'); segr = segs(segr, 12, 'torus');
        var prof = [];
        for (var k = 0; k < segr; k++) {
          var phi = (2 * Math.PI * k) / segr;
          prof.push([R + r * Math.cos(phi), r * Math.sin(phi)]);
        }
        return revolve(prof, segR, true, 'torus');
      },
      lathe: function (profile, seg) {
        if (!Array.isArray(profile) || profile.length < 2 || profile.length > 256) fail('g.lathe: profile must be a list of 2 to 256 [radius, y] points');
        profile.forEach(function (p, i) {
          if (!Array.isArray(p) || p.length !== 2 || !isNum(p[0]) || !isNum(p[1])) fail('g.lathe: profile point ' + i + ' must be [radius, y]');
          if (p[0] < 0) fail('g.lathe: profile point ' + i + ' has a negative radius');
        });
        seg = segs(seg, 24, 'lathe');
        return revolve(profile.map(function (p) { return [p[0], p[1]]; }), seg, false, 'lathe');
      },
      extrude: function (outline, depth) {
        if (!Array.isArray(outline) || outline.length < 3 || outline.length > 256) fail('g.extrude: outline must be a list of 3 to 256 [x, z] points');
        outline.forEach(function (p, i) {
          if (!Array.isArray(p) || p.length !== 2 || !isNum(p[0]) || !isNum(p[1])) fail('g.extrude: outline point ' + i + ' must be [x, z]');
        });
        pos(depth, 'depth', 'extrude');
        var pts = outline.map(function (p) { return [p[0], p[1]]; }), n = pts.length;
        charge(2 * (n - 2) + 2 * n, 'extrude');
        var ec = earClip(pts, 'extrude'), P = [], I = [], y = depth / 2, i, o;
        var order = ec.ccw ? pts.map(function (_, i) { return i; }) : pts.map(function (_, i) { return n - 1 - i; });
        // caps, each with its own vertices so the edge stays sharp
        o = P.length / 3; pts.forEach(function (p) { P.push(p[0], y, p[1]); });
        ec.tris.forEach(function (t) { I.push(o + t[0], o + t[2], o + t[1]); });
        o = P.length / 3; pts.forEach(function (p) { P.push(p[0], -y, p[1]); });
        ec.tris.forEach(function (t) { I.push(o + t[0], o + t[1], o + t[2]); });
        for (i = 0; i < n; i++) {
          var a = pts[order[i]], b = pts[order[(i + 1) % n]], q = P.length / 3;
          P.push(a[0], -y, a[1], b[0], -y, b[1], a[0], y, a[1], b[0], y, b[1]);
          I.push(q, q + 2, q + 1, q + 1, q + 2, q + 3);
        }
        orient(P, I);
        return shape(P, I);
      },
      tube: function (path, r, seg) {
        if (!Array.isArray(path) || path.length < 2 || path.length > 128) fail('g.tube: path must be a list of 2 to 128 [x, y, z] points');
        path.forEach(function (p, i) { if (!Array.isArray(p) || p.length !== 3 || !isNum(p[0]) || !isNum(p[1]) || !isNum(p[2])) fail('g.tube: path point ' + i + ' must be [x, y, z]'); });
        pos(r, 'radius', 'tube'); seg = segs(seg, 8, 'tube');
        var m = path.length;
        charge((m - 1) * seg * 2 + 2 * seg, 'tube');
        function sub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
        function nrm(v) { var l = Math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]); if (l < 1e-9) fail('g.tube: two path points are the same'); return [v[0] / l, v[1] / l, v[2] / l]; }
        function crs(a, b) { return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]; }
        var tang = [], k;
        for (k = 0; k < m; k++) {
          var d0 = k > 0 ? nrm(sub(path[k], path[k - 1])) : null, d1 = k < m - 1 ? nrm(sub(path[k + 1], path[k])) : null;
          tang.push(d0 && d1 ? nrm([d0[0] + d1[0], d0[1] + d1[1], d0[2] + d1[2]]) : d0 || d1);
        }
        var up = Math.abs(tang[0][1]) < 0.9 ? [0, 1, 0] : [1, 0, 0];
        var nv = nrm(crs(crs(tang[0], up), tang[0])), P = [], I = [], i;
        var frames = [];
        for (k = 0; k < m; k++) {
          var tk = tang[k], dot = nv[0] * tk[0] + nv[1] * tk[1] + nv[2] * tk[2];
          nv = nrm([nv[0] - dot * tk[0], nv[1] - dot * tk[1], nv[2] - dot * tk[2]]);
          frames.push([nv, crs(nv, tk)]);
          for (i = 0; i < seg; i++) {
            var a = (2 * Math.PI * i) / seg, c = Math.cos(a) * r, s = Math.sin(a) * r;
            P.push(path[k][0] + c * nv[0] + s * frames[k][1][0], path[k][1] + c * nv[1] + s * frames[k][1][1], path[k][2] + c * nv[2] + s * frames[k][1][2]);
          }
        }
        for (k = 0; k < m - 1; k++) {
          var b = k * seg, t = (k + 1) * seg;
          for (i = 0; i < seg; i++) { var i2 = (i + 1) % seg; I.push(b + i, t + i, b + i2, b + i2, t + i, t + i2); }
        }
        [0, m - 1].forEach(function (e, which) {
          var c = P.length / 3;
          P.push(path[e][0], path[e][1], path[e][2]);
          var base = P.length / 3;
          for (var j = 0; j < seg; j++) {
            var aa = (2 * Math.PI * j) / seg, cc = Math.cos(aa) * r, ss = Math.sin(aa) * r;
            P.push(path[e][0] + cc * frames[e][0][0] + ss * frames[e][1][0], path[e][1] + cc * frames[e][0][1] + ss * frames[e][1][1], path[e][2] + cc * frames[e][0][2] + ss * frames[e][1][2]);
          }
          for (var q = 0; q < seg; q++) { var q2 = (q + 1) % seg; if (which) I.push(c, base + q2, base + q); else I.push(c, base + q, base + q2); }
        });
        orient(P, I);
        return shape(P, I);
      },
    };
    Object.freeze(g);

    // Smooth where faces meet gently, sharp where they meet at an edge.
    function creased(posn, idx) {
      var tri = idx.length / 3, nx = new Array(tri), ny = new Array(tri), nz = new Array(tri), key = {}, t, c;
      function k3(v) { return Math.round(posn[v * 3] * 1e4) + ',' + Math.round(posn[v * 3 + 1] * 1e4) + ',' + Math.round(posn[v * 3 + 2] * 1e4); }
      for (t = 0; t < tri; t++) {
        var a = idx[t * 3] * 3, b = idx[t * 3 + 1] * 3, d = idx[t * 3 + 2] * 3;
        var ux = posn[b] - posn[a], uy = posn[b + 1] - posn[a + 1], uz = posn[b + 2] - posn[a + 2];
        var vx = posn[d] - posn[a], vy = posn[d + 1] - posn[a + 1], vz = posn[d + 2] - posn[a + 2];
        var x = uy * vz - uz * vy, y = uz * vx - ux * vz, z = ux * vy - uy * vx;
        nx[t] = x; ny[t] = y; nz[t] = z;     // area weighted, not unit
        for (c = 0; c < 3; c++) { var kk = k3(idx[t * 3 + c]); (key[kk] || (key[kk] = [])).push(t); }
      }
      var P = [], N = [], I = [], cosLimit = Math.cos(50 * Math.PI / 180);
      for (t = 0; t < tri; t++) {
        var len = Math.sqrt(nx[t] * nx[t] + ny[t] * ny[t] + nz[t] * nz[t]);
        if (len < 1e-12) continue;           // a degenerate triangle draws nothing
        var ux2 = nx[t] / len, uy2 = ny[t] / len, uz2 = nz[t] / len;
        for (c = 0; c < 3; c++) {
          var v = idx[t * 3 + c], sx = 0, sy = 0, sz = 0, near = key[k3(v)];
          for (var q = 0; q < near.length; q++) {
            var u = near[q], l2 = Math.sqrt(nx[u] * nx[u] + ny[u] * ny[u] + nz[u] * nz[u]);
            if (l2 < 1e-12) continue;
            if ((nx[u] * ux2 + ny[u] * uy2 + nz[u] * uz2) / l2 >= cosLimit) { sx += nx[u]; sy += ny[u]; sz += nz[u]; }
          }
          var sl = Math.sqrt(sx * sx + sy * sy + sz * sz) || 1;
          I.push(P.length / 3);
          P.push(posn[v * 3], posn[v * 3 + 1], posn[v * 3 + 2]);
          N.push(sx / sl, sy / sl, sz / sl);
        }
      }
      return { positions: P, normals: N, indices: I };
    }

    listen(function (msg) {
      // Tell the page the code is about to run, so the page times the code and not its own busy thread.
      post({ started: true });
      try {
        var fn = PARTS[msg.id];
        if (typeof fn !== 'function') fail('there is no code for part ' + msg.id);
        state.max = msg.maxTriangles;
        var p = Object.freeze(Object.assign({}, msg.params));
        var out = fn(p, g);
        if (!Array.isArray(out)) fail('build must return a list of shapes, for example: return [g.box(10, 10, 10)];');
        var shapes = out.map(function (s) {
          if (!s || !made.has(s)) fail('build must return only shapes made with g, such as g.box or g.cylinder');
          var c = creased(s.pos, s.idx);
          return { name: s.label, positions: c.positions, normals: c.normals, indices: c.indices };
        });
        post({ ok: true, shapes: shapes });
      } catch (e) {
        post({ ok: false, error: String((e && e.message) || e).slice(0, 300) });
      }
    });
  }

  /* ---- page side ---- */

  var BROWSER_PRE =
    'const __post = globalThis.postMessage.bind(globalThis);' +
    'const __listen = function (f) { globalThis.onmessage = function (e) { f(e.data); }; };';
  var BROWSER_POST = '__main(__PARTS, __post, __listen);';

  // The worker's source: the sandbox first, the agent's code last.
  function workerSource(part, pre, post) {
    return (
      "'use strict';\n" + (pre === undefined ? BROWSER_PRE : pre) + '\n' +
      '(' + harden.toString() + ')(' + JSON.stringify(HARDEN_NAMES) + ');\n' +
      'const __main = ' + workerMain.toString() + ';\n' +
      'const __PARTS = { ' + JSON.stringify(String(part.id)) + ': function (p, g) { "use strict";\n' +
      String(part.code) + '\n} };\n' +
      (post === undefined ? BROWSER_POST : post)
    );
  }

  function browserWorker(source) {
    var url = URL.createObjectURL(new Blob([source], { type: 'text/javascript' }));
    var w = new Worker(url);
    var handle = {
      postMessage: function (m) { w.postMessage(m); },
      terminate: function () { w.terminate(); URL.revokeObjectURL(url); },
      onmessage: null,
      onerror: null,
    };
    w.onmessage = function (e) { if (handle.onmessage) handle.onmessage(e); };
    w.onerror = function (e) { if (e.preventDefault) e.preventDefault(); if (handle.onerror) handle.onerror({ message: e.message || 'the code could not run' }); };
    return handle;
  }

  // What a worker sent back is data from an untrusted program. Check all of it.
  function sanitize(shapes, lim) {
    var maxTri = (lim && lim.maxTriangles) || LIMITS.maxPartTriangles, maxCoord = LIMITS.maxCoord;
    if (!Array.isArray(shapes)) return { error: 'the part returned no shapes' };
    var out = [], tris = 0;
    for (var s = 0; s < shapes.length; s++) {
      var sh = shapes[s];
      if (!sh || typeof sh !== 'object') return { error: 'shape ' + s + ' is not a shape' };
      var P = sh.positions, I = sh.indices, N = sh.normals;
      if (!Array.isArray(P) || !Array.isArray(I)) return { error: 'shape ' + s + ' has no geometry' };
      if (P.length % 3 || I.length % 3 || P.length === 0 || I.length === 0) return { error: 'shape ' + s + ' has an incomplete triangle list' };
      if (N !== undefined && (!Array.isArray(N) || N.length !== P.length)) return { error: 'shape ' + s + ' has normals that do not match its points' };
      tris += I.length / 3;
      if (tris > maxTri) return { error: 'the part makes ' + tris + ' triangles; at most ' + maxTri + ' are allowed' };
      var i, np = P.length / 3;
      for (i = 0; i < P.length; i++) {
        if (typeof P[i] !== 'number' || !isFinite(P[i]) || Math.abs(P[i]) > maxCoord) return { error: 'shape ' + s + ' has a point that is not a usable number' };
      }
      if (N) for (i = 0; i < N.length; i++) if (typeof N[i] !== 'number' || !isFinite(N[i])) return { error: 'shape ' + s + ' has a bad normal' };
      for (i = 0; i < I.length; i++) {
        if (!(Number.isInteger(I[i]) && I[i] >= 0 && I[i] < np)) return { error: 'shape ' + s + ' points at a vertex that does not exist' };
      }
      out.push({ name: typeof sh.name === 'string' ? sh.name.slice(0, 60) : '', positions: P, normals: N, indices: I });
    }
    return { shapes: out, triangles: tris };
  }

  function measure(shapes) {
    var min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity], vol = 0;
    shapes.forEach(function (sh) {
      var P = sh.positions, I = sh.indices, i;
      for (i = 0; i < P.length; i += 3) for (var a = 0; a < 3; a++) { if (P[i + a] < min[a]) min[a] = P[i + a]; if (P[i + a] > max[a]) max[a] = P[i + a]; }
      for (i = 0; i < I.length; i += 3) {
        var p = I[i] * 3, q = I[i + 1] * 3, r = I[i + 2] * 3;
        vol += (P[p] * (P[q + 1] * P[r + 2] - P[q + 2] * P[r + 1]) - P[p + 1] * (P[q] * P[r + 2] - P[q + 2] * P[r]) + P[p + 2] * (P[q] * P[r + 1] - P[q + 1] * P[r])) / 6;
      }
    });
    return { bbox: { min: min, max: max }, volume: vol };
  }

  function runPart(part, params, o) {
    return new Promise(function (resolve) {
      var done = false, w = null, timer = null;
      var limit = Math.min(Number.isInteger(part.max_triangles) ? part.max_triangles : o.maxPartTriangles, o.maxPartTriangles);
      function finish(extra) {
        if (done) return;
        done = true;
        if (timer) clearTimeout(timer);
        try { if (w) w.terminate(); } catch (e) { /* already gone */ }
        resolve(Object.assign({ id: part.id, name: part.name, kind: part.kind, body: !!part.body, triangles: 0 }, extra));
      }
      try {
        w = o.makeWorker(workerSource(part, o.pre, o.post));
      } catch (e) {
        return finish({ error: 'the sandbox could not start: ' + String((e && e.message) || e).slice(0, 200) });
      }
      // Two limits. Until the worker reports that it started, the page may simply be busy (it
      // loads and solves on the same thread), so that wait is long. Once the code is running,
      // the limit is on the code.
      timer = setTimeout(function () { finish({ error: 'the sandbox did not start within ' + o.startMs + ' ms' }); }, o.startMs);
      w.onmessage = function (e) {
        var m = e.data;
        if (m && m.started === true) {
          if (timer) clearTimeout(timer);
          timer = setTimeout(function () { finish({ error: 'timed out after ' + o.timeMs + ' ms: does it loop forever?' }); }, o.timeMs);
          return;
        }
        if (!m || m.ok !== true) return finish({ error: String((m && m.error) || 'the code failed').slice(0, 300) });
        var s = sanitize(m.shapes, { maxTriangles: limit });
        if (s.error) return finish({ error: s.error });
        var me = measure(s.shapes);
        finish({ shapes: s.shapes, triangles: s.triangles, bbox: me.bbox, volume: me.volume });
      };
      w.onerror = function (e) { finish({ error: 'the code did not run: ' + String((e && e.message) || 'error').slice(0, 300) }); };
      w.postMessage({ id: String(part.id), params: params, maxTriangles: limit });
    });
  }

  // Runs every part, each in its own worker, and gathers the results.
  function run(parts, params, opts) {
    var o = Object.assign({ makeWorker: browserWorker, timeMs: LIMITS.timeMs, startMs: LIMITS.startMs, maxPartTriangles: LIMITS.maxPartTriangles, maxTotalTriangles: LIMITS.maxTotalTriangles }, opts || {});
    return Promise.all(parts.map(function (p) { return runPart(p, params, o); })).then(function (results) {
      var total = 0, min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity], errors = [];
      results.forEach(function (r) {
        if (!r.error) {
          total += r.triangles;
          if (total > o.maxTotalTriangles) { r.error = 'the form as a whole is over ' + o.maxTotalTriangles + ' triangles'; r.shapes = []; r.triangles = 0; return; }
          for (var a = 0; a < 3; a++) { if (r.bbox.min[a] < min[a]) min[a] = r.bbox.min[a]; if (r.bbox.max[a] > max[a]) max[a] = r.bbox.max[a]; }
        }
        if (r.error) errors.push(r.name + ' (' + r.id + '): ' + r.error);
      });
      var any = isFinite(min[0]);
      return { parts: results, triangles: total, bbox: any ? { min: min, max: max } : null, errors: errors };
    });
  }

  /* ---- the page: draw it and offer its parameters ---- */

  var KIND_STYLE = {
    // Confidence per part (IMG-FORM-007): a bought part, a declared primitive and
    // generated geometry must not look alike.
    generated: { color: 0xb86a1f, label: 'Generated geometry' },
    primitive: { color: 0x2f64b8, label: 'Declared primitive' },
    component: { color: 0x2b3036, label: 'Bought component' },
  };

  function mount(env) {
    var THREE = env.THREE, doc = env.document || document, form = env.cfg.form, parts = form.authored.parts;
    var params = Object.assign({}, form.params), ranges = form.ranges || {};
    var group = new THREE.Group();
    group.name = 'authored-form';
    env.scene.add(group);

    var panel = doc.createElement('div');
    panel.className = 'fg-authored-panel';
    panel.setAttribute('data-fg-authored', 'panel');
    var title = doc.createElement('div'); title.className = 'fg-authored-title'; title.textContent = 'Form parameters'; panel.appendChild(title);
    var rows = doc.createElement('div'); panel.appendChild(rows);
    Object.keys(params).forEach(function (key) {
      var v = +params[key], r = ranges[key], lo = r ? +r[0] : Math.min(v * 0.25, v - 1), hi = r ? +r[1] : Math.max(v * 2, v + 1);
      if (v < lo) lo = v; if (v > hi) hi = v;
      var row = doc.createElement('label'); row.className = 'fg-authored-row';
      var name = doc.createElement('span'); name.textContent = key;
      var val = doc.createElement('span'); val.className = 'fg-authored-val'; val.textContent = String(v);
      var input = doc.createElement('input'); input.type = 'range'; input.min = String(lo); input.max = String(hi); input.step = String((hi - lo) / 200); input.value = String(v);
      input.setAttribute('data-param', key);
      input.addEventListener('input', function () { params[key] = +input.value; val.textContent = (+input.value).toFixed(1); schedule(); });
      row.appendChild(name); row.appendChild(val); row.appendChild(input); rows.appendChild(row);
    });
    var legend = doc.createElement('div'); legend.className = 'fg-authored-legend';
    Object.keys(KIND_STYLE).forEach(function (k) {
      var item = doc.createElement('span'); var sw = doc.createElement('i'); sw.style.background = '#' + KIND_STYLE[k].color.toString(16).padStart(6, '0');
      item.appendChild(sw); item.appendChild(doc.createTextNode(KIND_STYLE[k].label)); legend.appendChild(item);
    });
    panel.appendChild(legend);
    var problems = doc.createElement('div'); problems.className = 'fg-authored-errors'; panel.appendChild(problems);
    (env.host || doc.body).appendChild(panel);

    var seq = 0, timer = null, materials = {};
    function material(kind) {
      var s = KIND_STYLE[kind] || KIND_STYLE.generated;
      return materials[kind] || (materials[kind] = new THREE.MeshStandardMaterial({ color: s.color, roughness: 0.6, metalness: 0.05 }));
    }
    function clear() {
      while (group.children.length) { var m = group.children.pop(); if (m.geometry) m.geometry.dispose(); }
    }
    function publish(status, result) {
      var summary = {
        status: status,
        params: Object.assign({}, params),
        parts: result ? result.parts.map(function (p) { return { id: p.id, name: p.name, kind: p.kind, body: p.body, triangles: p.triangles, bbox: p.bbox || null, volume: p.volume || 0, error: p.error || null }; }) : [],
        bbox: result ? result.bbox : null,
        triangles: result ? result.triangles : 0,
        errors: result ? result.errors : [],
      };
      GLOBAL.FG_AUTHORED = summary;
      try { GLOBAL.dispatchEvent(new GLOBAL.CustomEvent('fg-authored', { detail: summary })); } catch (e) { /* no events here */ }
    }
    function draw(result) {
      clear();
      result.parts.forEach(function (p) {
        (p.shapes || []).forEach(function (sh) {
          var geo = new THREE.BufferGeometry();
          geo.setAttribute('position', new THREE.Float32BufferAttribute(sh.positions, 3));
          if (sh.normals) geo.setAttribute('normal', new THREE.Float32BufferAttribute(sh.normals, 3)); else geo.computeVertexNormals();
          geo.setIndex(sh.indices);
          var mesh = new THREE.Mesh(geo, material(p.kind));
          mesh.name = sh.name || p.name; mesh.castShadow = true; mesh.receiveShadow = true;
          mesh.userData = { part: p.id, kind: p.kind };
          group.add(mesh);
        });
      });
      problems.textContent = '';
      result.errors.forEach(function (e) { var d = doc.createElement('div'); d.textContent = e; problems.appendChild(d); });
      problems.style.display = result.errors.length ? 'block' : 'none';
      if (env.onDraw) env.onDraw(result);
    }
    function render() {
      var mine = ++seq;
      publish('running', null);
      return run(parts, params, env.options).then(function (result) {
        if (mine !== seq) return;
        draw(result);
        publish(result.errors.length ? 'error' : 'ready', result);
      });
    }
    function schedule() { if (timer) clearTimeout(timer); timer = setTimeout(render, 120); }
    render();
    return { render: render, group: group, panel: panel, params: params };
  }

  return { LIMITS: LIMITS, HARDEN_NAMES: HARDEN_NAMES, KIND_STYLE: KIND_STYLE, workerSource: workerSource, run: run, runPart: runPart, sanitize: sanitize, measure: measure, mount: mount };
});
