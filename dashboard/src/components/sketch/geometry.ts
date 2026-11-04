/**
 * Sketch space and shape geometry for the Create stage's sketch layer.
 *
 * Strokes are stored in a viewport-independent **sketch space**: the origin
 * is the centre of the drawing viewport and the shorter viewport side spans
 * one unit. The same drawing made on a phone and on a wide monitor therefore
 * has the same coordinates, and resizing the window never distorts it. The
 * unit has no physical size: a human sketch has an unknown scale.
 */

export type Point = [number, number];

export interface Viewport {
  width: number;
  height: number;
}

/** Pixels per sketch unit for a viewport (its shorter side). */
export function unitPx(view: Viewport): number {
  return Math.max(1, Math.min(view.width, view.height));
}

/** A viewport-relative pixel position to sketch units. */
export function toSketch(px: number, py: number, view: Viewport): Point {
  const s = unitPx(view);
  return [round((px - view.width / 2) / s), round((py - view.height / 2) / s)];
}

/** Sketch units back to viewport pixels. */
export function toScreen(p: Point, view: Viewport): Point {
  const s = unitPx(view);
  return [p[0] * s + view.width / 2, p[1] * s + view.height / 2];
}

/** The SVG viewBox that maps sketch units onto the viewport one to one. */
export function viewBoxFor(view: Viewport): string {
  const s = unitPx(view);
  const w = view.width / s;
  const h = view.height / s;
  return `${round(-w / 2)} ${round(-h / 2)} ${round(w)} ${round(h)}`;
}

/** Six decimals is far below a pixel and keeps the stored JSON short. */
export function round(v: number): number {
  return Math.round(v * 1e6) / 1e6;
}

function fmt(v: number): string {
  return String(Math.round(v * 1e4) / 1e4);
}

/** An SVG path for a polyline in sketch units. */
export function pathD(points: readonly Point[]): string {
  if (points.length === 0) return '';
  const [first, ...rest] = points;
  let d = `M${fmt(first![0])} ${fmt(first![1])}`;
  if (rest.length === 0) return `${d} L${fmt(first![0] + 0.0005)} ${fmt(first![1])}`;
  for (const p of rest) d += ` L${fmt(p[0])} ${fmt(p[1])}`;
  return d;
}

/** One more segment for a path being drawn (no rebuild of the whole string). */
export function pathSegment(p: Point): string {
  return ` L${fmt(p[0])} ${fmt(p[1])}`;
}

export function dist(a: Point, b: Point): number {
  return Math.hypot(a[0] - b[0], a[1] - b[1]);
}

/** Distance from `p` to the segment `a`-`b`. */
export function distToSegment(p: Point, a: Point, b: Point): number {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const len2 = dx * dx + dy * dy;
  if (len2 === 0) return dist(p, a);
  const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len2));
  return dist(p, [a[0] + t * dx, a[1] + t * dy]);
}

/** Does a polyline pass within `radius` of `p`? */
export function hitsPolyline(points: readonly Point[], p: Point, radius: number): boolean {
  if (points.length === 1) return dist(points[0]!, p) <= radius;
  for (let i = 1; i < points.length; i += 1) {
    if (distToSegment(p, points[i - 1]!, points[i]!) <= radius) return true;
  }
  return false;
}

/** Is any point of the polyline inside the rectangle spanned by `a` and `b`? */
export function touchesRect(points: readonly Point[], a: Point, b: Point): boolean {
  const [x0, x1] = a[0] < b[0] ? [a[0], b[0]] : [b[0], a[0]];
  const [y0, y1] = a[1] < b[1] ? [a[1], b[1]] : [b[1], a[1]];
  return points.some(([x, y]) => x >= x0 && x <= x1 && y >= y0 && y <= y1);
}

// ── Shapes ────────────────────────────────────────────────────────────────

export function lineShape(a: Point, b: Point): Point[] {
  return [a, b];
}

export function rectangleShape(a: Point, b: Point): Point[] {
  return [a, [b[0], a[1]], b, [a[0], b[1]], a];
}

