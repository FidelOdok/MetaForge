import { describe, it, expect } from 'vitest';
import { computeExplodeOffset } from '../explode';

describe('computeExplodeOffset', () => {
  it('radial spreads a part along its full 3D direction from center', () => {
    const offset = computeExplodeOffset({ x: 1, y: 2, z: 3 }, 'radial', 50);
    expect(offset).toEqual({ x: 1, y: 2, z: 3 });
  });

  it('axial spreads a part only along Y, ignoring X/Z', () => {
    const offset = computeExplodeOffset({ x: 1, y: 2, z: 3 }, 'axial', 50);
    expect(offset).toEqual({ x: 0, y: 2, z: 0 });
  });

  it('a 100% explode factor (the slider max) keeps parts within a sane multiple of their original distance', () => {
    // Regression: explodeFactor is a 0-100 percentage (the slider's native
    // range), not an already-normalized 0-1 fraction. Treating it as 0-1
    // made every part fly out to 100x its offset at max explode -- so far
    // outside the camera's fitted view that the whole model vanished.
    const offset = computeExplodeOffset({ x: 10, y: 10, z: 10 }, 'radial', 100);
    expect(offset).toEqual({ x: 20, y: 20, z: 20 });
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
