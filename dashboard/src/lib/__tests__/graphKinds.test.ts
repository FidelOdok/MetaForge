import { describe, expect, it } from 'vitest';
import type { TwinNode } from '../../types/twin';
import { countKinds, defaultKinds, kindOf, LARGE_TWIN } from '../graphKinds';

type Kindable = Pick<TwinNode, 'type' | 'properties'>;

const node = (wp_type: string) => ({ type: 'work_product', properties: { wp_type } }) as unknown as Kindable;

describe('kindOf', () => {
  it('groups work product types the way a person thinks of them', () => {
    expect(kindOf(node('cad_model'))).toBe('designs');
    expect(kindOf(node('design_sketch'))).toBe('designs');
    expect(kindOf(node('constraint_set'))).toBe('requirements');
    expect(kindOf(node('design_decision'))).toBe('decisions');
    expect(kindOf(node('load_case'))).toBe('analysis');
    expect(kindOf(node('bom'))).toBe('other');
  });

  it('falls back to the node type when there is no wp_type', () => {
    expect(kindOf({ type: 'evidence', properties: {} } as unknown as Kindable)).toBe('analysis');
  });
});

describe('defaultKinds', () => {
  it('shows everything on a small twin', () => {
    expect(defaultKinds(LARGE_TWIN).has('decisions')).toBe(true);
  });

  it('starts a large twin without the decision records', () => {
    const shown = defaultKinds(LARGE_TWIN + 1);
    expect(shown.has('decisions')).toBe(false);
    expect(shown.has('designs')).toBe(true);
    expect(shown.has('requirements')).toBe(true);
  });
});

describe('countKinds', () => {
  it('counts every node exactly once', () => {
    const counts = countKinds([node('cad_model'), node('design_decision'), node('design_decision'), node('bom')]);
    expect(counts).toEqual({ designs: 1, requirements: 0, decisions: 2, analysis: 0, other: 1 });
  });
});
