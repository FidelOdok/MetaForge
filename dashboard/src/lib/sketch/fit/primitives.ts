/**
 * Least-squares fits of single primitives to a run of points.
 *
 * - line: total least squares (principal axis of the points);
 * - circle: algebraic (Kasa) fit for a start, refined by Gauss-Newton on the
 *   geometric distance;
 * - ellipse: moments for a start, refined on an approximate geometric
 *   (radial) distance;
 * - spline: a smoothed run of through-points, the fallback when nothing
 *   simpler fits.
 *
 * Each returns its fitted parameters with the RMS and largest distance of
 * the points from the shape, so the caller can judge it against a tolerance.
 */

import { levenbergMarquardt, solveLinear } from '../solver/linalg';
import type { Vec } from '../model';

export interface LineFit {
  start: Vec;
  end: Vec;
  rms: number;
  maxDev: number;
  length: number;
}

export interface CircleFit {
  center: Vec;
  radius: number;
  rms: number;
  maxDev: number;
}

export interface EllipseFit {
  center: Vec;
  major: number;
  minor: number;
  /** Radians. */
  rotation: number;
  rms: number;
  maxDev: number;
}

function centroid(pts: readonly Vec[]): Vec {
  let x = 0;
  let y = 0;
  for (const p of pts) {
    x += p[0];
    y += p[1];
  }
  return [x / pts.length, y / pts.length];
}

/** Total least squares line through the points, ends projected from the first and last point. */
export function fitLine(pts: readonly Vec[]): LineFit {
  const [cx, cy] = centroid(pts);
  let sxx = 0;
  let syy = 0;
  let sxy = 0;
  for (const [x, y] of pts) {
    sxx += (x - cx) * (x - cx);
    syy += (y - cy) * (y - cy);
    sxy += (x - cx) * (y - cy);
  }
  const theta = 0.5 * Math.atan2(2 * sxy, sxx - syy);
  const dx = Math.cos(theta);
  const dy = Math.sin(theta);
  let ss = 0;
  let maxDev = 0;
  for (const [x, y] of pts) {
    const d = Math.abs(-(x - cx) * dy + (y - cy) * dx);
    ss += d * d;
    maxDev = Math.max(maxDev, d);
  }
  const project = (p: Vec): Vec => {
    const t = (p[0] - cx) * dx + (p[1] - cy) * dy;
    return [cx + t * dx, cy + t * dy];
  };
  const start = project(pts[0]!);
  const end = project(pts[pts.length - 1]!);
  return {
    start,
    end,
    rms: Math.sqrt(ss / pts.length),
    maxDev,
    length: Math.hypot(end[0] - start[0], end[1] - start[1]),
  };
}

function circleStats(pts: readonly Vec[], c: Vec, r: number): { rms: number; maxDev: number } {
  let ss = 0;
  let maxDev = 0;
  for (const p of pts) {
    const d = Math.abs(Math.hypot(p[0] - c[0], p[1] - c[1]) - r);
    ss += d * d;
    maxDev = Math.max(maxDev, d);
  }
  return { rms: Math.sqrt(ss / pts.length), maxDev };
}

/** Kasa algebraic circle fit, then a short Gauss-Newton refinement on geometric distance. */
export function fitCircle(pts: readonly Vec[]): CircleFit | null {
  if (pts.length < 3) return null;
  // Centre the data for conditioning.
  const [mx, my] = centroid(pts);
  let scale = 0;
  for (const [x, y] of pts) scale = Math.max(scale, Math.abs(x - mx), Math.abs(y - my));
  if (scale === 0) return null;
  // Minimise sum (x^2 + y^2 + D x + E y + F)^2 over D, E, F.
  const A = new Float64Array(9);
  const b = new Float64Array(3);
  for (const [px, py] of pts) {
    const x = (px - mx) / scale;
    const y = (py - my) / scale;
    const z = -(x * x + y * y);
    const row = [x, y, 1];
    for (let i = 0; i < 3; i += 1) {
      for (let j = 0; j < 3; j += 1) A[i * 3 + j] = A[i * 3 + j]! + row[i]! * row[j]!;
      b[i] = b[i]! + row[i]! * z;
    }
  }
  const sol = solveLinear(A, b, 3);
  if (!sol) return null;
  const D = sol[0]!;
  const E = sol[1]!;
  const F = sol[2]!;
  const r2 = (D * D + E * E) / 4 - F;
  if (!(r2 > 0)) return null;
  let c: Vec = [mx - (D / 2) * scale, my - (E / 2) * scale];
  let r = Math.sqrt(r2) * scale;
  // Geometric refinement.
  const res = levenbergMarquardt(
    (x) => {
      const out = new Float64Array(pts.length);
      for (let i = 0; i < pts.length; i += 1) {
        const p = pts[i]!;
        out[i] = Math.hypot(p[0] - x[0]!, p[1] - x[1]!) - x[2]!;
      }
      return out;
    },
    Float64Array.from([c[0], c[1], r]),
    { maxIterations: 20, residualTol: 0, stepTol: 1e-12 * scale },
  );
  if (Number.isFinite(res.x[0]!) && res.x[2]! > 0) {
    c = [res.x[0]!, res.x[1]!];
    r = res.x[2]!;
  }
  return { center: c, radius: r, ...circleStats(pts, c, r) };
}

