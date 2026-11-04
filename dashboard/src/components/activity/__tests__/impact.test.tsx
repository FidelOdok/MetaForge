import { beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { act, render, screen, waitFor, within } from '../../../test/test-utils';
import { opEvent } from '../../../test/operation-fixtures';
import { makeApproval } from '../../../test/approval-fixtures';
import { useOperationsStore } from '../../../store/operations-store';
import type { ImpactResult, InterfaceListResponse } from '../../../types/impact';

const getImpact = vi.fn();
const getInterfaces = vi.fn();
vi.mock('../../../api/endpoints/impact', () => ({
  getImpact: (...args: unknown[]) => getImpact(...args),
  getInterfaces: (...args: unknown[]) => getInterfaces(...args),
  previewImpact: vi.fn(),
}));

const getApproval = vi.fn();
vi.mock('../../../api/endpoints/approvals', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/endpoints/approvals')>();
  return { ...actual, getApproval: (...args: unknown[]) => getApproval(...args) };
});

vi.mock('../../../api/endpoints/operations', () => ({
  getOperations: vi.fn(),
  operationsStreamUrl: vi.fn(),
  cancelOperation: vi.fn(),
  retryOperation: vi.fn(),
}));

import { ActivityOverlay } from '../ActivityOverlay';
import { ImpactDetail, ImpactSummary } from '../ImpactPanel';
import { CrossDomainScene } from '../scenes/CrossDomainScene';
import { layoutGraph, sceneGraphFrom } from '../scenes/graph';

const IMPACT_ID = '11111111-2222-3333-4444-555555555555';

function impact(over: Partial<ImpactResult> = {}): ImpactResult {
  return {
    id: IMPACT_ID,
    project_id: 'p1',
    preview: false,
    change: { item_key: 'CAD-ENCLOSURE', old_revision: 1, new_revision: 2, changed_parameters: ['mounting_standoffs'] },
    source_domain: 'mechanical',
    source_label: 'Enclosure',
    impacted: [
      {
        ref: 'PCB-MAIN',
        label: 'Main board',
        domain: 'electronics',
        item_type: 'pcb_layout',
        revision: 3,
        depth: 1,
        direct: true,
        edge_id: 'e1',
        edge_type: 'mounts_to',
        edge_status: 'confirmed',
        via: ['CAD-ENCLOSURE', 'PCB-MAIN'],
        rationale: 'Enclosure: mounting hole positions changed; Main board mounting depends on them',
        proposed_action: 'Re-run kicad.run_drc',
        parameters: ['mounting_standoffs'],
        modules: [],
        certain: true,
        couplings: [
          {
            edge_id: 'e1',
            edge_type: 'mounts_to',
            edge_status: 'confirmed',
            rationale: 'Enclosure: mounting hole positions changed; Main board mounting depends on them',
          },
        ],
      },
      {
        ref: 'FW-FLIGHT',
        label: 'Flight firmware',
        domain: 'firmware',
        item_type: 'firmware_source',
        revision: 1,
        depth: 1,
        direct: true,
        edge_id: 'e2',
        edge_type: 'pin_maps_to',
        edge_status: 'confirmed',
        via: ['CAD-ENCLOSURE', 'FW-FLIGHT'],
        rationale: 'the pin assignment changed',
        proposed_action: 'Re-run firmware.build',
        parameters: [],
        modules: ['src/imu_driver.c'],
        certain: true,
        couplings: [{ edge_id: 'e2', edge_type: 'pin_maps_to', edge_status: 'confirmed', rationale: 'the pin assignment changed' }],
      },
    ],
    by_domain: { electronics: ['PCB-MAIN'], firmware: ['FW-FLIGHT'] },
    stale_results: [
      {
        record_id: 'rec-drc',
        record_type: 'evidence',
        check_kind: 'drc',
        name: 'kicad.run_drc result',
        pinned_ref: 'CAD-ENCLOSURE@1',
        current_ref: 'CAD-ENCLOSURE@2',
        belongs_to: 'PCB-MAIN@3',
        input_revisions: ['PCB-MAIN@3'],
        previous_status: 'PASS',
        status: 'STALE',
        reason: 'it read CAD-ENCLOSURE@1 through mounts_to',
        tool: 'kicad.run_drc',
        marked: true,
      },
    ],
    proposed_reruns: [
      {
        id: 'r1',
        tool: 'kicad.run_drc',
        check_kind: 'drc',
        target_ref: 'PCB-MAIN',
        target_domain: 'electronics',
        label: 'Re-run kicad.run_drc on PCB-MAIN',
        reason: 'r',
        consequential: false,
        disposition: 'may_run_unattended',
        disposition_reason: 'a read-only check; the guardrails never hold it',
        status: 'queued',
        record_id: 'rec-drc',
        decided_by: null,
        decided_at: null,
        decision_reason: null,
      },
      {
        id: 'r2',
        tool: 'firmware.build',
        check_kind: 'firmware_build',
        target_ref: 'FW-FLIGHT',
        target_domain: 'firmware',
        label: 'Re-run firmware.build on FW-FLIGHT',
        reason: 'r',
        consequential: true,
        disposition: 'requires_approval',
        disposition_reason: 'a firmware or PCB change, or a release-bound target: a person decides',
        status: 'proposed',
        record_id: null,
        decided_by: null,
        decided_at: null,
        decision_reason: null,
      },
    ],
    review_actions: [],
    not_affected: [{ ref: 'BOM-MAIN', label: 'BOM', domain: 'electronics', reason: 'no interface links it to CAD-ENCLOSURE' }],
    cycles: [],
    depth_limited: false,
    max_depth: 4,
    summary: 'This may affect the PCB fit and the firmware.',
    next_action: 'Review the proposed re-checks.',
    graph: {
      nodes: [
        { ref: 'CAD-ENCLOSURE', label: 'Enclosure', domain: 'mechanical', role: 'changed' },
        { ref: 'PCB-MAIN', label: 'Main board', domain: 'electronics', role: 'impacted' },
        { ref: 'FW-FLIGHT', label: 'Flight firmware', domain: 'firmware', role: 'impacted' },
      ],
      links: [
        { source: 'CAD-ENCLOSURE', target: 'PCB-MAIN', type: 'mounts_to', status: 'confirmed', impacted: true },
        { source: 'CAD-ENCLOSURE', target: 'FW-FLIGHT', type: 'pin_maps_to', status: 'confirmed', impacted: true },
      ],
    },
    approval_policy: { mode: 'off' },
    operation_id: `job:impact-${IMPACT_ID}`,
    created_at: '2026-11-04T10:00:00Z',
    ...over,
  };
}

