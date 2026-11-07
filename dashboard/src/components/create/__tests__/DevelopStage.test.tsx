import { beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor, within } from '../../../test/test-utils';
import type { TwinNode } from '../../../types/twin';
import type { CreationView } from '../../../api/endpoints/create';

vi.mock('../../../hooks/use-twin', async (orig) => ({
  ...(await orig<typeof import('../../../hooks/use-twin')>()),
  useNodeVersionHistory: () => ({ data: undefined }),
}));
vi.mock('../../preview/PreviewHost', () => ({
  PreviewHost: ({ node }: { node: TwinNode }) => <div data-testid="preview-host">{node.name}</div>,
}));
vi.mock('../../preview/ParametricStage', () => ({ ParametricStage: () => <div data-testid="parametric-stage" /> }));
const getReconstruction = vi.fn();
vi.mock('../../../api/endpoints/reconstruction', async (orig) => ({
  ...(await orig<typeof import('../../../api/endpoints/reconstruction')>()),
  getReconstruction: (...a: unknown[]) => getReconstruction(...a),
}));
// Build exact model (FORGE-808): the routes it calls.
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

import { DevelopStage } from '../DevelopStage';
import { definition } from '../../../lib/preview/__tests__/fixtures';
import { useOperationsStore } from '../../../store/operations-store';

const P = 'proj-develop';

function node(id: string, props: Record<string, string | number | boolean>, updatedAt = '2026-11-05T10:00:00Z'): TwinNode {
  return { id, name: id, type: 'work_product', domain: '', status: '', properties: props, updatedAt } as TwinNode;
}
const MESH = node('Reference model', { wp_type: 'render', format: 'glb', generated_by: 'twin.generate_concept_mesh', authority: 'illustrative' });
const CAD = node('Bracket', { wp_type: 'cad_model', format: 'step' });
const PSK = node('Parametric sketch: Slot', { wp_type: 'parametric_sketch', item_key: 'PSK-SLOT', item_revision: 1 });

function creation(blockers: { id: string; message: string; action: string }[] = []): CreationView {
  return {
    brief_keys: [],
    readiness: { stage: 'design_in_development', stage_index: 1, blockers, next_action: '', has_concept: true, has_engineering_model: false },
  } as unknown as CreationView;
}

function develop(nodes: TwinNode[], extra: Partial<Parameters<typeof DevelopStage>[0]> = {}) {
  const props = {
    projectId: P,
    nodes,
    view: creation(),
    selectedId: null,
    onSelect: vi.fn(),
    working: false,
    engineeringChecks: null,
    onAsk: vi.fn(),
    onSketchFix: vi.fn(),
    ...extra,
  };
  render(<DevelopStage {...props} />);
  return props;
}

beforeEach(() => {
  getReconstruction.mockReset().mockResolvedValue({ concept_accepted: false, accepted: false, components: [], summary: '' });
  getPreviewDefinition.mockReset();
  commitParameters.mockReset();
  requestExactBuild.mockReset();
  getManufacturingContext.mockReset();
  useOperationsStore.getState().reset();
});

