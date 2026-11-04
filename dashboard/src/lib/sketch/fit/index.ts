/**
 * Fit a freehand sketch's strokes with primitives (SKP-002).
 *
 * Per stroke: resample evenly, decide whether it closes on itself, cut it at
 * corners, and fit each run with the simplest primitive that stays inside
 * the tolerance: a line, then an arc, then (by splitting the run further)
 * lines and arcs, and only then a smoothing spline. A closed stroke without
 * corners is tried as a circle and an ellipse first.
 *
 * Tolerances are relative to the sketch's extent and never finer than a few
 * screen pixels at the scale it was drawn (`unitPx`), so a wobble a person
 * cannot see is not mistaken for a feature.
 *
 * Every primitive keeps a stable id (stroke id plus segment number), its
 * source stroke and point range (SKP-001, SKP-006), a residual and a
 * confidence, and is marked `estimated`: it is read off a drawing.
 */

import {
  DEG,
  type ClosedProfile,
  type FitReport,
  type Primitive,
  type StrokeRange,
  type Vec,
} from '../model';
import { fitCircle, fitEllipse, fitLine, fitSpline, type CircleFit, type LineFit } from './primitives';
import { farthestFromChord, findCorners, resample } from './segment';
import { findClosedProfiles } from './profiles';

export interface FitStroke {
  id: string;
  points: readonly Vec[];
}

export interface FitOptions {
  /** Screen pixels per sketch unit where the sketch is shown. */
  unitPx?: number;
  /** Tolerance as a fraction of the sketch extent. */
  relTol?: number;
  /** Tolerance floor in screen pixels. */
  pxTol?: number;
  /** Corner threshold, degrees of turn over the corner window. */
  cornerDeg?: number;
}

export interface FitResult {
  primitives: Primitive[];
  profiles: ClosedProfile[];
  report: FitReport;
}

export const DEFAULT_REL_TOL = 0.01;
export const DEFAULT_PX_TOL = 3;
export const DEFAULT_CORNER_DEG = 40;
/** A primitive with lower confidence is drawn as uncertain. */
export const CONFIDENT = 0.6;

const now = (): number => (typeof performance !== 'undefined' ? performance.now() : Date.now());

type Piece =
  | { kind: 'line'; fit: LineFit; from: number; to: number; acc: number }
  | { kind: 'arc'; fit: CircleFit; start: number; end: number; from: number; to: number; acc: number }
  | { kind: 'spline'; points: Vec[]; rms: number; from: number; to: number; acc: number; closed: boolean }
  | { kind: 'circle'; fit: CircleFit; from: number; to: number; acc: number }
  | {
      kind: 'ellipse';
      center: Vec;
      major: number;
      minor: number;
      rotation: number;
      rms: number;
      from: number;
      to: number;
      acc: number;
    };

interface Ctx {
  tol: number;
  extent: number;
}

function runLength(pts: readonly Vec[]): number {
  let s = 0;
  for (let i = 1; i < pts.length; i += 1) s += Math.hypot(pts[i]![0] - pts[i - 1]![0], pts[i]![1] - pts[i - 1]![1]);
  return s;
}

/** Signed angle swept round `c` walking the points, radians. */
function sweptAngle(pts: readonly Vec[], c: Vec): number {
  let total = 0;
  let prev = Math.atan2(pts[0]![1] - c[1], pts[0]![0] - c[0]);
  for (let i = 1; i < pts.length; i += 1) {
    const a = Math.atan2(pts[i]![1] - c[1], pts[i]![0] - c[0]);
    let d = a - prev;
    if (d > Math.PI) d -= 2 * Math.PI;
    if (d < -Math.PI) d += 2 * Math.PI;
    total += d;
    prev = a;
  }
  return total;
}

function lineAcc(ctx: Ctx, L: number): { rms: number; max: number } {
  return { rms: Math.max(0.4 * ctx.tol, 0.008 * L), max: Math.max(1.2 * ctx.tol, 0.025 * L) };
}

/** Arc and circle acceptance; `size` is the radius, or half the run length when that is smaller. */
function arcAcc(ctx: Ctx, size: number): { rms: number; max: number } {
  return { rms: Math.max(0.35 * ctx.tol, 0.02 * size), max: Math.max(1.0 * ctx.tol, 0.06 * size) };
}

