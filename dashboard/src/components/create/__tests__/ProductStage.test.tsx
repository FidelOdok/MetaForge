import { beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor, within } from '../../../test/test-utils';
import { useCreateLayoutStore } from '../../../store/create-layout-store';
import { useSketchStore } from '../../../store/sketch-store';
import type { TwinNode } from '../../../types/twin';

const startConceptMesh = vi.fn();
const getConceptMesh = vi.fn();
vi.mock('../../../api/endpoints/conceptMesh', async (orig) => ({
  ...(await orig<typeof import('../../../api/endpoints/conceptMesh')>()),
  startConceptMesh: (...a: unknown[]) => startConceptMesh(...a),
  getConceptMesh: (...a: unknown[]) => getConceptMesh(...a),
}));
vi.mock('../../../api/endpoints/sketchGeneration', async (orig) => ({
  ...(await orig<typeof import('../../../api/endpoints/sketchGeneration')>()),
  listGenerations: vi.fn().mockResolvedValue({ lines: [], proposals: [] }),
}));
vi.mock('../../../hooks/use-twin', async (orig) => ({
  ...(await orig<typeof import('../../../hooks/use-twin')>()),
  useNodeVersionHistory: () => ({ data: undefined }),
}));
vi.mock('../../../hooks/use-creation', () => ({ useCreation: () => ({ data: undefined }) }));
vi.mock('../../../hooks/use-sketch-plane', () => ({ useSketchPlane: () => ({ data: undefined }) }));
// The stage renders WebGL previews and the real sketch layer; both are covered on their own.
vi.mock('../../preview/PreviewHost', () => ({
  PreviewHost: ({ node }: { node: TwinNode }) => <div data-testid="preview-host">{node.name}</div>,
}));
vi.mock('../../sketch/SketchLayer', () => ({
  SketchLayer: (props: { onRequestPanel?: (p: 'plane') => void; editableHost?: HTMLElement | null }) => (
    <div data-testid="sketch-layer" data-editable-host={String(!!props.editableHost)}>
      <button type="button" onClick={() => props.onRequestPanel?.('plane')}>
        Use in 3D
      </button>
    </div>
  ),
}));
vi.mock('../../sketch/GeneratePanel', async (orig) => ({
  ...(await orig<typeof import('../../sketch/GeneratePanel')>()),
  GeneratePanel: ({ docked }: { docked?: boolean }) => <div data-testid="generate-panel" data-docked={String(!!docked)} />,
}));
vi.mock('../../sketch/PlanePrompt', () => ({ PlanePrompt: () => <div data-testid="plane-prompt" /> }));

import { ProductStage } from '../ProductStage';

const P = 'proj-stage';

function node(id: string, props: Record<string, string | number | boolean>, updatedAt = '2026-11-05T10:00:00Z'): TwinNode {
  return { id, name: id, type: 'work_product', domain: '', status: '', properties: props, updatedAt } as TwinNode;
}
const IMAGE = node('Concept photo', { wp_type: 'image', format: 'png' });
const MESH = node('Reference model', {
  wp_type: 'render',
  format: 'glb',
  generated_by: 'twin.generate_concept_mesh',
  authority: 'illustrative',
});
const CAD = node('Bracket', { wp_type: 'cad_model', format: 'step' });

function seedStrokes() {
  const store = useSketchStore.getState();
  store.ensure(P);
  store.loadRemote(P, {
    strokes: [{ id: 's1', tool: 'pen', points: [[0, 0], [0.1, 0.1]], width: 2, created_at: '2026-11-05T00:00:00Z', source: 'human' }],
    itemKey: 'SKT-A',
    itemRef: 'SKT-A@1',
    nodeId: 'n1',
    createdAt: null,
  });
}

function stage(nodes: TwinNode[], extra: Partial<Parameters<typeof ProductStage>[0]> = {}) {
  return render(
    <ProductStage projectId={P} nodes={nodes} selectedId={null} onSelect={vi.fn()} onDiscuss={vi.fn()} working={false} {...extra} />,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  useCreateLayoutStore.getState().reset();
  useSketchStore.getState().reset();
  startConceptMesh.mockReset();
  getConceptMesh.mockReset().mockResolvedValue({
    latest: null,
    active_job: null,
    next_source: null,
    spend: { used_last_hour: 0, cap: 3, policy_mode: 'off', policy_would_approve: false },
  });
});

