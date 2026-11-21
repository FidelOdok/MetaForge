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

// No WebGL in jsdom: the 3D board stands in as a labelled box.
vi.mock('../develop/BoardModel3D', () => ({
  BoardModel3D: ({ src, label }: { src: string; label: string }) => (
    <div data-testid="board-3d" data-src={src}>
      {label} in 3D
    </div>
  ),
}));

// AuthedImg has its own tests (it loads the file with the token); here it is
// a plain <img> so these tests can check which file each view asks for.
vi.mock('../../shared/AuthedImg', () => ({
  AuthedImg: (props: Record<string, unknown>) => <img {...props} />,
  AuthedFileLink: ({ src, children, ...rest }: { src: string; children: import('react').ReactNode } & Record<string, unknown>) => (
    <a href={src} {...rest}>
      {children}
    </a>
  ),
}));


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

  it('keeps what needs attention behind one button that opens and closes it', async () => {
    develop([CAD], { view: creation([{ id: 'check:x', message: 'The wall is thinner than 1.2 mm', action: 'Ask for a thicker wall' }]) });
    const button = screen.getByRole('button', { name: /Needs attention/ });
    expect(button).toHaveTextContent('1');
    expect(button).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByTestId('develop-attention')).toBeNull();
    await userEvent.click(button);
    expect(button).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByTestId('develop-attention')).toHaveTextContent('The wall is thinner than 1.2 mm');
    await userEvent.click(button);
    expect(screen.queryByTestId('develop-attention')).toBeNull();
  });

  it('scrolls an opened detail into view, so a click under a tall model shows something', async () => {
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    develop([CAD], { view: creation([{ id: 'check:x', message: 'The wall is thinner than 1.2 mm', action: 'Ask for a thicker wall' }]) });
    expect(scroll).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: /Needs attention/ }));
    expect(scroll).toHaveBeenCalledWith({ block: 'nearest', behavior: 'smooth' });
    delete (Element.prototype as Partial<Element>).scrollIntoView;
  });

  it('puts the model first in a wide column', () => {
    develop([CAD]);
    const col = document.querySelector('.cv-stage-col');
    expect(col).toHaveClass('is-model');
    // The studio's main area opens on the 3D view: the view switch, then the model.
    const main = document.querySelector('.cv-develop-main');
    expect(main?.children[1]).toHaveAttribute('aria-label', 'Product');
    expect(screen.getByRole('tab', { name: '3D' })).toHaveAttribute('aria-selected', 'true');
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


// The system schematic and each board's own sheets: not every component sits
// on the same board.
const ARCH = {
  ...node('Lamp architecture', { wp_type: 'system_architecture' }),
  systemArchitecture: {
    systemName: 'Lamp',
    components: [
      { name: 'Mains module', kind: 'module', location: 'base' },
      { name: 'Controller board', kind: 'board', location: 'base', parts: [{ ref: 'U1' }, { ref: 'U2' }] },
      { name: 'LED board', kind: 'board', location: 'head', parts: [{ ref: 'D1' }] },
    ],
    interfaces: [
      { from: 'Mains module', to: 'Controller board', kind: 'cable', wires: 2, length_mm: 120 },
      { from: 'Controller board', to: 'LED board', kind: 'cable', from_connector: 'J4', to_connector: 'J1', wires: 4, length_mm: 450 },
    ],
    boards: ['Controller board', 'LED board'],
  },
} as TwinNode;
const CTL_SCH = {
  ...node('Controller schematic', { wp_type: 'schematic' }),
  designFiles: { files: [{ path: 'ctl.kicad_sch' }], previews: ['ctl.svg'], layers: [], buildOutputs: [], sources: [], board: 'Controller board' },
} as TwinNode;

describe('System and boards', () => {
  it('asks for a system schematic when there is none', async () => {
    const props = develop([CAD]);
    await userEvent.click(screen.getByRole('tab', { name: 'System' }));
    expect(screen.getByTestId('system-empty')).toHaveTextContent('No system schematic yet');
    await userEvent.click(within(screen.getByTestId('system-empty')).getByRole('button', { name: 'Describe the system' }));
    expect(props.onAsk).toHaveBeenCalledWith(expect.stringContaining('boards, bought modules and cables'));
  });

  it('draws boards, the module and the cables, and opens a board', async () => {
    develop([CAD, ARCH, CTL_SCH]);
    await userEvent.click(screen.getByRole('tab', { name: 'System' }));
    const diagram = screen.getByTestId('system-diagram');
    expect(diagram).toHaveTextContent('Controller board');
    expect(diagram).toHaveTextContent('bought · interfaces only');
    expect(diagram).toHaveTextContent('J4→J1 · 4 wires · 450 mm');
    await userEvent.click(screen.getByRole('button', { name: 'Open the schematic of Controller board' }));
    expect(screen.getByRole('tab', { name: 'Schematic' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByAltText('Controller schematic: ctl.svg')).toBeInTheDocument();
  });

  it('shows each board its own sheets, and offers to generate a missing one', async () => {
    const props = develop([CAD, ARCH, CTL_SCH]);
    await userEvent.click(screen.getByRole('tab', { name: 'Schematic' }));
    await userEvent.click(screen.getByRole('button', { name: 'LED board' }));
    expect(screen.getByTestId('board-schematic-empty')).toHaveTextContent('No schematic for LED board yet');
    await userEvent.click(screen.getByRole('button', { name: 'Generate schematic' }));
    expect(props.onAsk).toHaveBeenCalledWith('Generate the schematic for LED board and run ERC.');
  });

  it("shows a board's layout or, when the layout was recorded with one, its populated 3D model", async () => {
    const pcb = {
      ...node('Controller layout', { wp_type: 'pcb_layout' }),
      designFiles: {
        files: [{ path: 'ctl.kicad_pcb' }],
        previews: ['preview/ctl-pcb.svg', 'preview/ctl-3d.glb'],
        layers: [],
        buildOutputs: [],
        sources: [],
        board: 'Controller board',
      },
    } as TwinNode;
    develop([CAD, ARCH, CTL_SCH, pcb]);
    await userEvent.click(screen.getByRole('tab', { name: 'PCB' }));
    const sheets = screen.getByTestId('board-pcb');
    // The GLB is a model, never drawn as an image.
    expect(within(sheets).getAllByRole('img').map((i) => i.getAttribute('src'))).toEqual([
      expect.stringContaining('ctl-pcb.svg'),
    ]);
    await userEvent.click(within(sheets).getByRole('tab', { name: '3D' }));
    expect(screen.getByTestId('board-3d')).toHaveTextContent('Controller board in 3D');
    expect(screen.getByTestId('board-3d').getAttribute('data-src')).toContain('ctl-3d.glb');
  });

  it('puts the nearest missing board sheet in the next step', () => {
    develop([CAD, ARCH, CTL_SCH]);
    expect(screen.getByTestId('develop-next')).toHaveTextContent('Controller board has no layout');
  });
});

it('draws a link marked only by its older interface_type as a cable', async () => {
  const arch = structuredClone(ARCH) as TwinNode;
  arch.systemArchitecture!.interfaces = arch.systemArchitecture!.interfaces.map((i) => ({ ...i, kind: undefined, interface_type: 'cable' }));
  develop([CAD, arch]);
  expect(screen.getByRole('button', { name: /Cables/ })).toHaveTextContent('2 between boards');
  await userEvent.click(screen.getByRole('tab', { name: 'System' }));
  expect(document.querySelectorAll('.cv-system-wire.is-cable')).toHaveLength(2);
});
