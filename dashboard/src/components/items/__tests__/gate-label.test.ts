import { describe, expect, it } from 'vitest';
import { gateLabel } from '../CurrentItemsPanel';

describe('gateLabel', () => {
  it('drops a gate code repeated after the name', () => {
    expect(gateLabel('Mechanical design review (G6) (G6)')).toBe('Mechanical design review (G6)');
    expect(gateLabel('Concept Design Gate (G5)(G5) (G5)')).toBe('Concept Design Gate (G5)');
  });

  it('leaves a label without repeats alone', () => {
    expect(gateLabel('Release (G7)')).toBe('Release (G7)');
    expect(gateLabel('Fit (A) (B)')).toBe('Fit (A) (B)');
  });
});
