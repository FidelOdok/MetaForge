/**
 * The pure fold behind the activity overlay (FORGE-786, ASVG-009, RT-016).
 *
 * Every rule that keeps the overlay honest lives here, with no React and no
 * I/O, so it can be tested exhaustively:
 *
 * - Operations are keyed by `operation_id`.
 * - An event whose `sequence` is at or below the last one applied to its
 *   operation is dropped (dedup, and no regression from a late delivery).
 * - A terminal operation (completed, failed, cancelled, superseded, stale)
 *   never moves back to an active state.
 * - An event that names an older `target_revision` than the one shown never
 *   replaces the newer revision or its artifacts.
 * - A snapshot (first load, resync, reconnect) is merged so that it cannot
 *   undo what a newer event already applied.
 */

import {
  isTerminal,
  type ActionRef,
  type ArtifactRef,
  type OperationEvent,
  type OperationEventType,
  type OperationSnapshot,
  type OperationsSnapshotResponse,
  type OperationStage,
  type OperationState,
} from '../types/operations';

/** A verified milestone the operation reached, in order. */
export interface OperationMilestone {
  sequence: number;
  type: OperationEventType;
  label: string;
  at: string;
  stageId: string | null;
}

/** A raw event line kept for Engineering's detail view. */
export interface OperationLogEntry {
  sequence: number;
  type: OperationEventType;
  state: OperationState;
  severity: OperationSnapshot['severity'];
  message: string;
  stageId: string | null;
  targetRevision: string | null;
  at: string;
}

export interface OperationView extends OperationSnapshot {
  milestones: OperationMilestone[];
  log: OperationLogEntry[];
}

export interface OperationsState {
  /** The highest `project_seq` seen; the stream resumes after it. */
  cursor: number;
  byId: Record<string, OperationView>;
}

export const EMPTY_OPERATIONS: OperationsState = { cursor: 0, byId: {} };

/** How many raw events an operation keeps for the detail view. */
export const LOG_LIMIT = 50;
/** How many milestones an operation keeps. */
export const MILESTONE_LIMIT = 20;

const MILESTONE_TYPES: ReadonlySet<OperationEventType> = new Set([
  'milestone',
  'partial_result',
  'preview_ready',
  'completed',
]);

/**
 * The number inside a revision label ("8", "r8", "rev-8"), or null when there
 * is none. Two revisions are only ordered when both carry one.
 */
export function revisionNumber(revision: string | null | undefined): number | null {
  if (!revision) return null;
  const match = /(\d+)(?!.*\d)/.exec(revision);
  return match ? Number(match[1]) : null;
}

/** True when `candidate` is known to be older than `current`. */
export function isOlderRevision(candidate: string | null | undefined, current: string | null | undefined): boolean {
  const a = revisionNumber(candidate);
  const b = revisionNumber(current);
  return a !== null && b !== null && a < b;
}

/** Failures and decisions are shown at once; routine updates are batched (RT-034). */
export function isUrgent(event: Pick<OperationEvent, 'type' | 'state' | 'requires_action'>): boolean {
  return (
    event.type === 'review_required' ||
    event.type === 'failed' ||
    event.state === 'failed' ||
    event.state === 'waiting_for_user' ||
    event.requires_action
  );
}

function mergeArtifacts(current: ArtifactRef[], incoming: ArtifactRef[]): ArtifactRef[] {
  if (incoming.length === 0) return current;
  const out = current.filter((a) => !incoming.some((b) => b.kind === a.kind && b.id === a.id));
  return [...out, ...incoming];
}

function upsertStage(stages: OperationStage[], event: OperationEvent): OperationStage[] {
  if (!event.stage_id) return stages;
  const existing = stages.find((s) => s.stage_id === event.stage_id);
  if (existing && isTerminal(existing.state) && !isTerminal(event.state)) return stages;
  const next: OperationStage = {
    stage_id: event.stage_id,
    stage_title: event.stage_title ?? existing?.stage_title ?? null,
    domain: event.domain ?? existing?.domain ?? 'general',
    state: event.state,
    updated_at: event.occurred_at,
  };
  return existing ? stages.map((s) => (s.stage_id === event.stage_id ? next : s)) : [...stages, next];
}

function viewFromEvent(event: OperationEvent): OperationView {
  return {
    operation_id: event.operation_id,
    kind: event.kind,
    domain: event.domain,
    title: event.title,
    state: event.state,
    run_id: event.run_id,
    started_at: null,
    updated_at: event.occurred_at,
    last_sequence: 0,
    base_revision: null,
    target_revision: null,
    message: '',
    severity: 'info',
    artifact_refs: [],
    requires_action: false,
    action_refs: [],
    progress: null,
    stages: [],
    milestones: [],
    log: [],
  };
}

/**
 * Fold one event into one operation. Returns the same object when the event
 * is ignored, so a caller can tell "dropped" from "applied" by identity.
 */
