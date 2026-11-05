import type { SketchConstraint } from '../../sketch/model';
import type { PreviewDefinition } from '../definition';
import { definition } from './fixtures';

type P = [number, number];

function rule(id: string, type: SketchConstraint['type'], refs: string[], value?: number): SketchConstraint {
  return {
    id,
    type,
    refs,
    status: 'accepted',
    source: value === undefined ? 'inferred' : 'human_confirmed',
    confidence: 1,
    ...(value === undefined ? {} : { value, unit: 'mm' as const }),
  };
}

/**
 * A 40 x 20 plate drawn as four joined lines, square corners, its top
 * length (L1) and right side (L2) confirmed: as the gateway returns it, the
 * plane elements v up and the solver sketch in sketch space (y down).
 * `bottomLength` adds a third, accepted length on the bottom (L3).
 */
export function boxDefinition(opts: { bottomLength?: number } = {}): PreviewDefinition {
  const sketch: { id: string; start: P; end: P }[] = [
    { id: 'top', start: [0, 0], end: [40, 0] },
    { id: 'right', start: [40, 0], end: [40, 20] },
    { id: 'bottom', start: [40, 20], end: [0, 20] },
    { id: 'left', start: [0, 20], end: [0, 0] },
  ];
  const flip = (p: P): P => [p[0], -p[1]];
  const constraints = [
    rule('k1', 'coincident', ['top.end', 'right.start']),
    rule('k2', 'coincident', ['right.end', 'bottom.start']),
    rule('k3', 'coincident', ['bottom.end', 'left.start']),
    rule('k4', 'coincident', ['left.end', 'top.start']),
    rule('h1', 'horizontal', ['top']),
    rule('h2', 'horizontal', ['bottom']),
    rule('v1', 'vertical', ['right']),
    rule('v2', 'vertical', ['left']),
    rule('L1', 'length', ['top'], 40),
    rule('L2', 'length', ['right'], 20),
    ...(opts.bottomLength ? [rule('L3', 'length', ['bottom'], opts.bottomLength)] : []),
  ];
  const lengthDim = (cid: string, id: string, value: number) => {
    const line = sketch.find((l) => l.id === id)!;
    return { constraint_id: cid, type: 'length' as const, primitive_id: id, value, unit: 'mm', source: 'human_confirmed', start: flip(line.start), end: flip(line.end) };
  };
  return definition({
    elements: sketch.map((l) => ({ type: 'line' as const, id: l.id, start: flip(l.start), end: flip(l.end) })),
    sketch_key: 'PSK-PLATE',
    sketch_ref: 'PSK-PLATE@1',
    dimensions: [
      lengthDim('L1', 'top', 40),
      lengthDim('L2', 'right', 20),
      ...(opts.bottomLength ? [lengthDim('L3', 'bottom', opts.bottomLength)] : []),
    ],
    handles: [
      { kind: 'extrude_depth', parameter: 'depth', axis: 'normal' },
      { kind: 'length', constraint_id: 'L1', primitive_id: 'top' },
      { kind: 'length', constraint_id: 'L2', primitive_id: 'right' },
    ],
    solver_sketch: {
      elements: sketch.map((l) => ({ type: 'line', id: l.id, start: l.start, end: l.end })),
      constraints,
    },
  });
}
