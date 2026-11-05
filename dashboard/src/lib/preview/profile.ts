import type { PlaneElement, Vec2 } from './definition';

/**
 * A profile's elements as closed loops: the browser half of
 * `twin_core/design_ir/feature_definition.py` (`profile_loops`). The segment
 * counts are the same on both sides, so the gateway's comparison measures
 * the shape this draws.
 */

export const ARC_SEGMENT_DEG = 6;
export const SPLINE_SEGMENTS_PER_SPAN = 8;
export const REVOLVE_SEGMENT_DEG = 6;

const RAD = Math.PI / 180;

function arcPoints(c: Vec2, r: number, startDeg: number, sweepDeg: number): Vec2[] {
  const n = Math.max(2, Math.ceil(Math.abs(sweepDeg) / ARC_SEGMENT_DEG));
  const out: Vec2[] = [];
  for (let i = 0; i <= n; i++) {
    const a = (startDeg + (sweepDeg * i) / n) * RAD;
    out.push([c[0] + r * Math.cos(a), c[1] + r * Math.sin(a)]);
  }
  return out;
}

function catmullRom(points: Vec2[], closed: boolean): Vec2[] {
  if (points.length < 2) return points.slice();
  const at = (i: number): Vec2 =>
    closed ? points[((i % points.length) + points.length) % points.length]! : points[Math.min(Math.max(i, 0), points.length - 1)]!;
  const spans = closed ? points.length : points.length - 1;
  const out: Vec2[] = [];
  for (let s = 0; s < spans; s++) {
    const [p0, p1, p2, p3] = [at(s - 1), at(s), at(s + 1), at(s + 2)];
    for (let k = 0; k < SPLINE_SEGMENTS_PER_SPAN; k++) {
      const t = k / SPLINE_SEGMENTS_PER_SPAN;
      const t2 = t * t;
      const t3 = t2 * t;
      const f = (a: number, b: number, c: number, d: number) =>
        0.5 * (2 * b + (-a + c) * t + (2 * a - 5 * b + 4 * c - d) * t2 + (-a + 3 * b - 3 * c + d) * t3);
      out.push([f(p0[0], p1[0], p2[0], p3[0]), f(p0[1], p1[1], p2[1], p3[1])]);
    }
  }
  if (!closed) out.push(points[points.length - 1]!);
  return out;
}

/** `[points, closed]` of one element, with the preview's segment counts. */
export function elementPolyline(el: PlaneElement): [Vec2[], boolean] {
  switch (el.type) {
    case 'line':
      return [[el.start, el.end], false];
    case 'arc': {
      let sweep = (((el.end_angle - el.start_angle) % 360) + 360) % 360;
      if (sweep === 0) sweep = 360;
      return [arcPoints(el.center, el.radius, el.start_angle, sweep), false];
    }
    case 'circle':
      return [arcPoints(el.center, el.radius, 0, 360).slice(0, -1), true];
    case 'ellipse': {
      const n = Math.ceil(360 / ARC_SEGMENT_DEG);
      const rot = el.rotation * RAD;
      const out: Vec2[] = [];
      for (let i = 0; i < n; i++) {
        const t = (2 * Math.PI * i) / n;
        const a = el.major_radius * Math.cos(t);
        const b = el.minor_radius * Math.sin(t);
        out.push([el.center[0] + a * Math.cos(rot) - b * Math.sin(rot), el.center[1] + a * Math.sin(rot) + b * Math.cos(rot)]);
      }
      return [out, true];
    }
    case 'rectangle': {
      const [x, y] = el.origin;
      return [
        [
          [x, y],
          [x + el.width, y],
          [x + el.width, y + el.height],
          [x, y + el.height],
        ],
        true,
      ];
    }
    case 'spline':
      return [catmullRom(el.points, el.closed), el.closed];
  }
}

export function signedArea(loop: Vec2[]): number {
  let s = 0;
  for (let i = 0; i < loop.length; i++) {
    const [x1, y1] = loop[i]!;
    const [x2, y2] = loop[(i + 1) % loop.length]!;
    s += x1 * y2 - x2 * y1;
  }
  return s / 2;
}