const INTERFACES: InterfaceListResponse = {
  project_id: 'p1',
  nodes: [
    { ref: 'CAD-ENCLOSURE', label: 'Enclosure', domain: 'mechanical', item_type: 'cad_model', head_revision: 2 },
    { ref: 'PCB-MAIN', label: 'Main board', domain: 'electronics', item_type: 'pcb_layout', head_revision: 3 },
    { ref: 'PIN-MAP', label: 'Pin map', domain: 'electronics', item_type: 'pinmap', head_revision: 1 },
  ],
  edges: [
    {
      id: 'e1',
      project_id: 'p1',
      type: 'mounts_to',
      source: { ref: 'CAD-ENCLOSURE', revision: 2, domain: 'mechanical', item_type: 'cad_model', node_id: null, label: 'Enclosure' },
      target: { ref: 'PCB-MAIN', revision: 3, domain: 'electronics', item_type: 'pcb_layout', node_id: null, label: 'Main board' },
      declared_by: 'human',
      confidence: 1,
      status: 'confirmed',
      coupled_parameters: [],
      rationale: '',
      evidence: {},
      derived: false,
      domain_pair: ['mechanical', 'electronics'],
      symmetric: true,
    },
    {
      id: 'e3',
      project_id: 'p1',
      type: 'clearance_to',
      source: { ref: 'CAD-ENCLOSURE', revision: 2, domain: 'mechanical', item_type: 'cad_model', node_id: null, label: 'Enclosure' },
      target: { ref: 'PIN-MAP', revision: 1, domain: 'electronics', item_type: 'pinmap', node_id: null, label: 'Pin map' },
      declared_by: 'inferred',
      confidence: 0.6,
      status: 'proposed',
      coupled_parameters: [],
      rationale: '',
      evidence: {},
      derived: false,
      domain_pair: ['mechanical', 'electronics'],
      symmetric: true,
    },
  ],
};