function ellipseRadial(p: Vec, cx: number, cy: number, a: number, b: number, rot: number): number {
  const dx = p[0] - cx;
  const dy = p[1] - cy;
  const cr = Math.cos(rot);
  const sr = Math.sin(rot);
  const u = dx * cr + dy * sr;
  const v = -dx * sr + dy * cr;
  const d = Math.sqrt((u / a) * (u / a) + (v / b) * (v / b));
  if (d === 0) return -Math.min(a, b);
  // Distance along the ray from the centre to the boundary.
  return Math.hypot(u, v) * (1 - 1 / d);
}

/**
 * Ellipse fit for a closed run of points sampled evenly along its length.
 *
 * Starts from the second moments (a uniform walk round an ellipse has
 * variance close to a^2/2 along the major axis) and refines on the radial
 * distance to the boundary, a close stand-in for the geometric distance.
 */
export function fitEllipse(pts: readonly Vec[]): EllipseFit | null {
  if (pts.length < 6) return null;
  const [cx, cy] = centroid(pts);
  let sxx = 0;
  let syy = 0;
  let sxy = 0;
  for (const [x, y] of pts) {
    sxx += (x - cx) * (x - cx);
    syy += (y - cy) * (y - cy);
    sxy += (x - cx) * (y - cy);
  }
  sxx /= pts.length;
  syy /= pts.length;
  sxy /= pts.length;
  const tr = sxx + syy;
  const det = sxx * syy - sxy * sxy;
  const disc = Math.sqrt(Math.max(0, (tr * tr) / 4 - det));
  const l1 = tr / 2 + disc;
  const l2 = Math.max(tr / 2 - disc, 1e-12);
  const rot0 = 0.5 * Math.atan2(2 * sxy, sxx - syy);
  const res = levenbergMarquardt(
    (x) => {
      const out = new Float64Array(pts.length);
      const a = Math.abs(x[2]!) + 1e-12;
      const b = Math.abs(x[3]!) + 1e-12;
      for (let i = 0; i < pts.length; i += 1) out[i] = ellipseRadial(pts[i]!, x[0]!, x[1]!, a, b, x[4]!);
      return out;
    },
    Float64Array.from([cx, cy, Math.sqrt(2 * l1), Math.sqrt(2 * l2), rot0]),
    { maxIterations: 40, residualTol: 0, stepTol: 1e-12 },
  );
  const ex = res.x[0]!;
  const ey = res.x[1]!;
  let [a, b, rot] = [Math.abs(res.x[2]!), Math.abs(res.x[3]!), res.x[4]!];
  if (!(a > 0 && b > 0) || !Number.isFinite(a + b + rot + ex + ey)) return null;
  if (b > a) {
    [a, b] = [b, a];
    rot += Math.PI / 2;
  }
  // Normalise the major-axis direction to (-90, 90] degrees.
  rot = Math.atan2(Math.sin(rot), Math.cos(rot));
  if (rot > Math.PI / 2) rot -= Math.PI;
  if (rot <= -Math.PI / 2) rot += Math.PI;
  let ss = 0;
  let maxDev = 0;
  for (const p of pts) {
    const d = Math.abs(ellipseRadial(p, ex, ey, a, b, rot));
    ss += d * d;
    maxDev = Math.max(maxDev, d);
  }
  return { center: [ex, ey], major: a, minor: b, rotation: rot, rms: Math.sqrt(ss / pts.length), maxDev };
}

