import { describe, expect, it } from 'vitest';
import {
  COLOR_STOPS,
  asFieldPayload,
  autoDeformScale,
  availableQuantities,
  colorAt,
  defaultQuantity,
  deformedPositions,
  fieldRange,
  formatFieldValue,
  normalize,
  probeValue,
  sharedRange,
  vertexColors,
} from '../fieldMath';
import { squarePayload } from './fixtures';

const luminance = ([r, g, b]: [number, number, number]) => 0.2126 * r + 0.7152 * g + 0.0722 * b;

describe('colour mapping', () => {
  it('maps the range ends to the first and last stops', () => {
    const [, first] = COLOR_STOPS[0]!;
    const [, last] = COLOR_STOPS[COLOR_STOPS.length - 1]!;
    expect(colorAt(0).map((c) => Math.round(c * 255))).toEqual(first);
    expect(colorAt(1).map((c) => Math.round(c * 255))).toEqual(last);
  });

  it('gets brighter as the value rises (sequential, not a rainbow)', () => {
    let previous = -1;
    for (let t = 0; t <= 1.0001; t += 0.05) {
      const l = luminance(colorAt(t));
      expect(l).toBeGreaterThan(previous - 1e-9);
      previous = l;
    }
  });

  it('clamps out-of-range and non-finite inputs', () => {
    expect(colorAt(-3)).toEqual(colorAt(0));
    expect(colorAt(7)).toEqual(colorAt(1));
    expect(colorAt(Number.NaN)).toEqual(colorAt(0));
  });

  it('normalises into the range, and a flat field to 0', () => {
    expect(normalize(50, { min: 0, max: 100 })).toBe(0.5);
    expect(normalize(150, { min: 0, max: 100 })).toBe(1);
    expect(normalize(-5, { min: 0, max: 100 })).toBe(0);
    expect(normalize(5, { min: 5, max: 5 })).toBe(0);
  });

  it('colours each vertex from its value', () => {
    const colors = vertexColors([0, 100], { min: 0, max: 100 });
    expect(colors).toHaveLength(6);
    expect(Array.from(colors.slice(0, 3))).toEqual(colorAt(0).map(Math.fround));
    expect(Array.from(colors.slice(3))).toEqual(colorAt(1).map(Math.fround));
  });
});

describe('legend range', () => {
  it('uses the full-field min/max, not the drawn surface values', () => {
    // Surface values top out at 100 but the field peak is 120 (an interior
    // node, or a vertex lost to decimation): the legend must say 120.
    expect(fieldRange(squarePayload(), 'von_mises')).toEqual({ min: 0, max: 120 });
  });

  it('is null for a quantity the payload does not carry', () => {
    expect(fieldRange(squarePayload(), 'temperature')).toBeNull();
  });

  it('spans both results for a compare', () => {
    const a = squarePayload();
    const b = squarePayload();
    b.fields.von_mises = { ...b.fields.von_mises!, min: 10, max: 300 };
    expect(sharedRange(a, b, 'von_mises')).toEqual({ min: 0, max: 300 });
  });

  it('lists quantities in display order and defaults to stress', () => {
    expect(availableQuantities(squarePayload())).toEqual(['von_mises', 'displacement_magnitude']);
    expect(defaultQuantity(squarePayload())).toBe('von_mises');
    const thermal = squarePayload({
      fields: { temperature: { label: 'Temperature', unit: 'C', values: [1, 2, 3, 4], min: 1, max: 4, peak: null } },
      displacement: null,
    });
    expect(defaultQuantity(thermal)).toBe('temperature');
  });
});

describe('deformation', () => {
  it('auto-scales the peak deflection to about 5% of the part size', () => {
    // diag = 14.14 mm, peak 0.1 mm -> 0.05 * 14.14 / 0.1 = 7.07 -> 7.1
    expect(autoDeformScale(squarePayload())).toBeCloseTo(7.1, 5);
  });

  it('is 0 without displacement (thermal)', () => {
    expect(autoDeformScale(squarePayload({ displacement: null }))).toBe(0);
  });

  it('displaces positions by scale times the vector', () => {
    const out = deformedPositions(squarePayload(), 10);
    expect(out[5]).toBeCloseTo(-1); // vertex 1 z: 0 + 10 * -0.1
    expect(out[0]).toBe(0);
  });
});

describe('probe', () => {
  it('interpolates barycentrically inside a triangle', () => {
    const p = squarePayload();
    const values = p.fields.von_mises!.values;
    expect(probeValue(p.positions, values, 0, 1, 2, [5, 0, 0])).toBeCloseTo(50);
    expect(probeValue(p.positions, values, 0, 1, 2, [10, 10, 0])).toBeCloseTo(100);
  });

  it('falls back to the nearest vertex on a degenerate triangle', () => {
    expect(probeValue([0, 0, 0, 0, 0, 0, 0, 0, 0], [1, 2, 3], 0, 1, 2, [0, 0, 0])).toBe(1);
  });

  it('formats values with units', () => {
    expect(formatFieldValue(123.456, 'MPa')).toBe('123.5 MPa');
    expect(formatFieldValue(0.0000123, 'mm')).toBe('1.23e-5 mm');
  });
});

describe('payload validation', () => {
  it('accepts a v1 payload and rejects anything else', () => {
    expect(asFieldPayload(squarePayload())).not.toBeNull();
    expect(asFieldPayload({ format: 'other' })).toBeNull();
    expect(asFieldPayload({ ...squarePayload(), version: 2 })).toBeNull();
    expect(asFieldPayload(null)).toBeNull();
  });
});
