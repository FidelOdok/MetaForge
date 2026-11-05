import { beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen, within } from '../../../test/test-utils';
import * as api from '../../../api/endpoints/reconstruction';
import type { ProposalView, ReconstructionView } from '../../../api/endpoints/reconstruction';
import { FeatureHistorySection } from '../FeatureHistorySection';

const P = 'p1';

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
  proposal: {
    component_id: 'CMP-enclosure',
    component_name: 'Enclosure',
    status: 'proposed',
    features: [
      {
        id: 'body',
        kind: 'extrude',
        name: 'Extrude',
        plain: 'a block',
        depends_on: ['profile'],
        parameters: {
          depth: { value: 160, unit: 'mm', status: 'estimated', source: { kind: 'l1_layout', ref: 'DSK-1@1', detail: '' }, question: '', minimum: null, minimum_rule: null },
          material: { value: 'PETG', unit: null, status: 'confirmed', source: { kind: 'manufacturing_context', ref: 'MFC-PROJECT@1', detail: '' }, question: '', minimum: null, minimum_rule: null },
        },
        placement: null,
        confidence: 0.7,
        suggested_by: 'deterministic',
        provenance: [],
        dfm_rules: [],
        buildable: true,
        existing_ref: null,
        status: 'proposed',
        note: '',
      },
      {
        id: 'shell',
        kind: 'shell',
        name: 'Shell',
        plain: 'hollowed out',
        depends_on: ['body'],
        parameters: {
          thickness: { value: null, unit: 'mm', status: 'unknown', source: null, question: 'How thick?', minimum: 0.8, minimum_rule: 'fdm.min_wall' },
        },
        placement: null,
        confidence: 0.6,
        suggested_by: 'deterministic',
        provenance: [],
        dfm_rules: ['fdm.min_wall'],
        buildable: false,
        existing_ref: null,
        status: 'proposed',
        note: '',
      },
      {
        id: 'llm-rib-1',
        kind: 'rib',
        name: 'Ribs',
        plain: 'ribs',
        depends_on: ['body'],
        parameters: {},
        placement: null,
        confidence: null,
        suggested_by: 'llm',
        provenance: [{ kind: 'llm', ref: 'm1', detail: 'suggested by a model: tall walls' }],
        dfm_rules: [],
        buildable: false,
        existing_ref: null,
        status: 'proposed',
        note: 'Suggested by a model, not read from the concept.',
      },
    ],
    assumptions: [{ id: 'layout-sizes', text: 'Sizes come from the concept layout and are estimated.', feature_id: 'profile' }],
    ambiguities: [{ id: 'shell-hollow', question: 'Hollow or solid?', options: [{ id: 'hollow', label: 'Hollow' }], feature_id: 'shell', consequential: true, default: 'hollow', choice: null }],
    dfm_strategy: {
      profile_id: 'fdm',
      profile_version: '1.1.0',
      process: 'FDM',
      process_authority: 'user_stated',
      context_ref: 'MFC-PROJECT@1',
      rules: [{ rule_id: 'fdm.min_wall', title: 'Minimum wall thickness', kind: 'general_guidance', limit: 0.8, unit: 'mm', source: 'General FDM design guidance', applied_as: 'shell wall at least 0.8 mm' }],
      text: 'Strategy for FDM (fdm 1.1.0): shaped by fdm.min_wall.',
    },
    summary: 'the enclosure: a block',
    inputs: { assembly_decision_id: 'dec-1', layout_ref: 'DSK-1@1', layout_node_id: 'l1', mesh_node_id: 'm1', reference_ids: ['ref-1'], sketch_refs: [], context_ref: 'MFC-PROJECT@1' },
    llm: { requested: true, outcome: 'used', provider: 'fake', model: 'm1', suggestions: 1, note: '' },
    accepted_sketches: {},
  },
};

const VIEW: ReconstructionView = {
  project_id: P,
  concept_accepted: true,
  assembly_decision_id: 'dec-1',
  components: [
    { component_id: 'CMP-enclosure', name: 'Enclosure', source: 'made', proposal: PV },
    { component_id: 'CMP-base', name: 'Base', source: 'made', proposal: null },
  ],
  summary: '',
  accepted: false,
};

describe('Engineering: Feature history (proposed)', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('is fetched only on demand', async () => {
    const get = vi.spyOn(api, 'getReconstruction').mockResolvedValue(VIEW);
    const user = userEvent.setup();
    render(<FeatureHistorySection projectId={P} componentId="CMP-enclosure" />);
    expect(get).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Feature history (proposed)' }));
    expect(await screen.findByTestId('fh-component-CMP-enclosure')).toBeInTheDocument();
    expect(get).toHaveBeenCalledTimes(1);
  });

  it('lists features, parameter statuses and sources, assumptions, the DFM note and provenance', async () => {
    vi.spyOn(api, 'getReconstruction').mockResolvedValue(VIEW);
    const user = userEvent.setup();
    render(<FeatureHistorySection projectId={P} componentId="CMP-enclosure" />);
    await user.click(screen.getByRole('button', { name: 'Feature history (proposed)' }));
    const depth = await screen.findByTestId('fh-param-body-depth');
    expect(depth).toHaveTextContent('160 mm');
    expect(depth).toHaveTextContent('estimated');
    expect(depth).toHaveTextContent('l1 layout · DSK-1@1');
    expect(screen.getByTestId('fh-param-body-material')).toHaveTextContent('confirmed');
    const wall = screen.getByTestId('fh-param-shell-thickness');
    expect(wall).toHaveTextContent('unknown');
    expect(wall).toHaveTextContent('at least 0.8 mm (fdm.min_wall)');
    expect(within(screen.getByTestId('fh-feature-llm-rib-1')).getByText('suggested by a model')).toBeInTheDocument();
    expect(screen.getByText('Sizes come from the concept layout and are estimated.')).toBeInTheDocument();
    const dfm = screen.getByTestId('fh-dfm-CMP-enclosure');
    expect(dfm).toHaveTextContent('fdm.min_wall (general guidance): shell wall at least 0.8 mm');
    const prov = screen.getByTestId('fh-provenance-CMP-enclosure');
    expect(prov).toHaveTextContent('DSK-1@1');
    expect(prov).toHaveTextContent('ref-1');
    expect(prov).toHaveTextContent('MFC-PROJECT@1');
    expect(prov).toHaveTextContent('used (m1)');
    // Only the selected component.
    expect(screen.queryByTestId('fh-component-CMP-base')).not.toBeInTheDocument();
  });

  it('proposes for the selected component when none exists', async () => {
    vi.spyOn(api, 'getReconstruction').mockResolvedValue(VIEW);
    const propose = vi.spyOn(api, 'proposeReconstruction').mockResolvedValue({
      project_id: P,
      operation_id: 'job:reconstruction-p1',
      proposals: [],
      reused: [],
      skipped: [],
      summary: '',
    });
    const user = userEvent.setup();
    render(<FeatureHistorySection projectId={P} componentId="CMP-base" />);
    await user.click(screen.getByRole('button', { name: 'Feature history (proposed)' }));
    expect(await screen.findByText(/No feature history is proposed for this component yet/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Propose a feature history' }));
    expect(propose).toHaveBeenCalledWith(P, { component_ids: ['CMP-base'] });
  });
});
