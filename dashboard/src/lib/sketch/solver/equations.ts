/**
 * Constraint equations over packed primitive parameters.
 *
 * Every primitive's parameters sit in one vector:
 *
 * | primitive | parameters |
 * |-----------|------------|
 * | line      | x1, y1, x2, y2 |
 * | circle    | cx, cy, r |
 * | arc       | cx, cy, r, start angle, end angle (radians) |
 * | ellipse   | cx, cy, major, minor, rotation (radians) |
 * | spline    | x, y of each through-point |
 *
 * Each accepted constraint contributes one or two residuals that are zero
 * when it holds. Angle-type residuals (parallel, perpendicular) are scaled
 * by the sketch's characteristic length so every residual is a length, and
 * one tolerance fits all. The gateway re-checks accepted constraints with a
 * mirror of these equations (`twin_core/design_ir/sketch_check.py`), so the
 * two must change together.
 */

import { DEG, RAD, parseRef, type Primitive, type PointKey, type SketchConstraint } from '../model';

export interface Slot {
  prim: Primitive;
  offset: number;
  size: number;
}

export interface Layout {
  slots: Map<string, Slot>;
  size: number;
  /** Characteristic length (the sketch extent) used to scale angle residuals. */
  scale: number;
}

function sizeOf(p: Primitive): number {
  switch (p.type) {
    case 'line':
      return 4;
    case 'circle':
      return 3;
    case 'arc':
    case 'ellipse':
      return 5;
    case 'spline':
      return 2 * p.points.length;
  }
}

export function layoutOf(primitives: readonly Primitive[], scale: number): Layout {
  const slots = new Map<string, Slot>();
  let offset = 0;
  for (const prim of primitives) {
    const size = sizeOf(prim);
    slots.set(prim.id, { prim, offset, size });
    offset += size;
  }
  return { slots, size: offset, scale: scale > 0 ? scale : 1 };
}

export function pack(layout: Layout): Float64Array {
  const x = new Float64Array(layout.size);
  for (const { prim, offset } of layout.slots.values()) {
    const put = (vals: number[]) => vals.forEach((v, i) => (x[offset + i] = v));
    switch (prim.type) {
      case 'line':
        put([prim.start[0], prim.start[1], prim.end[0], prim.end[1]]);
        break;
      case 'circle':
        put([prim.center[0], prim.center[1], prim.radius]);
        break;
      case 'arc': {
        const a0 = prim.start_angle * RAD;
        let a1 = prim.end_angle * RAD;
        // Keep the end ahead of the start so the sweep is continuous while solving.
        while (a1 <= a0) a1 += 2 * Math.PI;
        put([prim.center[0], prim.center[1], prim.radius, a0, a1]);
        break;
      }
      case 'ellipse':
        put([prim.center[0], prim.center[1], prim.major_radius, prim.minor_radius, prim.rotation * RAD]);
        break;
      case 'spline':
        put(prim.points.flatMap((p) => [p[0], p[1]]));
        break;
    }
  }
  return x;
}

const norm360 = (d: number): number => {
  let v = d % 360;
  if (v < 0) v += 360;
  return v;
};

/** New primitives carrying the solved parameters. */
export function unpack(layout: Layout, x: Float64Array): Primitive[] {
  const out: Primitive[] = [];
  for (const { prim, offset: o } of layout.slots.values()) {
    const v = (i: number) => x[o + i]!;
    switch (prim.type) {
      case 'line':
        out.push({ ...prim, start: [v(0), v(1)], end: [v(2), v(3)] });
        break;
      case 'circle':
        out.push({ ...prim, center: [v(0), v(1)], radius: Math.abs(v(2)) });
        break;
      case 'arc':
        out.push({
          ...prim,
          center: [v(0), v(1)],
          radius: Math.abs(v(2)),
          start_angle: norm360(v(3) * DEG),
          end_angle: norm360(v(4) * DEG),
        });
        break;
      case 'ellipse': {
        let a = Math.abs(v(2));
        let b = Math.abs(v(3));
        let rot = v(4) * DEG;
        if (b > a) {
          [a, b] = [b, a];
          rot += 90;
        }
        out.push({ ...prim, center: [v(0), v(1)], major_radius: a, minor_radius: b, rotation: rot });
        break;
      }
      case 'spline':
        out.push({ ...prim, points: prim.points.map((_, i) => [v(2 * i), v(2 * i + 1)] as [number, number]) });
        break;
    }
  }
  return out;
}

type PointFn = (x: Float64Array) => [number, number];