function tryLine(pts: readonly Vec[], ctx: Ctx, from: number, to: number, short = false): Piece | null {
  const fit = fitLine(pts);
  const acc = lineAcc(ctx, fit.length);
  if (fit.length < (short ? 1 : 2) * ctx.tol) return null;
  if (fit.rms <= acc.rms && fit.maxDev <= acc.max) return { kind: 'line', fit, from, to, acc: acc.rms };
  return null;
}

function tryArc(pts: readonly Vec[], ctx: Ctx, from: number, to: number): Piece | null {
  const fit = fitCircle(pts);
  if (!fit) return null;
  const acc = arcAcc(ctx, Math.min(fit.radius, runLength(pts) / 2));
  if (fit.radius > 4 * ctx.extent || fit.rms > acc.rms || fit.maxDev > acc.max) return null;
  const sweep = sweptAngle(pts, fit.center);
  if (Math.abs(sweep) < (12 * Math.PI) / 180) return null;
  const a0 = Math.atan2(pts[0]![1] - fit.center[1], pts[0]![0] - fit.center[0]);
  const a1 = Math.atan2(pts[pts.length - 1]![1] - fit.center[1], pts[pts.length - 1]![0] - fit.center[0]);
  // Arcs run counter-clockwise (increasing angle) from start to end.
  const [start, end] = sweep > 0 ? [a0, a1] : [a1, a0];
  return { kind: 'arc', fit, start, end, from, to, acc: acc.rms };
}

function splinePiece(pts: readonly Vec[], ctx: Ctx, from: number, to: number, closed: boolean): Piece {
  const L = runLength(pts);
  const count = Math.max(4, Math.min(12, Math.round(L / (6 * ctx.tol))));
  const fit = fitSpline(pts, closed, count);
  return { kind: 'spline', points: fit.points, rms: fit.rms, from, to, acc: Math.max(0.5 * ctx.tol, 1e-12), closed };
}

/** Douglas-Peucker breakpoints (indices, first and last included). */
function breakpoints(pts: readonly Vec[], eps: number): number[] {
  const keep = new Set<number>([0, pts.length - 1]);
  const stack: [number, number][] = [[0, pts.length - 1]];
  while (stack.length) {
    const [i, j] = stack.pop()!;
    if (j - i < 2) continue;
    const { index, distance } = farthestFromChord(pts.slice(i, j + 1));
    if (distance > eps) {
      keep.add(i + index);
      stack.push([i, i + index], [i + index, j]);
    }
  }
  return [...keep].sort((x, y) => x - y);
}

/**
 * Fit one open run greedily over its Douglas-Peucker breakpoints: from each
 * breakpoint, take the line or the arc that reaches furthest while staying
 * inside the tolerance. A run that only breaks into slivers is a spline.
 */
function fitRun(pts: Vec[], from: number, ctx: Ctx): Piece[] {
  const to = from + pts.length - 1;
  const whole = tryLine(pts, ctx, from, to) ?? tryArc(pts, ctx, from, to);
  if (whole) return [whole];
  const bp = breakpoints(pts, 0.6 * ctx.tol);
  const pieces: Piece[] = [];
  let i = 0;
  while (i < bp.length - 1) {
    let best: Piece | null = null;
    let reach = i;
    let misses = 0;
    for (let j = i + 1; j < bp.length && misses < 2; j += 1) {
      const a = bp[i]!;
      const b = bp[j]!;
      const run = pts.slice(a, b + 1);
      const cand = tryLine(run, ctx, from + a, from + b, true) ?? (run.length >= 5 ? tryArc(run, ctx, from + a, from + b) : null);
      if (cand) {
        best = cand;
        reach = j;
        misses = 0;
      } else {
        misses += 1;
      }
    }
    if (!best) {
      // Nothing fits even one span: let a spline carry this run.
      return [splinePiece(pts, ctx, from, to, false)];
    }
    pieces.push(best);
    i = reach;
  }
  const slivers = pieces.filter((p) => (p.to - p.from) * ctx.tol * 0.5 < 3 * ctx.tol).length;
  if (pieces.length > 8 || (pieces.length > 3 && slivers > pieces.length / 2)) {
    return [splinePiece(pts, ctx, from, to, false)];
  }
  return pieces;
}

