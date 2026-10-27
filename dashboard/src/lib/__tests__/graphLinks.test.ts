import { describe, expect, it } from 'vitest';
import { DEFAULT_SHOWN, groupOf, splitLinks, type LinkGroup } from '../graphLinks';
import type { TwinEndpoint, TwinRelationship } from '../../types/twin';

const edge = (id: string, source: string, target: string, type = 'depends_on'): TwinRelationship => ({
  id,
  sourceId: source,
  targetId: target,
  type,
  label: type.replace('_', ' '),
});

const endpoints: Record<string, TwinEndpoint> = {
  c1: { kind: 'constraint', name: 'Max mass 250 g' },
  b1: { kind: 'baseline', name: 'Release candidate' },
  p1: { kind: 'bom_item', name: 'XING2-1404' },
  i1: { kind: 'intent', name: 'Hover indoors' },
  u1: { kind: 'unknown', name: 'abcd1234' },
};

const visible = new Set(['w1', 'w2']);
const all: ReadonlySet<LinkGroup> = new Set<LinkGroup>(['constraints', 'intent', 'parts', 'baselines', 'other']);

describe('groupOf', () => {
  it('groups the kinds the twin stores', () => {
    expect(groupOf('constraint')).toBe('constraints');
    expect(groupOf('baseline')).toBe('baselines');
    expect(groupOf('bom_item')).toBe('parts');
    expect(groupOf('intent')).toBe('intent');
    expect(groupOf('verification_case')).toBe('intent');
    expect(groupOf('engineering_entity')).toBe('intent');
  });

  it('sends anything unrecognised to other, so it is never lost', () => {
    expect(groupOf('unknown')).toBe('other');
    expect(groupOf('something_new')).toBe('other');
  });
});

describe('splitLinks', () => {
  it('draws an edge whose two ends are visible work products', () => {
    const out = splitLinks({
      relationships: [edge('e1', 'w1', 'w2')],
      endpoints,
      visibleIds: visible,
      shown: DEFAULT_SHOWN,
    });
    expect(out.drawn).toHaveLength(1);
    expect(out.ghosts).toEqual({});
    expect(out.hidden).toBe(0);
  });

  it('draws an edge to a constraint and returns the constraint as a node to draw', () => {
    const out = splitLinks({
      relationships: [edge('e1', 'w1', 'c1', 'constrained_by')],
      endpoints,
      visibleIds: visible,
      shown: DEFAULT_SHOWN,
    });
    expect(out.drawn.map((e) => e.id)).toEqual(['e1']);
    expect(out.ghosts).toEqual({ c1: endpoints.c1 });
  });

  it('draws an edge that points into a work product from an entity', () => {
    const out = splitLinks({
      relationships: [edge('e1', 'i1', 'w1')],
      endpoints,
      visibleIds: visible,
      shown: DEFAULT_SHOWN,
    });
    expect(out.drawn).toHaveLength(1);
    expect(out.ghosts).toEqual({ i1: endpoints.i1 });
  });

  it('counts a switched-off group as hidden, and still counts it for its switch', () => {
    const out = splitLinks({
      relationships: [edge('e1', 'w1', 'b1', 'included_in_baseline'), edge('e2', 'w2', 'b1', 'included_in_baseline')],
      endpoints,
      visibleIds: visible,
      shown: DEFAULT_SHOWN,
    });
    expect(out.drawn).toHaveLength(0);
    expect(out.hidden).toBe(2);
    expect(out.counts.baselines).toBe(2);
  });

  it('draws the baseline edges once that group is switched on', () => {
    const out = splitLinks({
      relationships: [edge('e1', 'w1', 'b1', 'included_in_baseline')],
      endpoints,
      visibleIds: visible,
      shown: all,
    });
    expect(out.drawn).toHaveLength(1);
    expect(out.hidden).toBe(0);
  });

  it('puts an unreadable end under other and draws it, not drops it', () => {
    const out = splitLinks({
      relationships: [edge('e1', 'w1', 'u1')],
      endpoints,
      visibleIds: visible,
      shown: DEFAULT_SHOWN,
    });
    expect(out.drawn).toHaveLength(1);
    expect(out.counts.other).toBe(1);
  });

  it('reports an edge whose work product is filtered out as offscreen, not hidden', () => {
    const out = splitLinks({
      relationships: [edge('e1', 'w9', 'c1', 'constrained_by'), edge('e2', 'w1', 'w9')],
      endpoints,
      visibleIds: visible,
      shown: DEFAULT_SHOWN,
    });
    expect(out.drawn).toHaveLength(0);
    expect(out.offscreen).toBe(2);
    expect(out.hidden).toBe(0);
  });

  it('accounts for every edge: drawn, hidden or offscreen', () => {
    const relationships = [
      edge('e1', 'w1', 'w2'),
      edge('e2', 'w1', 'c1', 'constrained_by'),
      edge('e3', 'w2', 'b1', 'included_in_baseline'),
      edge('e4', 'w9', 'p1', 'contains'),
      edge('e5', 'i1', 'w2'),
    ];
    const out = splitLinks({ relationships, endpoints, visibleIds: visible, shown: DEFAULT_SHOWN });
    expect(out.drawn.length + out.hidden + out.offscreen).toBe(relationships.length);
  });

  it('draws nothing extra when the gateway sent no endpoints (an older gateway)', () => {
    const out = splitLinks({
      relationships: [edge('e1', 'w1', 'c1', 'constrained_by')],
      endpoints: {},
      visibleIds: visible,
      shown: DEFAULT_SHOWN,
    });
    expect(out.drawn).toHaveLength(0);
    expect(out.offscreen).toBe(1);
  });
});
