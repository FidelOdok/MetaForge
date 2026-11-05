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
// Build exact model (FORGE-808): the routes it calls, and a stage without WebGL.
const getPreviewDefinition = vi.fn();
const commitParameters = vi.fn();
const requestExactBuild = vi.fn();
const getManufacturingContext = vi.fn();
vi.mock('../../../api/endpoints/parametricPreview', () => ({
  getPreviewDefinition: (...a: unknown[]) => getPreviewDefinition(...a),
  commitParameters: (...a: unknown[]) => commitParameters(...a),
}));
vi.mock('../../../api/endpoints/cad-artifacts', async (orig) => ({
  ...(await orig<typeof import('../../../api/endpoints/cad-artifacts')>()),
  requestExactBuild: (...a: unknown[]) => requestExactBuild(...a),
}));
vi.mock('../../../api/endpoints/manufacturingContext', async (orig) => ({
  ...(await orig<typeof import('../../../api/endpoints/manufacturingContext')>()),
  getManufacturingContext: (...a: unknown[]) => getManufacturingContext(...a),
}));
vi.mock('../../preview/ParametricStage', () => ({ ParametricStage: () => <div data-testid="parametric-stage" /> }));

import { ProductStage } from '../ProductStage';
import { definition } from '../../../lib/preview/__tests__/fixtures';
import { useOperationsStore } from '../../../store/operations-store';

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

describe('Build exact model', () => {
  const PSK = node('Parametric sketch: Slot', { wp_type: 'parametric_sketch', item_key: 'PSK-SLOT', item_revision: 1 });

  beforeEach(() => {
    getPreviewDefinition.mockReset();
    commitParameters.mockReset();
    requestExactBuild.mockReset();
    getManufacturingContext.mockReset();
    useOperationsStore.getState().reset();
  });

  async function clickBuild() {
    const primary = screen.queryByTestId('stage-primary');
    if (primary?.textContent?.includes('Build exact model')) {
      await userEvent.click(primary);
      return;
    }
    await userEvent.click(screen.getByRole('button', { name: /More/ }));
    await userEvent.click(screen.getByRole('menuitem', { name: 'Build exact model' }));
  }

  it('starts the exact build when every required input is known', async () => {
    getPreviewDefinition.mockResolvedValue(definition());
    requestExactBuild.mockResolvedValue({ job_id: 'j1', status: 'running' });
    const onBuildExact = vi.fn();
    stage([MESH, PSK], { facts: { described: true }, onBuildExact });
    expect(screen.getByTestId('stage-primary')).toHaveTextContent('Build exact model');
    await clickBuild();
    await waitFor(() => expect(requestExactBuild).toHaveBeenCalledTimes(1));
    expect(requestExactBuild).toHaveBeenCalledWith('PSK-SLOT', P, null, { featureId: 'feat-extrude-1' });
    expect(commitParameters).not.toHaveBeenCalled();
    expect(onBuildExact).not.toHaveBeenCalled();
    expect(await screen.findByTestId('build-exact-notice')).toHaveTextContent('Building the exact model. Its progress is in the activity panel.');
    // Progress is shown in the activity overlay.
    expect(useOperationsStore.getState().projects[P]?.size).toBe('expanded');
    expect(screen.queryByTestId('build-exact-question')).toBeNull();
  });

  it('asks one question at a time for a missing input, never inventing one, then starts the build', async () => {
    getPreviewDefinition.mockResolvedValue(
      definition({
        buildable: false,
        missing: ['material', 'depth'],
        parameters: {
          depth: { value: 2, status: 'required_input', unit: 'mm', question: 'How thick should this be?', assumed: true },
          material: { value: null, status: 'required_input', unit: null, question: 'What material is it made of?', assumed: false },
        },
      }),
    );
    getManufacturingContext.mockResolvedValue({
      ref: 'MFC-1@1',
      record: {
        selected_material: { value: 'PETG', authority: 'user_stated' },
        material_candidates: { value: ['PETG', 'ASA'], authority: 'assumption' },
      },
    });
    commitParameters.mockResolvedValue({ build: { status: 'running', job_id: 'j2', operation_id: 'job:j2' } });
    stage([MESH, PSK], { facts: { described: true } });
    await clickBuild();

    // The depth first: concrete choices from the sketch, or another value. Nothing is sent yet.
    const first = await screen.findByTestId('build-exact-question');
    expect(first).toHaveAccessibleName('How thick should this be?');
    expect(within(first).getByRole('button', { name: 'A quarter of its width (10 mm)' })).toBeInTheDocument();
    expect(commitParameters).not.toHaveBeenCalled();
    expect(requestExactBuild).not.toHaveBeenCalled();
    await userEvent.type(within(first).getByRole('spinbutton', { name: 'Another thickness (mm)' }), '6');
    await userEvent.click(within(first).getByRole('button', { name: 'Use this' }));

    // Then the material, from the manufacturing notes.
    const second = await screen.findByRole('group', { name: 'What material is it made of?' });
    expect(within(second).getByRole('button', { name: 'ASA (a candidate in your manufacturing notes)' })).toBeInTheDocument();
    expect(commitParameters).not.toHaveBeenCalled();
    await userEvent.click(within(second).getByRole('button', { name: 'PETG (from your manufacturing notes)' }));

    await waitFor(() => expect(commitParameters).toHaveBeenCalledTimes(1));
    expect(commitParameters).toHaveBeenCalledWith(P, 'PSK-SLOT', {
      base_revision: 1,
      feature_id: 'feat-extrude-1',
      values: { depth: 6, material: 'PETG' },
      source: 'numeric',
    });
    expect(requestExactBuild).not.toHaveBeenCalled();
    expect(await screen.findByTestId('build-exact-notice')).toHaveTextContent('Building the exact model');
  });

  it('says what must be done first, and never starts a build from an unapproved sketch', async () => {
    getPreviewDefinition.mockResolvedValue(definition({ approved: false }));
    stage([MESH, PSK], { facts: { described: true } });
    await clickBuild();
    const blocked = await screen.findByTestId('build-exact-blocked');
    expect(blocked).toHaveTextContent('Approve the editable sketch first');
    expect(within(blocked).getByRole('button', { name: 'Open the editable sketch' })).toBeInTheDocument();
    expect(requestExactBuild).not.toHaveBeenCalled();
    expect(commitParameters).not.toHaveBeenCalled();
  });

  it('without a parametric sketch, still prepares a design request', async () => {
    const onBuildExact = vi.fn();
    stage([MESH], { facts: { described: true }, onBuildExact });
    await clickBuild();
    expect(onBuildExact).toHaveBeenCalled();
    expect(getPreviewDefinition).not.toHaveBeenCalled();
  });
});
