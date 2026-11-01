/**
 * The real graph canvas, not a stand-in (FORGE-704).
 *
 * The page tests replace TwinGraphCanvas with a mock, which is how this went
 * unnoticed: the canvas's custom nodes declared no connection handles, React Flow
 * 12 cannot anchor an edge without them, and so the graph never drew a link. It
 * logged error 008 for each edge and nothing else. This renders the actual
 * component and checks every node can take an edge, and that the linked-record
 * nodes are there.
 */

import { describe, expect, it, vi, beforeAll } from 'vitest';
import { render } from '@testing-library/react';
import { TwinGraphCanvas } from '../TwinGraphCanvas';
import type { TwinNode, TwinRelationship } from '../../../types/twin';

beforeAll(() => {
  // jsdom has no layout engine; React Flow measures nodes with these.
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal('ResizeObserver', ResizeObserverStub);
  if (!('DOMMatrixReadOnly' in globalThis)) {
    vi.stubGlobal(
      'DOMMatrixReadOnly',
      class {
        m22 = 1;
        constructor() {}
      },
    );
  }
});

const node = (id: string, name: string, domain = 'mechanical'): TwinNode =>
  ({ id, name, type: 'work_product', domain, status: 'valid', properties: {}, updatedAt: '2026-10-27T00:00:00Z' }) as TwinNode;

const rel = (id: string, source: string, target: string, type = 'depends_on'): TwinRelationship => ({
  id,
  sourceId: source,
  targetId: target,
  type,
  label: type.replace(/_/g, ' '),
});

function mount(ghosts: Record<string, { kind: string; name: string }> = {}) {
  const out = render(
    <div style={{ width: 800, height: 600 }}>
      <TwinGraphCanvas
        nodes={[node('w1', 'Arm'), node('w2', 'Bracket', 'electronics')]}
        relationships={[rel('r1', 'w1', 'w2'), rel('r2', 'w1', 'c1', 'constrained_by')]}
        ghosts={ghosts}
        selectedId={null}
        onSelectNode={() => {}}
      />
    </div>,
  );
  return out;
}

describe('TwinGraphCanvas', () => {
  it('gives every work-product node a target and a source handle, so an edge can attach', () => {
    const { container } = mount();
    const nodes = container.querySelectorAll('.react-flow__node-twin');
    expect(nodes.length).toBe(2);
    nodes.forEach((n) => {
      expect(n.querySelectorAll('.react-flow__handle.target')).toHaveLength(1);
      expect(n.querySelectorAll('.react-flow__handle.source')).toHaveLength(1);
    });
  });

  it('draws linked records as their own nodes, with handles too', () => {
    const { container, getByText } = mount({ c1: { kind: 'constraint', name: 'Max mass 250 g' } });
    const ref = container.querySelectorAll('.react-flow__node-ref');
    expect(ref).toHaveLength(1);
    expect(getByText('Max mass 250 g')).toBeInTheDocument();
    expect(ref[0]?.querySelectorAll('.react-flow__handle')).toHaveLength(2);
  });

  it('names the kind of a linked record', () => {
    const { getByText } = mount({ c1: { kind: 'bom_item', name: 'XING2-1404' } });
    expect(getByText('bom item')).toBeInTheDocument();
  });

  it('wraps a long layer into columns instead of one tall stack, and tolerates a cycle', () => {
    const many = Array.from({ length: 20 }, (_, i) => node(`n${i}`, `Part ${i}`));
    const { container } = render(
      <div style={{ width: 800, height: 600 }}>
        <TwinGraphCanvas
          nodes={many}
          relationships={[rel('a', 'n0', 'n1'), rel('b', 'n1', 'n0')]}
          selectedId={null}
          onSelectNode={() => {}}
        />
      </div>,
    );
    const xs = new Set<string>();
    const ys = new Set<string>();
    container.querySelectorAll<HTMLElement>('.react-flow__node-twin').forEach((n) => {
      const m = /translate\(([-\d.]+)px,\s*([-\d.]+)px\)/.exec(n.style.transform);
      if (m) {
        xs.add(m[1] ?? '');
        ys.add(m[2] ?? '');
      }
    });
    expect(ys.size).toBeLessThanOrEqual(8);
    expect(xs.size).toBeGreaterThan(1);
  });

  it('draws no linked-record node when none is shown', () => {
    const { container } = mount({});
    expect(container.querySelectorAll('.react-flow__node-ref')).toHaveLength(0);
  });
});
