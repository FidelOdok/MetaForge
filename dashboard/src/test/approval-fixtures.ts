import type { ApprovalItem } from '../types/approvals';

/** Fixture layer for the FORGE-506 contract; works before the gateway lands. */
export function makeApproval(overrides: Partial<ApprovalItem> = {}): ApprovalItem {
  return {
    id: 'gate:run_abc',
    kind: 'gate',
    status: 'pending',
    title: 'Requirements gate',
    summary: 'Sign off on requirements.',
    project_id: 'p1',
    created_at: '2026-10-03T10:00:00Z',
    deadline: null,
    route: 'dashboard',
    requested_by: null,
    reason_held: 'Two deliverables are missing.',
    findings: [],
    allowed_decisions: ['approve', 'reject', 'retry', 'rework'],
    rework_targets: ['requirements', 'architecture'],
    reason_required_for: ['reject', 'retry', 'rework'],
    decidable: true,
    not_decidable_reason: null,
    detail: {
      run_id: 'run_abc',
      phase: 'requirements',
      gate: 'requirements_gate',
      attempt: 1,
      retries_left: 2,
      rework_cycles_left: 1,
    },
    decision: null,
    ...overrides,
  };
}
