import { beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor } from '../../../test/test-utils';
import * as api from '../../../api/endpoints/conceptParts';
import type { ConceptPartsState, ConceptRevision, MappedPart } from '../../../api/endpoints/conceptParts';
import { ConceptPartsPanel, confirmedPartFor, workingRevision } from '../ConceptPartsPanel';
import { isPendingConceptRevision, preferredDisplayNode } from '../stage-logic';
import { ConceptPartsSection } from '../../viewer/ConceptPartsSection';
import type { TwinNode } from '../../../types/twin';

const P = 'p1';
const SPEND = { used_last_hour: 0, cap: 3, policy_mode: 'off', policy_would_approve: false };

function part(over: Partial<MappedPart>): MappedPart {
  return {
    part: 'tripo_part_0',
    node_index: 0,
    label: null,
    proposed_component_id: null,
    proposed_name: null,
    confidence: 0,
    basis: [],
    candidates: [],
    state: 'question',
    ...over,
  };
}

function rev(over: Partial<ConceptRevision> = {}): ConceptRevision {
  return {
    node_id: 'seg',
    name: '3D concept in parts: Lamp',
    created_at: '2026-11-05T10:00:00Z',
    generated_by: 'twin.segment_concept_mesh',
    concept_revision: 2,
    concept_status: 'proposed',
    operation: 'segmented',
    lineage: null,
    segmentation: {
      provider: 'tripo',
      model_version: 'v1.0-20250506',
      granularity: null,
      task_id: 'task-seg',
      input_kind: 'task_id',
      credits: 40,
      status: 'segmented',
      note: '3 parts, one node each',
      labelled: false,
      part_count: 3,
    },
    mapping: {
      status: 'proposed',
      parts: [
        part({ part: 'tripo_part_0', proposed_component_id: 'CMP-base', proposed_name: 'Base', confidence: 0.82, basis: ['geometry'], state: 'proposed' }),
        part({ part: 'tripo_part_1', node_index: 1 }),
      ],
      questions: [{ part: 'tripo_part_1', question: 'Which component is tripo_part_1?', options: [] }],
      unmatched_components: [],
    },
    regenerated_parts: [],
    completed_parts: [],
    authority: 'illustrative',
    label: 'AI 3D concept in parts, illustrative only',
    ...over,
  };
}

function state(over: Partial<ConceptPartsState> = {}): ConceptPartsState {
  return {
    current: rev({ node_id: 'orig', generated_by: 'twin.generate_concept_mesh', concept_status: 'accepted', concept_revision: 1, segmentation: null, mapping: null, operation: 'generated' }),
    proposals: [],
    components: [
      { component_id: 'CMP-base', name: 'Base' },
      { component_id: 'CMP-shade', name: 'Shade' },
    ],
    active_jobs: [],
    spend: SPEND,
    ...over,
  };
}

const confirmedRev = rev({
  concept_status: 'accepted',
  mapping: {
    status: 'confirmed',
    parts: [
      part({ part: 'tripo_part_0', state: 'confirmed', confirmed_component_id: 'CMP-base', confirmed_name: 'Base' }),
      part({ part: 'tripo_part_2', state: 'confirmed', confirmed_component_id: 'CMP-shade', confirmed_name: 'Shade' }),
    ],
    questions: [],
    unmatched_components: [],
  },
});

const spendError = {
  response: { status: 409, data: { detail: { code: 'spend_confirmation_required', message: '3 paid steps this hour.' } } },
};
const started = { job_id: 'j1', status: 'running', operation_id: 'job:j1', tool: 'x', spend: SPEND, error: null };

