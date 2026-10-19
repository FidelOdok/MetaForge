import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '../../test/test-utils';
import {
  applyToolEvent,
  MAX_TOOL_CALLS,
  useRunToolEvents,
  type ToolProgressEvent,
} from '../use-run-tool-events';

function ev(over: Partial<ToolProgressEvent>): ToolProgressEvent {
  return {
    run_id: 'r1',
    seq: 1,
    call_id: 'c1',
    tool: 'calculix.run_fea',
    phase: 'simulation',
    status: 'started',
    duration_ms: null,
    ...over,
  };
}

describe('applyToolEvent', () => {
  it('folds started then finished into one call', () => {
    let calls = applyToolEvent([], ev({}));
    expect(calls).toHaveLength(1);
    expect(calls[0]?.status).toBe('running');
    calls = applyToolEvent(calls, ev({ seq: 2, status: 'finished', duration_ms: 1200 }));
    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({ status: 'finished', durationMs: 1200, seq: 2 });
  });

  it('does not move a finished call back to running on a replay', () => {
    let calls = applyToolEvent([], ev({}));
    calls = applyToolEvent(calls, ev({ seq: 2, status: 'finished', duration_ms: 5 }));
    const replayed = applyToolEvent(calls, ev({ seq: 1 }));
    expect(replayed).toBe(calls);
    expect(replayed[0]?.status).toBe('finished');
  });

  it('records a failed call', () => {
    const calls = applyToolEvent(applyToolEvent([], ev({})), ev({ seq: 2, status: 'failed' }));
    expect(calls[0]?.status).toBe('failed');
  });

  it('keeps at most MAX_TOOL_CALLS, dropping the oldest', () => {
    let calls = [] as ReturnType<typeof applyToolEvent>;
    for (let i = 0; i < MAX_TOOL_CALLS + 5; i++) {
      calls = applyToolEvent(calls, ev({ seq: i + 1, call_id: `c${i}` }));
    }
    expect(calls).toHaveLength(MAX_TOOL_CALLS);
    expect(calls[0]?.callId).toBe('c5');
  });
});

function sseResponse(frames: string[]): Response {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const f of frames) controller.enqueue(encoder.encode(f));
        // Left open, like a live run: the hook must keep reading.
      },
    }),
    { status: 200 },
  );
}

afterEach(() => vi.unstubAllGlobals());

describe('useRunToolEvents', () => {
  it('streams tool calls from the run events endpoint, asking for history first', async () => {
    const fetchMock = vi.fn(async () =>
      sseResponse([
        'event: run.status\ndata: {"status":"running"}\n\n',
        `id: 1\nevent: tool.progress\ndata: ${JSON.stringify(ev({}))}\n\n`,
        `id: 2\nevent: tool.progress\ndata: ${JSON.stringify(ev({ seq: 2, status: 'finished', duration_ms: 800 }))}\n\n`,
      ]),
    );
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useRunToolEvents('r1', true));
    await waitFor(() => expect(result.current.calls).toHaveLength(1));
    expect(result.current.calls[0]).toMatchObject({ tool: 'calculix.run_fea', status: 'finished' });
    expect(result.current.connected).toBe(true);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain('/runs/r1/events');
    expect((init.headers as Record<string, string>)['Last-Event-ID']).toBe('0');
  });

  it('opens no stream for an inactive run or no run id', () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useRunToolEvents('r1', false));
    renderHook(() => useRunToolEvents(undefined, true));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(result.current.connected).toBe(false);
    expect(result.current.calls).toEqual([]);
  });
});