function seedImpactOperation(state: 'waiting_for_user' | 'completed' = 'waiting_for_user') {
  act(() => {
    const store = useOperationsStore.getState();
    store.applyEvents('p1', [
      opEvent({
        operation_id: `job:impact-${IMPACT_ID}`,
        run_id: null,
        kind: 'job',
        domain: 'cross_domain',
        title: 'What CAD-ENCLOSURE@2 affects',
        sequence: 20,
        type: 'milestone',
        state: 'running',
        stage_id: 'dependencies_invalidated',
        stage_title: 'Dependencies invalidated',
        artifact_refs: [{ kind: 'work_product', id: 'PCB-MAIN', label: 'Main board (electronics)', url: null, authority: null }],
      }),
      opEvent({
        operation_id: `job:impact-${IMPACT_ID}`,
        run_id: null,
        kind: 'job',
        domain: 'cross_domain',
        title: 'What CAD-ENCLOSURE@2 affects',
        sequence: 30,
        type: state === 'completed' ? 'completed' : 'review_required',
        state,
        requires_action: state !== 'completed',
        action_refs:
          state === 'completed'
            ? []
            : [{ kind: 'approval', id: `rerun:${IMPACT_ID}:r2`, label: 'Re-run firmware.build on FW-FLIGHT' }],
      }),
    ]);
    store.setHealth('p1', 'live');
  });
}

beforeEach(() => {
  useOperationsStore.getState().reset();
  getImpact.mockReset();
  getInterfaces.mockReset();
  getApproval.mockReset();
});

