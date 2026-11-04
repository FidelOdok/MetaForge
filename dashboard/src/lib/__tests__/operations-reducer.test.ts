import { describe, expect, it } from 'vitest';
import {
  activeOperations,
  applyEvent,
  applyEvents,
  EMPTY_OPERATIONS,
  isOlderRevision,
  isUrgent,
  LOG_LIMIT,
  mergeSnapshot,
  revisionNumber,
  sortedOperations,
} from '../operations-reducer';
import { opEvent, opSnapshot, snapshotResponse } from '../../test/operation-fixtures';

describe('applyEvent: dedup and ordering', () => {
  it('creates an operation from its first event', () => {
    const s = applyEvent(EMPTY_OPERATIONS, opEvent({ sequence: 1, project_seq: 4 }));
    const op = s.byId['run:a'];
    expect(op).toMatchObject({ state: 'running', last_sequence: 1, title: 'Shaping your enclosure' });
    expect(s.cursor).toBe(4);
  });

  it('drops a duplicate delivery of the same sequence', () => {
    const first = applyEvent(EMPTY_OPERATIONS, opEvent({ sequence: 1, message: 'first' }));
    const again = applyEvent(first, opEvent({ sequence: 1, message: 'duplicate' }));
    expect(again.byId['run:a']).toBe(first.byId['run:a']);
    expect(again.byId['run:a']?.message).toBe('first');
  });

  it('drops an out-of-order event older than the last applied', () => {
    let s = applyEvent(EMPTY_OPERATIONS, opEvent({ sequence: 5, message: 'five', stage_id: 'cad', stage_title: 'Profile' }));
    s = applyEvent(s, opEvent({ sequence: 3, message: 'three', state: 'queued' }));
    expect(s.byId['run:a']).toMatchObject({ last_sequence: 5, message: 'five', state: 'running' });
  });

  it('applies events with gaps in sequence', () => {
    let s = applyEvent(EMPTY_OPERATIONS, opEvent({ sequence: 1 }));
    s = applyEvent(s, opEvent({ sequence: 4, type: 'milestone', message: 'Profile drawn' }));
    expect(s.byId['run:a']?.last_sequence).toBe(4);
    expect(s.byId['run:a']?.milestones.map((m) => m.label)).toEqual(['Profile drawn']);
  });

  it('keeps operations independent: one sequence does not gate another', () => {
    let s = applyEvent(EMPTY_OPERATIONS, opEvent({ operation_id: 'run:a', sequence: 9 }));
    s = applyEvent(s, opEvent({ operation_id: 'job:b', sequence: 1, kind: 'job', domain: 'electronics' }));
    expect(Object.keys(s.byId).sort()).toEqual(['job:b', 'run:a']);
  });

  it('advances the cursor even for a dropped event, never backwards', () => {
    let s = applyEvent(EMPTY_OPERATIONS, opEvent({ sequence: 2, project_seq: 20 }));
    s = applyEvent(s, opEvent({ sequence: 2, project_seq: 21 }));
    expect(s.cursor).toBe(21);
    s = applyEvent(s, opEvent({ sequence: 3, project_seq: 5 }));
    expect(s.cursor).toBe(21);
  });
});

describe('applyEvent: no terminal regression', () => {
  for (const terminal of ['completed', 'failed', 'cancelled', 'superseded', 'stale'] as const) {
    it(`never moves ${terminal} back to running`, () => {
      let s = applyEvent(EMPTY_OPERATIONS, opEvent({ sequence: 1 }));
      s = applyEvent(s, opEvent({ sequence: 2, type: terminal === 'completed' ? 'completed' : 'failed', state: terminal }));
      s = applyEvent(s, opEvent({ sequence: 3, type: 'resumed', state: 'running' }));
      expect(s.byId['run:a']?.state).toBe(terminal);
      expect(s.byId['run:a']?.last_sequence).toBe(2);
    });
  }

  it('accepts a later terminal after a terminal (for example completed then stale)', () => {
    let s = applyEvent(EMPTY_OPERATIONS, opEvent({ sequence: 1, type: 'completed', state: 'completed' }));
    s = applyEvent(s, opEvent({ sequence: 2, type: 'stale', state: 'stale' }));
    expect(s.byId['run:a']?.state).toBe('stale');
  });
});

