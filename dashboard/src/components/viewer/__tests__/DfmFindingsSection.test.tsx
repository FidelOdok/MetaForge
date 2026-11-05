import { describe, it, expect, vi, beforeEach } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen, within } from '../../../test/test-utils';
import * as api from '../../../api/endpoints/dfmFindings';
import type { DfmComponentFindings, DfmView } from '../../../api/endpoints/dfmFindings';
import { DfmFindingsSection } from '../DfmFindingsSection';

const P = 'p1';

function component(id: string, name: string, over: Partial<DfmComponentFindings> = {}): DfmComponentFindings {
  return {
    component_id: id,
    name,
    finding_set_id: `set-${id}`,
    status: 'issues_found',
    status_label: 'Manufacturing issues found',
    note: '1 blocking manufacturing issue for FDM (preliminary)',
    process: 'FDM',
    process_authority: 'user_stated',
    profile: { id: 'fdm', version: '1.0.0' },
    geometry_level: 'exact',
    geometry_refs: ['CAD-ENCLOSURE@2'],
    context_ref: 'MFC-PROJECT@3',
    staleness: { status: 'current', reason: null },
    findings: [
      {
        id: 'dfmf-1',
        rule_id: 'fdm.build_volume',
        rule_title: 'Build volume fit',
        profile_id: 'fdm',
        profile_version: '1.0.0',
        process: 'FDM',
        rule_kind: 'machine_limit',
        severity: 'blocking',
        anchor: { component_id: id, feature: 'bounding_box', face_signature: 'CMP-enclosure::top', faces: [] },
        quantity: 'envelope',
        measured: '300 x 100 x 50',
        limit: '250 x 210 x 220',
        unit: 'mm',
        comparison: 'fits within',
        confidence: 'exact',
        limit_source: 'Prusa MK4S build_volume_mm (manufacturer_spec: https://example.com)',
        evidence_refs: ['CAD-ENCLOSURE@2'],
        message: 'The part is about 300 x 100 x 50 mm; the build volume is 250 x 210 x 220 mm.',
        suggested_fix: 'Split the part, scale it down, or choose a larger machine.',
        fix_changes: ['envelope'],
        decision: null,
      },
    ],
    not_assessed: [
      {
        rule_id: 'fdm.bridge',
        title: 'Unsupported bridge length',
        reason: 'the unsupported bridge length is unknown',
        text: 'Not assessed for unsupported bridge length because the unsupported bridge length is unknown',
      },
    ],
    rules_run: ['fdm.build_volume'],
    evaluated_at: '2026-11-05T10:00:00Z',
    trigger: 'geometry_changed',
    ...over,
  };
}

function view(over: Partial<DfmView> = {}): DfmView {
  return {
    project_id: P,
    status: 'issues_found',
    status_label: 'Manufacturing issues found',
    note: '',
    suggestions: 1,
    blocking: 1,
    components: [component('CMP-enclosure', 'Enclosure'), component('CMP-lid', 'Lid', { findings: [], status: 'preliminary_guidance' })],
    history: [
      component('CMP-enclosure', 'Enclosure', {
        finding_set_id: 'old',
        staleness: { status: 'stale', reason: 'it was for CAD-ENCLOSURE@1, and CAD-ENCLOSURE is now @2' },
      }),
    ],
    profiles: [{ id: 'fdm', version: '1.0.0', process: 'FDM' }],
    heuristic_only: true,
    disclaimer: 'Preliminary manufacturing guidance from heuristic rules. Not an analysis pass.',
    ...over,
  };
}

describe('the Engineering DFM findings', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, 'getReleaseChecklist').mockResolvedValue({
      project_id: P,
      applies: false,
      readiness: { level: 'not_assessed', label: 'Manufacturing not assessed', prototype: false, production: false, next_steps: [] },
    });
  });

  it('shows the profile, measured against limit, anchors, evidence and staleness', async () => {
    vi.spyOn(api, 'getDfm').mockResolvedValue(view());
    render(<DfmFindingsSection projectId={P} />);
    const block = await screen.findByTestId('dfm-component-CMP-enclosure');
    const scope = within(block);
    expect(scope.getByText('fdm 1.0.0')).toBeInTheDocument();
    expect(scope.getByText(/Measured 300 x 100 x 50 mm against fits within 250 x 210 x 220 mm/)).toBeInTheDocument();
    expect(scope.getByText(/Anchor: CMP-enclosure · feature bounding_box · face CMP-enclosure::top/)).toBeInTheDocument();
    expect(scope.getByText(/Limit source: Prusa MK4S build_volume_mm/)).toBeInTheDocument();
    expect(scope.getByText('Evidence: CAD-ENCLOSURE@2')).toBeInTheDocument();
    expect(scope.getByText('current')).toBeInTheDocument();
    expect(scope.getByText('blocking')).toBeInTheDocument();
    expect(scope.getByText(/because the unsupported bridge length is unknown/)).toBeInTheDocument();
    expect(screen.getByText(/Not an analysis pass/)).toBeInTheDocument();
  });

  it('narrows to the selected component', async () => {
    vi.spyOn(api, 'getDfm').mockResolvedValue(view());
    render(<DfmFindingsSection projectId={P} componentId="CMP-lid" />);
    expect(await screen.findByTestId('dfm-component-CMP-lid')).toBeInTheDocument();
    expect(screen.queryByTestId('dfm-component-CMP-enclosure')).not.toBeInTheDocument();
    expect(screen.getByText('No findings from the checks that ran.')).toBeInTheDocument();
  });

  it('shows stale findings only on request', async () => {
    const get = vi.spyOn(api, 'getDfm').mockResolvedValue(view());
    const user = userEvent.setup();
    render(<DfmFindingsSection projectId={P} />);
    await screen.findByTestId('dfm-findings');
    expect(screen.queryByText(/is now @2/)).not.toBeInTheDocument();
    await user.click(screen.getByRole('checkbox', { name: /Show stale and earlier findings/ }));
    expect(await screen.findByText(/stale: it was for CAD-ENCLOSURE@1/)).toBeInTheDocument();
    expect(get).toHaveBeenLastCalledWith(P, expect.objectContaining({ history: true }));
  });

  it('runs the checks on demand', async () => {
    vi.spyOn(api, 'getDfm').mockResolvedValue(view());
    const evaluate = vi.spyOn(api, 'evaluateDfm').mockResolvedValue(view());
    const user = userEvent.setup();
    render(<DfmFindingsSection projectId={P} />);
    await user.click(await screen.findByRole('button', { name: 'Run checks' }));
    expect(evaluate).toHaveBeenCalledWith(P);
  });
});
