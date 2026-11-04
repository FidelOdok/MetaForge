/**
 * Project operations (CDE slice 1, FORGE-785 / FORGE-786).
 *
 * Mirrors the operation event contract the gateway serves at
 * `/v1/projects/{id}/operations` (snapshot) and `.../operations/stream` (SSE).
 * A design-flow run is one operation (`run:<id>`), a tool job is one
 * (`job:<id>`), a Create chat turn is one (`chat:<id>`). Approvals are not
 * operations: they arrive as `review_required` events with `action_refs`.
 */

export type OperationKind = 'run' | 'job' | 'chat';

export type OperationDomain =
  | 'concept'
  | 'sketch'
  | 'mechanical'
  | 'electronics'
  | 'firmware'
  | 'simulation'
  | 'manufacturing'
  | 'cross_domain'
  | 'general';

export type OperationEventType =
  | 'accepted'
  | 'queued'
  | 'started'
  | 'milestone'
  | 'partial_result'
  | 'preview_ready'
  | 'review_required'
  | 'blocked'
  | 'resumed'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'superseded'
  | 'stale';

export type OperationState =
  | 'queued'
  | 'running'
  | 'waiting_for_user'
  | 'blocked'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'superseded'
  | 'stale';

export type OperationSeverity = 'info' | 'warning' | 'error';

export type ArtifactAuthority = 'illustrative' | 'preview' | 'exact' | 'validated';

export interface ArtifactRef {
  kind: 'work_product' | 'file' | 'glb' | 'image' | 'step';
  id: string;
  label: string;
  url: string | null;
  authority: ArtifactAuthority | null;
}

export interface ActionRef {
  kind: 'approval' | 'retry' | 'cancel' | 'open';
  id: string;
  label: string;
}

/** Only present when a measurable total exists (for example checks done / total). */
export interface OperationProgress {
  done: number;
  total: number;
  unit: string;
}

export interface OperationEvent {
  event_id: string;
  project_id: string;
  project_seq: number;
  operation_id: string;
  sequence: number;
  run_id: string | null;
  kind: OperationKind;
  title: string;
  domain: OperationDomain;
  stage_id: string | null;
  stage_title: string | null;
  type: OperationEventType;
  state: OperationState;
  occurred_at: string;
  base_revision: string | null;
  target_revision: string | null;
  /** What the operation works on (an item key); a newer one on it supersedes this (FORGE-788). */
  target_key?: string | null;
  /** The failed operation this one retries (FORGE-788). */
  retry_of?: string | null;
  /** Server-authored plain text, never prompts or tool arguments. */
  message: string;
  severity: OperationSeverity;
  artifact_refs: ArtifactRef[];
  requires_action: boolean;
  action_refs: ActionRef[];
  progress: OperationProgress | null;
}

export interface OperationStage {
  stage_id: string;
  stage_title: string | null;
  domain: OperationDomain;
  state: OperationState;
  updated_at: string;
}

export interface OperationSnapshot {
  operation_id: string;
  kind: OperationKind;
  domain: OperationDomain;
  title: string;
  state: OperationState;
  run_id: string | null;
  started_at: string;
  updated_at: string;
  last_sequence: number;
  base_revision: string | null;
  target_revision: string | null;
  target_key?: string | null;
  retry_of?: string | null;
  message: string;
  severity: OperationSeverity;
  artifact_refs: ArtifactRef[];
  requires_action: boolean;
  action_refs: ActionRef[];
  progress: OperationProgress | null;
  stages: OperationStage[];
}

export interface OperationsSnapshotResponse {
  project_id: string;
  /** The latest `project_seq`; the stream resumes after it. */
  cursor: number;
  operations: OperationSnapshot[];
}

export interface OperationCancelResponse {
  operation_id: string;
  state: OperationState;
}

/** `POST .../operations/{op}/retry`: the new operation and the one it retries. */
export interface OperationRetryResponse {
  operation_id: string;
  retry_of: string;
  state: OperationState;
}

/** `event: heartbeat` data. */
export interface OperationHeartbeat {
  server_time: string;
  cursor: number;
}

/** `event: resync` data: the cursor is unknown or older than retention. */
export interface OperationResync {
  reason: string;
  cursor: number;
}

export const TERMINAL_STATES: ReadonlySet<OperationState> = new Set([
  'completed',
  'failed',
  'cancelled',
  'superseded',
  'stale',
]);

export function isTerminal(state: OperationState): boolean {
  return TERMINAL_STATES.has(state);
}

/** How the client is hearing about operations. */
export type ConnectionHealth = 'connecting' | 'live' | 'reconnecting' | 'offline';
