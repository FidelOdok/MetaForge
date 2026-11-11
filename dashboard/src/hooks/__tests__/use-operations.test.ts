import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '../../test/test-utils';
import { opEvent, opSnapshot, snapshotResponse } from '../../test/operation-fixtures';
import type { SseMessage, StreamSseOptions } from '../../lib/sse';

const getOperations = vi.fn();
vi.mock('../../api/endpoints/operations', () => ({
  getOperations: (...args: unknown[]) => getOperations(...args),
  operationsStreamUrl: (projectId: string) => `/api/v1/projects/${projectId}/operations/stream`,
  cancelOperation: vi.fn(),
}));

interface OpenStream {
  url: string;
  options: StreamSseOptions;
  end: () => void;
  fail: (error?: Error) => void;
  send: (message: SseMessage) => void;
}
const streams: OpenStream[] = [];
vi.mock('../../lib/sse', () => ({
  streamSse: (url: string, options: StreamSseOptions) =>
    new Promise<void>((resolve, reject) => {
      options.signal?.addEventListener('abort', () => resolve());
      streams.push({
        url,
        options,
        end: resolve,
        fail: (error) => reject(error ?? new Error('dropped')),
        send: (message) => options.onMessage(message),
      });
    }),
}));

import { useOperations } from '../use-operations';
import { useOperationsStore } from '../../store/operations-store';

function frame(event: ReturnType<typeof opEvent>): SseMessage {
  return { event: 'operation', id: String(event.project_seq), data: JSON.stringify(event) };
}

const opState = () => useOperationsStore.getState().projects.p1;

