import { useEffect, useState } from 'react';

import { getAccessToken } from '../auth/accessToken';
import { apiBase } from '../lib/gatewayConfig';
import { streamSse } from '../lib/sse';

/** One committed part placed in an assembly that is being put together. */
export interface LivePart {
  nodeId: string;
  name: string;
  /** mm */
  position: [number, number, number];
  /** degrees about x, y, z */
  rotation: [number, number, number];
  seq: number;
}

export interface LiveAssembly {
  name: string;
  parts: LivePart[];
}

export interface LiveState {
  seq: number;
  assemblies: LiveAssembly[];
}

export const EMPTY_LIVE_STATE: LiveState = { seq: 0, assemblies: [] };

interface WirePart {
  node_id: string;
  name?: string;
  position?: number[];
  rotation?: number[];
  seq?: number;
}

/** The `live.snapshot` payload (FORGE-647). */
export interface LiveSnapshotWire {
  seq: number;
  assemblies: { name: string; parts: WirePart[] }[];
}

/** A `live.event` payload. */
export interface LiveEventWire {
  type: 'assembly.part_placed' | 'assembly.part_removed' | 'assembly.cleared';
  seq: number;
  assembly: string;
  part?: { node_id: string; name?: string };
  position?: number[];
  rotation?: number[];
}

function vec3(value: number[] | undefined): [number, number, number] {
  return [value?.[0] ?? 0, value?.[1] ?? 0, value?.[2] ?? 0];
}

export function stateFromSnapshot(snapshot: LiveSnapshotWire): LiveState {
  return {
    seq: snapshot.seq,
    assemblies: snapshot.assemblies.map((a) => ({
      name: a.name,
      parts: a.parts.map((p) => ({
        nodeId: p.node_id,
        name: p.name ?? p.node_id,
        position: vec3(p.position),
        rotation: vec3(p.rotation),
        seq: p.seq ?? 0,
      })),
    })),
  };
}

/**
 * Fold one change into the state. A change at or before the state's sequence
 * is ignored, so a replay after a reconnect cannot undo a later placement.
 */
export function applyLiveEvent(state: LiveState, event: LiveEventWire): LiveState {
  if (event.seq <= state.seq) return state;
  const assemblies = state.assemblies.map((a) => ({ ...a, parts: [...a.parts] }));
  let target = assemblies.find((a) => a.name === event.assembly);
  if (!target) {
    target = { name: event.assembly, parts: [] };
    assemblies.push(target);
  }
  const nodeId = event.part?.node_id;
  if (event.type === 'assembly.cleared') {
    target.parts = [];
  } else if (nodeId && event.type === 'assembly.part_removed') {
    target.parts = target.parts.filter((p) => p.nodeId !== nodeId);
  } else if (nodeId && event.type === 'assembly.part_placed') {
    const placed: LivePart = {
      nodeId,
      name: event.part?.name ?? nodeId,
      position: vec3(event.position),
      rotation: vec3(event.rotation),
      seq: event.seq,
    };
    target.parts = [...target.parts.filter((p) => p.nodeId !== nodeId), placed];
  }
  return { seq: event.seq, assemblies };
}

const RETRY_MIN_MS = 1_000;
const RETRY_MAX_MS = 15_000;

/** How long to wait for the stream's first frame before falling back to polling. */
export const FIRST_FRAME_MS = 4_000;
/** How often the picture is re-read while the stream is unavailable. */
export const POLL_MS = 1_500;
/** How often the stream is tried again while polling. */
export const PROBE_MS = 30_000;

/**
 * How the view is getting its data.
 *
 * - `stream`: server-sent events, under a second behind.
 * - `polling`: the stream could not be opened on this connection (a tunnel or
 *   proxy that holds a streamed response back), so the current picture is
 *   re-read every {@link POLL_MS}. A second or two behind, and the panel says so.
 * - `connecting`: nothing has answered yet.
 * - `offline`: neither the stream nor a plain request works.
 */
export type LiveMode = 'connecting' | 'stream' | 'polling' | 'offline';

export interface LiveAssemblyTiming {
  firstFrameMs?: number;
  pollMs?: number;
  probeMs?: number;
}

