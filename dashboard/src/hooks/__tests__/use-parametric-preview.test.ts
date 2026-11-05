import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '../../test/test-utils';
import { definition, validatedCad } from '../../lib/preview/__tests__/fixtures';
import { opEvent } from '../../test/operation-fixtures';
import { useOperationsStore } from '../../store/operations-store';
import type { PreviewDefinition } from '../../lib/preview/definition';
import type { OperationEvent } from '../../types/operations';

/**
 * The L3 swap is event driven: the stage follows its exact build on the
 * operations stream and polls the twin only while the stream is offline.
 */

const getPreviewDefinition = vi.fn();
const commitParameters = vi.fn();
vi.mock('../../api/endpoints/parametricPreview', () => ({
  getPreviewDefinition: (...a: unknown[]) => getPreviewDefinition(...a),
  commitParameters: (...a: unknown[]) => commitParameters(...a),
}));
vi.mock('../../api/endpoints/operations', async (orig) => ({
  ...(await orig<typeof import('../../api/endpoints/operations')>()),
  retryOperation: vi.fn(),
}));

import { buildSignal, FALLBACK_POLL_MS, useParametricPreview } from '../use-parametric-preview';

const PROJECT = 'proj-1';
const committed = definition({ depth: 7, base_revision: 2, sketch_ref: 'PSK-SLOT@2', definition_hash: 'hash-7' });
const running: PreviewDefinition = { ...committed, build: { job_id: 'j1', status: 'running', operation_id: 'job:j1' } };
const validated: PreviewDefinition = {
  ...committed,
  cad: validatedCad('hash-7'),
  build: { job_id: 'j1', status: 'succeeded', operation_id: 'job:j1' },
};
const failed: PreviewDefinition = { ...committed, build: { job_id: 'j1', status: 'failed', operation_id: 'job:j1' } };

let replies: PreviewDefinition[] = [];
function serve(...defs: PreviewDefinition[]) {
  replies = defs;
  let i = 0;
  getPreviewDefinition.mockImplementation(async () => replies[Math.min(i++, replies.length - 1)]);
}

function jobEvent(over: Partial<OperationEvent>): OperationEvent {
  return opEvent({
    project_id: PROJECT,
    operation_id: 'job:j1',
    run_id: null,
    kind: 'job',
    target_key: 'CAD-SLOT',
    ...over,
  });
}

function emit(...events: OperationEvent[]) {
  act(() => useOperationsStore.getState().applyEvents(PROJECT, events));
}

async function committedHook() {
  const hook = renderHook(() => useParametricPreview(PROJECT, 'PSK-SLOT'));
  await waitFor(() => expect(hook.result.current.definition).toBeDefined());
  await act(async () => {
    await hook.result.current.commit('numeric', { amount: 7 });
  });
  await waitFor(() => expect(hook.result.current.definition?.build?.status).toBe('running'));
  return hook;
}

async function idle(ms: number) {
  await act(async () => {
    await new Promise((r) => setTimeout(r, ms));
  });
}

beforeEach(() => {
  getPreviewDefinition.mockReset();
  commitParameters.mockReset().mockResolvedValue({ build: { status: 'running', job_id: 'j1', operation_id: 'job:j1' } });
  useOperationsStore.getState().reset();
});

describe('useParametricPreview: the L3 swap', () => {
  it('follows the build on the live stream, without polling the twin', async () => {
    act(() => useOperationsStore.getState().setHealth(PROJECT, 'live'));
    serve(definition(), running);
    const hook = await committedHook();
    expect(hook.result.current.updates).toBe('stream');

    const calls = getPreviewDefinition.mock.calls.length;
    await idle(FALLBACK_POLL_MS + 300);
    expect(getPreviewDefinition.mock.calls.length).toBe(calls);

    // The exact B-Rep is built: the stage reads the definition again.
    serve(running);
    emit(jobEvent({ sequence: 2, type: 'started', state: 'running' }));
    await idle(20);
    expect(getPreviewDefinition.mock.calls.length).toBe(calls);
    emit(jobEvent({ sequence: 30, type: 'milestone', state: 'running', stage_id: 'exact_geometry_ready' }));
    await waitFor(() => expect(getPreviewDefinition.mock.calls.length).toBe(calls + 1));

    // The verdict: the validated L3 model of this definition arrives.
    serve(validated);
    emit(jobEvent({ sequence: 90, type: 'completed', state: 'completed' }));
    await waitFor(() => expect(hook.result.current.definition?.cad.matches_definition).toBe(true));
    expect(hook.result.current.display?.show).toBe('l3');
    expect(hook.result.current.updates).toBe('idle');
  });

  it('shows the failure the moment the stream reports it', async () => {
    act(() => useOperationsStore.getState().setHealth(PROJECT, 'live'));
    serve(definition(), running);
    const hook = await committedHook();
    serve(failed);
    emit(jobEvent({ sequence: 95, type: 'failed', state: 'failed', severity: 'error' }));
    await waitFor(() => expect(hook.result.current.display?.attention).toBe(true));
    expect(hook.result.current.display?.retryOperation).toBe('job:j1');
  });

  it('polls the twin only while the stream is offline', async () => {
    act(() => useOperationsStore.getState().setHealth(PROJECT, 'offline'));
    serve(definition(), running);
    const hook = await committedHook();
    expect(hook.result.current.updates).toBe('polling');
    serve(validated);
    await idle(FALLBACK_POLL_MS + 300);
    await waitFor(() => expect(hook.result.current.definition?.build?.status).toBe('succeeded'));
    expect(hook.result.current.display?.show).toBe('l3');
  });
});

describe('buildSignal', () => {
  const view = (over: Partial<OperationEvent>) => {
    useOperationsStore.getState().applyEvents(PROJECT, [jobEvent(over)]);
    return useOperationsStore.getState().projects[PROJECT]!.ops.byId;
  };

  it('changes only on the events that change the stage', () => {
    let ops = view({ sequence: 1, type: 'started', state: 'running' });
    expect(buildSignal(ops, 'job:j1', null)).toBe('');
    ops = view({ sequence: 30, type: 'milestone', stage_id: 'exact_geometry_ready' });
    expect(buildSignal(ops, 'job:j1', null)).toBe('job:j1::exact_geometry_ready');
    ops = view({ sequence: 90, type: 'completed', state: 'completed' });
    expect(buildSignal(ops, 'job:j1', null)).toBe('job:j1:completed:exact_geometry_ready');
  });

  it('also follows another build of the same CAD item, and ignores other items', () => {
    const ops = view({ operation_id: 'job:other', sequence: 90, type: 'failed', state: 'failed' });
    expect(buildSignal(ops, null, 'CAD-SLOT')).toBe('job:other:failed:');
    expect(buildSignal(ops, null, 'CAD-ELSE')).toBe('');
  });
});