beforeEach(() => {
  streams.length = 0;
  getOperations.mockReset();
  useOperationsStore.getState().reset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useOperations', () => {
  it('fetches the snapshot first, then streams from its cursor', async () => {
    getOperations.mockResolvedValue(snapshotResponse([opSnapshot({ last_sequence: 2 })], 10));
    const { result } = renderHook(() => useOperations('p1', { retryMinMs: 5 }));

    await waitFor(() => expect(streams).toHaveLength(1));
    expect(getOperations).toHaveBeenCalledTimes(1);
    expect(streams[0]?.url).toBe('/api/v1/projects/p1/operations/stream');
    expect(streams[0]?.options.headers?.['Last-Event-ID']).toBe('10');
    expect(result.current.health).toBe('live');
    expect(result.current.known).toBe(true);
    expect(result.current.active).toHaveLength(1);
  });

  it('batches routine events but applies failures and decisions at once', async () => {
    getOperations.mockResolvedValue(snapshotResponse([], 10));
    renderHook(() => useOperations('p1', { retryMinMs: 5 }));
    await waitFor(() => expect(streams).toHaveLength(1));

    act(() => streams[0]?.send(frame(opEvent({ operation_id: 'run:a', sequence: 1, project_seq: 11 }))));
    // Routine: not yet applied in the same tick.
    expect(opState()?.ops.byId['run:a']).toBeUndefined();
    await waitFor(() => expect(opState()?.ops.byId['run:a']?.state).toBe('running'));

    act(() =>
      streams[0]?.send(frame(opEvent({ operation_id: 'run:b', sequence: 1, project_seq: 12, type: 'failed', state: 'failed' }))),
    );
    // Urgent: applied synchronously.
    expect(opState()?.ops.byId['run:b']?.state).toBe('failed');
  });

  it('refetches the snapshot on resync and resumes from the new cursor', async () => {
    getOperations
      .mockResolvedValueOnce(snapshotResponse([opSnapshot({ last_sequence: 1 })], 10))
      .mockResolvedValueOnce(snapshotResponse([opSnapshot({ last_sequence: 9, state: 'completed' })], 40));
    renderHook(() => useOperations('p1', { retryMinMs: 5 }));
    await waitFor(() => expect(streams).toHaveLength(1));

    act(() => streams[0]?.send({ event: 'resync', data: JSON.stringify({ reason: 'cursor_expired', cursor: 40 }) }));

    await waitFor(() => expect(streams).toHaveLength(2));
    expect(getOperations).toHaveBeenCalledTimes(2);
    expect(streams[1]?.options.headers?.['Last-Event-ID']).toBe('40');
    expect(opState()?.ops.byId['run:a']?.state).toBe('completed');
  });

  it('reports reconnecting after a dropped stream, then resumes from a fresh snapshot', async () => {
    getOperations.mockResolvedValue(snapshotResponse([], 10));
    renderHook(() => useOperations('p1', { retryMinMs: 20 }));
    await waitFor(() => expect(streams).toHaveLength(1));

    act(() => streams[0]?.send(frame(opEvent({ sequence: 1, project_seq: 15 }))));
    await act(async () => {
      streams[0]?.fail();
    });
    await waitFor(() => expect(opState()?.health).toBe('reconnecting'));

    await waitFor(() => expect(streams).toHaveLength(2));
    expect(getOperations).toHaveBeenCalledTimes(2);
    expect(streams[1]?.options.headers?.['Last-Event-ID']).toBe('15');
    await waitFor(() => expect(opState()?.health).toBe('live'));
  });

  it('goes offline after repeated failures', async () => {
    getOperations.mockRejectedValue(new Error('network'));
    renderHook(() => useOperations('p1', { retryMinMs: 1, retryMaxMs: 2 }));
    await waitFor(() => expect(opState()?.health).toBe('offline'));
    expect(streams).toHaveLength(0);
  });

  it('stops quietly when the gateway has no operations routes', async () => {
    const notFound = Object.assign(new Error('404'), { isAxiosError: true, response: { status: 404 } });
    getOperations.mockRejectedValue(notFound);
    renderHook(() => useOperations('p1', { retryMinMs: 1 }));
    await waitFor(() => expect(opState()?.health).toBe('offline'));
    await new Promise((r) => setTimeout(r, 20));
    expect(getOperations).toHaveBeenCalledTimes(1);
  });

  it('does nothing without a project', () => {
    renderHook(() => useOperations(undefined));
    expect(getOperations).not.toHaveBeenCalled();
  });

  it('closes the stream while the tab is hidden and picks up from its cursor when it is shown', async () => {
    // Every open tab held a stream for good, and Chrome allows six connections per host, so
    // background tabs starved the one on screen: health failed and requests timed out.
    const visibility = vi.spyOn(document, 'visibilityState', 'get');
    visibility.mockReturnValue('visible');
    getOperations.mockResolvedValue(snapshotResponse([], 10));
    renderHook(() => useOperations('p1', { retryMinMs: 5 }));
    await waitFor(() => expect(streams).toHaveLength(1));

    visibility.mockReturnValue('hidden');
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(streams[0]?.options.signal?.aborted).toBe(true);
    await new Promise((r) => setTimeout(r, 40));
    expect(streams).toHaveLength(1);
    expect(getOperations).toHaveBeenCalledTimes(1);

    visibility.mockReturnValue('visible');
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await waitFor(() => expect(streams).toHaveLength(2));
    expect(getOperations).toHaveBeenCalledTimes(2);
    expect(streams[1]?.options.headers?.['Last-Event-ID']).toBe('10');
    visibility.mockRestore();
  });

  it('does not open a stream for a tab that starts hidden', async () => {
    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    getOperations.mockResolvedValue(snapshotResponse([], 10));
    renderHook(() => useOperations('p1', { retryMinMs: 5 }));
    await new Promise((r) => setTimeout(r, 30));
    expect(getOperations).not.toHaveBeenCalled();
    visibility.mockRestore();
  });

  it('aborts the stream on unmount', async () => {
    getOperations.mockResolvedValue(snapshotResponse([], 10));
    const { unmount } = renderHook(() => useOperations('p1'));
    await waitFor(() => expect(streams).toHaveLength(1));
    unmount();
    expect(streams[0]?.options.signal?.aborted).toBe(true);
  });
});