function pointFn(slot: Slot, key: PointKey | null): PointFn | null {
  const o = slot.offset;
  const p = slot.prim;
  switch (p.type) {
    case 'line':
      if (key === 'start') return (x) => [x[o]!, x[o + 1]!];
      if (key === 'end') return (x) => [x[o + 2]!, x[o + 3]!];
      return null;
    case 'arc':
      if (key === 'center') return (x) => [x[o]!, x[o + 1]!];
      if (key === 'start' || key === 'end') {
        const k = key === 'start' ? 3 : 4;
        return (x) => [x[o]! + x[o + 2]! * Math.cos(x[o + k]!), x[o + 1]! + x[o + 2]! * Math.sin(x[o + k]!)];
      }
      return null;
    case 'circle':
    case 'ellipse':
      return key === 'center' ? (x) => [x[o]!, x[o + 1]!] : null;
    case 'spline': {
      if (p.closed) return null;
      const last = 2 * (p.points.length - 1);
      if (key === 'start') return (x) => [x[o]!, x[o + 1]!];
      if (key === 'end') return (x) => [x[o + last]!, x[o + last + 1]!];
      return null;
    }
  }
}

export type Equation = (x: Float64Array) => number[];

export class ConstraintError extends Error {}

function slotOf(layout: Layout, ref: string | undefined): Slot {
  if (!ref) throw new ConstraintError('a reference is missing');
  const { id } = parseRef(ref);
  const slot = layout.slots.get(id);
  if (!slot) throw new ConstraintError(`it refers to ${id}, which is not in the sketch`);
  return slot;
}

function point(layout: Layout, ref: string | undefined): PointFn {
  const slot = slotOf(layout, ref);
  const { key } = parseRef(ref!);
  const fn = pointFn(slot, key);
  if (!fn) throw new ConstraintError(`${ref} is not a point of a ${slot.prim.type}`);
  return fn;
}

function line(layout: Layout, ref: string | undefined): Slot {
  const slot = slotOf(layout, ref);
  if (slot.prim.type !== 'line') throw new ConstraintError(`${ref} is a ${slot.prim.type}, not a line`);
  return slot;
}

function round(layout: Layout, ref: string | undefined): Slot {
  const slot = slotOf(layout, ref);
  if (slot.prim.type !== 'circle' && slot.prim.type !== 'arc') {
    throw new ConstraintError(`${ref} is a ${slot.prim.type}, not a circle or arc`);
  }
  return slot;
}

const dir = (s: Slot, x: Float64Array): [number, number] => [
  x[s.offset + 2]! - x[s.offset]!,
  x[s.offset + 3]! - x[s.offset + 1]!,
];

