import type { OperationEvent, OperationSnapshot, OperationsSnapshotResponse } from '../types/operations';

let seq = 0;

/** An operation event with sensible defaults; override what the test is about. */
export function opEvent(over: Partial<OperationEvent> = {}): OperationEvent {
  seq += 1;
  return {
    event_id: `evt-${seq}`,
    project_id: 'p1',
    project_seq: seq,
    operation_id: 'run:a',
    sequence: 1,
    run_id: 'a',
    kind: 'run',
    title: 'Shaping your enclosure',
    domain: 'mechanical',
    stage_id: null,
    stage_title: null,
    type: 'started',
    state: 'running',
    occurred_at: '2026-11-04T10:00:00Z',
    base_revision: null,
    target_revision: null,
    message: 'Working on the shape',
    severity: 'info',
    artifact_refs: [],
    requires_action: false,
    action_refs: [],
    progress: null,
    ...over,
  };
}

export function opSnapshot(over: Partial<OperationSnapshot> = {}): OperationSnapshot {
  return {
    operation_id: 'run:a',
    kind: 'run',
    domain: 'mechanical',
    title: 'Shaping your enclosure',
    state: 'running',
    run_id: 'a',
    started_at: '2026-11-04T10:00:00Z',
    updated_at: '2026-11-04T10:00:05Z',
    last_sequence: 1,
    base_revision: null,
    target_revision: null,
    message: 'Working on the shape',
    severity: 'info',
    artifact_refs: [],
    requires_action: false,
    action_refs: [],
    progress: null,
    stages: [],
    ...over,
  };
}

export function snapshotResponse(
  operations: OperationSnapshot[],
  cursor = 10,
  projectId = 'p1',
): OperationsSnapshotResponse {
  return { project_id: projectId, cursor, operations };
}
