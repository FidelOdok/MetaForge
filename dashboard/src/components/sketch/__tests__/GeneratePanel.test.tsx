import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '../../../test/test-utils';
import { EMPTY_GENERATION, GENERATE_DEBOUNCE_MS, useSketchGenerationStore } from '../../../store/sketch-generation-store';
import type { GeneratedConcept, GenerationOverview } from '../../../api/endpoints/sketchGeneration';
import type { ReferenceRecord } from '../../../api/endpoints/create';

const startGeneration = vi.fn();
const listGenerations = vi.fn();
const acceptGeneration = vi.fn();
const rejectGeneration = vi.fn();
const branchGeneration = vi.fn();
const compareGenerations = vi.fn();
vi.mock('../../../api/endpoints/sketchGeneration', async (orig) => ({
  ...(await orig<typeof import('../../../api/endpoints/sketchGeneration')>()),
  startGeneration: (...a: unknown[]) => startGeneration(...a),
  listGenerations: (...a: unknown[]) => listGenerations(...a),
  acceptGeneration: (...a: unknown[]) => acceptGeneration(...a),
  rejectGeneration: (...a: unknown[]) => rejectGeneration(...a),
  branchGeneration: (...a: unknown[]) => branchGeneration(...a),
  compareGenerations: (...a: unknown[]) => compareGenerations(...a),
}));

const getCreation = vi.fn();
const putReference = vi.fn();
vi.mock('../../../api/endpoints/create', async (orig) => ({
  ...(await orig<typeof import('../../../api/endpoints/create')>()),
  getCreation: (...a: unknown[]) => getCreation(...a),
  putReference: (...a: unknown[]) => putReference(...a),
}));

import { GeneratePanel, modeFor } from '../GeneratePanel';
import { ReferenceChip } from '../../create/ReferenceChip';

const P = 'proj-gen';

function concept(id: string, extra: Partial<GeneratedConcept> = {}): GeneratedConcept {
  return {
    node_id: id,
    name: `Generated concept ${id}`,
    generation_status: 'proposed',
    review_required: false,
    generation_id: 'g1',
    variant_index: 0,
    item_key: null,
    item_ref: null,
    accepted_as: null,
    baseline_for: null,
    mode: 'text',
    style: 'industrial_sketch',
    intent: 'a stool',
    provider: 'fake',
    model: 'm',
    base_revision: 'none',
    preservation: null,
    latency_s: 1.2,
    size: '1024x1024',
    stale_reason: null,
    created_at: null,
    authority: 'illustrative',
    label: 'Generated concept · illustrative',
    editable: false,
    ...extra,
  };
}

const EMPTY: GenerationOverview = { lines: [], proposals: [] };

function creation(references: ReferenceRecord[] = []) {
  return {
    creation: {
      project_id: P,
      version: 3,
      brief: {
        items: {},
        measurements: [],
        assumptions: [],
        references: references.map((r) => r.work_product_id),
        reference_details: references,
      },
      route: { route: null, process: '', machine_id: null, material: '', supplier: '', note: '' },
      inspection_plan: [],
      builds: [],
      history: [],
      updated_at: '',
    },
    readiness: {},
    brief_keys: [],
  };
}

function record(id: string, extra: Partial<ReferenceRecord> = {}): ReferenceRecord {
  return {
    work_product_id: id,
    name: `Photo ${id}`,
    role: 'inspiration',
    associations: [],
    active: true,
    protected_features: [],
    source_hash: 'h',
    versions: [],
    facts: [],
    disclosures: [],
    ...extra,
  };
}

const started = { job_id: 'j1', status: 'running', operation_id: 'job:j1', mode: 'text', target_key: 'CONCEPT:full', base_revision: 'none', reused: false, expected_seconds: 25, authority: 'illustrative' };

beforeEach(() => {
  useSketchGenerationStore.setState({ sessions: {} });
  for (const m of [startGeneration, listGenerations, acceptGeneration, rejectGeneration, branchGeneration, compareGenerations, getCreation, putReference]) m.mockReset();
  listGenerations.mockResolvedValue(EMPTY);
  getCreation.mockResolvedValue(creation());
  startGeneration.mockResolvedValue(started);
});

function goButton(): HTMLElement {
  const all = screen.getAllByRole('button', { name: /Generate/ });
  return all[all.length - 1]!;
}

function open() {
  fireEvent.click(screen.getByRole('button', { name: /Generate$/ }));
}