describe('ConceptPartsPanel', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('splits the concept into parts, asking before spending past the cap', async () => {
    vi.spyOn(api, 'getConceptParts').mockResolvedValue(state());
    const seg = vi.spyOn(api, 'segmentConcept').mockRejectedValueOnce(spendError).mockResolvedValueOnce(started);
    render(<ConceptPartsPanel projectId={P} selectedComponent={null} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Split into parts' }));
    expect(await screen.findByTestId('parts-spend')).toHaveTextContent('3 paid steps this hour.');
    await userEvent.click(screen.getByRole('button', { name: 'Run it anyway' }));
    await waitFor(() => expect(seg).toHaveBeenLastCalledWith(P, { confirm_spend: true }));
  });

  it('asks only about unclear parts and confirms the answers with the sure proposals', async () => {
    vi.spyOn(api, 'getConceptParts').mockResolvedValue(state({ proposals: [rev()] }));
    const confirm = vi.spyOn(api, 'confirmMapping').mockResolvedValue(rev());
    render(<ConceptPartsPanel projectId={P} selectedComponent={null} />);
    expect(await screen.findByText(/Base · 82% sure/)).toBeInTheDocument();
    expect(screen.getByText('Not sure: please choose')).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText('Component for tripo_part_1'), 'CMP-shade');
    await userEvent.click(screen.getByRole('button', { name: 'Confirm parts' }));
    await waitFor(() =>
      expect(confirm).toHaveBeenCalledWith(P, 'seg', {
        assignments: [{ part: 'tripo_part_1', component_id: 'CMP-shade' }],
        accept_proposals: true,
      }),
    );
  });

  it('redoes the selected part from words', async () => {
    vi.spyOn(api, 'getConceptParts').mockResolvedValue(state({ current: confirmedRev }));
    const redo = vi.spyOn(api, 'regeneratePart').mockResolvedValue(started);
    render(<ConceptPartsPanel projectId={P} selectedComponent={{ componentId: 'CMP-shade', name: 'Shade' }} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Redo this part' }));
    await userEvent.type(screen.getByLabelText('How should this part look?'), 'a dome');
    await userEvent.click(screen.getByRole('button', { name: 'Make it' }));
    await waitFor(() =>
      expect(redo).toHaveBeenCalledWith(P, 'seg', { component_id: 'CMP-shade', source: 'text', prompt: 'a dome', confirm_spend: false }),
    );
  });

  it('seals the selected part, and offers nothing for an unmapped component', async () => {
    vi.spyOn(api, 'getConceptParts').mockResolvedValue(state({ current: confirmedRev }));
    const seal = vi.spyOn(api, 'completePart').mockRejectedValueOnce(spendError).mockResolvedValueOnce(started);
    const { unmount } = render(<ConceptPartsPanel projectId={P} selectedComponent={{ componentId: 'CMP-base', name: 'Base' }} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Seal this part' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Run it anyway' }));
    await waitFor(() =>
      expect(seal).toHaveBeenLastCalledWith(P, 'seg', { component_id: 'CMP-base', completion_mode: 'quick_cap', confirm_spend: true }),
    );
    unmount();
    render(<ConceptPartsPanel projectId={P} selectedComponent={{ componentId: 'CMP-arm', name: 'Arm' }} />);
    await screen.findByTestId('concept-parts');
    expect(screen.queryByRole('button', { name: 'Redo this part' })).not.toBeInTheDocument();
  });

  it('keeps the accepted concept until a proposal is chosen', async () => {
    vi.spyOn(api, 'getConceptParts').mockResolvedValue(state({ proposals: [rev()] }));
    const decide = vi.spyOn(api, 'decideRevision').mockResolvedValue(rev({ concept_status: 'accepted' }));
    render(<ConceptPartsPanel projectId={P} selectedComponent={null} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Use this version' }));
    await waitFor(() => expect(decide).toHaveBeenCalledWith(P, 'seg', 'accept'));
  });
});

describe('concept parts logic', () => {
  it('works on the newest proposal in parts, and finds a confirmed part by component', () => {
    expect(workingRevision(null, [rev()])?.node_id).toBe('seg');
    expect(confirmedPartFor(confirmedRev, 'CMP-shade')?.part).toBe('tripo_part_2');
    expect(confirmedPartFor(confirmedRev, 'CMP-arm')).toBeNull();
  });

  it('never puts a proposed concept revision on the stage by itself', () => {
    const node = (id: string, props: Record<string, unknown>, updatedAt: string): TwinNode =>
      ({ id, name: id, type: 'work_product', properties: { wp_type: 'render', format: 'glb', ...props }, updatedAt }) as unknown as TwinNode;
    const accepted = node('a', { generated_by: 'twin.generate_concept_mesh' }, '2026-11-05T09:00:00Z');
    const proposed = node('b', { generated_by: 'twin.regenerate_concept_part', concept_status: 'proposed' }, '2026-11-05T10:00:00Z');
    expect(isPendingConceptRevision(proposed)).toBe(true);
    expect(preferredDisplayNode([accepted, proposed])?.id).toBe('a');
  });
});

describe('ConceptPartsSection (Engineering)', () => {
  it('shows the segmentation provenance and the part mapping', async () => {
    vi.spyOn(api, 'getConceptParts').mockResolvedValue(state({ proposals: [rev()] }));
    render(<ConceptPartsSection projectId={P} />);
    expect(await screen.findByText(/task-seg/)).toBeInTheDocument();
    expect(screen.getByText('40')).toBeInTheDocument();
    expect(screen.getByTestId('part-row-tripo_part_0')).toHaveTextContent('Base');
    expect(screen.getByTestId('part-row-tripo_part_1')).toHaveTextContent('question');
  });
});