describe('impact in Create (UXS-015)', () => {
  it('says what may be affected in one sentence with one next action', async () => {
    const user = userEvent.setup();
    getApproval.mockResolvedValue(
      makeApproval({ id: `rerun:${IMPACT_ID}:r2`, kind: 'rerun', title: 'Re-run firmware.build on FW-FLIGHT' }),
    );
    render(<ImpactSummary impact={impact()} />);
    const box = screen.getByTestId('impact-summary');
    expect(box).toHaveTextContent('This may affect the PCB fit and the firmware.');
    const buttons = within(box).getAllByRole('button');
    expect(buttons).toHaveLength(1);
    expect(buttons[0]).toHaveTextContent('Review the proposed re-checks');
    await user.click(buttons[0]!);
    await waitFor(() => expect(getApproval).toHaveBeenCalledWith(`rerun:${IMPACT_ID}:r2`));
    expect(await screen.findByText('Re-run firmware.build on FW-FLIGHT')).toBeInTheDocument();
  });

  it('shows the next action as text when nothing waits for a decision', () => {
    render(
      <ImpactSummary
        impact={impact({ proposed_reruns: [], summary: 'This may affect the PCB fit.', next_action: 'Review the mounting.' })}
      />,
    );
    expect(screen.getByTestId('impact-summary')).toHaveTextContent('Review the mounting.');
    expect(within(screen.getByTestId('impact-summary')).queryByRole('button')).toBeNull();
  });

  it('appears in the activity card of an impact operation, with only its own action', async () => {
    getImpact.mockResolvedValue(impact());
    getInterfaces.mockResolvedValue(INTERFACES);
    seedImpactOperation();
    render(<ActivityOverlay projectId="p1" />);
    expect(await screen.findByText('This may affect the PCB fit and the firmware.')).toBeInTheDocument();
    expect(getImpact).toHaveBeenCalledWith('p1', IMPACT_ID, expect.anything());
    // The per-rerun approval buttons stay in Engineering; Create has one action.
    expect(screen.queryByRole('button', { name: 'Re-run firmware.build on FW-FLIGHT' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Review the proposed re-checks' })).toBeInTheDocument();
  });
});

describe('impact in Engineering (UI-008)', () => {
  it('lists affected entities by domain, stale results with their revisions, and reruns', async () => {
    const user = userEvent.setup();
    getApproval.mockResolvedValue(
      makeApproval({ id: `rerun:${IMPACT_ID}:r2`, kind: 'rerun', title: 'Firmware rebuild approval' }),
    );
    render(<ImpactDetail impact={impact()} />);
    const detail = screen.getByTestId('impact-detail');
    await user.click(within(detail).getByText(/Impact of CAD-ENCLOSURE@1 to @2/));

    const electronics = detail.querySelector('[data-domain="electronics"]') as HTMLElement;
    expect(electronics).toHaveTextContent('PCB-MAIN@3');
    expect(electronics).toHaveTextContent('mounting hole positions changed');
    expect(detail.querySelector('[data-domain="firmware"]')).toHaveTextContent('src/imu_driver.c');

    const stale = detail.querySelector('[data-record="rec-drc"]') as HTMLElement;
    expect(stale).toHaveTextContent('was PASS, now STALE');
    expect(stale).toHaveTextContent('CAD-ENCLOSURE@1');
    expect(stale).toHaveTextContent('CAD-ENCLOSURE@2');

    expect(detail.querySelector('[data-rerun="r1"]')).toHaveTextContent('can run without a decision');
    const r2 = detail.querySelector('[data-rerun="r2"]') as HTMLElement;
    expect(r2).toHaveTextContent('waiting for your decision');
    expect(within(detail.querySelector('[data-rerun="r1"]') as HTMLElement).queryByRole('button')).toBeNull();
    await user.click(within(r2).getByRole('button', { name: 'Approve or reject' }));
    await waitFor(() => expect(getApproval).toHaveBeenCalledWith(`rerun:${IMPACT_ID}:r2`));
    expect(await screen.findByText('Firmware rebuild approval')).toBeInTheDocument();
    expect(detail).toHaveTextContent('BOM-MAIN');
  });

  it('is in the Engineering activity card and keeps the per-rerun approvals', async () => {
    getImpact.mockResolvedValue(impact());
    getInterfaces.mockResolvedValue(INTERFACES);
    seedImpactOperation();
    render(<ActivityOverlay projectId="p1" detail />);
    expect(await screen.findByTestId('impact-detail')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Re-run firmware.build on FW-FLIGHT' })).toBeInTheDocument();
  });
});

describe('cross-domain scene from the dependency graph (11C)', () => {
  it('builds the graph from interfaces plus the impact, marking what the change reaches', () => {
    const graph = sceneGraphFrom(INTERFACES, impact());
    expect(graph).not.toBeNull();
    const roles = Object.fromEntries(graph!.nodes.map((n) => [n.id, n.role]));
    expect(roles).toEqual({
      'CAD-ENCLOSURE': 'changed',
      'PCB-MAIN': 'impacted',
      'FW-FLIGHT': 'impacted',
      'PIN-MAP': 'idle',
    });
    const proposed = graph!.links.find((l) => l.target === 'PIN-MAP');
    expect(proposed?.proposed).toBe(true);
    const positions = layoutGraph(graph!);
    expect(positions.get('CAD-ENCLOSURE')!.x).toBeLessThan(positions.get('PCB-MAIN')!.x);
  });

  it('is null without a graph, so the scene stays illustrative', () => {
    expect(sceneGraphFrom(undefined, undefined)).toBeNull();
    expect(sceneGraphFrom({ project_id: 'p1', nodes: [], edges: [] }, null)).toBeNull();
  });

  it('draws real nodes and links, and an illustrative placeholder only when there is none', () => {
    const graph = sceneGraphFrom(INTERFACES, impact())!;
    const real = render(<CrossDomainScene state="completed" stageIndex={0} reducedMotion paused={false} graph={graph} />);
    const svg = real.container.querySelector('svg')!;
    expect(svg.getAttribute('data-grounded')).toBe('true');
    expect(svg.getAttribute('aria-label')).toContain('Dependency graph: Enclosure and 2 affected items');
    expect(real.container.querySelector('[data-node="PCB-MAIN"]')?.getAttribute('data-role')).toBe('impacted');
    expect(real.container.querySelector('[data-link="CAD-ENCLOSURE>PCB-MAIN"]')?.getAttribute('data-impacted')).toBe('true');
    expect(real.container.querySelector('[data-link="CAD-ENCLOSURE>PIN-MAP"]')?.getAttribute('class')).toContain('is-proposed');
    real.unmount();

    const placeholder = render(<CrossDomainScene state="running" stageIndex={1} reducedMotion paused={false} />);
    const plain = placeholder.container.querySelector('svg')!;
    expect(plain.getAttribute('data-grounded')).toBe('false');
    expect(plain.getAttribute('aria-label')).toContain('Illustrative cross-domain activity');
  });

  it('labels the overlay scene as the design dependencies when a graph exists', async () => {
    const user = userEvent.setup();
    getImpact.mockResolvedValue(impact());
    getInterfaces.mockResolvedValue(INTERFACES);
    seedImpactOperation('completed');
    render(<ActivityOverlay projectId="p1" />);
    await user.click(screen.getByRole('button', { name: 'Expand activity' }));
    await waitFor(() => expect(screen.getByTestId('activity-visual-label')).toHaveTextContent('Design dependencies'));
    expect(document.querySelector('[data-node="FW-FLIGHT"]')).not.toBeNull();
  });

  it('keeps the illustrative label when the project has no graph', async () => {
    const user = userEvent.setup();
    getImpact.mockRejectedValue(new Error('not found'));
    getInterfaces.mockResolvedValue({ project_id: 'p1', nodes: [], edges: [] });
    seedImpactOperation('completed');
    render(<ActivityOverlay projectId="p1" />);
    await user.click(screen.getByRole('button', { name: 'Expand activity' }));
    await waitFor(() => expect(getInterfaces).toHaveBeenCalled());
    expect(screen.getByTestId('activity-visual-label')).toHaveTextContent('Illustrative activity');
  });
});