describe('applyEvent: no revision regression', () => {
  it('parses revision numbers', () => {
    expect(revisionNumber('8')).toBe(8);
    expect(revisionNumber('r12')).toBe(12);
    expect(revisionNumber('rev-3')).toBe(3);
    expect(revisionNumber('abc')).toBeNull();
    expect(revisionNumber(null)).toBeNull();
    expect(isOlderRevision('7', '8')).toBe(true);
    expect(isOlderRevision('9', '8')).toBe(false);
    expect(isOlderRevision('x', '8')).toBe(false);
  });

  it('does not let an older revision replace a newer one or its artifacts', () => {
    const newer = { kind: 'work_product' as const, id: 'wp-9', label: 'Rev 9', url: null, authority: 'preview' as const };
    const older = { kind: 'work_product' as const, id: 'wp-8', label: 'Rev 8', url: null, authority: 'exact' as const };
    let s = applyEvent(
      EMPTY_OPERATIONS,
      opEvent({ sequence: 1, type: 'preview_ready', target_revision: '9', artifact_refs: [newer] }),
    );
    s = applyEvent(
      s,
      opEvent({ sequence: 2, type: 'partial_result', target_revision: '8', base_revision: '7', artifact_refs: [older] }),
    );
    const op = s.byId['run:a'];
    expect(op?.target_revision).toBe('9');
    expect(op?.artifact_refs.map((a) => a.id)).toEqual(['wp-9']);
    // The event itself is still recorded as seen.
    expect(op?.last_sequence).toBe(2);
  });

  it('accepts a newer revision', () => {
    let s = applyEvent(EMPTY_OPERATIONS, opEvent({ sequence: 1, target_revision: '8' }));
    s = applyEvent(s, opEvent({ sequence: 2, target_revision: '9' }));
    expect(s.byId['run:a']?.target_revision).toBe('9');
  });
});

describe('applyEvent: stages, milestones, actions, progress', () => {
  it('upserts stages and keeps parallel domains', () => {
    const s = applyEvents(EMPTY_OPERATIONS, [
      opEvent({ sequence: 1, stage_id: 'cad', stage_title: 'Shape', domain: 'mechanical' }),
      opEvent({ sequence: 2, stage_id: 'eda', stage_title: 'Board', domain: 'electronics' }),
      opEvent({ sequence: 3, stage_id: 'cad', stage_title: 'Shape', domain: 'mechanical', type: 'milestone', state: 'completed' }),
    ]);
    const stages = s.byId['run:a']?.stages ?? [];
    expect(stages.map((x) => [x.stage_id, x.state])).toEqual([
      ['cad', 'completed'],
      ['eda', 'running'],
    ]);
  });

  it('does not move a finished stage back to running', () => {
    const s = applyEvents(EMPTY_OPERATIONS, [
      opEvent({ sequence: 1, stage_id: 'cad', state: 'running' }),
      opEvent({ sequence: 2, stage_id: 'cad', state: 'completed' }),
      opEvent({ sequence: 3, stage_id: 'cad', state: 'running' }),
    ]);
    expect(s.byId['run:a']?.stages[0]?.state).toBe('completed');
  });

  it('replaces action_refs with the latest set', () => {
    let s = applyEvent(
      EMPTY_OPERATIONS,
      opEvent({
        sequence: 1,
        type: 'review_required',
        state: 'waiting_for_user',
        requires_action: true,
        action_refs: [{ kind: 'approval', id: 'gate:a', label: 'Review the shape' }],
      }),
    );
    expect(s.byId['run:a']?.requires_action).toBe(true);
    s = applyEvent(s, opEvent({ sequence: 2, type: 'resumed', state: 'running' }));
    expect(s.byId['run:a']?.action_refs).toEqual([]);
    expect(s.byId['run:a']?.requires_action).toBe(false);
  });

  it('keeps progress only when a total is measurable', () => {
    let s = applyEvent(EMPTY_OPERATIONS, opEvent({ sequence: 1, progress: { done: 3, total: 8, unit: 'checks' } }));
    expect(s.byId['run:a']?.progress).toEqual({ done: 3, total: 8, unit: 'checks' });
    s = applyEvent(s, opEvent({ sequence: 2, progress: { done: 1, total: 0, unit: 'checks' } }));
    expect(s.byId['run:a']?.progress).toBeNull();
  });

  it('caps the raw log', () => {
    const events = Array.from({ length: LOG_LIMIT + 10 }, (_, i) => opEvent({ sequence: i + 1 }));
    const s = applyEvents(EMPTY_OPERATIONS, events);
    expect(s.byId['run:a']?.log).toHaveLength(LOG_LIMIT);
    expect(s.byId['run:a']?.log[0]?.sequence).toBe(11);
  });
});