/** Join neighbouring pieces that are really one line or one arc. */
function mergePieces(pieces: Piece[], pts: readonly Vec[], ctx: Ctx): Piece[] {
  const out: Piece[] = [];
  for (const piece of pieces) {
    const prev = out[out.length - 1];
    if (prev && prev.kind === 'line' && piece.kind === 'line') {
      const merged = tryLine(pts.slice(prev.from, piece.to + 1), ctx, prev.from, piece.to);
      const d1 = Math.atan2(prev.fit.end[1] - prev.fit.start[1], prev.fit.end[0] - prev.fit.start[0]);
      const d2 = Math.atan2(piece.fit.end[1] - piece.fit.start[1], piece.fit.end[0] - piece.fit.start[0]);
      let diff = Math.abs(d1 - d2) % (2 * Math.PI);
      if (diff > Math.PI) diff = 2 * Math.PI - diff;
      if (merged && diff < (8 * Math.PI) / 180) {
        out[out.length - 1] = merged;
        continue;
      }
    }
    if (prev && prev.kind === 'arc' && piece.kind === 'arc') {
      const merged = tryArc(pts.slice(prev.from, piece.to + 1), ctx, prev.from, piece.to);
      const close =
        Math.hypot(prev.fit.center[0] - piece.fit.center[0], prev.fit.center[1] - piece.fit.center[1]) <
        0.15 * Math.max(prev.fit.radius, piece.fit.radius);
      if (merged && close) {
        out[out.length - 1] = merged;
        continue;
      }
    }
    out.push(piece);
  }
  return out;
}

function intersect(a: LineFit, b: LineFit): Vec | null {
  const d1x = a.end[0] - a.start[0];
  const d1y = a.end[1] - a.start[1];
  const d2x = b.end[0] - b.start[0];
  const d2y = b.end[1] - b.start[1];
  const den = d1x * d2y - d1y * d2x;
  if (Math.abs(den) < 1e-12) return null;
  const t = ((b.start[0] - a.start[0]) * d2y - (b.start[1] - a.start[1]) * d2x) / den;
  return [a.start[0] + t * d1x, a.start[1] + t * d1y];
}

/** Meet neighbouring lines at their intersection, when it is close to the drawn corner. */
function snapCorners(pieces: Piece[], closed: boolean, ctx: Ctx): void {
  const n = pieces.length;
  const pairs = closed && n > 1 ? n : n - 1;
  for (let i = 0; i < pairs; i += 1) {
    const a = pieces[i]!;
    const b = pieces[(i + 1) % n]!;
    if (a.kind !== 'line' || b.kind !== 'line') continue;
    const x = intersect(a.fit, b.fit);
    if (!x) continue;
    if (Math.hypot(x[0] - a.fit.end[0], x[1] - a.fit.end[1]) > 4 * ctx.tol) continue;
    if (Math.hypot(x[0] - b.fit.start[0], x[1] - b.fit.start[1]) > 4 * ctx.tol) continue;
    a.fit = { ...a.fit, end: x };
    b.fit = { ...b.fit, start: x };
  }
}

const deg = (r: number): number => {
  let d = (r * DEG) % 360;
  if (d < 0) d += 360;
  return d;
};

function confidence(rms: number, acc: number, factor = 1): number {
  const c = (1 - rms / Math.max(acc, 1e-12)) * factor;
  return Math.max(0.05, Math.min(0.99, Math.round(c * 100) / 100));
}