/** A circle centred on `centre` through `edge`. */
export function circleShape(centre: Point, edge: Point, segments = 64): Point[] {
  const r = dist(centre, edge);
  const out: Point[] = [];
  for (let i = 0; i <= segments; i += 1) {
    const t = (i / segments) * Math.PI * 2;
    out.push([round(centre[0] + r * Math.cos(t)), round(centre[1] + r * Math.sin(t))]);
  }
  return out;
}

/**
 * A circular arc from the first to the last point of a drag, through the
 * drag point farthest from that chord. A nearly straight drag is a line.
 */
export function arcShape(drag: readonly Point[], segments = 48): Point[] {
  const a = drag[0];
  const b = drag[drag.length - 1];
  if (!a || !b) return [];
  let mid: Point = a;
  let best = -1;
  for (const p of drag) {
    const d = distToSegment(p, a, b);
    if (d > best) {
      best = d;
      mid = p;
    }
  }
  const chord = dist(a, b);
  if (chord === 0 || best < chord * 0.02) return lineShape(a, b);
  const centre = circumcentre(a, mid, b);
  if (!centre) return lineShape(a, b);
  const r = dist(centre, a);
  const ang = (p: Point) => Math.atan2(p[1] - centre[1], p[0] - centre[0]);
  const a0 = ang(a);
  const am = ang(mid);
  const a1 = ang(b);
  // Sweep from a0 to a1 in the direction that passes through am.
  const ccw = (from: number, to: number) => {
    let d = to - from;
    while (d < 0) d += Math.PI * 2;
    return d;
  };
  let sweep = ccw(a0, a1);
  if (ccw(a0, am) > sweep) sweep -= Math.PI * 2;
  const out: Point[] = [];
  for (let i = 0; i <= segments; i += 1) {
    const t = a0 + (sweep * i) / segments;
    out.push([round(centre[0] + r * Math.cos(t)), round(centre[1] + r * Math.sin(t))]);
  }
  return out;
}

function circumcentre(a: Point, b: Point, c: Point): Point | null {
  const d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]));
  if (Math.abs(d) < 1e-12) return null;
  const a2 = a[0] * a[0] + a[1] * a[1];
  const b2 = b[0] * b[0] + b[1] * b[1];
  const c2 = c[0] * c[0] + c[1] * c[1];
  return [
    (a2 * (b[1] - c[1]) + b2 * (c[1] - a[1]) + c2 * (a[1] - b[1])) / d,
    (a2 * (c[0] - b[0]) + b2 * (a[0] - c[0]) + c2 * (b[0] - a[0])) / d,
  ];
}

/** A smooth Catmull-Rom spline through a thinned copy of the drag. */
export function splineShape(drag: readonly Point[], maxControls = 24, perSegment = 8): Point[] {
  if (drag.length < 3) return [...drag];
  const step = Math.max(1, Math.ceil(drag.length / maxControls));
  const ctrl: Point[] = drag.filter((_, i) => i % step === 0);
  const last = drag[drag.length - 1]!;
  if (ctrl[ctrl.length - 1] !== last) ctrl.push(last);
  const out: Point[] = [ctrl[0]!];
  for (let i = 0; i < ctrl.length - 1; i += 1) {
    const p0 = ctrl[Math.max(0, i - 1)]!;
    const p1 = ctrl[i]!;
    const p2 = ctrl[i + 1]!;
    const p3 = ctrl[Math.min(ctrl.length - 1, i + 2)]!;
    for (let k = 1; k <= perSegment; k += 1) {
      const t = k / perSegment;
      const t2 = t * t;
      const t3 = t2 * t;
      const f = (a: number, b: number, c: number, d: number) =>
        0.5 * (2 * b + (-a + c) * t + (2 * a - 5 * b + 4 * c - d) * t2 + (-a + 3 * b - 3 * c + d) * t3);
      out.push([round(f(p0[0], p1[0], p2[0], p3[0])), round(f(p0[1], p1[1], p2[1], p3[1]))]);
    }
  }
  return out;
}
