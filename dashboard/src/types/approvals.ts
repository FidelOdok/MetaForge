/** Unified approvals contract (FORGE-506). Mirrors `/v1/approvals`. */

export type ApprovalKind =
  | 'gate'
  | 'flow_proposal'
  | 'flow_version'
  | 'flow_patch'
  | 'tool_call'
  | 'human_authority'
  | 'design_change'
  | 'design_loop'
  | 'sketch'
  | 'drawing';

export type ApprovalStatus =
  | 'pending'
  | 'approved'
  | 'rejected'
  | 'expired'
  | 'canceled'
  | 'retried'
  | 'reworked';

export type ApprovalDecision = 'approve' | 'reject' | 'retry' | 'rework';

export type ApprovalStatusFilter = 'pending' | 'decided' | 'all';

export type FindingKind =
  | 'missing_deliverable'
  | 'ungrounded'
  | 'constraint_violation'
  | 'analysis'
  | 'geometry'
  | 'other';

export type FindingSeverity = 'error' | 'warning' | 'info';

export interface ApprovalFinding {
  kind: FindingKind;
  severity: FindingSeverity;
  message: string;
}

export interface ApprovalDecisionRecord {
  decision: ApprovalDecision;
  reason: string | null;
  approver: string | null;
  approver_verified: boolean;
  surface: string | null;
  on_behalf_of: string | null;
  agent?: string | null;
  decided_at: string;
}

export interface ApprovalItem {
  id: string;
  kind: ApprovalKind;
  status: ApprovalStatus;
  title: string;
  summary: string;
  project_id: string | null;
  created_at: string;
  deadline: string | null;
  route: 'dashboard' | 'elicitation' | null;
  requested_by: string | null;
  reason_held: string | null;
  findings: ApprovalFinding[];
  allowed_decisions: ApprovalDecision[];
  rework_targets: string[];
  reason_required_for: ApprovalDecision[];
  decidable: boolean;
  not_decidable_reason: string | null;
  detail: Record<string, unknown>;
  decision: ApprovalDecisionRecord | null;
}

export interface ApprovalList {
  items: ApprovalItem[];
  /** Items left out of a project-scoped list for carrying no project. */
  unscopedCount: number;
}

export interface ApprovalDecisionRequest {
  decision: ApprovalDecision;
  reason?: string;
  to_phase?: string;
}

export interface ApprovalListFilters {
  status?: ApprovalStatusFilter;
  projectId?: string;
  kind?: ApprovalKind;
}
