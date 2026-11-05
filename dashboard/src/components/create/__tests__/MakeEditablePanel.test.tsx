import { beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor } from '../../../test/test-utils';
import * as api from '../../../api/endpoints/reconstruction';
import type { FeatureHistoryProposal, ProposalView, ReconstructionView } from '../../../api/endpoints/reconstruction';
import { MakeEditablePanel } from '../MakeEditablePanel';

const P = 'p1';

function proposal(over: Partial<FeatureHistoryProposal> = {}): FeatureHistoryProposal {
  return {
    component_id: 'CMP-enclosure',
    component_name: 'Enclosure',
    status: 'proposed',
    features: [
      { id: 'profile', kind: 'sketch_profile', name: 'Main profile', plain: 'a rectangle on XY', depends_on: [], parameters: {}, placement: null, confidence: 0.7, suggested_by: 'deterministic', provenance: [], dfm_rules: [], buildable: true, existing_ref: null, status: 'proposed', note: '' },
      { id: 'body', kind: 'extrude', name: 'Extrude', plain: 'a block', depends_on: ['profile'], parameters: {}, placement: null, confidence: 0.7, suggested_by: 'deterministic', provenance: [], dfm_rules: [], buildable: true, existing_ref: null, status: 'proposed', note: '' },
      { id: 'llm-rib-1', kind: 'rib', name: 'Ribs', plain: 'ribs', depends_on: ['body'], parameters: {}, placement: null, confidence: null, suggested_by: 'llm', provenance: [], dfm_rules: [], buildable: false, existing_ref: null, status: 'proposed', note: '' },
    ],
    assumptions: [],
    ambiguities: [
      { id: 'shell-hollow', question: 'Should Enclosure be hollow, or solid?', options: [{ id: 'hollow', label: 'Hollow' }, { id: 'solid', label: 'Solid' }], feature_id: 'shell', consequential: true, default: 'hollow', choice: null },
    ],
    dfm_strategy: { profile_id: 'fdm', profile_version: '1.1.0', process: 'FDM', process_authority: 'user_stated', context_ref: 'MFC-PROJECT@1', rules: [], text: 'Strategy for FDM' },
    summary: 'the enclosure: a block, hollowed out',
    inputs: { assembly_decision_id: 'd1', layout_ref: 'DSK-1@1', layout_node_id: null, mesh_node_id: null, reference_ids: [], sketch_refs: [], context_ref: 'MFC-PROJECT@1' },
    llm: { requested: false, outcome: 'not_requested', provider: null, model: null, suggestions: 0, note: '' },
    accepted_sketches: {},
    ...over,
  };
}

function viewWith(p: ProposalView | null, over: Partial<ReconstructionView> = {}): ReconstructionView {
  return {
    project_id: P,
    concept_accepted: true,
    assembly_decision_id: 'd1',
    components: [{ component_id: 'CMP-enclosure', name: 'Enclosure', source: 'made', proposal: p }],
    summary: p ? "We'd build this as: the enclosure: a block, hollowed out." : '',
    accepted: false,
    ...over,
  };
}

const PV: ProposalView = {
  component_id: 'CMP-enclosure',
  component_name: 'Enclosure',
  item_key: 'FHP-ENCLOSURE',
  revision: 1,
  item_ref: 'FHP-ENCLOSURE@1',
  node_id: 'n1',
  status: 'proposed',
  staleness: 'current',
  open_questions: 1,
  proposal: proposal(),
};

describe('Create: Make it editable', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('shows the plain proposal and asks the open question before accepting', async () => {
    vi.spyOn(api, 'getReconstruction').mockResolvedValue(viewWith(PV));
    const accept = vi.spyOn(api, 'acceptReconstruction').mockResolvedValue({
      project_id: P,
      proposal: { ...PV, status: 'accepted' },
      accepted: ['profile', 'body'],
      rejected: ['llm-rib-1'],
      sketches: [{ feature_id: 'body', item_ref: 'PSK-REC-ENCLOSURE@1', node_id: 's1', approval_id: 'parametric_sketch:s1', required_input: ['depth'] }],
      existing: {},
      process_written: null,
    });
    const user = userEvent.setup();
    render(<MakeEditablePanel projectId={P} onClose={() => {}} />);
    expect(await screen.findByTestId('make-editable-summary')).toHaveTextContent("We'd build this as: the enclosure: a block, hollowed out.");
    // No feature tree in Create: no parameters, no feature kinds.
    expect(screen.queryByText('Extrude')).not.toBeInTheDocument();
    expect(screen.queryByText('Main profile')).not.toBeInTheDocument();
    const button = screen.getByTestId('make-editable-accept');
    expect(button).toBeDisabled();
    expect(screen.getByText('1 question to answer first')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Solid' }));
    expect(button).toBeEnabled();
    await user.click(button);
    await waitFor(() => expect(accept).toHaveBeenCalled());
    // The concept's own features are accepted; the model's suggestion is not.
    expect(accept).toHaveBeenCalledWith(P, 'FHP-ENCLOSURE', {
      feature_ids: ['profile', 'body'],
      choices: { 'shell-hollow': 'solid' },
      base_revision: 1,
    });
    expect(await screen.findByTestId('make-editable-done')).toHaveTextContent('1 part wait for your sign-off');
  });

  it('asks for a proposal when the accepted concept has none yet', async () => {
    const get = vi
      .spyOn(api, 'getReconstruction')
      .mockResolvedValueOnce(viewWith(null))
      .mockResolvedValue(viewWith(PV));
    const propose = vi.spyOn(api, 'proposeReconstruction').mockResolvedValue({
      project_id: P,
      operation_id: 'job:reconstruction-p1',
      proposals: [PV],
      reused: [],
      skipped: [],
      summary: '',
    });
    render(<MakeEditablePanel projectId={P} onClose={() => {}} />);
    await waitFor(() => expect(propose).toHaveBeenCalledTimes(1));
    expect(await screen.findByTestId('make-editable-summary')).toBeInTheDocument();
    expect(get.mock.calls.length).toBeGreaterThan(1);
  });

  it('says a concept must be chosen first', async () => {
    vi.spyOn(api, 'getReconstruction').mockResolvedValue(viewWith(null, { concept_accepted: false, components: [] }));
    const propose = vi.spyOn(api, 'proposeReconstruction');
    render(<MakeEditablePanel projectId={P} onClose={() => {}} />);
    expect(await screen.findByText(/Choose a concept first/)).toBeInTheDocument();
    expect(propose).not.toHaveBeenCalled();
  });
});
