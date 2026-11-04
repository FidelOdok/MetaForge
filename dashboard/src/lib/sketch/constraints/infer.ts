/**
 * Constraint inference (SKP-003): rules a person probably meant, read off
 * the fitted primitives. Every proposal is `proposed` with a confidence;
 * nothing is applied until a person accepts it.
 *
 * Rules, each with its own threshold relative to the fit tolerance:
 *
 * - coincident: ends that nearly meet;
 * - horizontal / vertical: lines within a few degrees of an axis;
 * - parallel / perpendicular: lines nearly so, proposed only where not
 *   already implied (two horizontals are not also proposed parallel, and a
 *   direction class is linked once, so accepting everything stays free of
 *   redundant rules);
 * - tangent: line-arc and arc-arc pairs that meet smoothly, and a line just
 *   touching a circle;
 * - equal: lines of nearly the same length, circles and arcs of nearly the
 *   same radius;
 * - concentric: circles and arcs sharing a centre;
 * - symmetric: point pairs mirrored about the sketch's vertical or
 *   horizontal centre line, when at least two pairs make it obvious (a
 *   construction axis is added for it);
 * - dimensions: the length of every line and the radius of every circle and
 *   arc, `source: estimated`, unit unknown. They are read off a drawing and
 *   are never measurements (PR-003, FSG-009).
 *
 * Ids are deterministic (rule plus refs), so the same fit proposes the same
 * ids and a person's accept or reject can be carried across a re-fit.
 */

import {
  arcSweep,
  endpointKeys,
  lineLength,
  pointOf,
  RAD,
  type ArcPrim,
  type CirclePrim,
  type LinePrim,
  type Primitive,
  type SketchConstraint,
  type SketchUnit,
  type Vec,
} from '../model';

export interface InferOptions {
  /** The fit tolerance, sketch units. */
  tolerance: number;
  unit?: SketchUnit;
  /** Degrees off an axis still proposed horizontal or vertical. */
  axisDeg?: number;
  /** Degrees off still proposed parallel or perpendicular. */
  angleDeg?: number;
  /** Relative size difference still proposed equal. */
  equalRel?: number;
}

export interface InferResult {
  constraints: SketchConstraint[];
  /** Construction geometry added for a rule (a symmetry axis). */
  construction: Primitive[];
}

const clamp = (v: number) => Math.max(0.05, Math.min(0.99, Math.round(v * 100) / 100));

function lineAngle(l: LinePrim): number {
  return Math.atan2(l.end[1] - l.start[1], l.end[0] - l.start[0]);
}

/** Smallest angle between two undirected directions, radians in [0, pi/2]. */
function undirectedDiff(a: number, b: number): number {
  let d = Math.abs(a - b) % Math.PI;
  if (d > Math.PI / 2) d = Math.PI - d;
  return d;
}

function unitTangentAt(p: Primitive, key: 'start' | 'end'): Vec | null {
  if (p.type === 'line') {
    const L = lineLength(p) || 1;
    return [(p.end[0] - p.start[0]) / L, (p.end[1] - p.start[1]) / L];
  }
  if (p.type === 'arc') {
    const a = (key === 'start' ? p.start_angle : p.end_angle) * RAD;
    return [-Math.sin(a), Math.cos(a)];
  }
  return null;
}

function constraintId(type: string, refs: readonly string[]): string {
  return `c-${type}-${refs.join('~')}`;
}

