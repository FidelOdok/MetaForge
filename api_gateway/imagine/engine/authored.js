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

  /* What the page may say about an authored form. The engine's own checks, verdict, cost and
     assembly are computed from an enclosure; none of them was run on this form, so none is
     shown. What is shown is measured from the geometry, and what was not assessed is listed
     with its reason, never as a pass. */
  var NOT_ASSESSED = [
    { id: 'wall', label: 'Wall thickness, draft and moulding rules', why: 'They are computed from an enclosure shell; this form has none.' },
    { id: 'fit', label: 'Fit of the electronics inside the form', why: 'The engine places parts only inside an enclosure.' },
    { id: 'thermal', label: 'Vents, heat and radio checks', why: 'They depend on the enclosure walls and openings.' },
    { id: 'verdict', label: 'Manufacturing verdict and unit cost', why: 'Cost and process fit are priced from the enclosure.' },
    { id: 'assembly', label: 'Assembly steps and time', why: 'The assembly model follows the enclosure and its fixings.' },
    { id: 'tests', label: 'Product tests and requirements met', why: 'Their inputs come from the enclosure model.' },
    { id: 'bom', label: 'Bill of materials for the form itself', why: 'The shell line is priced from the enclosure.' },
  ];

  function fmtMm(v) { return String(Math.abs(v) >= 100 ? Math.round(v) : Math.round(v * 10) / 10); }

  function describe(summary, material) {
    var sm = summary || {}, parts = sm.parts || [], errors = sm.errors || [];
    var drawn = parts.filter(function (p) { return !p.error && p.triangles > 0; }).length;
    var d = { headline: '', dims: '', facts: [], notAssessed: NOT_ASSESSED.slice(), problems: errors.slice() };
    if (sm.status === 'running' || !sm.parts || !sm.parts.length) { d.headline = 'Drawing the form'; return d; }
    if (errors.length) { d.headline = drawn ? 'Form partly drawn' : 'Form not drawn'; return d; }
    var b = sm.bbox;
    if (!b) { d.headline = 'Form not drawn'; return d; }
    var vol = parts.reduce(function (a, p) { return a + (p.volume || 0); }, 0) / 1000;     // mm3 to cm3
    var size = fmtMm(b.max[0] - b.min[0]) + ' × ' + fmtMm(b.max[2] - b.min[2]) + ' × ' + fmtMm(b.max[1] - b.min[1]) + ' mm';
    d.headline = 'Form drawn';
    d.dims = size + ' · ' + (Math.round(vol * 10) / 10) + ' cm³';
    d.facts.push(['Envelope', size]);
    d.facts.push(['Volume of the shapes', (Math.round(vol * 10) / 10) + ' cm³, overlaps counted twice']);
    d.facts.push(['Parts', String(parts.length)]);
    d.facts.push(['Triangles', String(sm.triangles)]);
    if (material && typeof material.density === 'number') d.facts.push(['Mass if solid in ' + material.name, Math.round(vol * material.density) + ' g']);
    return d;
  }

  // The engine wraps its own sections into collapsible cards once, at start-up. These blocks are
  // added later, so each builds the same card (header, summary, body) itself.
  function makeCard(doc, id, title, summary, dotClass, open) {
    var sec = doc.createElement('section'); sec.className = open ? 'blk open' : 'blk'; sec.id = id;
    sec.setAttribute('data-acc', title);
    var head = doc.createElement('button'); head.type = 'button'; head.className = 'acc-h'; head.setAttribute('aria-expanded', String(!!open));
    var t = doc.createElement('span'); t.className = 'acc-t'; t.textContent = title;
    var sum = doc.createElement('span'); sum.className = 'acc-s';
    var dot = doc.createElement('i'); dot.className = 'sdot' + (dotClass ? ' ' + dotClass : '');
    var st = doc.createElement('span'); st.textContent = summary;
    sum.appendChild(dot); sum.appendChild(st);
    var chev = doc.createElement('span'); chev.className = 'acc-c'; chev.setAttribute('aria-hidden', 'true');
    head.appendChild(t); head.appendChild(sum); head.appendChild(chev);
    var body = doc.createElement('div'); body.className = 'acc-b';
    head.addEventListener('click', function () {
      var now = !sec.classList.contains('open');
      sec.classList.toggle('open', now); head.setAttribute('aria-expanded', String(now));
    });
    sec.appendChild(head); sec.appendChild(body);
    return { section: sec, body: body };
  }

  function renderSummary(doc, host, d) {
    if (!host) return null;
    if (typeof host.querySelector === 'function') {
      ['authBlk', 'authNa'].forEach(function (id) { var old = host.querySelector('#' + id); if (old && old.parentNode) old.parentNode.removeChild(old); });
    }
    var form = makeCard(doc, 'authBlk', d.headline,
      d.problems.length ? d.problems.length + ' problem' + (d.problems.length > 1 ? 's' : '') : (d.dims || ''),
      d.problems.length ? 's-fail' : d.dims ? 's-pass' : '', true);
    var body = form.body;
    if (d.dims) { var note = doc.createElement('p'); note.className = 'note'; note.textContent = 'The size and volume are measured from the shapes the code makes.'; body.appendChild(note); }
    if (d.facts.length) {
      var dl = doc.createElement('dl'); dl.className = 'fg-authored-facts';
      d.facts.forEach(function (f) {
        var tile = doc.createElement('div'); tile.className = 'fg-fact';
        var dt = doc.createElement('dt'); dt.textContent = f[0]; var dd = doc.createElement('dd');
        var cut = String(f[1]).indexOf(', ');   // "295 cm³, overlaps counted twice": the figure, then its caveat
        if (cut > 0) {
          dd.appendChild(doc.createTextNode(String(f[1]).slice(0, cut)));
          var small = doc.createElement('small'); small.textContent = String(f[1]).slice(cut + 2); dd.appendChild(small);
        } else dd.textContent = f[1];
        tile.appendChild(dt); tile.appendChild(dd); dl.appendChild(tile);
      });
      body.appendChild(dl);
    }
    if (d.problems.length) {
      var bad = doc.createElement('div'); bad.className = 'fg-authored-errors'; bad.style.display = 'block';
      d.problems.forEach(function (t) { var line = doc.createElement('div'); line.textContent = t; bad.appendChild(line); });
      body.appendChild(bad);
    }
    var na = makeCard(doc, 'authNa', 'Not assessed', 'authored form, ' + d.notAssessed.length + ' checks', 's-warn', false);
    var ul = doc.createElement('ul'); ul.className = 'rlist';
    d.notAssessed.forEach(function (n) {
      var li = doc.createElement('li'); li.className = 'rule st-warn';
      var chip = doc.createElement('span'); chip.className = 'chip'; chip.textContent = 'not assessed';
      var wrap = doc.createElement('div'); wrap.className = 'rt';
      var l1 = doc.createElement('div'); l1.className = 'rl'; l1.textContent = n.label;
      var l2 = doc.createElement('div'); l2.className = 'rv'; l2.textContent = n.why;
      wrap.appendChild(l1); wrap.appendChild(l2); li.appendChild(chip); li.appendChild(wrap); ul.appendChild(li);
    });
    na.body.appendChild(ul);
    // Inserted first, so the form card ends up above it.
    [na.section, form.section].forEach(function (sec) {
      if (typeof host.insertBefore === 'function') host.insertBefore(sec, host.firstChild); else host.appendChild(sec);
    });
    return form.section;
  }

  // A slider's grid. A range input snaps its value to min + n * step, so a step that does not
  // divide the declared value moves it on first touch, and a person (or an agent) asking for
  // 400 gets 400.8. Pick the largest step, at most 1, that has enough positions, lands exactly
  // on the declared value and keeps whole numbers reachable.
  function sliderGrid(v, lo, hi) {
    var steps = [1, 0.5, 0.1, 0.05, 0.01, 0.005, 0.001], i, s, a, b;
    function round(x) { return Math.round(x * 1e6) / 1e6; }
    for (i = 0; i < steps.length; i++) {
      s = steps[i];
      a = round(v - Math.floor((v - lo) / s + 1e-9) * s);
      b = round(a + Math.ceil((hi - a) / s - 1e-9) * s);
      var off = (Math.ceil(a) - a) / s;
      if ((b - a) / s >= 50 && Math.abs(off - Math.round(off)) < 1e-6) return { lo: a, hi: b, step: s };
    }
    s = steps[steps.length - 1];
    a = round(v - Math.floor((v - lo) / s + 1e-9) * s);
    return { lo: a, hi: round(a + Math.max(1, Math.ceil((hi - a) / s)) * s), step: s };
  }

  /* ---- The Engineer graph: parameters feed the parts that use them, and the parts make the form.
          buildGraph is pure: the nodes, where they sit and the wires between them. The geometry is
          fixed (every row is ROW_H tall), so a wire's end is computed, not measured, and the same
          numbers draw it in a browser and in a test. ---- */
  var NODE_W = 196, HEAD_H = 30, ROW_H = 24, FOOT_H = 22, PBODY_H = 44, GAP_Y = 16, COL_X = [24, 300, 580];

  function labelOf(key) {
    var t = String(key).replace(/_+/g, ' ').trim();
    return t.charAt(0).toUpperCase() + t.slice(1);
  }

  function buildGraph(form) {
    var names = Object.keys((form && form.params) || {});
    var parts = (form && form.authored && form.authored.parts) || [];
    var nodes = [], links = [], y = 24, bottom = 24;
    names.forEach(function (key) {
      var h = HEAD_H + PBODY_H;
      nodes.push({ id: 'param:' + key, kind: 'param', key: key, title: labelOf(key), x: COL_X[0], y: y, w: NODE_W, h: h });
      y += h + GAP_Y;
    });
    y = 24;
    parts.forEach(function (p) {
      var uses = (p.uses || []).filter(function (k) { return names.indexOf(k) >= 0; });
      var h = HEAD_H + (uses.length + 1) * ROW_H + FOOT_H;
      nodes.push({ id: 'part:' + p.id, kind: 'part', partId: p.id, title: p.name || p.id, partKind: p.kind, body: !!p.body, uses: uses, x: COL_X[1], y: y, w: NODE_W, h: h });
      uses.forEach(function (k) { links.push({ from: { node: 'param:' + k, port: 'out' }, to: { node: 'part:' + p.id, port: k }, type: 'num' }); });
      y += h + GAP_Y;
    });
    bottom = Math.max(bottom, y - GAP_Y);
    var fh = HEAD_H + parts.length * ROW_H + FOOT_H * 2;
    nodes.push({ id: 'form', kind: 'form', title: (form && form.label) || 'Form', partIds: parts.map(function (p) { return p.id; }),
      x: COL_X[2], y: 24 + Math.max(0, (bottom - 24 - fh) / 2), w: NODE_W, h: fh });
    parts.forEach(function (p) { links.push({ from: { node: 'part:' + p.id, port: 'shape' }, to: { node: 'form', port: p.id }, type: 'shape' }); });
    return { nodes: nodes, links: links };
  }

  // Where a wire meets a node: the right edge for an output, the left edge for an input.
  function portPos(node, dir, key) {
    var x = dir === 'out' ? node.x + node.w : node.x, row;
    if (node.kind === 'param') return [x, node.y + HEAD_H + PBODY_H / 2];
    if (node.kind === 'part') row = dir === 'out' ? node.uses.length : Math.max(0, node.uses.indexOf(key));
    else row = Math.max(0, node.partIds.indexOf(key));
    return [x, node.y + HEAD_H + row * ROW_H + ROW_H / 2];
  }

  function curvePath(a, b) {
    var dx = Math.max(40, Math.abs(b[0] - a[0]) * 0.5);
    return 'M' + a[0] + ',' + a[1] + ' C' + (a[0] + dx) + ',' + a[1] + ' ' + (b[0] - dx) + ',' + b[1] + ' ' + b[0] + ',' + b[1];
  }

  function mount(env) {
    var THREE = env.THREE, doc = env.document || document, form = env.cfg.form, parts = form.authored.parts;
    var params = Object.assign({}, form.params), ranges = form.ranges || {};
    var group = new THREE.Group();
    group.name = 'authored-form';
    env.scene.add(group);

    var quick = env.quick || null;
    var partsBox = null, graphApi = null, selected = null;
    // Every slider for one parameter, in the controls panel and in the graph, stays in step.
    var regs = {};
    function setParam(key, v) {
      params[key] = v;
      (regs[key] || []).forEach(function (r) {
        if (+r.input.value !== v) r.input.value = String(v);
        r.valueEl.textContent = String(v);
      });
      schedule();
    }
    function reg(key, input, valueEl) {
      (regs[key] = regs[key] || []).push({ input: input, valueEl: valueEl });
      input.addEventListener('input', function () { setParam(key, +input.value); });
    }
    function el(tag, cls, text) {
      var e = doc.createElement(tag);
      if (cls) e.className = cls;
      if (text !== undefined) e.textContent = text;
      return e;
    }
    // A code identifier shown as a label: slot_len becomes "Slot len".
    function humanise(key) {
      var t = String(key).replace(/_+/g, ' ').trim();
      return t.charAt(0).toUpperCase() + t.slice(1);
    }
    function gridOf(key) {
      var v = +params[key], r = ranges[key], lo = r ? +r[0] : Math.min(v * 0.25, v - 1), hi = r ? +r[1] : Math.max(v * 2, v + 1);
      if (v < lo) lo = v;
      if (v > hi) hi = v;
      return sliderGrid(v, lo, hi);
    }
    function legendInto(parent) {
      var legend = el('div', 'fg-authored-legend');
      Object.keys(KIND_STYLE).forEach(function (k) {
        var item = doc.createElement('span'), sw = doc.createElement('i');
        sw.style.background = '#' + KIND_STYLE[k].color.toString(16).padStart(6, '0');
        item.appendChild(sw); item.appendChild(doc.createTextNode(KIND_STYLE[k].label)); legend.appendChild(item);
      });
      parent.appendChild(legend);
    }

    var panel, problems;
    if (quick) {
      // The engine's own markup for its Simple-mode controls, so an authored form looks and
      // behaves like every other studio: the same sections, sliders and headings.
      panel = quick;
      quick.textContent = '';
      quick.appendChild(el('p', 'qintro', 'This form is built from code. Drag a slider and it is redrawn with no model call.'));
      var det = el('details', 'pp');
      det.open = true; det.setAttribute('open', ''); det.setAttribute('data-pps', 'form');
      var sum = el('summary');
      sum.appendChild(el('span', '', form.label || (env.cfg.product && env.cfg.product.name) || 'Form'));
      det.appendChild(sum);
      var body = el('div', 'ppb'), grp = el('div', 'qgrp');
      grp.appendChild(el('h3', '', 'Size and shape'));
      Object.keys(params).forEach(function (key) {
        var v = +params[key], grid = gridOf(key);
        var q = el('div', 'qs');
        q.setAttribute('data-q', key);
        var qt = el('div', 'qt'), value = el('b', '', String(v));
        qt.appendChild(el('span', '', humanise(key)));
        qt.appendChild(value);
        q.appendChild(qt);
        var input = el('input', 'rng');
        input.type = 'range';
        input.min = String(grid.lo); input.max = String(grid.hi); input.step = String(grid.step); input.value = String(v);
        input.setAttribute('aria-label', humanise(key));
        input.setAttribute('data-param', key);
        // No manufacturing limit applies to a free form, so the whole track is allowed.
        if (input.style && input.style.setProperty) { input.style.setProperty('--lo', '0%'); input.style.setProperty('--hi', '100%'); }
        reg(key, input, value);
        q.appendChild(input);
        grp.appendChild(q);
      });
      body.appendChild(grp); det.appendChild(body); quick.appendChild(det);
      var pdet = el('details', 'pp'), psum = el('summary');
      pdet.open = true; pdet.setAttribute('open', ''); pdet.setAttribute('data-pps', 'parts');
      psum.appendChild(el('span', '', 'Parts')); pdet.appendChild(psum);
      partsBox = el('div', 'ppb'); pdet.appendChild(partsBox);
      legendInto(pdet);
      quick.appendChild(pdet);
      problems = el('div', 'fg-authored-errors'); quick.appendChild(problems);
    } else {
      panel = doc.createElement('div');
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
        var input = doc.createElement('input'); input.type = 'range'; var grid = sliderGrid(v, lo, hi); input.min = String(grid.lo); input.max = String(grid.hi); input.step = String(grid.step); input.value = String(v);
        input.setAttribute('data-param', key);
        input.addEventListener('input', function () { params[key] = +input.value; val.textContent = String(+input.value); schedule(); });
        row.appendChild(name); row.appendChild(val); row.appendChild(input); rows.appendChild(row);
      });
      legendInto(panel);
      problems = doc.createElement('div'); problems.className = 'fg-authored-errors'; panel.appendChild(problems);
      (env.host || doc.body).appendChild(panel);
    }

    var seq = 0, timer = null, materials = {}, litMaterials = {};
    function litMaterial(kind) {
      var st = KIND_STYLE[kind] || KIND_STYLE.generated;
      return litMaterials[kind] || (litMaterials[kind] = new THREE.MeshStandardMaterial({ color: st.color, roughness: 0.6, metalness: 0.05, emissive: 0x2f6fff, emissiveIntensity: 0.55 }));
    }
    // The selected part glows in the 3D view; everything else keeps its kind's colour.
    function applySelection() {
      group.children.forEach(function (mesh) {
        var kind = mesh.userData && mesh.userData.kind;
        mesh.material = selected !== null && selected === 'part:' + (mesh.userData && mesh.userData.part) ? litMaterial(kind) : material(kind);
      });
    }
    function setSelected(id) {
      selected = id;
      if (graphApi) graphApi.refresh();
      applySelection();
    }
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
      var desc = describe(summary, env.material ? env.material() : null);
      if (env.summaryHost) renderSummary(doc, env.summaryHost, desc);
      if (env.onSummary) env.onSummary(desc);
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
      if (partsBox) {
        partsBox.textContent = '';
        result.parts.forEach(function (p) {
          var row = el('div', 'qs'), qt = el('div', 'qt'), name = doc.createElement('span'), sw = doc.createElement('i');
          row.setAttribute('data-part', p.id);
          sw.style.background = '#' + (KIND_STYLE[p.kind] || KIND_STYLE.generated).color.toString(16).padStart(6, '0');
          sw.className = 'fg-authored-swatch';
          name.appendChild(sw); name.appendChild(doc.createTextNode(' ' + p.name));
          var b = p.bbox;
          qt.appendChild(name);
          qt.appendChild(el('b', '', p.error ? 'not drawn' : b ? fmtMm(b.max[0] - b.min[0]) + ' \u00d7 ' + fmtMm(b.max[2] - b.min[2]) + ' \u00d7 ' + fmtMm(b.max[1] - b.min[1]) + ' mm' : ''));
          row.appendChild(qt);
          partsBox.appendChild(row);
        });
      }
      problems.textContent = '';
      result.errors.forEach(function (e) { var d = doc.createElement('div'); d.textContent = e; problems.appendChild(d); });
      problems.style.display = result.errors.length ? 'block' : 'none';
      if (graphApi) graphApi.update(result);
      applySelection();
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
    function buildGraphUi() {
      var g = buildGraph(form), box = env.graph, svgNs = 'http://www.w3.org/2000/svg';
      var view = { x: 0, y: 0, k: 1 }, drag = null, fitted = false, nodeEls = {}, footEls = {}, byId = {};
      g.nodes.forEach(function (n) { byId[n.id] = n; });
      box.textContent = '';
      var bar = el('div', 'agx-bar'), fitBtn = el('button', 'agx-fit', 'Fit');
      fitBtn.type = 'button';
      bar.appendChild(el('span', 'agx-hint', 'Parameters feed the parts that use them, and the parts make the form. Drag a slider to reshape it. Scroll to zoom, drag the background to pan.'));
      bar.appendChild(fitBtn);
      box.appendChild(bar);
      var canvas = el('div', 'agx-canvas'), world = el('div', 'agx-world');
      var svg = doc.createElementNS(svgNs, 'svg');
      svg.setAttribute('class', 'agx-wires');
      world.appendChild(svg);
      canvas.appendChild(world);
      box.appendChild(canvas);

      function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }
      function applyView() { world.style.transform = 'translate(' + view.x + 'px,' + view.y + 'px) scale(' + view.k + ')'; }
      function place(n) { var e = nodeEls[n.id]; e.style.left = n.x + 'px'; e.style.top = n.y + 'px'; }
      function port(parent, dir, type, key) {
        var p = el('span', 'port ' + dir);
        p.setAttribute('data-t', type); p.setAttribute('data-k', key); p.setAttribute('data-dir', dir);
        parent.appendChild(p);
        return p;
      }
      function row(label, dir, type, key) {
        var r = el('div', 'agx-row ' + dir);
        r.setAttribute('data-k', key);
        r.appendChild(el('span', 'agx-label', label));
        port(r, dir, type, key);
        return r;
      }
      function header(n, cls) {
        var head = el('div', 'nh drag'), dot = el('i', 'dot');
        if (n.partKind) dot.style.background = '#' + (KIND_STYLE[n.partKind] || KIND_STYLE.generated).color.toString(16).padStart(6, '0');
        head.appendChild(dot);
        head.appendChild(el('span', 'title', n.title));
        // A header drags its node and, for a part, selects it. A slider inside the node never starts a drag.
        head.addEventListener('pointerdown', function (e) {
          if (e.button) return;
          if (e.stopPropagation) e.stopPropagation();
          if (n.kind === 'part') setSelected(n.id);
          drag = { n: n, sx: e.clientX, sy: e.clientY, ox: n.x, oy: n.y };
          try { head.setPointerCapture(e.pointerId); } catch (err) { /* not capturable */ }
        });
        head.addEventListener('pointermove', function (e) {
          if (!drag || drag.n !== n) return;
          n.x = drag.ox + (e.clientX - drag.sx) / view.k;
          n.y = drag.oy + (e.clientY - drag.sy) / view.k;
          place(n); drawWires();
        });
        head.addEventListener('pointerup', function () { drag = null; });
        return head;
      }

      g.nodes.forEach(function (n) {
        var e = el('div', 'node agx-node ' + (n.kind === 'param' ? 't-param' : n.kind === 'part' ? 'op t-part' : 'op t-out'));
        e.setAttribute('data-id', n.id);
        e.appendChild(header(n));
        if (n.kind === 'param') {
          var body = el('div', 'agx-pbody'), q = el('div', 'qs'), qt = el('div', 'qt'), val = el('b', '', String(params[n.key]));
          var grid = gridOf(n.key), input = el('input', 'rng');
          qt.appendChild(el('span', '', grid.lo + ' to ' + grid.hi));
          qt.appendChild(val);
          q.appendChild(qt);
          input.type = 'range';
          input.min = String(grid.lo); input.max = String(grid.hi); input.step = String(grid.step); input.value = String(params[n.key]);
          input.setAttribute('aria-label', n.title); input.setAttribute('data-param', n.key);
          if (input.style && input.style.setProperty) { input.style.setProperty('--lo', '0%'); input.style.setProperty('--hi', '100%'); }
          input.addEventListener('pointerdown', function (ev) { if (ev.stopPropagation) ev.stopPropagation(); });
          reg(n.key, input, val);
          q.appendChild(input);
          body.appendChild(q);
          port(body, 'out', 'num', 'out');
          e.appendChild(body);
        } else if (n.kind === 'part') {
          n.uses.forEach(function (k) { e.appendChild(row(labelOf(k), 'in', 'num', k)); });
          e.appendChild(row('shape · ' + n.partKind + (n.body ? ' · body' : ''), 'out', 'shape', 'shape'));
          footEls[n.id] = el('div', 'agx-foot', '');
          e.appendChild(footEls[n.id]);
        } else {
          n.partIds.forEach(function (id) {
            var pn = byId['part:' + id];
            e.appendChild(row(pn ? pn.title : id, 'in', 'shape', id));
          });
          footEls[n.id] = el('div', 'agx-foot', '');
          e.appendChild(footEls[n.id]);
          e.appendChild(el('div', 'agx-foot agx-foot2', ''));
        }
        nodeEls[n.id] = e;
        place(n);
        world.appendChild(e);
      });

      function drawWires() {
        svg.textContent = '';
        g.links.forEach(function (l) {
          var a = portPos(byId[l.from.node], 'out', l.from.port), b = portPos(byId[l.to.node], 'in', l.to.port);
          var hl = selected !== null && (l.from.node === selected || l.to.node === selected);
          var path = doc.createElementNS(svgNs, 'path');
          path.setAttribute('class', 'wire link' + (l.type === 'shape' ? ' shape' : '') + (hl ? ' hl' : ''));
          path.setAttribute('d', curvePath(a, b));
          svg.appendChild(path);
        });
      }
      function refresh() {
        g.nodes.forEach(function (n) { nodeEls[n.id].classList.toggle('sel', n.id === selected); });
        drawWires();
      }
      function update(result) {
        result.parts.forEach(function (p) {
          var foot = footEls['part:' + p.id];
          if (!foot) return;
          var b = p.bbox;
          foot.textContent = p.error ? 'not drawn: ' + String(p.error).slice(0, 60)
            : p.triangles + ' tri · ' + (b ? fmtMm(b.max[0] - b.min[0]) + ' × ' + fmtMm(b.max[2] - b.min[2]) + ' × ' + fmtMm(b.max[1] - b.min[1]) + ' mm' : '');
          foot.classList.toggle('agx-err', !!p.error);
        });
        var ff = footEls.form, b2 = result.bbox;
        if (ff) ff.textContent = b2 ? fmtMm(b2.max[0] - b2.min[0]) + ' × ' + fmtMm(b2.max[2] - b2.min[2]) + ' × ' + fmtMm(b2.max[1] - b2.min[1]) + ' mm' : 'not drawn';
        nodeEls.form.children[nodeEls.form.children.length - 1].textContent = result.triangles + ' triangles';
      }
      function fit() {
        var cw = canvas.clientWidth, ch = canvas.clientHeight;
        if (!cw || !ch) return false;
        var x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
        g.nodes.forEach(function (n) { x0 = Math.min(x0, n.x); y0 = Math.min(y0, n.y); x1 = Math.max(x1, n.x + n.w); y1 = Math.max(y1, n.y + n.h); });
        var k = clamp(Math.min((cw - 32) / Math.max(1, x1 - x0), (ch - 32) / Math.max(1, y1 - y0)), 0.3, 1.1);
        view.k = k; view.x = (cw - (x1 - x0) * k) / 2 - x0 * k; view.y = Math.max(16, (ch - (y1 - y0) * k) / 2) - y0 * k;
        applyView();
        return true;
      }

      // Zoom about the pointer, pan by dragging the background, click the background to deselect.
      canvas.addEventListener('wheel', function (e) {
        if (e.preventDefault) e.preventDefault();
        var r = canvas.getBoundingClientRect(), cx = e.clientX - r.left, cy = e.clientY - r.top;
        var k2 = clamp(view.k * Math.exp(-e.deltaY * 0.002), 0.3, 1.6);
        view.x = cx - (cx - view.x) * (k2 / view.k); view.y = cy - (cy - view.y) * (k2 / view.k); view.k = k2;
        applyView();
      });
      canvas.addEventListener('pointerdown', function (e) {
        if (e.button) return;
        if (e.target !== canvas && e.target !== world && e.target !== svg) return;
        drag = { pan: true, sx: e.clientX, sy: e.clientY, ox: view.x, oy: view.y, moved: false };
        try { canvas.setPointerCapture(e.pointerId); } catch (err) { /* not capturable */ }
      });
      canvas.addEventListener('pointermove', function (e) {
        if (!drag || !drag.pan) return;
        if (Math.abs(e.clientX - drag.sx) + Math.abs(e.clientY - drag.sy) > 3) drag.moved = true;
        view.x = drag.ox + (e.clientX - drag.sx); view.y = drag.oy + (e.clientY - drag.sy);
        applyView();
      });
      canvas.addEventListener('pointerup', function () {
        if (drag && drag.pan && !drag.moved) setSelected(null);
        drag = null;
      });
      fitBtn.addEventListener('click', fit);

      fitted = fit();
      if (!fitted && typeof ResizeObserver !== 'undefined') {
        // Hidden in Simple level, so it has no size yet: fit the first time it is shown.
        var ro = new ResizeObserver(function () { if (!fitted && fit()) { fitted = true; ro.disconnect(); } });
        ro.observe(canvas);
      }
      applyView();
      drawWires();
      return {
        nodes: g.nodes, links: g.links, view: view, refresh: refresh, update: update, fit: fit,
        nodeAt: function (id) { return byId[id]; },
        select: setSelected, selected: function () { return selected; },
      };
    }

    if (env.graph) graphApi = buildGraphUi();
    render();
    return { render: render, group: group, panel: panel, params: params, graph: graphApi, setParam: setParam };
  }

  return { LIMITS: LIMITS, HARDEN_NAMES: HARDEN_NAMES, KIND_STYLE: KIND_STYLE, NOT_ASSESSED: NOT_ASSESSED, describe: describe, renderSummary: renderSummary, workerSource: workerSource, run: run, runPart: runPart, sanitize: sanitize, measure: measure, sliderGrid: sliderGrid, buildGraph: buildGraph, portPos: portPos, mount: mount };
});