describe('ProductStage modes', () => {
  it('starts in View with only the artifact, its maturity and its status', () => {
    stage([MESH]);
    expect(screen.getByTestId('preview-host')).toHaveTextContent('Reference model');
    expect(screen.queryByTestId('sketch-layer')).toBeNull();
    expect(screen.getByRole('button', { name: /View/ })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('stage-maturity')).toHaveTextContent('Illustrative');
    expect(screen.getByTestId('stage-status')).toHaveTextContent('3D Concept · illustrative');
    expect(screen.getByTestId('stage-mfg-note')).toHaveTextContent('Manufacturing not assessed');
  });

  it('mounts the sketch only in Sketch, with no interactive viewer under it', async () => {
    seedStrokes();
    stage([IMAGE]);
    await userEvent.click(screen.getByRole('button', { name: /Sketch$/ }));
    expect(screen.getByTestId('sketch-layer')).toBeInTheDocument();
    expect(screen.queryByTestId('preview-host')).toBeNull();
    expect(screen.queryByTestId('stage-trace')).toBeNull();

    // Trace: a faint still image, never the live viewer.
    await userEvent.click(screen.getByRole('checkbox', { name: 'Trace over the current view' }));
    const still = screen.getByTestId('stage-trace');
    expect(still.tagName).toBe('IMG');
    expect(still).toHaveAttribute('aria-hidden', 'true');
    expect(still).toHaveClass('cv-trace');
    expect(screen.queryByTestId('preview-host')).toBeNull();

    // Leaving Sketch keeps the sketch; View can show it as a thin overlay.
    await userEvent.click(screen.getByRole('button', { name: /View/ }));
    expect(screen.queryByTestId('sketch-layer')).toBeNull();
    expect(screen.getByTestId('preview-host')).toBeInTheDocument();
    expect(useSketchStore.getState().doc(P).strokes).toHaveLength(1);
    await userEvent.click(screen.getByRole('button', { name: 'My sketch' }));
    expect(screen.getByTestId('sketch-overlay')).toHaveAttribute('aria-hidden', 'true');
  });
});

describe('the drawer', () => {
  it('shows one panel at a time beside the drawing, and collapses and closes', async () => {
    stage([IMAGE]);
    const more = () => screen.getByRole('button', { name: /More/ });
    await userEvent.click(more());
    await userEvent.click(screen.getByRole('menuitem', { name: 'Generate a concept' }));
    const drawer = screen.getByTestId('stage-drawer');
    expect(drawer).toHaveAttribute('data-panel', 'generate');
    expect(screen.getByTestId('generate-panel')).toHaveAttribute('data-docked', 'true');
    // Beside the drawing, never inside it or over the stage's own controls.
    expect(screen.getByTestId('stage-canvas').contains(drawer)).toBe(false);
    expect(drawer.closest('.cv-stage-bar')).toBeNull();

    await userEvent.click(more());
    await userEvent.click(screen.getByRole('menuitem', { name: 'Add reference' }));
    expect(screen.getAllByTestId('stage-drawer')).toHaveLength(1);
    expect(screen.getByTestId('stage-drawer')).toHaveAttribute('data-panel', 'references');
    expect(screen.queryByTestId('generate-panel')).toBeNull();

    await userEvent.click(screen.getByRole('button', { name: 'Collapse References' }));
    expect(screen.getByTestId('stage-drawer')).toHaveClass('is-collapsed');
    expect(screen.queryByText('No references yet.')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Expand References' }));
    expect(screen.getByText('No references yet.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Close References' }));
    expect(screen.queryByTestId('stage-drawer')).toBeNull();
  });

  it('puts the editable sketch in the drawer in Sketch mode', async () => {
    seedStrokes();
    stage([MESH]);
    await userEvent.click(screen.getByTestId('stage-primary'));
    expect(screen.getByTestId('stage-drawer')).toHaveAttribute('data-panel', 'editable');
    await waitFor(() => expect(screen.getByTestId('sketch-layer')).toHaveAttribute('data-editable-host', 'true'));
    expect(screen.queryByTestId('preview-host')).toBeNull();
    // The plane choice replaces it: still one panel.
    await userEvent.click(screen.getByRole('button', { name: 'Use in 3D' }));
    expect(screen.getByTestId('stage-drawer')).toHaveAttribute('data-panel', 'plane');
    expect(screen.getByTestId('sketch-layer')).toHaveAttribute('data-editable-host', 'false');
  });
});