describe('modeFor', () => {
  it('prefers the sketch, then references, then text', () => {
    expect(modeFor('SKT-A', 2)).toBe('sketch');
    expect(modeFor(null, 1)).toBe('reference');
    expect(modeFor(null, 0)).toBe('text');
  });
});

describe('GeneratePanel', () => {
  it('chooses the mode from context and debounces Generate into one request', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      render(<GeneratePanel projectId={P} sketchKey={null} sketchRevision={null} sketchUnsaved={false} />);
      open();
      expect(screen.getByTestId('generate-mode')).toHaveTextContent('From your description');
      fireEvent.change(screen.getByLabelText('Describe it'), { target: { value: 'a three legged stool' } });
      const go = goButton;
      fireEvent.click(go());
      fireEvent.click(go());
      fireEvent.click(go());
      expect(startGeneration).not.toHaveBeenCalled();
      await act(async () => {
        vi.advanceTimersByTime(GENERATE_DEBOUNCE_MS + 10);
      });
      await waitFor(() => expect(startGeneration).toHaveBeenCalledTimes(1));
      const [project, body] = startGeneration.mock.calls[0]!;
      expect(project).toBe(P);
      expect(body).toMatchObject({ intent: 'a three legged stool', style: 'industrial_sketch', n_variants: 1, mask: null, sketch_key: null, expected_baseline: 'none' });
    } finally {
      vi.useRealTimers();
    }
  });

  it('refines the saved sketch when there is one', async () => {
    render(<GeneratePanel projectId={P} sketchKey="SKT-A" sketchRevision="SKT-A@2" sketchUnsaved={false} />);
    open();
    expect(screen.getByTestId('generate-mode')).toHaveTextContent('From your sketch');
    fireEvent.change(screen.getByLabelText('Describe it'), { target: { value: 'clean it up' } });
    fireEvent.change(screen.getByLabelText('Style'), { target: { value: 'line_art' } });
    fireEvent.click(goButton());
    await waitFor(() => expect(startGeneration).toHaveBeenCalled(), { timeout: 2000 });
    expect(startGeneration.mock.calls[0]![1]).toMatchObject({ sketch_key: 'SKT-A', sketch_revision: 'SKT-A@2', style: 'line_art' });
  });

  it('sends the selected region as the mask of a region-only request', async () => {
    useSketchGenerationStore.getState().setRegion(P, [-0.1, -0.2], [0.2, 0.1]);
    render(<GeneratePanel projectId={P} sketchKey={null} sketchRevision={null} sketchUnsaved={false} />);
    open();
    expect(screen.getByTestId('generate-mode')).toHaveTextContent('selected region only');
    expect(screen.getByLabelText('Change only the selected region')).toBeChecked();
    fireEvent.change(screen.getByLabelText('Describe it'), { target: { value: 'round the seat' } });
    fireEvent.click(goButton());
    await waitFor(() => expect(startGeneration).toHaveBeenCalled(), { timeout: 2000 });
    const body = startGeneration.mock.calls[0]![1];
    expect(body.mask.polygon).toEqual([[-0.1, -0.2], [0.2, -0.2], [0.2, 0.1], [-0.1, 0.1]]);
    expect(body.mask_instruction).toBe('round the seat');
  });

  it('asks before going past the hourly image cap, and sends the confirmation', async () => {
    startGeneration.mockRejectedValueOnce({
      response: { status: 409, data: { detail: { code: 'spend_confirmation_required', message: '20 concept images in the last hour' } } },
    });
    render(<GeneratePanel projectId={P} sketchKey={null} sketchRevision={null} sketchUnsaved={false} />);
    open();
    fireEvent.change(screen.getByLabelText('Describe it'), { target: { value: 'a lamp' } });
    fireEvent.click(goButton());
    expect(await screen.findByRole('alert', {}, { timeout: 2000 })).toHaveTextContent('20 concept images');
    fireEvent.click(screen.getByRole('button', { name: 'Generate anyway' }));
    await waitFor(() => expect(startGeneration).toHaveBeenCalledTimes(2), { timeout: 2000 });
    expect(startGeneration.mock.calls[1]![1].confirm_spend).toBe(true);
  });

  it('includes only active references and shows their roles', async () => {
    getCreation.mockResolvedValue(creation([record('r1', { role: 'geometry' }), record('r2', { active: false })]));
    render(<GeneratePanel projectId={P} sketchKey={null} sketchRevision={null} sketchUnsaved={false} />);
    open();
    await waitFor(() => expect(screen.getAllByTestId('reference-chip')).toHaveLength(2));
    expect(screen.getByTestId('generate-mode')).toHaveTextContent('From your references');
    expect(screen.getByLabelText('Role of Photo r1')).toHaveValue('geometry');
    fireEvent.change(screen.getByLabelText('Describe it'), { target: { value: 'a chair' } });
    fireEvent.click(goButton());
    await waitFor(() => expect(startGeneration).toHaveBeenCalled(), { timeout: 2000 });
    expect(startGeneration.mock.calls[0]![1].reference_ids).toEqual(['r1']);
  });
});