export function applyToOperation(current: OperationView | undefined, event: OperationEvent): OperationView {
  const base = current ?? viewFromEvent(event);
  if (current && event.sequence <= current.last_sequence) return current;
  if (current && isTerminal(current.state) && !isTerminal(event.state)) return current;

  const olderRevision = isOlderRevision(event.target_revision, base.target_revision);
  const label = event.message || event.stage_title || event.title;
  const milestones = MILESTONE_TYPES.has(event.type)
    ? [
        ...base.milestones,
        { sequence: event.sequence, type: event.type, label, at: event.occurred_at, stageId: event.stage_id },
      ].slice(-MILESTONE_LIMIT)
    : base.milestones;
  const log = [
    ...base.log,
    {
      sequence: event.sequence,
      type: event.type,
      state: event.state,
      severity: event.severity,
      message: event.message,
      stageId: event.stage_id,
      targetRevision: event.target_revision,
      at: event.occurred_at,
    },
  ].slice(-LOG_LIMIT);

  const actions: ActionRef[] = event.action_refs ?? [];
  return {
    ...base,
    kind: event.kind ?? base.kind,
    domain: event.domain ?? base.domain,
    title: event.title || base.title,
    state: event.state,
    run_id: event.run_id ?? base.run_id,
    // The clock starts when the operation leaves the queue, as the gateway's does.
    started_at: base.started_at ?? (event.state === 'queued' ? null : event.occurred_at),
    updated_at: event.occurred_at,
    last_sequence: event.sequence,
    base_revision: olderRevision ? base.base_revision : (event.base_revision ?? base.base_revision),
    target_revision: olderRevision ? base.target_revision : (event.target_revision ?? base.target_revision),
    target_key: event.target_key ?? base.target_key ?? null,
    message: event.message || base.message,
    severity: event.severity ?? base.severity,
    artifact_refs: olderRevision ? base.artifact_refs : mergeArtifacts(base.artifact_refs, event.artifact_refs ?? []),
    requires_action: event.requires_action,
    action_refs: actions,
    progress: event.progress && event.progress.total > 0 ? event.progress : null,
    stages: upsertStage(base.stages, event),
    milestones,
    log,
  };
}

/** Fold one event into the project's operations. */
export function applyEvent(state: OperationsState, event: OperationEvent): OperationsState {
  const cursor = Math.max(state.cursor, event.project_seq ?? 0);
  const current = state.byId[event.operation_id];
  const next = applyToOperation(current, event);
  if (next === current) return cursor === state.cursor ? state : { ...state, cursor };
  return { cursor, byId: { ...state.byId, [event.operation_id]: next } };
}

/** Fold a batch in arrival order. */
export function applyEvents(state: OperationsState, events: readonly OperationEvent[]): OperationsState {
  return events.reduce(applyEvent, state);
}

function viewFromSnapshot(snapshot: OperationSnapshot, previous?: OperationView): OperationView {
  return {
    ...snapshot,
    // A snapshot of an operation that has not run yet carries no start time;
    // keep the one the client already knows instead of losing it.
    started_at: snapshot.started_at ?? previous?.started_at ?? null,
    progress: snapshot.progress && snapshot.progress.total > 0 ? snapshot.progress : null,
    artifact_refs: snapshot.artifact_refs ?? [],
    action_refs: snapshot.action_refs ?? [],
    stages: snapshot.stages ?? [],
    milestones: previous?.milestones ?? [],
    log: previous?.log ?? [],
  };
}

/**
 * Merge an authoritative snapshot. The snapshot wins unless the client has
 * already applied a later event for that operation, the client holds a
 * terminal state the snapshot would undo, or the snapshot names an older
 * revision. Operations absent from the snapshot are kept: a snapshot may be
 * limited, and an operation the user saw finish should not vanish on resync.
 */
export function mergeSnapshot(state: OperationsState, snapshot: OperationsSnapshotResponse): OperationsState {
  const byId = { ...state.byId };
  for (const op of snapshot.operations) {
    const current = byId[op.operation_id];
    if (current) {
      if (op.last_sequence < current.last_sequence) continue;
      if (isTerminal(current.state) && !isTerminal(op.state)) continue;
      if (isOlderRevision(op.target_revision, current.target_revision)) continue;
    }
    byId[op.operation_id] = viewFromSnapshot(op, current);
  }
  return { cursor: Math.max(state.cursor, snapshot.cursor ?? 0), byId };
}

/** Lower sorts first: decisions, then failures, then live work, then the rest. */
function priority(op: OperationView): number {
  switch (op.state) {
    case 'waiting_for_user':
      return 0;
    case 'blocked':
      return 1;
    case 'failed':
      return 2;
    case 'running':
      return 3;
    case 'queued':
      return 4;
    default:
      return 5;
  }
}

/** Every operation, most important first, newest first within a tier. */
export function sortedOperations(state: OperationsState, dismissed: readonly string[] = []): OperationView[] {
  return Object.values(state.byId)
    .filter((op) => !dismissed.includes(op.operation_id))
    .sort((a, b) => priority(a) - priority(b) || b.updated_at.localeCompare(a.updated_at));
}

export function activeOperations(state: OperationsState): OperationView[] {
  return Object.values(state.byId).filter((op) => !isTerminal(op.state));
}