function toPrimitive(
  piece: Piece,
  id: string,
  strokeId: string,
  orig: readonly number[],
): Primitive {
  const range: StrokeRange = { stroke_id: strokeId, start: orig[piece.from] ?? 0, end: orig[piece.to] ?? 0 };
  const base = {
    id,
    source_stroke_ids: [strokeId],
    source_ranges: [range],
    estimated: true,
  };
  switch (piece.kind) {
    case 'line':
      return {
        ...base,
        type: 'line',
        start: piece.fit.start,
        end: piece.fit.end,
        residual: piece.fit.rms,
        confidence: confidence(piece.fit.rms, piece.acc),
      };
    case 'arc':
      return {
        ...base,
        type: 'arc',
        center: piece.fit.center,
        radius: piece.fit.radius,
        start_angle: deg(piece.start),
        end_angle: deg(piece.end),
        residual: piece.fit.rms,
        confidence: confidence(piece.fit.rms, piece.acc),
      };
    case 'circle':
      return {
        ...base,
        type: 'circle',
        center: piece.fit.center,
        radius: piece.fit.radius,
        residual: piece.fit.rms,
        confidence: confidence(piece.fit.rms, piece.acc),
      };
    case 'ellipse':
      return {
        ...base,
        type: 'ellipse',
        center: piece.center,
        major_radius: piece.major,
        minor_radius: piece.minor,
        rotation: piece.rotation * DEG,
        residual: piece.rms,
        confidence: confidence(piece.rms, piece.acc),
      };
    case 'spline':
      return {
        ...base,
        type: 'spline',
        points: piece.points,
        closed: piece.closed,
        residual: piece.rms,
        // A spline is the fallback: always shown as uncertain.
        confidence: Math.min(0.55, confidence(piece.rms, piece.acc * 2, 0.6)),
      };
  }
}

/** A safe id fragment from a stroke id (refs use `.` to name a point). */
function idPart(strokeId: string): string {
  return strokeId.replace(/[^A-Za-z0-9_-]/g, '_');
}

function fitClosed(
  pts: Vec[],
  ctx: Ctx,
  w: number,
  minGap: number,
  cornerRad: number,
): { pieces: Piece[]; rotate: number } {
  const n = pts.length;
  const L = runLength(pts);
  // A closed stroke that is a circle or an ellipse is taken as one, whatever
  // its corner detector says (a small circle turns sharply everywhere).
  const circle = fitCircle(pts);
  const ellipse = fitEllipse(pts);
  const cAcc = circle ? arcAcc(ctx, circle.radius) : null;
  const circleOk = !!circle && !!cAcc && circle.rms <= cAcc.rms && circle.maxDev <= cAcc.max * 1.5;
  if (ellipse) {
    const eAcc = arcAcc(ctx, ellipse.minor);
    const ellipseOk = ellipse.rms <= eAcc.rms && ellipse.maxDev <= eAcc.max * 1.5;
    const elongated = ellipse.major / ellipse.minor > 1.12;
    if (ellipseOk && elongated && (!circleOk || ellipse.rms < 0.6 * circle!.rms)) {
      return {
        pieces: [{ kind: 'ellipse', center: ellipse.center, major: ellipse.major, minor: ellipse.minor, rotation: ellipse.rotation, rms: ellipse.rms, from: 0, to: n - 1, acc: eAcc.rms }],
        rotate: 0,
      };
    }
  }
  if (circleOk) return { pieces: [{ kind: 'circle', fit: circle!, from: 0, to: n - 1, acc: cAcc!.rms }], rotate: 0 };
  const corners = findCorners(pts, w, cornerRad, true, minGap);
  if (corners.length === 0) {
    // A smooth closed outline (a slot, a rounded rectangle): lines and arcs
    // meeting tangentially, when they fit; otherwise one closed spline.
    const parts = fitRun([...pts, pts[0]!], 0, ctx);
    if (parts.length > 1 && !parts.some((p) => p.kind === 'spline')) return { pieces: parts, rotate: 0 };
    const count = Math.max(5, Math.min(12, Math.round(L / (6 * ctx.tol))));
    const fit = fitSpline(pts, true, count);
    return { pieces: [{ kind: 'spline', points: fit.points, rms: fit.rms, from: 0, to: n - 1, acc: 0.5 * ctx.tol, closed: true }], rotate: 0 };
  }
  // A closed outline with corners: walk it from the first corner, round to the same corner.
  const start = corners[0]!;
  const loop = [...pts.slice(start), ...pts.slice(0, start), pts[start]!];
  const local = corners.map((c) => (c - start + n) % n).sort((a, b) => a - b);
  local.push(n);
  const pieces: Piece[] = [];
  for (let k = 0; k + 1 < local.length; k += 1) {
    const a = local[k]!;
    const b = local[k + 1]!;
    if (b - a < 2) continue;
    pieces.push(...fitRun(loop.slice(a, b + 1), a, ctx));
  }
  return { pieces, rotate: start };
}