describe('variants strip', () => {
  it('accepts a proposal against the baseline the person sees, and compares two', async () => {
    listGenerations.mockResolvedValue({ lines: [], proposals: [concept('a'), concept('b', { review_required: true, preservation: { method: 'composited', score: 0.5, passed: false, failures: ['mounting holes changed'], threshold: 0.9 } })] });
    acceptGeneration.mockResolvedValue({ accepted: 'GSK-CONCEPT@1', node_id: 'acc', previous_baseline: null });
    compareGenerations.mockResolvedValue({ a: concept('a'), b: concept('b'), pixel_difference: 0.42, changed: ['prompt'] });
    render(<GeneratePanel projectId={P} sketchKey={null} sketchRevision={null} sketchUnsaved={false} />);
    const strip = await screen.findByTestId('variants-strip');
    expect(strip).toHaveTextContent('Generated concept · illustrative');
    expect(strip).toHaveTextContent('Check it: mounting holes changed');
    fireEvent.click(screen.getAllByRole('button', { name: 'Accept this concept' })[0]!);
    await waitFor(() => expect(acceptGeneration).toHaveBeenCalledWith(P, 'a', 'none'));
    const compare = screen.getAllByRole('button', { name: 'Compare this concept' });
    fireEvent.click(compare[0]!);
    fireEvent.click(compare[1]!);
    expect(useSketchGenerationStore.getState().session(P).compare).toEqual(['a', 'b']);
    await waitFor(() => expect(screen.getByTestId('compare-view')).toHaveTextContent('42% of the image differs'));
    expect(compareGenerations).toHaveBeenCalledWith(P, 'a', 'b');
  });

  it('keeps the accepted concept in view while a new one is proposed', async () => {
    listGenerations.mockResolvedValue({
      lines: [{ key: 'GSK-CONCEPT', baseline_for: 'CONCEPT', head_ref: 'GSK-CONCEPT@2', head: concept('acc', { generation_status: 'accepted', item_ref: 'GSK-CONCEPT@2' }), history: [] }],
      proposals: [concept('new')],
    });
    render(<GeneratePanel projectId={P} sketchKey={null} sketchRevision={null} sketchUnsaved={false} />);
    expect(await screen.findByText('Accepted · GSK-CONCEPT@2')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Accept this concept' }));
    await waitFor(() => expect(acceptGeneration).toHaveBeenCalledWith(P, 'new', 'GSK-CONCEPT@2'));
  });
});

describe('ReferenceChip', () => {
  it('sets the role and the include toggle through the reference route', async () => {
    putReference.mockResolvedValue({ ...creation(), reference: null, conflicts: [] });
    const onLocal = vi.fn();
    render(<ReferenceChip projectId={P} record={record('wp1')} onLocalChange={onLocal} />);
    fireEvent.change(screen.getByLabelText('Role of Photo wp1'), { target: { value: 'specification' } });
    await waitFor(() => expect(putReference).toHaveBeenCalledWith(P, 'wp1', { role: 'specification' }));
    fireEvent.click(screen.getByRole('button', { name: 'Leave Photo wp1 out of the next instruction' }));
    await waitFor(() => expect(putReference).toHaveBeenCalledWith(P, 'wp1', { active: false }));
    expect(onLocal).toHaveBeenCalledWith({ active: false });
    // Management opens only when asked.
    expect(screen.queryByLabelText('Keep unchanged')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'More about Photo wp1' }));
    expect(screen.getByLabelText('Keep unchanged')).toBeInTheDocument();
  });
});

describe('store', () => {
  it('starts with one variant and no mask', () => {
    expect(EMPTY_GENERATION.variants).toBe(1);
    expect(useSketchGenerationStore.getState().session(P).mask).toBeNull();
  });
});