describe('the one primary action', () => {
  it('offers Describe, Sketch it and Add reference before anything exists', () => {
    stage([]);
    const start = screen.getByRole('group', { name: 'Start' });
    expect(within(start).getAllByRole('button').map((b) => b.textContent?.trim())).toEqual(['Describe', 'Sketch it', 'Add reference']);
    expect(screen.queryByTestId('stage-primary')).toBeNull();
  });

  it('follows the state', async () => {
    const { unmount } = stage([IMAGE]);
    expect(screen.getByTestId('stage-primary')).toHaveTextContent('Make a 3D concept');
    unmount();

    seedStrokes();
    const second = stage([MESH]);
    expect(screen.getByTestId('stage-primary')).toHaveTextContent('Make editable');
    second.unmount();

    useSketchStore.getState().reset();
    const third = stage([MESH], { facts: { described: true } });
    expect(screen.getByTestId('stage-primary')).toHaveTextContent('Build exact model');
    third.unmount();

    const onPrepare = vi.fn();
    stage([CAD, MESH], { onPrepare });
    expect(screen.getByTestId('stage-primary')).toHaveTextContent('Prepare prototype');
    await userEvent.click(screen.getByTestId('stage-primary'));
    expect(onPrepare).toHaveBeenCalled();
    // Everything else is in More.
    await userEvent.click(screen.getByRole('button', { name: /More/ }));
    expect(screen.getByRole('menuitem', { name: 'Make a 3D concept' })).toBeInTheDocument();
  });
});

describe('Make a 3D concept', () => {
  it('calls the route, and asks before going past the hourly cap', async () => {
    startConceptMesh
      .mockRejectedValueOnce({
        response: {
          status: 409,
          data: { detail: { code: 'spend_confirmation_required', message: '3 3D concepts were made in this project in the last hour (cap 3).' } },
        },
      })
      .mockResolvedValueOnce({ job_id: 'j1', status: 'running', operation_id: 'job:j1' });
    stage([IMAGE], { facts: { described: true } });
    await userEvent.click(screen.getByTestId('stage-primary'));
    expect(startConceptMesh).toHaveBeenCalledWith(P, { confirm_spend: false });
    const notice = await screen.findByTestId('concept-mesh-notice');
    expect(notice).toHaveTextContent('cap 3');
    // The notice is inline in the stage, not over the drawing.
    expect(screen.getByTestId('stage-canvas').contains(notice)).toBe(false);

    await userEvent.click(screen.getByRole('button', { name: 'Make it anyway' }));
    expect(startConceptMesh).toHaveBeenLastCalledWith(P, { confirm_spend: true });
    await waitFor(() => expect(screen.queryByTestId('concept-mesh-notice')).toBeNull());
    expect(screen.getByTestId('stage-primary')).toHaveTextContent('Making a 3D concept…');
    expect(screen.getByTestId('stage-primary')).toBeDisabled();
  });

  it('shows a concept manufacturing note only as given, guarded', () => {
    stage([node('Lamp mesh', { ...MESH.properties, manufacturing_note: 'Likely printable with supports (preliminary)' })]);
    expect(screen.getByTestId('stage-mfg-note')).toHaveTextContent('Likely printable with supports (preliminary)');
    expect(screen.getByTestId('stage-status')).not.toHaveTextContent(/verified|production/i);
  });
});
