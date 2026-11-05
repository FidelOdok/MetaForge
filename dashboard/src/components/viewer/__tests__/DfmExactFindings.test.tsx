import { describe, it, expect, vi, beforeEach } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen, within } from '../../../test/test-utils';
import * as api from '../../../api/endpoints/dfmFindings';
import * as planes from '../../../api/endpoints/sketchPlanes';
import type { DfmComponentFindings, DfmView, ReleaseChecklist } from '../../../api/endpoints/dfmFindings';
import { DfmFindingsSection } from '../DfmFindingsSection';

vi.mock('../../sketch/FacePickCanvas', () => ({
  default: ({ highlighted }: { highlighted: string[] }) => <div data-testid="face-canvas">{highlighted.join(',')}</div>,
}));

const P = 'p1';

function exactComponent(over: Partial<DfmComponentFindings> = {}): DfmComponentFindings {
  return {
    component_id: 'CMP-enclosure',
    name: 'Enclosure',
    finding_set_id: 'set-exact',
    status: 'issues_found',
    status_label: 'Manufacturing issues found',
    note: '1 manufacturing issue for FDM (exact checks)',
    process: 'FDM',
    process_authority: 'user_stated',
    profile: { id: 'fdm', version: '1.1.0' },
    geometry_level: 'exact',
    geometry_refs: ['CAD-ENCLOSURE@2'],
    context_ref: 'MFC-PROJECT@3',
    staleness: { status: 'current', reason: null },
    findings: [
      {
        id: 'dfmf-wall',
        rule_id: 'fdm.min_wall',
        rule_title: 'Minimum wall thickness',
        profile_id: 'fdm',
        profile_version: '1.1.0',
        process: 'FDM',
        rule_kind: 'general_guidance',
        severity: 'advisory',
        anchor: { component_id: 'CMP-enclosure', feature: 's0f1', face_signature: 'normal (0, 0, 1) area 1600', faces: ['s0f1', 's0f7'] },
        quantity: 'wall_thickness',
        measured: 0.6,
        limit: 0.8,
        unit: 'mm',
        comparison: '>=',
        confidence: 'exact',
        limit_source: 'General FDM design guidance',
        evidence_refs: ['CAD-ENCLOSURE@2'],
        message: 'The wall is about 0.6 mm; FDM walls under 0.8 mm print weak or with gaps.',
        suggested_fix: 'Thicken the wall to at least 0.8 mm.',
        fix_changes: ['wall_thickness'],
        decision: null,
      },
    ],
    not_assessed: [],
    rules_run: ['fdm.min_wall', 'fdm.min_feature', 'fdm.overhang', 'fdm.build_volume', 'fdm.min_hole'],
    evaluated_at: '2026-11-05T10:00:00Z',
    trigger: 'exact_validated',
    kind: 'exact',
    complete: true,
    checks_required: ['fdm.min_wall', 'fdm.min_feature', 'fdm.overhang', 'fdm.build_volume', 'fdm.min_hole'],
    guidance_only_rules: ['fdm.bridge', 'fdm.support_access'],
    exact_evidence: {
      tool: 'cadquery.dfm_measure',
      wall_method: 'ray cast along the inward surface normal',
      wall_samples: 250,
      build_direction: [0, 0, 1],
      build_direction_source: 'default +Z (no orientation stated)',
    },
    verification_items: [
      {
        id: 'dfmv-1',
        rule_id: 'fdm.layer_strength',
        component_id: 'CMP-enclosure',
        title: 'Strength across the layers',
        kind: 'physical_test',
        needed: 'A load or use test of a part printed in the intended orientation.',
        reason: 'the production goal is functional prototype',
        source: 'FDM parts are weakest between layers',
        status: 'open',
        evidence: [],
        satisfied_by: null,
      },
    ],
    ...over,
  };
}

function view(components: DfmComponentFindings[]): DfmView {
  return {
    project_id: P,
    status: 'issues_found',
    status_label: 'Manufacturing issues found',
    note: '',
    suggestions: 1,
    blocking: 0,
    components,
    history: [],
    profiles: [{ id: 'fdm', version: '1.1.0', process: 'FDM' }],
    heuristic_only: false,
    verification_open: 1,
    disclaimer: 'Exact checks run on the validated model. A pass is not a guarantee of build success.',
  };
}

function checklist(over: Partial<ReleaseChecklist> = {}): ReleaseChecklist {
  return {
    project_id: P,
    applies: true,
    releasable: false,
    blockers: ['Enclosure: 1 manufacturing issue(s) open'],
    items: [
      { id: 'components_exact', label: 'Every required component has a validated exact model', status: 'pass', detail: '1 required component(s) exact or beyond', blockers: [], next_steps: [] },
      { id: 'dfm', label: 'Manufacturing analysis passed, or its issues accepted by a person', status: 'fail', detail: 'Enclosure: 1 manufacturing issue(s) open', blockers: ['Enclosure: 1 manufacturing issue(s) open'], next_steps: ['Resolve or accept 1 manufacturing issue(s) on Enclosure'] },
      { id: 'verification', label: 'Physical and supplier verification recorded and approved', status: 'fail', detail: 'Enclosure: Strength across the layers (no evidence recorded)', blockers: ['x'], next_steps: [] },
      { id: 'approvals', label: 'Manufacturing release approved', status: 'fail', detail: 'no approved release approval', blockers: ['y'], next_steps: [] },
      { id: 'step_round_trip', label: 'STEP export of each CAD revision passed its round trip', status: 'pass', detail: '1 CAD item(s) round-tripped', blockers: [], next_steps: [] },
    ],
    readiness: { level: 'in_progress', label: 'Not ready to make yet', prototype: false, production: false, next_steps: ['Resolve or accept 1 manufacturing issue(s) on Enclosure'] },
    ...over,
  };
}

