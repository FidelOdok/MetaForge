/** Synthetic noisy strokes for the compiler tests and benchmarks. */

import type { Vec } from '../model';

/** Deterministic Gaussian noise (mulberry32 plus Box-Muller). */
export function rng(seed: number): () => number {
  let a = seed >>> 0;
  const uniform = () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  return () => {
    const u = Math.max(uniform(), 1e-12);
    const v = uniform();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  };
}

export function noisy(pts: Vec[], sigma: number, seed = 1): Vec[] {
  const g = rng(seed);
  return pts.map(([x, y]) => [x + sigma * g(), y + sigma * g()]);
}

export function linePts(a: Vec, b: Vec, n = 40): Vec[] {
  return Array.from({ length: n }, (_, i) => {
    const t = i / (n - 1);
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t] as Vec;
  });
}

/** Arc from angle a0 to a1 (radians; a1 < a0 walks clockwise). */
export function arcPts(c: Vec, r: number, a0: number, a1: number, n = 60): Vec[] {
  return Array.from({ length: n }, (_, i) => {
    const a = a0 + ((a1 - a0) * i) / (n - 1);
    return [c[0] + r * Math.cos(a), c[1] + r * Math.sin(a)] as Vec;
  });
}

export function circlePts(c: Vec, r: number, n = 120): Vec[] {
  return arcPts(c, r, 0, 2 * Math.PI, n + 1).slice(0, n + 1);
}

export function ellipsePts(c: Vec, a: number, b: number, rot: number, n = 160): Vec[] {
  return Array.from({ length: n + 1 }, (_, i) => {
    const t = (2 * Math.PI * i) / n;
    const x = a * Math.cos(t);
    const y = b * Math.sin(t);
    return [c[0] + x * Math.cos(rot) - y * Math.sin(rot), c[1] + x * Math.sin(rot) + y * Math.cos(rot)] as Vec;
  });
}

export function polyline(corners: Vec[], perSide = 30): Vec[] {
  const out: Vec[] = [];
  for (let i = 0; i + 1 < corners.length; i += 1) {
    const seg = linePts(corners[i]!, corners[i + 1]!, perSide);
    out.push(...(i === 0 ? seg : seg.slice(1)));
  }
  return out;
}

/**
 * A bracket-like sketch of about 20 primitives: an L-shaped plate outline
 * with a rounded corner (arcs), drawn as one closed stroke per feature, plus
 * mounting holes.
 */
export function bracketStrokes(sigma = 0.0015): { id: string; points: Vec[] }[] {
  // Outer outline: lines with one rounded outer corner, drawn as one stroke.
  const r = 0.08;
  const outline: Vec[] = [
    ...linePts([-0.4, -0.3], [0.1, -0.3], 50),
    ...linePts([0.1, -0.3], [0.1, 0.0], 30).slice(1),
    ...linePts([0.1, 0.0], [0.4 - r, 0.0], 30).slice(1),
    ...arcPts([0.4 - r, 0.0 + r], r, -Math.PI / 2, 0, 20).slice(1),
    ...linePts([0.4, 0.0 + r], [0.4, 0.3], 22).slice(1),
    ...linePts([0.4, 0.3], [-0.4, 0.3], 80).slice(1),
    ...linePts([-0.4, 0.3], [-0.4, -0.3], 60).slice(1),
  ];
  const strokes: { id: string; points: Vec[] }[] = [{ id: 'outline', points: noisy(outline, sigma, 11) }];
  const holes: Vec[] = [
    [-0.3, -0.2],
    [0.0, -0.2],
    [-0.3, 0.2],
    [0.3, 0.2],
  ];
  holes.forEach((c, i) => strokes.push({ id: `hole${i}`, points: noisy(circlePts(c, 0.035, 60), sigma * 0.6, 20 + i) }));
  // A slot: two lines and two half circles, drawn as one closed stroke.
  const slot: Vec[] = [
    ...linePts([-0.15, 0.05], [0.05, 0.05], 30),
    ...arcPts([0.05, 0.1], 0.05, -Math.PI / 2, Math.PI / 2, 24).slice(1),
    ...linePts([0.05, 0.15], [-0.15, 0.15], 30).slice(1),
    ...arcPts([-0.15, 0.1], 0.05, Math.PI / 2, (3 * Math.PI) / 2, 24).slice(1),
  ];
  strokes.push({ id: 'slot', points: noisy(slot, sigma * 0.6, 40) });
  // A rib drawn as separate line strokes forming a triangle gusset.
  strokes.push({ id: 'rib1', points: noisy(linePts([-0.35, -0.25], [-0.05, -0.25], 30), sigma, 50) });
  strokes.push({ id: 'rib2', points: noisy(linePts([-0.05, -0.25], [-0.35, 0.0], 30), sigma, 51) });
  strokes.push({ id: 'rib3', points: noisy(linePts([-0.35, 0.0], [-0.35, -0.25], 30), sigma, 52) });
  return strokes;
}
