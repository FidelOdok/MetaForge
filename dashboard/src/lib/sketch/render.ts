/**
 * Drawing helpers for fitted primitives: a polyline per primitive, and the
 * point a constraint's badge sits on. Pure, in the sketch's model space.
 */

import { sampleSpline } from './fit/primitives';
import { RAD, arcSweep, parseRef, pointOf, type Primitive, type SketchConstraint, type Vec } from './model';

/** A polyline that draws the primitive. */
export function primitivePolyline(p: Primitive, segments = 48): Vec[] {
  switch (p.type) {
    case 'line':
      return [p.start, p.end];
    case 'circle':
      return Array.from({ length: segments + 1 }, (_, i) => {
        const a = (2 * Math.PI * i) / segments;
        return [p.center[0] + p.radius * Math.cos(a), p.center[1] + p.radius * Math.sin(a)] as Vec;
      });
    case 'arc': {
      const sweep = arcSweep(p) * RAD;
      const a0 = p.start_angle * RAD;
      const n = Math.max(4, Math.round((segments * sweep) / (2 * Math.PI)));
      return Array.from({ length: n + 1 }, (_, i) => {
        const a = a0 + (sweep * i) / n;
        return [p.center[0] + p.radius * Math.cos(a), p.center[1] + p.radius * Math.sin(a)] as Vec;
      });
    }
    case 'ellipse': {
      const r = p.rotation * RAD;
      return Array.from({ length: segments + 1 }, (_, i) => {
        const t = (2 * Math.PI * i) / segments;
        const x = p.major_radius * Math.cos(t);
        const y = p.minor_radius * Math.sin(t);
        return [p.center[0] + x * Math.cos(r) - y * Math.sin(r), p.center[1] + x * Math.sin(r) + y * Math.cos(r)] as Vec;
      });
    }
    case 'spline':
      return sampleSpline(p.points, p.closed, 8);
  }
}

/** Where on a primitive a label for it reads best: a line's middle, the top of a round. */
export function primitiveAnchor(p: Primitive): Vec {
  switch (p.type) {
    case 'line':
      return [(p.start[0] + p.end[0]) / 2, (p.start[1] + p.end[1]) / 2];
    case 'circle':
      return [p.center[0], p.center[1] - p.radius];
    case 'arc': {
      const a = (p.start_angle + arcSweep(p) / 2) * RAD;
      return [p.center[0] + p.radius * Math.cos(a), p.center[1] + p.radius * Math.sin(a)];
    }
    case 'ellipse':
      return [p.center[0], p.center[1] - p.minor_radius];
    case 'spline': {
      const mid = p.points[Math.floor(p.points.length / 2)];
      return mid ?? [0, 0];
    }
  }
}

/** The point a constraint's badge is drawn at. */
export function constraintAnchor(c: SketchConstraint, primitives: readonly Primitive[]): Vec | null {
  const ref = c.refs[c.type === 'symmetric' ? 0 : c.refs.length > 1 ? 1 : 0];
  if (!ref) return null;
  const { id, key } = parseRef(ref);
  const p = primitives.find((q) => q.id === id);
  if (!p) return null;
  if (key) return pointOf(p, key) ?? primitiveAnchor(p);
  return primitiveAnchor(p);
}

/** A short glyph per rule, for its badge. */
export const CONSTRAINT_GLYPH: Record<SketchConstraint['type'], string> = {
  coincident: '•',
  horizontal: 'H',
  vertical: 'V',
  parallel: '∥',
  perpendicular: '⊥',
  tangent: 'T',
  equal: '=',
  concentric: '◎',
  symmetric: '⇆',
  radius: 'R',
  length: 'L',
};