describe('mergeSnapshot', () => {
  it('loads a snapshot into an empty state', () => {
    const s = mergeSnapshot(EMPTY_OPERATIONS, snapshotResponse([opSnapshot()], 12));
    expect(s.cursor).toBe(12);
    expect(s.byId['run:a']?.state).toBe('running');
  });

  it('does not undo a later event already applied', () => {
    const s0 = applyEvent(EMPTY_OPERATIONS, opEvent({ sequence: 5, message: 'newer' }));
    const s = mergeSnapshot(s0, snapshotResponse([opSnapshot({ last_sequence: 3, message: 'older' })], 2));
    expect(s.byId['run:a']?.message).toBe('newer');
    expect(s.cursor).toBe(s0.cursor);
  });

  it('does not reopen a terminal operation', () => {
    const s0 = applyEvent(EMPTY_OPERATIONS, opEvent({ sequence: 5, type: 'failed', state: 'failed' }));
    const s = mergeSnapshot(s0, snapshotResponse([opSnapshot({ last_sequence: 6, state: 'running' })]));
    expect(s.byId['run:a']?.state).toBe('failed');
  });

  it('does not regress the revision', () => {
    const s0 = applyEvent(EMPTY_OPERATIONS, opEvent({ sequence: 5, target_revision: '9' }));
    const s = mergeSnapshot(s0, snapshotResponse([opSnapshot({ last_sequence: 6, target_revision: '8' })]));
    expect(s.byId['run:a']?.target_revision).toBe('9');
  });

  it('takes a newer snapshot and keeps the milestones already seen', () => {
    const s0 = applyEvent(EMPTY_OPERATIONS, opEvent({ sequence: 2, type: 'milestone', message: 'Profile drawn' }));
    const s = mergeSnapshot(s0, snapshotResponse([opSnapshot({ last_sequence: 7, state: 'completed' })]));
    expect(s.byId['run:a']?.state).toBe('completed');
    expect(s.byId['run:a']?.milestones).toHaveLength(1);
  });
});

describe('selectors', () => {
  it('sorts decisions first, then failures, then live work, then finished', () => {
    const s = applyEvents(EMPTY_OPERATIONS, [
      opEvent({ operation_id: 'a', sequence: 1, state: 'completed', type: 'completed' }),
      opEvent({ operation_id: 'b', sequence: 1, state: 'running' }),
      opEvent({ operation_id: 'c', sequence: 1, state: 'waiting_for_user', type: 'review_required' }),
      opEvent({ operation_id: 'd', sequence: 1, state: 'failed', type: 'failed' }),
    ]);
    expect(sortedOperations(s).map((o) => o.operation_id)).toEqual(['c', 'd', 'b', 'a']);
    expect(sortedOperations(s, ['c']).map((o) => o.operation_id)).toEqual(['d', 'b', 'a']);
    expect(activeOperations(s).map((o) => o.operation_id).sort()).toEqual(['b', 'c']);
  });

  it('marks decisions and failures as urgent', () => {
    expect(isUrgent(opEvent({ type: 'review_required', state: 'waiting_for_user' }))).toBe(true);
    expect(isUrgent(opEvent({ type: 'failed', state: 'failed' }))).toBe(true);
    expect(isUrgent(opEvent({ type: 'milestone', state: 'running' }))).toBe(false);
  });
});