/** The residual function for one constraint. Throws `ConstraintError` when it cannot apply. */
export function equationFor(c: SketchConstraint, layout: Layout): Equation {
  const L = layout.scale;
  const r = c.refs;
  switch (c.type) {
    case 'coincident': {
      const a = point(layout, r[0]);
      const b = point(layout, r[1]);
      return (x) => {
        const pa = a(x);
        const pb = b(x);
        return [pa[0] - pb[0], pa[1] - pb[1]];
      };
    }
    case 'horizontal':
    case 'vertical': {
      const s = line(layout, r[0]);
      const k = c.type === 'horizontal' ? 1 : 0;
      return (x) => [x[s.offset + 2 + k]! - x[s.offset + k]!];
    }
    case 'parallel':
    case 'perpendicular': {
      const a = line(layout, r[0]);
      const b = line(layout, r[1]);
      return (x) => {
        const d1 = dir(a, x);
        const d2 = dir(b, x);
        const n = Math.hypot(d1[0], d1[1]) * Math.hypot(d2[0], d2[1]) || 1e-12;
        const v = c.type === 'parallel' ? d1[0] * d2[1] - d1[1] * d2[0] : d1[0] * d2[0] + d1[1] * d2[1];
        return [(v / n) * L];
      };
    }
    case 'tangent': {
      const s0 = slotOf(layout, r[0]);
      const s1 = slotOf(layout, r[1]);
      if (s0.prim.type === 'line' || s1.prim.type === 'line') {
        const ln = s0.prim.type === 'line' ? s0 : s1;
        const rd = round(layout, s0.prim.type === 'line' ? r[1] : r[0]);
        return (x) => {
          const d = dir(ln, x);
          const len = Math.hypot(d[0], d[1]) || 1e-12;
          const cx = x[rd.offset]! - x[ln.offset]!;
          const cy = x[rd.offset + 1]! - x[ln.offset + 1]!;
          return [Math.abs(d[0] * cy - d[1] * cx) / len - Math.abs(x[rd.offset + 2]!)];
        };
      }
      const a = round(layout, r[0]);
      const b = round(layout, r[1]);
      const internal = c.variant === 'internal';
      return (x) => {
        const dist = Math.hypot(x[a.offset]! - x[b.offset]!, x[a.offset + 1]! - x[b.offset + 1]!);
        const ra = Math.abs(x[a.offset + 2]!);
        const rb = Math.abs(x[b.offset + 2]!);
        return [internal ? dist - Math.abs(ra - rb) : dist - (ra + rb)];
      };
    }
    case 'equal': {
      const s0 = slotOf(layout, r[0]);
      const s1 = slotOf(layout, r[1]);
      if (s0.prim.type === 'line' && s1.prim.type === 'line') {
        return (x) => {
          const d1 = dir(s0, x);
          const d2 = dir(s1, x);
          return [Math.hypot(d1[0], d1[1]) - Math.hypot(d2[0], d2[1])];
        };
      }
      const a = round(layout, r[0]);
      const b = round(layout, r[1]);
      return (x) => [Math.abs(x[a.offset + 2]!) - Math.abs(x[b.offset + 2]!)];
    }
    case 'concentric': {
      const a = point(layout, `${parseRef(r[0] ?? '').id}.center`);
      const b = point(layout, `${parseRef(r[1] ?? '').id}.center`);
      return (x) => {
        const pa = a(x);
        const pb = b(x);
        return [pa[0] - pb[0], pa[1] - pb[1]];
      };
    }
    case 'symmetric': {
      const a = point(layout, r[0]);
      const b = point(layout, r[1]);
      const axis = line(layout, r[2]);
      return (x) => {
        const pa = a(x);
        const pb = b(x);
        const d = dir(axis, x);
        const len = Math.hypot(d[0], d[1]) || 1e-12;
        const mx = (pa[0] + pb[0]) / 2 - x[axis.offset]!;
        const my = (pa[1] + pb[1]) / 2 - x[axis.offset + 1]!;
        return [(d[0] * my - d[1] * mx) / len, ((pb[0] - pa[0]) * d[0] + (pb[1] - pa[1]) * d[1]) / len];
      };
    }
    case 'radius': {
      const s = round(layout, r[0]);
      const v = requireValue(c);
      return (x) => [Math.abs(x[s.offset + 2]!) - v];
    }
    case 'length': {
      const s = line(layout, r[0]);
      const v = requireValue(c);
      return (x) => {
        const d = dir(s, x);
        return [Math.hypot(d[0], d[1]) - v];
      };
    }
  }
}

function requireValue(c: SketchConstraint): number {
  if (typeof c.value !== 'number' || !Number.isFinite(c.value) || c.value <= 0) {
    throw new ConstraintError('a dimension needs a positive value');
  }
  return c.value;
}

/** Rigid motions of the whole sketch as parameter-space directions: move u, move v, turn. */
export function rigidMotions(layout: Layout, x: Float64Array): Float64Array[] {
  const n = layout.size;
  const tx = new Float64Array(n);
  const ty = new Float64Array(n);
  const rot = new Float64Array(n);
  // Turn about the sketch's centroid of points, which keeps the vectors well scaled.
  let sx = 0;
  let sy = 0;
  let count = 0;
  const pts: [number, number][] = [];
  for (const { prim, offset: o } of layout.slots.values()) {
    const addPt = (i: number) => {
      pts.push([o + i, o + i + 1]);
      sx += x[o + i]!;
      sy += x[o + i + 1]!;
      count += 1;
    };
    if (prim.type === 'line') {
      addPt(0);
      addPt(2);
    } else if (prim.type === 'spline') {
      for (let i = 0; i < prim.points.length; i += 1) addPt(2 * i);
    } else {
      addPt(0);
      // Arc angles and ellipse rotation turn with the sketch.
      if (prim.type === 'arc') {
        rot[o + 3] = 1;
        rot[o + 4] = 1;
      }
      if (prim.type === 'ellipse') rot[o + 4] = 1;
    }
  }
  const cx = count ? sx / count : 0;
  const cy = count ? sy / count : 0;
  for (const [ix, iy] of pts) {
    tx[ix] = 1;
    ty[iy] = 1;
    rot[ix] = -(x[iy]! - cy);
    rot[iy] = x[ix]! - cx;
  }
  return [tx, ty, rot];
}
