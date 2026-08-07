import { describe, it, expect } from 'vitest';
import { computeExplodeOffset } from '../explode';

describe('computeExplodeOffset', () => {
  it('radial spreads a part along its full 3D direction from center', () => {
    const offset = computeExplodeOffset({ x: 1, y: 2, z: 3 }, 'radial', 50);
    expect(offset).toEqual({ x: 100, y: 200, z: 300 });
  });

  it('axial spreads a part only along Y, ignoring X/Z', () => {
    const offset = computeExplodeOffset({ x: 1, y: 2, z: 3 }, 'axial', 50);
    expect(offset).toEqual({ x: 0, y: 200, z: 0 });
  });

  it('radial and axial genuinely differ for the same input (regression: direction used to be ignored)', () => {
    const fromCenter = { x: 5, y: 1, z: -5 };
    const radial = computeExplodeOffset(fromCenter, 'radial', 40);
    const axial = computeExplodeOffset(fromCenter, 'axial', 40);
    expect(radial).not.toEqual(axial);
  });

  it('a zero explode factor collapses both directions to the origin', () => {
    expect(computeExplodeOffset({ x: 9, y: 9, z: 9 }, 'radial', 0)).toEqual({ x: 0, y: 0, z: 0 });
    expect(computeExplodeOffset({ x: 9, y: 9, z: 9 }, 'axial', 0)).toEqual({ x: 0, y: 0, z: 0 });
  });
});