export function pointInLoop(p: Vec2, loop: Vec2[]): boolean {
  let inside = false;
  for (let i = 0, j = loop.length - 1; i < loop.length; j = i++) {
    const [xi, yi] = loop[i]!;
    const [xj, yj] = loop[j]!;
    if (yi > p[1] !== yj > p[1]) {
      const x = ((xj - xi) * (p[1] - yi)) / (yj - yi || 1e-300) + xi;
      if (p[0] < x) inside = !inside;
    }
  }
  return inside;
}

export interface Profile {
  outer: Vec2[];
  /** Loops inside the outer one: through-holes once extruded. */
  holes: Vec2[][];
  /** Loops outside it: separate islands. */
  islands: Vec2[][];
  /** Open chains that do not close: not drawn. */
  openChains: number;
}

const d2 = (a: Vec2, b: Vec2) => (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2;

/**
 * Chain the elements into closed loops and sort them: the largest is the
 * outer boundary, loops inside it are holes, loops outside it islands.
 * Throws when nothing closes.
 */
export function profileLoops(elements: PlaneElement[]): Profile {
  const loops: Vec2[][] = [];
  const chains: Vec2[][] = [];
  const every: Vec2[] = [];
  for (const el of elements) {
    if (el.construction) continue;
    const [pts, closed] = elementPolyline(el);
    every.push(...pts);
    if (closed) loops.push(pts);
    else if (pts.length >= 2) chains.push(pts);
  }
  let extent = 0;
  if (every.length) {
    const xs = every.map((p) => p[0]);
    const ys = every.map((p) => p[1]);
    extent = Math.hypot(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys));
  }
  const tol2 = Math.max(1e-6, extent * 1e-4) ** 2;
  let openChains = 0;
  const unused = chains.slice();
  while (unused.length) {
    const loop = unused.shift()!.slice();
    let closed = d2(loop[0]!, loop[loop.length - 1]!) <= tol2;
    while (!closed) {
      const end = loop[loop.length - 1]!;
      let found = -1;
      let next: Vec2[] = [];
      for (let i = 0; i < unused.length; i++) {
        const c = unused[i]!;
        if (d2(c[0]!, end) <= tol2) {
          found = i;
          next = c;
          break;
        }
        if (d2(c[c.length - 1]!, end) <= tol2) {
          found = i;
          next = c.slice().reverse();
          break;
        }
      }
      if (found < 0) break;
      unused.splice(found, 1);
      loop.push(...next.slice(1));
      closed = d2(loop[0]!, loop[loop.length - 1]!) <= tol2;
    }
    if (closed) loops.push(loop.length > 3 ? loop.slice(0, -1) : loop);
    else openChains += 1;
  }
  if (!loops.length) throw new Error('the profile has no closed loop to draw');
  loops.sort((a, b) => Math.abs(signedArea(b)) - Math.abs(signedArea(a)));
  const [outer, ...rest] = loops as [Vec2[], ...Vec2[][]];
  const holes: Vec2[][] = [];
  const islands: Vec2[][] = [];
  for (const lp of rest) (pointInLoop(lp[0]!, outer) ? holes : islands).push(lp);
  return { outer, holes, islands, openChains };
}

/** `loop` moved inward by `distance` (mitred; approximate on concave corners). */
export function insetLoop(loop: Vec2[], distance: number): Vec2[] {
  const pts = signedArea(loop) < 0 ? loop.slice().reverse() : loop.slice();
  const n = pts.length;
  const out: Vec2[] = [];
  for (let i = 0; i < n; i++) {
    const p0 = pts[(i - 1 + n) % n]!;
    const p1 = pts[i]!;
    const p2 = pts[(i + 1) % n]!;
    const e1: Vec2 = [p1[0] - p0[0], p1[1] - p0[1]];
    const e2: Vec2 = [p2[0] - p1[0], p2[1] - p1[1]];
    const l1 = Math.hypot(...e1) || 1e-12;
    const l2 = Math.hypot(...e2) || 1e-12;
    const n1: Vec2 = [-e1[1] / l1, e1[0] / l1];
    const n2: Vec2 = [-e2[1] / l2, e2[0] / l2];
    const bx = n1[0] + n2[0];
    const by = n1[1] + n2[1];
    const bl = Math.hypot(bx, by) || 1e-12;
    const cosHalf = Math.max(0.2, (bx * n1[0] + by * n1[1]) / bl);
    const k = distance / cosHalf;
    out.push([p1[0] + (bx / bl) * k, p1[1] + (by / bl) * k]);
  }
  return out;
}
