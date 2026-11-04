/**
 * Stroke preparation and corner segmentation.
 *
 * A stroke is resampled evenly along its length (keeping, for every
 * resampled point, the index of the raw point it came from, so a primitive
 * can name the exact range of the stroke it was fitted to). Corners are
 * where the direction turns sharply over a short window: the turning angle
 * between the chord behind a point and the chord ahead of it is a local
 * maximum above a threshold.
 */

import type { Vec } from '../model';

export interface Resampled {
  pts: Vec[];
  /** Raw point index each resampled point came from. */
  orig: number[];
  length: number;
}

/** Points evenly spaced `spacing` apart along the polyline. */
export function resample(raw: readonly Vec[], spacing: number): Resampled {
  const pts: Vec[] = [];
  const orig: number[] = [];
  // Drop exact repeats first.
  const clean: Vec[] = [];
  const cleanIdx: number[] = [];
  raw.forEach((p, i) => {
    const last = clean[clean.length - 1];
    if (!last || last[0] !== p[0] || last[1] !== p[1]) {
      clean.push(p);
      cleanIdx.push(i);
    }
  });
  if (clean.length === 0) return { pts, orig, length: 0 };
  pts.push([clean[0]![0], clean[0]![1]]);
  orig.push(cleanIdx[0]!);
  let length = 0;
  let carry = 0;
  for (let i = 1; i < clean.length; i += 1) {
    const a = clean[i - 1]!;
    const b = clean[i]!;
    const seg = Math.hypot(b[0] - a[0], b[1] - a[1]);
    length += seg;
    let t = spacing - carry;
    while (t <= seg) {
      const f = t / seg;
      pts.push([a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f]);
      orig.push(f < 0.5 ? cleanIdx[i - 1]! : cleanIdx[i]!);
      t += spacing;
    }
    carry = seg - (t - spacing);
  }
  const lastRaw = clean[clean.length - 1]!;
  const lastPt = pts[pts.length - 1]!;
  if (Math.hypot(lastRaw[0] - lastPt[0], lastRaw[1] - lastPt[1]) > spacing * 0.25) {
    pts.push([lastRaw[0], lastRaw[1]]);
    orig.push(cleanIdx[cleanIdx.length - 1]!);
  } else {
    pts[pts.length - 1] = [lastRaw[0], lastRaw[1]];
    orig[orig.length - 1] = cleanIdx[cleanIdx.length - 1]!;
  }
  return { pts, orig, length };
}

function angleBetween(ax: number, ay: number, bx: number, by: number): number {
  const la = Math.hypot(ax, ay);
  const lb = Math.hypot(bx, by);
  if (la === 0 || lb === 0) return 0;
  const c = Math.max(-1, Math.min(1, (ax * bx + ay * by) / (la * lb)));
  return Math.acos(c);
}

/** Turning angle (radians) at every point, over a window of `w` points each side. */
export function turningAngles(pts: readonly Vec[], w: number, closed: boolean): number[] {
  const n = pts.length;
  const out = new Array<number>(n).fill(0);
  for (let i = 0; i < n; i += 1) {
    let ib = i - w;
    let ia = i + w;
    if (closed) {
      ib = (ib + n) % n;
      ia = ia % n;
    } else if (ib < 0 || ia >= n) {
      continue;
    }
    const p = pts[i]!;
    const b = pts[ib]!;
    const a = pts[ia]!;
    out[i] = angleBetween(p[0] - b[0], p[1] - b[1], a[0] - p[0], a[1] - p[1]);
  }
  return out;
}

/** A short moving average, so hand tremor does not read as corners. */
export function smooth(pts: readonly Vec[], radius: number, closed: boolean): Vec[] {
  const n = pts.length;
  if (radius < 1 || n < 3) return pts.map((p) => [p[0], p[1]] as Vec);
  return pts.map((p, i) => {
    if (!closed && (i < radius || i > n - 1 - radius)) return [p[0], p[1]] as Vec;
    let x = 0;
    let y = 0;
    for (let k = -radius; k <= radius; k += 1) {
      const q = pts[closed ? (i + k + n) % n : i + k]!;
      x += q[0];
      y += q[1];
    }
    return [x / (2 * radius + 1), y / (2 * radius + 1)] as Vec;
  });
}

/**
 * Indices of corners: local maxima of the turning angle above `threshold`
 * (radians). Strongest first, a corner is kept only when it is at least
 * `minGap` points from every corner already kept (and, on an open stroke,
 * from both ends), so tremor near a real corner never splits off a sliver.
 */
export function findCorners(
  pts: readonly Vec[],
  w: number,
  threshold: number,
  closed: boolean,
  minGap = w,
): number[] {
  const n = pts.length;
  const turn = turningAngles(smooth(pts, 2, closed), w, closed);
  const candidates: number[] = [];
  for (let i = 0; i < n; i += 1) {
    if (turn[i]! < threshold) continue;
    let isMax = true;
    for (let k = -w; k <= w && isMax; k += 1) {
      if (k === 0) continue;
      const j = closed ? (i + k + n) % n : i + k;
      if (j < 0 || j >= n) continue;
      if (turn[j]! > turn[i]! || (turn[j]! === turn[i]! && j < i)) isMax = false;
    }
    if (isMax) candidates.push(i);
  }
  candidates.sort((a, b) => turn[b]! - turn[a]!);
  const kept: number[] = [];
  const gap = (a: number, b: number) => {
    const d = Math.abs(a - b);
    return closed ? Math.min(d, n - d) : d;
  };
  for (const i of candidates) {
    if (!closed && (i < minGap || i > n - 1 - minGap)) continue;
    if (kept.every((k) => gap(i, k) >= minGap)) kept.push(i);
  }
  return kept.sort((a, b) => a - b);
}

/** Index of the point farthest from the chord between the run's ends. */
export function farthestFromChord(pts: readonly Vec[]): { index: number; distance: number } {
  const a = pts[0]!;
  const b = pts[pts.length - 1]!;
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const L = Math.hypot(dx, dy);
  let best = 0;
  let index = Math.floor(pts.length / 2);
  for (let i = 1; i < pts.length - 1; i += 1) {
    const p = pts[i]!;
    const d = L === 0 ? Math.hypot(p[0] - a[0], p[1] - a[1]) : Math.abs((p[0] - a[0]) * dy - (p[1] - a[1]) * dx) / L;
    if (d > best) {
      best = d;
      index = i;
    }
  }
  return { index, distance: best };
}
