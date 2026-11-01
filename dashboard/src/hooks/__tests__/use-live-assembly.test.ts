import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '../../test/test-utils';
import {
  applyLiveEvent,
  EMPTY_LIVE_STATE,
  stateFromSnapshot,
  useLiveAssembly,
  type LiveEventWire,
} from '../use-live-assembly';

function placed(seq: number, nodeId: string, over: Partial<LiveEventWire> = {}): LiveEventWire {
  return {
    type: 'assembly.part_placed',
    seq,
    assembly: 'Pi5',
    part: { node_id: nodeId, name: nodeId.toUpperCase() },
    position: [1, 2, 3],
    rotation: [0, 90, 0],
    ...over,
  };
}

describe('applyLiveEvent', () => {
  it('places parts into a new assembly in order', () => {
    let s = applyLiveEvent(EMPTY_LIVE_STATE, placed(1, 'a'));
    s = applyLiveEvent(s, placed(2, 'b'));
    expect(s.assemblies).toHaveLength(1);
    expect(s.assemblies[0]?.parts.map((p) => p.nodeId)).toEqual(['a', 'b']);
    expect(s.assemblies[0]?.parts[0]).toMatchObject({ position: [1, 2, 3], rotation: [0, 90, 0] });
  });

  it('treats a second placement of the same part as a move', () => {
    let s = applyLiveEvent(EMPTY_LIVE_STATE, placed(1, 'a'));
    s = applyLiveEvent(s, placed(2, 'a', { position: [9, 9, 9] }));
    expect(s.assemblies[0]?.parts).toHaveLength(1);
    expect(s.assemblies[0]?.parts[0]?.position).toEqual([9, 9, 9]);
  });

  it('removes a part and clears an assembly', () => {
    let s = applyLiveEvent(EMPTY_LIVE_STATE, placed(1, 'a'));
    s = applyLiveEvent(s, placed(2, 'b'));
    s = applyLiveEvent(s, placed(3, 'a', { type: 'assembly.part_removed' }));
    expect(s.assemblies[0]?.parts.map((p) => p.nodeId)).toEqual(['b']);
    s = applyLiveEvent(s, { type: 'assembly.cleared', seq: 4, assembly: 'Pi5' });
    expect(s.assemblies[0]?.parts).toEqual([]);
  });

  it('ignores an event at or before the state sequence', () => {
    const s = applyLiveEvent(EMPTY_LIVE_STATE, placed(5, 'a'));
    expect(applyLiveEvent(s, placed(5, 'b'))).toBe(s);
    expect(applyLiveEvent(s, placed(3, 'b'))).toBe(s);
  });
});

describe('stateFromSnapshot', () => {
  it('maps the wire shape and defaults missing vectors to zero', () => {
    const s = stateFromSnapshot({
      seq: 7,
      assemblies: [{ name: 'Pi5', parts: [{ node_id: 'a', name: 'Base', position: [1, 2, 3] }] }],
    });
    expect(s.seq).toBe(7);
    expect(s.assemblies[0]?.parts[0]).toMatchObject({
      nodeId: 'a',
      name: 'Base',
      position: [1, 2, 3],
      rotation: [0, 0, 0],
    });
  });
});

function sseResponse(frames: string[]): Response {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const f of frames) controller.enqueue(encoder.encode(f));
      },
    }),
    { status: 200 },
  );
}

afterEach(() => vi.unstubAllGlobals());

describe('useLiveAssembly', () => {
  it('shows the snapshot, then each placement as it arrives', async () => {
    const snapshot = { seq: 1, assemblies: [{ name: 'Pi5', parts: [{ node_id: 'a', name: 'Base', seq: 1 }] }] };
    const fetchMock = vi.fn(async () =>
      sseResponse([
        `id: 1\nevent: live.snapshot\ndata: ${JSON.stringify(snapshot)}\n\n`,
        `id: 2\nevent: live.event\ndata: ${JSON.stringify(placed(2, 'b'))}\n\n`,
      ]),
    );
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useLiveAssembly('p1'));
    await waitFor(() => expect(result.current.state.assemblies[0]?.parts).toHaveLength(2));
    expect(result.current.connected).toBe(true);
    const [url] = fetchMock.mock.calls[0] as unknown as [string];
    expect(url).toContain('/twin/projects/p1/live');
  });

  it('opens no stream without a project', () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useLiveAssembly(undefined));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(result.current.connected).toBe(false);
  });
});

// A stream response that sends headers and never a frame, like a Cloudflare quick tunnel,
// and that honours abort, as a real fetch does.
function stalledStream(signal: AbortSignal | undefined): Response {
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        signal?.addEventListener('abort', () =>
          controller.error(new DOMException('Aborted', 'AbortError')),
        );
      },
    }),
    { status: 200 },
  );
}

const SNAPSHOT = {
  seq: 3,
  assemblies: [{ name: 'Pi5', parts: [{ node_id: 'a', name: 'Base', seq: 3 }] }],
};
const FAST = { firstFrameMs: 40, pollMs: 25, probeMs: 60 };

describe('useLiveAssembly fallback to polling', () => {
  it('falls back to polling the state when the stream never sends a frame', async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push(url);
        if (url.endsWith('/live/state')) return new Response(JSON.stringify(SNAPSHOT), { status: 200 });
        return stalledStream(init?.signal ?? undefined);
      }),
    );
    const { result } = renderHook(() => useLiveAssembly('p1', FAST));
    expect(result.current.mode).toBe('connecting');
    await waitFor(() => expect(result.current.mode).toBe('polling'));
    await waitFor(() => expect(result.current.state.assemblies[0]?.parts).toHaveLength(1));
    expect(result.current.connected).toBe(true);
    expect(calls.some((u) => u.endsWith('/live/state'))).toBe(true);
  });

  it('picks up a change on the next poll', async () => {
    let state: unknown = SNAPSHOT;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) =>
        url.endsWith('/live/state')
          ? new Response(JSON.stringify(state), { status: 200 })
          : stalledStream(init?.signal ?? undefined),
      ),
    );
    const { result } = renderHook(() => useLiveAssembly('p1', FAST));
    await waitFor(() => expect(result.current.state.assemblies[0]?.parts).toHaveLength(1));
    state = {
      seq: 4,
      assemblies: [{ name: 'Pi5', parts: [...SNAPSHOT.assemblies[0]!.parts, { node_id: 'b', name: 'Lid', seq: 4 }] }],
    };
    await waitFor(() => expect(result.current.state.assemblies[0]?.parts).toHaveLength(2));
  });

  it('is offline when neither the stream nor a plain request works', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('no', { status: 502 })));
    const { result } = renderHook(() => useLiveAssembly('p1', FAST));
    await waitFor(() => expect(result.current.mode).toBe('offline'));
    expect(result.current.connected).toBe(false);
  });

  it('goes back to the stream when a later probe connects', async () => {
    let streamCalls = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url.endsWith('/live/state')) return new Response(JSON.stringify(SNAPSHOT), { status: 200 });
        streamCalls += 1;
        if (streamCalls === 1) return stalledStream(init?.signal ?? undefined);
        return sseResponse([`id: 3\nevent: live.snapshot\ndata: ${JSON.stringify(SNAPSHOT)}\n\n`]);
      }),
    );
    const { result } = renderHook(() => useLiveAssembly('p1', FAST));
    await waitFor(() => expect(result.current.mode).toBe('polling'));
    await waitFor(() => expect(result.current.mode).toBe('stream'));
    expect(streamCalls).toBeGreaterThanOrEqual(2);
  });
});