export function inferConstraints(primitives: readonly Primitive[], opts: InferOptions): InferResult {
  const tol = opts.tolerance;
  const axisRad = (opts.axisDeg ?? 6) * RAD;
  const angleRad = (opts.angleDeg ?? 5) * RAD;
  const equalRel = opts.equalRel ?? 0.07;
  const unit = opts.unit ?? 'unknown';
  const out: SketchConstraint[] = [];
  const add = (c: Omit<SketchConstraint, 'id' | 'status'>) => {
    out.push({ id: constraintId(c.type, c.refs), status: 'proposed', ...c });
  };
  const real = primitives.filter((p) => !p.construction);
  const lines = real.filter((p): p is LinePrim => p.type === 'line');
  const rounds = real.filter((p): p is CirclePrim | ArcPrim => p.type === 'circle' || p.type === 'arc');

  // ── Coincident ends: cluster ends within the gap, link each to the cluster's first.
  const gap = 2 * tol;
  const ends: { ref: string; pt: Vec; prim: Primitive; key: 'start' | 'end' }[] = [];
  for (const p of real)
    for (const key of endpointKeys(p)) {
      const pt = pointOf(p, key);
      if (pt) ends.push({ ref: `${p.id}.${key}`, pt, prim: p, key: key as 'start' | 'end' });
    }
  const used = new Set<number>();
  const meetings: [number, number][] = [];
  for (let i = 0; i < ends.length; i += 1) {
    if (used.has(i)) continue;
    for (let j = i + 1; j < ends.length; j += 1) {
      if (used.has(j)) continue;
      const a = ends[i]!;
      const b = ends[j]!;
      if (a.prim.id === b.prim.id && a.prim.type === 'line') continue;
      const d = Math.hypot(a.pt[0] - b.pt[0], a.pt[1] - b.pt[1]);
      if (d > gap) continue;
      used.add(j);
      meetings.push([i, j]);
      add({ type: 'coincident', refs: [a.ref, b.ref], source: 'inferred', confidence: clamp(1 - (0.5 * d) / gap) });
    }
  }

  // ── Horizontal / vertical.
  const axisOf = new Map<string, 'h' | 'v'>();
  for (const l of lines) {
    if (lineLength(l) < 2 * tol) continue;
    const a = lineAngle(l);
    const offH = undirectedDiff(a, 0);
    const offV = undirectedDiff(a, Math.PI / 2);
    if (offH <= axisRad) {
      axisOf.set(l.id, 'h');
      add({ type: 'horizontal', refs: [l.id], source: 'inferred', confidence: clamp(1 - offH / axisRad / 2) });
    } else if (offV <= axisRad) {
      axisOf.set(l.id, 'v');
      add({ type: 'vertical', refs: [l.id], source: 'inferred', confidence: clamp(1 - offV / axisRad / 2) });
    }
  }

  // ── Parallel / perpendicular, linking direction classes once.
  const free = lines.filter((l) => !axisOf.has(l.id) && lineLength(l) >= 2 * tol);
  const classRep: LinePrim[] = [];
  for (const l of free) {
    const a = lineAngle(l);
    const match = classRep.findIndex((rep) => undirectedDiff(a, lineAngle(rep)) <= angleRad);
    if (match >= 0) {
      const rep = classRep[match]!;
      const off = undirectedDiff(a, lineAngle(rep));
      add({ type: 'parallel', refs: [rep.id, l.id], source: 'inferred', confidence: clamp(1 - off / angleRad / 2) });
    } else {
      classRep.push(l);
    }
  }
  const linkedClasses = new Set<number>();
  for (let i = 0; i < classRep.length; i += 1)
    for (let j = i + 1; j < classRep.length; j += 1) {
      const a = classRep[i]!;
      const b = classRep[j]!;
      const off = Math.abs(undirectedDiff(lineAngle(a), lineAngle(b)) - Math.PI / 2);
      if (off > angleRad || linkedClasses.has(j)) continue;
      linkedClasses.add(j);
      add({ type: 'perpendicular', refs: [a.id, b.id], source: 'inferred', confidence: clamp(1 - off / angleRad / 2) });
    }

  // ── Tangent where a line and an arc, or two arcs, meet smoothly.
  for (const [i, j] of meetings) {
    const a = ends[i]!;
    const b = ends[j]!;
    if (a.prim.type !== 'arc' && b.prim.type !== 'arc') continue;
    if (a.prim.type !== 'line' && a.prim.type !== 'arc') continue;
    if (b.prim.type !== 'line' && b.prim.type !== 'arc') continue;
    const ta = unitTangentAt(a.prim, a.key);
    const tb = unitTangentAt(b.prim, b.key);
    if (!ta || !tb) continue;
    const off = undirectedDiff(Math.atan2(ta[1], ta[0]), Math.atan2(tb[1], tb[0]));
    if (off > 12 * RAD) continue;
    const conf = clamp(1 - off / (12 * RAD) / 2);
    if (a.prim.type === 'arc' && b.prim.type === 'arc') {
      const d = Math.hypot(a.prim.center[0] - b.prim.center[0], a.prim.center[1] - b.prim.center[1]);
      const ext = Math.abs(d - (a.prim.radius + b.prim.radius));
      const int = Math.abs(d - Math.abs(a.prim.radius - b.prim.radius));
      add({ type: 'tangent', refs: [a.prim.id, b.prim.id], source: 'inferred', confidence: conf, variant: ext <= int ? 'external' : 'internal' });
    } else {
      const [ln, arc] = a.prim.type === 'line' ? [a.prim, b.prim] : [b.prim, a.prim];
      add({ type: 'tangent', refs: [ln.id, arc.id], source: 'inferred', confidence: conf });
    }
  }
  // A line just touching a circle (no shared end).
  for (const l of lines)
    for (const c of rounds) {
      if (c.type !== 'circle') continue;
      const L = lineLength(l);
      if (L < 2 * tol) continue;
      const dx = (l.end[0] - l.start[0]) / L;
      const dy = (l.end[1] - l.start[1]) / L;
      const t = (c.center[0] - l.start[0]) * dx + (c.center[1] - l.start[1]) * dy;
      if (t < 0 || t > L) continue;
      const dist = Math.abs((c.center[1] - l.start[1]) * dx - (c.center[0] - l.start[0]) * dy);
      const off = Math.abs(dist - c.radius);
      if (off > tol) continue;
      add({ type: 'tangent', refs: [l.id, c.id], source: 'inferred', confidence: clamp(1 - off / tol / 2) });
    }

  // ── Equal sizes, chained within a group.
  const groupEqual = <T extends Primitive>(items: T[], size: (p: T) => number) => {
    const sorted = [...items].filter((p) => size(p) >= 2 * tol).sort((a, b) => size(a) - size(b));
    let head: T | null = null;
    for (const p of sorted) {
      if (head && (size(p) - size(head)) / size(head) <= equalRel) {
        add({ type: 'equal', refs: [head.id, p.id], source: 'inferred', confidence: clamp(1 - (size(p) - size(head)) / size(head) / equalRel / 2) });
      } else {
        head = p;
      }
    }
  };
  groupEqual(lines, lineLength);
  groupEqual(rounds, (p) => p.radius);

  // ── Concentric.
  for (let i = 0; i < rounds.length; i += 1)
    for (let j = i + 1; j < rounds.length; j += 1) {
      const a = rounds[i]!;
      const b = rounds[j]!;
      const d = Math.hypot(a.center[0] - b.center[0], a.center[1] - b.center[1]);
      if (d > gap) continue;
      add({ type: 'concentric', refs: [a.id, b.id], source: 'inferred', confidence: clamp(1 - (0.5 * d) / gap) });
    }

  // ── Symmetry about the vertical or horizontal centre line.
  const construction: Primitive[] = [];
  const points: { ref: string; pt: Vec }[] = [];
  ends.forEach((e, i) => {
    // One point per meeting: the cluster's first end.
    if (meetings.some(([, j]) => j === i)) return;
    points.push({ ref: e.ref, pt: e.pt });
  });
  for (const c of rounds) points.push({ ref: `${c.id}.center`, pt: c.center });
  if (points.length >= 4) {
    const xs = points.map((p) => p.pt[0]);
    const ys = points.map((p) => p.pt[1]);
    const box = { x0: Math.min(...xs), x1: Math.max(...xs), y0: Math.min(...ys), y1: Math.max(...ys) };
    for (const axis of ['v', 'h'] as const) {
      const mid = axis === 'v' ? (box.x0 + box.x1) / 2 : (box.y0 + box.y1) / 2;
      const along = axis === 'v' ? 1 : 0;
      const across = axis === 'v' ? 0 : 1;
      const pairs: [string, string, number][] = [];
      const taken = new Set<number>();
      for (let i = 0; i < points.length; i += 1) {
        if (taken.has(i)) continue;
        const a = points[i]!;
        if (Math.abs(a.pt[across] - mid) < gap) continue; // on the axis
        for (let j = i + 1; j < points.length; j += 1) {
          if (taken.has(j)) continue;
          const b = points[j]!;
          const offAcross = Math.abs(a.pt[across] + b.pt[across] - 2 * mid);
          const offAlong = Math.abs(a.pt[along] - b.pt[along]);
          if (offAcross <= gap && offAlong <= gap) {
            taken.add(i);
            taken.add(j);
            pairs.push([a.ref, b.ref, Math.max(offAcross, offAlong)]);
            break;
          }
        }
      }
      if (pairs.length < 2) continue;
      const id = axis === 'v' ? 'axis-v' : 'axis-h';
      const pad = 0.08 * Math.max(box.x1 - box.x0, box.y1 - box.y0);
      const axisLine: LinePrim = {
        id,
        type: 'line',
        start: axis === 'v' ? [mid, box.y0 - pad] : [box.x0 - pad, mid],
        end: axis === 'v' ? [mid, box.y1 + pad] : [box.x1 + pad, mid],
        construction: true,
        estimated: true,
        confidence: 0.9,
        residual: 0,
        source_stroke_ids: [],
        source_ranges: [],
      };
      construction.push(axisLine);
      add({ type: axis === 'v' ? 'vertical' : 'horizontal', refs: [id], source: 'inferred', confidence: 0.9 });
      for (const [a, b, off] of pairs) {
        add({ type: 'symmetric', refs: [a, b, id], source: 'inferred', confidence: clamp(1 - (0.5 * off) / gap) });
      }
    }
  }

  // ── Dimensions: estimated, unit unknown until one is confirmed.
  for (const l of lines) {
    add({ type: 'length', refs: [l.id], value: lineLength(l), unit, source: 'estimated', confidence: l.confidence });
  }
  for (const c of rounds) {
    if (c.type === 'arc' && arcSweep(c) * RAD * c.radius < 2 * tol) continue;
    add({ type: 'radius', refs: [c.id], value: c.radius, unit, source: 'estimated', confidence: c.confidence });
  }

  // Deduplicate ids (a pair can match two rules the same way).
  const byId = new Map<string, SketchConstraint>();
  for (const c of out) if (!byId.has(c.id)) byId.set(c.id, c);
  return { constraints: [...byId.values()], construction };
}