describe('Develop', () => {
  it('has exactly one primary action, and the rest in one More menu', async () => {
    develop([MESH]);
    expect(screen.getAllByTestId('stage-primary')).toHaveLength(1);
    expect(screen.getByTestId('stage-primary')).toHaveTextContent('Ask for the exact model');
    expect(screen.getAllByRole('button', { name: /More/ })).toHaveLength(1);
    await userEvent.click(screen.getByRole('button', { name: /More/ }));
    expect(screen.getAllByRole('menuitem').map((m) => m.textContent)).toEqual(['Show as simple 3D parts', 'Discuss this model']);
  });

  it('is not a second editor: no concept part editing, no automatic refine loop, no sketch tools', () => {
    develop([MESH, PSK]);
    expect(screen.queryByText(/Refine the model automatically/)).toBeNull();
    expect(screen.queryByTestId('concept-parts')).toBeNull();
    expect(screen.queryByRole('button', { name: /Sketch/ })).toBeNull();
    expect(screen.queryByText(/Generate a concept/)).toBeNull();
  });

  it('asks the assistant for the exact model, or for a change once one exists', async () => {
    const first = develop([MESH]);
    await userEvent.click(screen.getByTestId('stage-primary'));
    expect(first.onAsk).toHaveBeenCalledWith('Build an exact, dimensioned model from the handed-over concept.');
  });

  it('describes a change once a dimensioned model exists', async () => {
    const props = develop([CAD]);
    expect(screen.getByTestId('stage-primary')).toHaveTextContent('Describe a change');
    await userEvent.click(screen.getByTestId('stage-primary'));
    expect(props.onAsk).toHaveBeenCalledWith('');
  });

  it('lists what needs attention from the twin', () => {
    develop([CAD], { view: creation([{ id: 'check:x', message: 'The wall is thinner than 1.2 mm', action: 'Ask for a thicker wall' }]) });
    expect(screen.getByTestId('develop-attention')).toHaveTextContent('The wall is thinner than 1.2 mm');
  });

  it('offers Make it editable for an accepted concept', async () => {
    getReconstruction.mockResolvedValue({ concept_accepted: true, accepted: false, components: [], summary: '' });
    develop([MESH]);
    await waitFor(() => expect(screen.getByTestId('stage-primary')).toHaveTextContent('Make it editable'));
  });
});

describe('Build exact model', () => {
  it('starts the exact build when every required input is known', async () => {
    getPreviewDefinition.mockResolvedValue(definition());
    requestExactBuild.mockResolvedValue({ job_id: 'j1', status: 'running' });
    const props = develop([MESH, PSK]);
    expect(screen.getByTestId('stage-primary')).toHaveTextContent('Build exact model');
    await userEvent.click(screen.getByTestId('stage-primary'));
    await waitFor(() => expect(requestExactBuild).toHaveBeenCalledTimes(1));
    expect(requestExactBuild).toHaveBeenCalledWith('PSK-SLOT', P, null, { featureId: 'feat-extrude-1' });
    expect(commitParameters).not.toHaveBeenCalled();
    expect(props.onAsk).not.toHaveBeenCalled();
    expect(await screen.findByTestId('build-exact-notice')).toHaveTextContent('Building the exact model. Its progress is in the activity panel.');
    expect(useOperationsStore.getState().projects[P]?.size).toBe('expanded');
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
    develop([MESH, PSK]);
    await userEvent.click(screen.getByTestId('stage-primary'));

    const first = await screen.findByTestId('build-exact-question');
    expect(first).toHaveAccessibleName('How thick should this be?');
    expect(commitParameters).not.toHaveBeenCalled();
    await userEvent.type(within(first).getByRole('spinbutton', { name: 'Another thickness (mm)' }), '6');
    await userEvent.click(within(first).getByRole('button', { name: 'Use this' }));

    const second = await screen.findByRole('group', { name: 'What material is it made of?' });
    await userEvent.click(within(second).getByRole('button', { name: 'PETG (from your manufacturing notes)' }));

    await waitFor(() => expect(commitParameters).toHaveBeenCalledTimes(1));
    expect(commitParameters).toHaveBeenCalledWith(P, 'PSK-SLOT', {
      base_revision: 1,
      feature_id: 'feat-extrude-1',
      values: { depth: 6, material: 'PETG' },
      source: 'numeric',
    });
    expect(requestExactBuild).not.toHaveBeenCalled();
  });

  it('sends a sketch fix back to Imagine, and never starts a build from an unapproved sketch', async () => {
    getPreviewDefinition.mockResolvedValue(definition({ approved: false }));
    const props = develop([MESH, PSK]);
    await userEvent.click(screen.getByTestId('stage-primary'));
    const blocked = await screen.findByTestId('build-exact-blocked');
    expect(blocked).toHaveTextContent('Approve the editable sketch first');
    await userEvent.click(within(blocked).getByRole('button', { name: 'Open the editable sketch' }));
    expect(props.onSketchFix).toHaveBeenCalledWith('editable');
    expect(requestExactBuild).not.toHaveBeenCalled();
  });
});
