import { describe, it, expect, vi, beforeEach } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen } from '../../../test/test-utils';
import * as api from '../../../api/endpoints/dfmFindings';
import type { DfmComponentFindings, DfmFinding, DfmView } from '../../../api/endpoints/dfmFindings';
import { DfmGuidanceBadge, guidanceBadgeText } from '../DfmGuidanceBadge';
import { useComponentSelection } from '../../../store/component-selection-store';

const P = 'p1';

function finding(over: Partial<DfmFinding> = {}): DfmFinding {
  return {
    id: 'dfmf-1',
    rule_id: 'fdm.min_wall',
    rule_title: 'Minimum wall thickness',
    profile_id: 'fdm',
    profile_version: '1.0.0',
    process: 'FDM',
    rule_kind: 'general_guidance',
    severity: 'advisory',
    anchor: { component_id: 'CMP-enclosure', feature: 'wall_thickness', face_signature: null, faces: [] },
    quantity: 'wall_thickness',
    measured: 0.5,
    limit: 0.8,
    unit: 'mm',
    comparison: '>=',
    confidence: 'design_parameter',
    limit_source: 'General FDM design guidance',
    evidence_refs: ['CAD-ENCLOSURE@1'],
    message: 'The wall is about 0.5 mm; FDM walls under 0.8 mm print weak or with gaps.',
    suggested_fix: 'Thicken the wall to at least 0.8 mm.',
    fix_changes: ['wall_thickness'],
    decision: null,
    ...over,
  };
}

function component(findings: DfmFinding[]): DfmComponentFindings {
  return {
    component_id: 'CMP-enclosure',
    name: 'Enclosure',
    finding_set_id: 'set-1',
    status: findings.length ? 'issues_found' : 'preliminary_guidance',
    status_label: 'Manufacturing issues found',
    note: '1 manufacturing suggestion for FDM (preliminary)',
    process: 'FDM',
    process_authority: 'user_stated',
    profile: { id: 'fdm', version: '1.0.0' },
    geometry_level: 'exact',
    geometry_refs: ['CAD-ENCLOSURE@1'],
    context_ref: 'MFC-PROJECT@1',
    staleness: { status: 'current', reason: null },
    findings,
    not_assessed: [],
    rules_run: ['fdm.min_wall'],
    evaluated_at: '2026-11-05T10:00:00Z',
    trigger: 'manual',
  };
}

function view(findings: DfmFinding[]): DfmView {
  return {
    project_id: P,
    status: findings.length ? 'issues_found' : 'preliminary_guidance',
    status_label: 'Manufacturing issues found',
    note: '',
    suggestions: findings.length,
    blocking: 0,
    components: [component(findings)],
    history: [],
    profiles: [{ id: 'fdm', version: '1.0.0', process: 'FDM' }],
    heuristic_only: true,
    disclaimer: 'Preliminary manufacturing guidance from heuristic rules.',
  };
}

describe('the manufacturing guidance badge', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useComponentSelection.setState({ selected: {} });
  });

  it('counts suggestions and never shows a percentage', async () => {
    vi.spyOn(api, 'getDfm').mockResolvedValue(view([finding(), finding({ id: 'dfmf-2', rule_id: 'fdm.min_hole' })]));
    render(<DfmGuidanceBadge projectId={P} />);
    const badge = await screen.findByRole('button', { name: /Manufacturing guidance: 2 suggestions/ });
    expect(badge.textContent).not.toMatch(/%/);
    expect(guidanceBadgeText(1)).toBe('Manufacturing guidance: 1 suggestion');
  });

  it('shows nothing when no check found anything', async () => {
    const spy = vi.spyOn(api, 'getDfm').mockResolvedValue(view([]));
    const { container } = render(<DfmGuidanceBadge projectId={P} />);
    await vi.waitFor(() => expect(spy).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('does not count a suggestion the person kept the design for', async () => {
    vi.spyOn(api, 'getDfm').mockResolvedValue(
      view([finding(), finding({ id: 'dfmf-2', decision: { decision: 'keep_design' } })]),
    );
    render(<DfmGuidanceBadge projectId={P} />);
    expect(await screen.findByRole('button', { name: /1 suggestion$/ })).toBeInTheDocument();
  });

  it('highlights the affected part and offers the three actions', async () => {
    vi.spyOn(api, 'getDfm').mockResolvedValue(view([finding()]));
    const user = userEvent.setup();
    render(<DfmGuidanceBadge projectId={P} />);
    await user.click(await screen.findByRole('button', { name: /Manufacturing guidance/ }));
    await user.click(screen.getByRole('button', { name: /Enclosure/ }));
    expect(useComponentSelection.getState().selected[P]).toEqual({
      componentId: 'CMP-enclosure',
      name: 'Enclosure',
      from: 'create',
    });
    for (const label of ['Keep design', 'Try suggested fix', 'Explore another process']) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument();
    }
    expect(screen.getByText('Thicken the wall to at least 0.8 mm.')).toBeInTheDocument();
  });

  it('sends each decision and shows what came back', async () => {
    vi.spyOn(api, 'getDfm').mockResolvedValue(view([finding()]));
    const decide = vi.spyOn(api, 'decideDfmFinding').mockImplementation(async (_p, id, decision) => ({
      finding_id: id,
      decision,
      finding_set_id: 'set-1',
      component_id: 'CMP-enclosure',
      applied: false,
      message:
        decision === 'try_fix'
          ? 'This fix would change something you protected, so it waits for a person.'
          : 'Other ways to make this part.',
      requires_approval: decision === 'try_fix',
      alternatives:
        decision === 'explore_process'
          ? [
              {
                process: 'Injection moulding',
                profile: { id: 'injection_moulding', version: '1.0.0' },
                status: 'preliminary_guidance',
                note: 'No issues in 2 preliminary Injection moulding checks',
                findings: 0,
                blocking: 0,
                not_assessed: 3,
                preview: true,
              },
            ]
          : undefined,
    }));
    const user = userEvent.setup();
    render(<DfmGuidanceBadge projectId={P} />);
    await user.click(await screen.findByRole('button', { name: /Manufacturing guidance/ }));
    await user.click(screen.getByRole('button', { name: /Enclosure/ }));

    await user.click(screen.getByRole('button', { name: 'Try suggested fix' }));
    expect(decide).toHaveBeenLastCalledWith(P, 'dfmf-1', 'try_fix');
    expect(await screen.findByText(/waits for a person/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Explore another process' }));
    expect(decide).toHaveBeenLastCalledWith(P, 'dfmf-1', 'explore_process');
    expect(await screen.findByText(/Injection moulding: No issues/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Keep design' }));
    expect(decide).toHaveBeenLastCalledWith(P, 'dfmf-1', 'keep_design');
  });
});