describe('the Engineering exact DFM findings', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, 'getDfm').mockResolvedValue(view([exactComponent()]));
    vi.spyOn(api, 'getReleaseChecklist').mockResolvedValue(checklist());
  });

  it('labels exact checks and shows the measured values and the method', async () => {
    render(<DfmFindingsSection projectId={P} />);
    const block = await screen.findByTestId('dfm-component-CMP-enclosure');
    const scope = within(block);
    expect(scope.getByTestId('dfm-kind-CMP-enclosure')).toHaveTextContent('Exact checks');
    expect(scope.getByText(/Every exact check ran \(5 of 5\)/)).toBeInTheDocument();
    expect(scope.getByText(/guidance only: fdm.bridge, fdm.support_access/)).toBeInTheDocument();
    expect(scope.getByText(/Measured 0.6 mm against >= 0.8 mm/)).toBeInTheDocument();
    expect(scope.getByText(/confidence exact/)).toBeInTheDocument();
    expect(scope.getByText(/Measured on the exact model with cadquery.dfm_measure/)).toBeInTheDocument();
  });

  it('highlights the anchored faces on the model', async () => {
    vi.spyOn(planes, 'getCandidateFaces').mockResolvedValue({
      project_id: P,
      item_key: 'CAD-ENCLOSURE',
      revision: 2,
      item_ref: 'CAD-ENCLOSURE@2',
      node_id: 'n2',
      geometry_state: 'validated',
      part_size: 100,
      solid_count: 1,
      face_count: 8,
      faces: [
        {
          face_id: 's0f1',
          normal: [0, 0, 1],
          offset: 20,
          area: 1600,
          centroid: [0, 0, 20],
          bounding_box: null,
          outer_loop_edges: 4,
          longest_edge: null,
          adjacent: [],
          mesh: null,
          semantic_tag: null,
          signature_summary: 'normal (0, 0, 1) area 1600',
        },
      ],
    } as unknown as planes.FaceList);
    const twin = await import('../../../api/endpoints/twin');
    vi.spyOn(twin, 'getNodeModel').mockResolvedValue({ glb_url: '/v1/twin/nodes/n2/model.glb' } as never);
    const user = userEvent.setup();
    render(<DfmFindingsSection projectId={P} />);
    await user.click(await screen.findByRole('button', { name: 'Show 2 faces on the model' }));
    const highlight = await screen.findByTestId('dfm-face-highlight');
    expect(within(highlight).getByText(/1 flat face marked; also s0f7 \(not flat, listed only\)/)).toBeInTheDocument();
    expect(await screen.findByTestId('face-canvas')).toHaveTextContent('s0f1');
    expect(planes.getCandidateFaces).toHaveBeenCalledWith(P, 'CAD-ENCLOSURE', 2, expect.anything());
  });

  it('records verification evidence for approval, never as satisfied', async () => {
    const record = vi
      .spyOn(api, 'recordVerificationEvidence')
      .mockResolvedValue({ approval_id: 'change:1', requires_approval: true, message: 'Evidence recorded. It counts once a reviewer approves it.' });
    const user = userEvent.setup();
    render(<DfmFindingsSection projectId={P} />);
    const item = await screen.findByTestId('dfm-verification-fdm.layer_strength');
    const button = within(item).getByRole('button', { name: 'Record evidence for approval' });
    expect(button).toBeDisabled();
    await user.type(within(item).getByLabelText('Evidence note'), 'Held 20 N for an hour');
    await user.click(button);
    expect(record).toHaveBeenCalledWith(P, 'dfmv-1', { note: 'Held 20 N for an hour', attachment_name: null, attachment_base64: null });
    expect(await within(item).findByText(/counts once a reviewer approves it/)).toBeInTheDocument();
  });

  it('shows the release checklist and generates the report', async () => {
    const gen = vi.spyOn(api, 'generateDfmReport').mockResolvedValue({
      report_id: 'r1',
      generated_at: '2026-11-05T10:00:00Z',
      generated_by: 'ana',
      staleness: { status: 'current' },
      report: { status: 'issues_found', status_label: 'Manufacturing issues found', product_revision: { refs: ['CAD-ENCLOSURE@2'], key: 'abc123' } },
      markdown: '# DFM report',
    });
    const user = userEvent.setup();
    render(<DfmFindingsSection projectId={P} />);
    const panel = await screen.findByTestId('dfm-release-checklist');
    expect(within(panel).getByText('Not ready to make yet')).toBeInTheDocument();
    expect(within(screen.getByTestId('checklist-dfm')).getByText('Open')).toBeInTheDocument();
    expect(within(screen.getByTestId('checklist-components_exact')).getByText('Resolved')).toBeInTheDocument();
    await user.click(within(panel).getByRole('button', { name: 'Generate DFM report' }));
    expect(gen).toHaveBeenCalledWith(P);
    const link = await screen.findByTestId('dfm-report-link');
    expect(link).toHaveTextContent('abc123');
    expect(link.getAttribute('href')).toContain(`/twin/projects/${P}/dfm/report?format=markdown`);
  });
});
