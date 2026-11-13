import { describe, expect, it } from 'vitest';
import { centred, lookFor, materialTag, MATERIAL_LOOKS, PLAIN } from '../libraryLook';

describe('libraryLook', () => {
  it('reads the material tag at the end of a drawn shape name', () => {
    expect(materialTag('lead [tin]')).toBe('tin');
    expect(materialTag('body')).toBeNull();
  });

  it("keeps a maker's stated colour over any material look", () => {
    expect(lookFor('body [tin]', [1, 0, 0]).color).toBe(0xff0000);
  });

  it('gives a tagged shape its material look and anything else plain grey, never a made-up colour', () => {
    expect(lookFor('lens [pmma_clear]')).toEqual(MATERIAL_LOOKS.pmma_clear);
    expect(lookFor('lens [no_such_material]')).toEqual(PLAIN);
    expect(lookFor('Body', null)).toEqual(PLAIN);
  });

  it('centres a model on the origin and stands it on y = 0', () => {
    const { shapes, size } = centred([
      { name: 'box', positions: [10, 2, 20, 17, 2.85, 27], indices: [0, 1, 0], look: PLAIN },
    ]);
    expect(size.map((v) => +v.toFixed(3))).toEqual([7, 0.85, 7]);
    expect(shapes[0]?.positions.map((v) => +v.toFixed(3))).toEqual([-3.5, 0, -3.5, 3.5, 0.85, 3.5]);
  });
});
