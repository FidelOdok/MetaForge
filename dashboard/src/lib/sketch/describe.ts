/**
 * Plain-language names for primitives and constraints, so the compiler's
 * reports read as sentences ("Line 2 and Line 4 parallel") rather than ids.
 */

import { parseRef, type Primitive, type SketchConstraint } from './model';

const TYPE_NAME: Record<Primitive['type'], string> = {
  line: 'Line',
  circle: 'Circle',
  arc: 'Arc',
  ellipse: 'Ellipse',
  spline: 'Curve',
};

/** `Line 1`, `Arc 2`, ... numbered per type in sketch order; construction lines are `Axis n`. */
export function primitiveLabels(primitives: readonly Primitive[]): Map<string, string> {
  const counts = new Map<string, number>();
  const out = new Map<string, string>();
  for (const p of primitives) {
    const name = p.construction ? 'Axis' : TYPE_NAME[p.type];
    const n = (counts.get(name) ?? 0) + 1;
    counts.set(name, n);
    out.set(p.id, `${name} ${n}`);
  }
  return out;
}

const POINT_NAME: Record<string, string> = { start: 'start', end: 'end', center: 'centre' };

function refLabel(ref: string, labels: Map<string, string>): string {
  const { id, key } = parseRef(ref);
  const name = labels.get(id) ?? id;
  return key && key !== 'center' ? `${name} ${POINT_NAME[key]}` : key === 'center' ? `${name} centre` : name;
}

export function formatValue(v: number | null | undefined): string {
  if (typeof v !== 'number' || !Number.isFinite(v)) return '?';
  const abs = Math.abs(v);
  const digits = abs >= 100 ? 1 : abs >= 1 ? 2 : 3;
  return String(Math.round(v * 10 ** digits) / 10 ** digits);
}

export function constraintLabel(c: SketchConstraint, labels: Map<string, string>): string {
  const names = c.refs.map((r) => refLabel(r, labels));
  const [a, b, axis] = names;
  switch (c.type) {
    case 'coincident':
      return `${a} meets ${b}`;
    case 'horizontal':
      return `${a} horizontal`;
    case 'vertical':
      return `${a} vertical`;
    case 'parallel':
      return `${a} parallel to ${b}`;
    case 'perpendicular':
      return `${a} square to ${b}`;
    case 'tangent':
      return `${a} tangent to ${b}`;
    case 'equal':
      return `${a} same size as ${b}`;
    case 'concentric':
      return `${a} same centre as ${b}`;
    case 'symmetric':
      return `${a} mirrors ${b} about ${axis}`;
    case 'radius':
      return `Radius of ${a} = ${formatValue(c.value)}`;
    case 'length':
      return `Length of ${a} = ${formatValue(c.value)}`;
  }
}

/** `a`, `a and b`, `a, b and c`. */
export function joinNames(names: readonly string[]): string {
  if (names.length <= 1) return names[0] ?? '';
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}
