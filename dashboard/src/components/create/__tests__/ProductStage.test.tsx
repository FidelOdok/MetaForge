import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '../../../test/test-utils';
import type { TwinNode } from '../../../types/twin';

vi.mock('../../../hooks/use-twin', async (orig) => ({
  ...(await orig<typeof import('../../../hooks/use-twin')>()),
  useNodeVersionHistory: () => ({ data: undefined }),
}));
// The stage renders WebGL previews; they are covered on their own.
vi.mock('../../preview/PreviewHost', () => ({
  PreviewHost: ({ node }: { node: TwinNode }) => <div data-testid="preview-host">{node.name}</div>,
}));
vi.mock('../../preview/ParametricStage', () => ({ ParametricStage: () => <div data-testid="parametric-stage" /> }));

import { ProductStage } from '../ProductStage';

const P = 'proj-stage';

function node(id: string, props: Record<string, string | number | boolean>, updatedAt = '2026-11-05T10:00:00Z'): TwinNode {
  return { id, name: id, type: 'work_product', domain: '', status: '', properties: props, updatedAt } as TwinNode;
}
const MESH = node('Reference model', {
  wp_type: 'render',
  format: 'glb',
  generated_by: 'twin.generate_concept_mesh',
  authority: 'illustrative',
  manufacturing_note: 'Production ready',
});
const CAD = node('Bracket', { wp_type: 'cad_model', format: 'step' });
const SKETCH = node('My sketch', { wp_type: 'sketch_strokes' });

function stage(nodes: TwinNode[], extra: Partial<Parameters<typeof ProductStage>[0]> = {}) {
  return render(<ProductStage projectId={P} nodes={nodes} selectedId={null} working={false} {...extra} />);
}

describe('ProductStage (the twin model, read only)', () => {
  it('shows the model with one label: its maturity and its representation', () => {
    stage([MESH]);
    expect(screen.getByTestId('preview-host')).toHaveTextContent('Reference model');
    expect(screen.getByTestId('stage-maturity')).toHaveTextContent('Illustrative');
    expect(screen.getByTestId('stage-status')).toHaveTextContent('3D Concept · illustrative');
    // One label only: no stacked manufacturing note, readiness or DFM badges.
    expect(screen.getAllByTestId('stage-maturity')).toHaveLength(1);
    expect(screen.queryByTestId('stage-mfg-note')).toBeNull();
    expect(screen.queryByTestId('readiness-status')).toBeNull();
    expect(screen.queryByText(/Manufacturing guidance/)).toBeNull();
    expect(screen.getByTestId('stage-status')).not.toHaveTextContent(/production/i);
  });

  it('prefers the exact model and never shows a sketch as a result', () => {
    stage([MESH, CAD, SKETCH]);
    expect(screen.getByTestId('preview-host')).toHaveTextContent('Bracket');
    expect(screen.getByTestId('stage-status')).toHaveTextContent('Bracket');
  });

  it('has no view or sketch mode, and no sketch under or over the model', () => {
    stage([MESH]);
    expect(screen.queryByRole('group', { name: 'Stage mode' })).toBeNull();
    expect(screen.queryByRole('button', { name: /Sketch/ })).toBeNull();
    expect(screen.queryByTestId('sketch-layer')).toBeNull();
    expect(screen.queryByTestId('sketch-overlay')).toBeNull();
    expect(screen.queryByTestId('stage-trace')).toBeNull();
  });

  it('says what is running, and keeps the last result on screen', () => {
    const { unmount } = stage([CAD], { working: true });
    expect(screen.getByRole('status')).toHaveTextContent('Working on the next version');
    unmount();
    stage([CAD], { engineeringChecks: 'running' });
    expect(screen.getByRole('status')).toHaveTextContent('Engineering checks running');
  });

  it('is calm when there is no model yet', () => {
    stage([]);
    expect(screen.getByRole('heading', { name: 'No model yet' })).toBeInTheDocument();
  });
});