/**
 * The assemblies being put together in a project (FORGE-647), streamed from
 * `GET /twin/projects/{id}/live`, or polled from `.../live/state` when the
 * stream cannot be opened.
 *
 * The first frame of every stream is a full snapshot, so a viewer that opens
 * late, or reconnects after a gap, is correct without replaying events. A
 * stream that answers with headers but never a frame (Cloudflare quick tunnels
 * do this) is given {@link FIRST_FRAME_MS}, then abandoned for polling, and
 * tried again every {@link PROBE_MS}. `connected` is true in both live modes;
 * an empty list while `offline` means "can't see", not "nothing is being built".
 */
export function useLiveAssembly(projectId: string | undefined, timing: LiveAssemblyTiming = {}) {
  const firstFrameMs = timing.firstFrameMs ?? FIRST_FRAME_MS;
  const pollMs = timing.pollMs ?? POLL_MS;
  const probeMs = timing.probeMs ?? PROBE_MS;
  const [state, setState] = useState<LiveState>(EMPTY_LIVE_STATE);
  const [mode, setMode] = useState<LiveMode>('connecting');

  useEffect(() => {
    setState(EMPTY_LIVE_STATE);
    if (!projectId) {
      setMode('offline');
      return undefined;
    }
    setMode('connecting');
    const root = new AbortController();
    let streamAbort: AbortController | undefined;
    let streaming = false;
    let polling = false;
    let pollTimer: ReturnType<typeof setTimeout> | undefined;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let delay = RETRY_MIN_MS;

    const base = () => `${apiBase()}/twin/projects/${projectId}/live`;
    const headers = (): Record<string, string> => {
      const token = getAccessToken();
      return token ? { Authorization: `Bearer ${token}` } : {};
    };

    const stopPolling = () => {
      polling = false;
      if (pollTimer) clearTimeout(pollTimer);
    };

    const pollOnce = async (): Promise<void> => {
      if (!polling || root.signal.aborted) return;
      try {
        const response = await fetch(`${base()}/state`, { headers: headers(), signal: root.signal });
        if (!response.ok) throw new Error(`state answered ${response.status}`);
        const body = (await response.json()) as LiveSnapshotWire;
        if (polling && !streaming) {
          setState(stateFromSnapshot(body));
          setMode('polling');
        }
      } catch {
        if (polling && !streaming && !root.signal.aborted) setMode('offline');
      }
      if (polling && !root.signal.aborted) pollTimer = setTimeout(() => void pollOnce(), pollMs);
    };

    const startPolling = () => {
      if (polling || root.signal.aborted) return;
      polling = true;
      void pollOnce();
    };

    const connect = async (): Promise<void> => {
      if (root.signal.aborted) return;
      const attempt = new AbortController();
      streamAbort = attempt;
      let gotFrame = false;
      const onRootAbort = () => attempt.abort();
      root.signal.addEventListener('abort', onRootAbort);
      // A stream that sends headers and then nothing never rejects, so the
      // wait for the first frame is a timer, not a catch.
      const frameTimer = setTimeout(() => {
        if (gotFrame) return;
        attempt.abort();
        startPolling();
      }, firstFrameMs);
      try {
        await streamSse(base(), {
          headers: headers(),
          signal: attempt.signal,
          onMessage: (message) => {
            let body: unknown;
            try {
              body = JSON.parse(message.data);
            } catch {
              return;
            }
            if (message.event === 'live.snapshot') {
              gotFrame = true;
              streaming = true;
              delay = RETRY_MIN_MS;
              clearTimeout(frameTimer);
              stopPolling();
              setMode('stream');
              setState(stateFromSnapshot(body as LiveSnapshotWire));
            } else if (message.event === 'live.event') {
              setState((current) => applyLiveEvent(current, body as LiveEventWire));
            }
          },
        });
      } catch {
        // Fall through. An abort is not an error.
      }
      clearTimeout(frameTimer);
      root.signal.removeEventListener('abort', onRootAbort);
      if (root.signal.aborted) return;
      const wasStreaming = streaming;
      streaming = false;
      startPolling();
      // A stream that was working is retried quickly; one that never opened is
      // only probed now and then.
      const wait = wasStreaming ? delay : probeMs;
      if (wasStreaming) delay = Math.min(delay * 2, RETRY_MAX_MS);
      retryTimer = setTimeout(() => void connect(), wait);
    };

    void connect();
    return () => {
      root.abort();
      streamAbort?.abort();
      stopPolling();
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, [projectId, firstFrameMs, pollMs, probeMs]);

  return { state, mode, connected: mode === 'stream' || mode === 'polling' };
}
