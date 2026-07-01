/** Harness run types for the dashboard (MET-548). Mirrors the /v1/runs API. */

export type RunStatus =
  | 'queued'
  | 'running'
  | 'awaiting_approval'
  | 'completed'
  | 'failed'
  | 'rejected'
  | 'canceled';

export type ApprovalDecision = 'approve' | 'reject';

export interface HarnessRun {
  id: string;
  status: RunStatus;
  request: Record<string, unknown>;
  createdAt: number;
  updatedAt: number;
  error?: string;
  approvalReason?: string;
  result?: Record<string, unknown>;
  history: RunStatus[];
}
