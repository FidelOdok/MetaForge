import type { FieldQuantity, SimFieldPayload } from '../../types/simulationField';

/**
 * Pure helpers behind the FORGE-532 field viewer: colour mapping, legend
 * range, deformation and the hover probe. Kept out of the R3F component so
 * they are testable without WebGL.
 */

/** Perceptually uniform sequential map (matplotlib "plasma" stops). Not a
 * rainbow: lightness rises monotonically from low to high, so a reader (or
 * a colour-blind reader, or a greyscale print) can still rank two regions,
 * and the low end stays visible on the dark viewer background. */
export const COLOR_STOPS: Array<[number, [number, number, number]]> = [
  [0.0, [13, 8, 135]],
  [0.125, [76, 2, 161]],
  [0.25, [126, 3, 168]],
  [0.375, [169, 35, 149]],
  [0.5, [204, 71, 120]],
  [0.625, [229, 107, 93]],
  [0.75, [248, 149, 64]],
  [0.875, [253, 197, 39]],
  [1.0, [240, 249, 33]],
];

export interface Range {
  min: number;
  max: number;
}

/** Position of `value` in `range`, clamped to [0, 1]. A flat field (min ==
 * max) maps to 0 rather than dividing by zero. */
export function normalize(value: number, range: Range): number {
  const span = range.max - range.min;
  if (!Number.isFinite(value) || !(span > 0)) return 0;
  return Math.min(1, Math.max(0, (value - range.min) / span));
}

/** Colour of a normalised value, as 0..1 RGB. */
export function colorAt(t: number): [number, number, number] {
  const x = Math.min(1, Math.max(0, Number.isFinite(t) ? t : 0));
  for (let i = 1; i < COLOR_STOPS.length; i++) {
    const [t1, c1] = COLOR_STOPS[i]!;
    if (x <= t1) {
      const [t0, c0] = COLOR_STOPS[i - 1]!;
      const f = (x - t0) / (t1 - t0);
      return [
        (c0[0] + (c1[0] - c0[0]) * f) / 255,
        (c0[1] + (c1[1] - c0[1]) * f) / 255,
        (c0[2] + (c1[2] - c0[2]) * f) / 255,
      ];
    }
  }
  const last = COLOR_STOPS[COLOR_STOPS.length - 1]![1];
  return [last[0] / 255, last[1] / 255, last[2] / 255];
}

/** Flat RGB array, one colour per vertex. */
export function vertexColors(values: number[], range: Range): Float32Array {
  const out = new Float32Array(values.length * 3);
  values.forEach((v, i) => {
    const [r, g, b] = colorAt(normalize(v, range));
    out[i * 3] = r;
    out[i * 3 + 1] = g;
    out[i * 3 + 2] = b;
  });
  return out;
}

/** CSS linear-gradient for the legend bar, low at the bottom. */
export function legendGradient(): string {
  const stops = COLOR_STOPS.map(([t, [r, g, b]]) => `rgb(${r}, ${g}, ${b}) ${(t * 100).toFixed(1)}%`);
  return `linear-gradient(to top, ${stops.join(', ')})`;
}

/** Quantities the payload carries, in a stable display order. */
export function availableQuantities(payload: SimFieldPayload): FieldQuantity[] {
  const order: FieldQuantity[] = ['von_mises', 'displacement_magnitude', 'temperature'];
  return order.filter((q) => payload.fields[q] !== undefined);
}

/** The default quantity: stress for a structural run, temperature for thermal. */
export function defaultQuantity(payload: SimFieldPayload): FieldQuantity | null {
  return availableQuantities(payload)[0] ?? null;
}

/** Legend range of one quantity, from the full-field min/max the payload
 * records (not recomputed from the possibly decimated surface values). */
export function fieldRange(payload: SimFieldPayload, quantity: FieldQuantity): Range | null {
  const field = payload.fields[quantity];
  if (!field) return null;
  return { min: field.min, max: field.max };
}

/** One range covering both payloads, so two results compared side by side
 * share a colour scale: the same colour means the same value in both. */
export function sharedRange(
  a: SimFieldPayload,
  b: SimFieldPayload,
  quantity: FieldQuantity,
): Range | null {
  const ra = fieldRange(a, quantity);
  const rb = fieldRange(b, quantity);
  if (!ra || !rb) return ra ?? rb;
  return { min: Math.min(ra.min, rb.min), max: Math.max(ra.max, rb.max) };
}

function bboxDiagonal(payload: SimFieldPayload): number {
  const { min, max } = payload.bbox;
  return Math.hypot(max[0] - min[0], max[1] - min[1], max[2] - min[2]);
}