/** Fit every stroke. Pure and deterministic for the same strokes and options. */
export function fitSketch(strokes: readonly FitStroke[], opts: FitOptions = {}): FitResult {
  const t0 = now();
  const unitPx = opts.unitPx && opts.unitPx > 0 ? opts.unitPx : 600;
  const relTol = opts.relTol ?? DEFAULT_REL_TOL;
  const pxTol = opts.pxTol ?? DEFAULT_PX_TOL;
  const cornerRad = ((opts.cornerDeg ?? DEFAULT_CORNER_DEG) * Math.PI) / 180;

  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const s of strokes)
    for (const [x, y] of s.points) {
      minX = Math.min(minX, x);
      minY = Math.min(minY, y);
      maxX = Math.max(maxX, x);
      maxY = Math.max(maxY, y);
    }
  const extent = Number.isFinite(minX) ? Math.hypot(maxX - minX, maxY - minY) : 0;
  const tol = Math.max(relTol * extent, pxTol / unitPx, 1e-6);
  const ctx: Ctx = { tol, extent: Math.max(extent, tol) };

  const primitives: Primitive[] = [];
  const skipped: string[] = [];

  for (const stroke of strokes) {
    const length0 = runLength(stroke.points);
    if (stroke.points.length < 2 || length0 < 3 * tol) {
      skipped.push(stroke.id);
      continue;
    }
    const spacing = Math.max(tol / 2, length0 / 2000);
    const rs = resample(stroke.points, spacing);
    const pts = rs.pts;
    const w = Math.max(2, Math.min(Math.round((2 * tol) / spacing), Math.floor(pts.length / 8)));
    const minGap = Math.max(w, Math.min(Math.round((3 * tol) / spacing), Math.floor(pts.length / 6)));
    const gap = Math.hypot(pts[0]![0] - pts[pts.length - 1]![0], pts[0]![1] - pts[pts.length - 1]![1]);
    const closed = rs.length > 8 * tol && gap < Math.max(3 * tol, 0.06 * rs.length) && pts.length >= 8;

    let pieces: Piece[];
    let orig = rs.orig;
    if (closed) {
      // Drop the overlapping tail so the loop does not double back on itself.
      const loopPts = pts.slice(0, -1);
      const loopOrig = rs.orig.slice(0, -1);
      const { pieces: ps, rotate } = fitClosed(loopPts, ctx, w, minGap, cornerRad);
      pieces = ps;
      const m = loopOrig.length;
      orig = [...loopOrig.slice(rotate), ...loopOrig.slice(0, rotate), loopOrig[rotate % m]!];
      if (pieces.length > 1) {
        pieces = mergePieces(pieces, [...loopPts.slice(rotate), ...loopPts.slice(0, rotate), loopPts[rotate]!], ctx);
        snapCorners(pieces, true, ctx);
      }
    } else {
      const corners = findCorners(pts, w, cornerRad, false, minGap);
      const cuts = [0, ...corners, pts.length - 1];
      pieces = [];
      for (let k = 0; k + 1 < cuts.length; k += 1) {
        const a = cuts[k]!;
        const b = cuts[k + 1]!;
        if (b - a < 2) continue;
        pieces.push(...fitRun(pts.slice(a, b + 1), a, ctx));
      }
      pieces = mergePieces(pieces, pts, ctx);
      snapCorners(pieces, false, ctx);
    }
    pieces.forEach((piece, k) => {
      primitives.push(toPrimitive(piece, `p-${idPart(stroke.id)}-${k}`, stroke.id, orig));
    });
  }

  const profiles = findClosedProfiles(primitives, 2 * tol);
  return {
    primitives,
    profiles,
    report: {
      extent,
      tolerance: tol,
      unit_px: unitPx,
      stroke_count: strokes.length,
      skipped_stroke_ids: skipped,
      duration_ms: Math.round((now() - t0) * 100) / 100,
    },
  };
}
