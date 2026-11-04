import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '../../../test/test-utils';
import { RevisionExportControls } from '../RevisionExportControls';
import type { NodeArtifacts } from '../../../api/endpoints/cad-artifacts';
import type { TwinNode } from '../../../types/twin';

const exportMutate = vi.fn();
const buildMutate = vi.fn();
let artifacts: { data: NodeArtifacts | undefined; isLoading: boolean } = { data: undefined, isLoading: false };

vi.mock('../../../hooks/use-cad-artifacts', () => ({
  useNodeArtifacts: () => artifacts,
  useExportStep: () => ({ mutate: exportMutate, isPending: false }),
  useExactBuild: () => ({ mutate: buildMutate, isPending: false }),
}));

const NODE: TwinNode = {
  id: 'n2',
  name: 'Bracket',
  type: 'work_product',
  domain: 'mechanical',
  status: 'valid',
  properties: { wp_type: 'cad_model', item_key: 'CAD-BRACKET' },
  updatedAt: new Date().toISOString(),
  projectId: 'p1',
};

function view(over: Partial<NodeArtifacts> = {}): NodeArtifacts {
  return {
    node_id: 'n2',
    item_key: 'CAD-BRACKET',
    revision: 2,
    head_revision: 2,
    is_head: true,
    geometry_state: 'validated',
    badge: 'Validated',
    authority: 'validated',
    validity: { outcome: 'pass' },
    latest_attempt: null,
    last_validated: { revision: 2, node_id: 'n2', item_ref: 'CAD-BRACKET@2', is_head: true },
    can_export_step: true,
    exports: [
      { format: 'glb', state: 'READY', detail: '' },
      { format: 'native', state: 'READY', detail: '' },
      { format: 'step', state: 'NONE', detail: 'not exported yet' },
    ],
    ...over,
  };
}

function stateOf(format: string): string | null {
  const row = document.querySelector(`[data-export="${format}"] [data-state]`);
  return row?.getAttribute('data-state') ?? null;
}

describe('RevisionExportControls', () => {
  beforeEach(() => {
    exportMutate.mockClear();
    buildMutate.mockClear();
  });

  it('shows GLB, native CAD and STEP states for the selected revision', () => {
    artifacts = { data: view(), isLoading: false };
    render(<RevisionExportControls node={NODE} projectId="p1" />);
    expect(stateOf('glb')).toBe('READY');
    expect(stateOf('native')).toBe('READY');
    expect(stateOf('step')).toBe('NONE');
    expect(screen.getByText('Validated · revision 2')).toBeInTheDocument();
    expect(screen.getByText('CAD-BRACKET@2')).toBeInTheDocument();
  });

  it('queues the STEP export of a validated revision', () => {
    artifacts = { data: view(), isLoading: false };
    render(<RevisionExportControls node={NODE} projectId="p1" />);
    fireEvent.click(screen.getByRole('button', { name: 'Export STEP' }));
    expect(exportMutate).toHaveBeenCalledWith(false);
  });

  it('shows a queued export and does not queue it twice', () => {
    artifacts = {
      data: view({ exports: [...view().exports.slice(0, 2), { format: 'step', state: 'QUEUED', detail: '' }] }),
      isLoading: false,
    };
    render(<RevisionExportControls node={NODE} projectId="p1" />);
    expect(stateOf('step')).toBe('QUEUED');
    expect(screen.getByRole('button', { name: 'STEP queued…' })).toBeDisabled();
  });

  it('keeps an older revision STEP downloadable but marked stale, and names the last validated one', () => {
    artifacts = {
      data: view({
        revision: 1,
        is_head: false,
        head_revision: 3,
        last_validated: { revision: 2, node_id: 'n2b', item_ref: 'CAD-BRACKET@2', is_head: false },
        exports: [
          { format: 'glb', state: 'READY', detail: '' },
          { format: 'native', state: 'NONE', detail: '' },
          { format: 'step', state: 'STALE', detail: 'CAD-BRACKET is now @3', schema: 'AP214', round_trip: 'pass', url: '/x' },
        ],
      }),
      isLoading: false,
    };
    render(<RevisionExportControls node={NODE} projectId="p1" />);
    expect(stateOf('step')).toBe('STALE');
    expect(screen.getByText(/AP214/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Download STEP (older revision)' })).toBeInTheDocument();
    expect(screen.getByText('CAD-BRACKET@1 · current is @3')).toBeInTheDocument();
    expect(screen.getByTestId('last-validated')).toHaveTextContent('Last validated: CAD-BRACKET@2');
  });

  it('shows a failed export and offers no export of unvalidated geometry', () => {
    artifacts = {
      data: view({
        geometry_state: 'exact_ready',
        badge: 'Exact',
        can_export_step: false,
        last_validated: null,
        latest_attempt: { state: 'failed', outcome: 'fail' },
        exports: [
          { format: 'glb', state: 'READY', detail: '' },
          { format: 'native', state: 'READY', detail: '' },
          { format: 'step', state: 'FAILED', detail: 'export failed' },
        ],
      }),
      isLoading: false,
    };
    render(<RevisionExportControls node={NODE} projectId="p1" />);
    expect(stateOf('step')).toBe('FAILED');
    expect(screen.getByRole('button', { name: 'Export STEP' })).toBeDisabled();
    expect(screen.getByText('Exact · revision 2')).toBeInTheDocument();
    expect(screen.getByRole('note')).toHaveTextContent('last engineering check');
    fireEvent.click(screen.getByRole('button', { name: 'Validate geometry' }));
    expect(buildMutate).toHaveBeenCalledWith({ itemKey: 'CAD-BRACKET', projectId: 'p1', baseRevision: 2 });
  });
});