/** Largest displacement magnitude on the drawn surface, in mm. */
export function maxDisplacement(payload: SimFieldPayload): number {
  const d = payload.displacement;
  if (!d) return 0;
  let best = 0;
  for (let i = 0; i < d.length; i += 3) {
    best = Math.max(best, Math.hypot(d[i] ?? 0, d[i + 1] ?? 0, d[i + 2] ?? 0));
  }
  return best;
}

/** A deformation scale that makes the largest deflection about 5% of the
 * part's size: real FEA deflections are usually invisible at true scale.
 * Never below 1 (true scale) for a part that already deflects visibly. */
export function autoDeformScale(payload: SimFieldPayload): number {
  const peak = maxDisplacement(payload);
  if (!(peak > 0)) return 0;
  const scale = (0.05 * bboxDiagonal(payload)) / peak;
  return Math.max(1, roundScale(scale));
}

/** Round a scale factor to 2 significant digits, for a readable slider. */
export function roundScale(scale: number): number {
  if (!(scale > 0)) return 0;
  const magnitude = 10 ** (Math.floor(Math.log10(scale)) - 1);
  return Math.round(scale / magnitude) * magnitude;
}

/** Positions displaced by `scale` times the displacement vector. */
export function deformedPositions(payload: SimFieldPayload, scale: number): Float32Array {
  const out = Float32Array.from(payload.positions);
  const d = payload.displacement;
  if (!d || scale === 0) return out;
  for (let i = 0; i < out.length && i < d.length; i++) out[i] = (out[i] ?? 0) + (d[i] ?? 0) * scale;
  return out;
}

/** Field value at `point` on triangle (a, b, c), by barycentric weights on
 * the drawn (possibly deformed) positions. Falls back to the nearest
 * vertex's value for a degenerate triangle. */
export function probeValue(
  positions: ArrayLike<number>,
  values: number[],
  a: number,
  b: number,
  c: number,
  point: [number, number, number],
): number {
  const p = (i: number): [number, number, number] => [
    positions[i * 3] ?? 0,
    positions[i * 3 + 1] ?? 0,
    positions[i * 3 + 2] ?? 0,
  ];
  const val = (i: number): number => values[i] ?? 0;
  const pa = p(a);
  const pb = p(b);
  const pc = p(c);
  const v0 = sub(pb, pa);
  const v1 = sub(pc, pa);
  const v2 = sub(point, pa);
  const d00 = dot(v0, v0);
  const d01 = dot(v0, v1);
  const d11 = dot(v1, v1);
  const d20 = dot(v2, v0);
  const d21 = dot(v2, v1);
  const denom = d00 * d11 - d01 * d01;
  if (Math.abs(denom) < 1e-18) {
    const dists = [pa, pb, pc].map((q) => dot(sub(point, q), sub(point, q)));
    const nearest = [a, b, c][dists.indexOf(Math.min(...dists))] ?? a;
    return val(nearest);
  }
  const v = (d11 * d20 - d01 * d21) / denom;
  const w = (d00 * d21 - d01 * d20) / denom;
  const u = 1 - v - w;
  return u * val(a) + v * val(b) + w * val(c);
}

function sub(x: [number, number, number], y: [number, number, number]): [number, number, number] {
  return [x[0] - y[0], x[1] - y[1], x[2] - y[2]];
}

function dot(x: [number, number, number], y: [number, number, number]): number {
  return x[0] * y[0] + x[1] * y[1] + x[2] * y[2];
}

/** A field value for display: 4 significant digits, no exponent for the
 * everyday range, exponent for very small displacements. */
export function formatFieldValue(value: number, unit: string): string {
  if (!Number.isFinite(value)) return `n/a ${unit}`;
  const abs = Math.abs(value);
  const text = abs !== 0 && (abs < 1e-3 || abs >= 1e6) ? value.toExponential(2) : Number(value.toPrecision(4)).toString();
  return `${text} ${unit}`;
}

/** Validate an untrusted JSON body as a v1 payload; null when it is not one. */
export function asFieldPayload(body: unknown): SimFieldPayload | null {
  if (!body || typeof body !== 'object') return null;
  const p = body as Partial<SimFieldPayload>;
  if (p.format !== 'metaforge.sim_field' || p.version !== 1) return null;
  if (!Array.isArray(p.positions) || !Array.isArray(p.indices) || !p.fields || !p.bbox) return null;
  return {
    ...(p as SimFieldPayload),
    markers: Array.isArray(p.markers) ? p.markers : [],
    displacement: Array.isArray(p.displacement) ? p.displacement : null,
  };
}