/** Catmull-Rom samples through `pts` (closed or open), `per` samples per span. */
export function sampleSpline(pts: readonly Vec[], closed: boolean, per = 8): Vec[] {
  const n = pts.length;
  if (n < 2) return pts.map((p) => [p[0], p[1]] as Vec);
  const at = (i: number): Vec => {
    if (closed) return pts[((i % n) + n) % n]!;
    return pts[Math.max(0, Math.min(n - 1, i))]!;
  };
  const out: Vec[] = [];
  const spans = closed ? n : n - 1;
  for (let i = 0; i < spans; i += 1) {
    const p0 = at(i - 1);
    const p1 = at(i);
    const p2 = at(i + 1);
    const p3 = at(i + 2);
    for (let k = 0; k < per; k += 1) {
      const t = k / per;
      const t2 = t * t;
      const t3 = t2 * t;
      const f = (a: number, b: number, c: number, d: number) =>
        0.5 * (2 * b + (-a + c) * t + (2 * a - 5 * b + 4 * c - d) * t2 + (-a + 3 * b - 3 * c + d) * t3);
      out.push([f(p0[0], p1[0], p2[0], p3[0]), f(p0[1], p1[1], p2[1], p3[1])]);
    }
  }
  out.push(closed ? [pts[0]![0], pts[0]![1]] : [pts[n - 1]![0], pts[n - 1]![1]]);
  return out;
}

function distToSegment(p: Vec, a: Vec, b: Vec): number {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const L = dx * dx + dy * dy;
  const t = L === 0 ? 0 : Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L));
  return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
}

/** Distance from a point to a polyline. */
export function distToPolyline(p: Vec, poly: readonly Vec[]): number {
  let best = Infinity;
  for (let i = 1; i < poly.length; i += 1) best = Math.min(best, distToSegment(p, poly[i - 1]!, poly[i]!));
  return poly.length === 1 ? Math.hypot(p[0] - poly[0]![0], p[1] - poly[0]![1]) : best;
}

export interface SplineFit {
  points: Vec[];
  rms: number;
  maxDev: number;
}

/**
 * The smoothing-spline fallback: `count` through-points evenly spaced along
 * the run, interior ones lightly smoothed, joined by a Catmull-Rom curve.
 */
export function fitSpline(pts: readonly Vec[], closed: boolean, count: number): SplineFit {
  const n = pts.length;
  const k = Math.max(closed ? 4 : 3, Math.min(count, n));
  // Arc-length positions.
  const s = [0];
  for (let i = 1; i < n; i += 1) s.push(s[i - 1]! + Math.hypot(pts[i]![0] - pts[i - 1]![0], pts[i]![1] - pts[i - 1]![1]));
  const total = s[n - 1]!;
  const raw: Vec[] = [];
  const steps = closed ? k : k - 1;
  let j = 0;
  for (let i = 0; i < k; i += 1) {
    const target = (total * i) / steps;
    while (j < n - 2 && s[j + 1]! < target) j += 1;
    const seg = s[j + 1]! - s[j]!;
    const t = seg > 0 ? (target - s[j]!) / seg : 0;
    const a = pts[j]!;
    const b = pts[Math.min(j + 1, n - 1)]!;
    raw.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]);
  }
  // Light smoothing of interior points (and every point when closed).
  const through: Vec[] = raw.map((p, i) => {
    if (!closed && (i === 0 || i === raw.length - 1)) return p;
    const prev = raw[(i - 1 + raw.length) % raw.length]!;
    const next = raw[(i + 1) % raw.length]!;
    return [0.25 * prev[0] + 0.5 * p[0] + 0.25 * next[0], 0.25 * prev[1] + 0.5 * p[1] + 0.25 * next[1]];
  });
  const poly = sampleSpline(through, closed, 8);
  let ss = 0;
  let maxDev = 0;
  for (const p of pts) {
    const d = distToPolyline(p, poly);
    ss += d * d;
    maxDev = Math.max(maxDev, d);
  }
  return { points: through, rms: Math.sqrt(ss / n), maxDev };
}
