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

/**
 * The assemblies being put together in a project, streamed from
 * `GET /twin/projects/{id}/live` (FORGE-647).
 *
 * The first frame of every connection is a full snapshot, so a viewer that
 * opens late, or reconnects after a gap, is correct without replaying events.
 * `connected` is false until the stream is open: an empty list while
 * disconnected means "can't see", not "nothing is being built".
 */
export function useLiveAssembly(projectId: string | undefined) {
  const [state, setState] = useState<LiveState>(EMPTY_LIVE_STATE);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    setState(EMPTY_LIVE_STATE);
    if (!projectId) {
      setConnected(false);
      return undefined;
    }
    const abort = new AbortController();
    let delay = RETRY_MIN_MS;
    let retry: ReturnType<typeof setTimeout> | undefined;

    const connect = async (): Promise<void> => {
      const headers: Record<string, string> = {};
      const token = getAccessToken();
      if (token) headers.Authorization = `Bearer ${token}`;
      try {
        await streamSse(`${apiBase()}/twin/projects/${projectId}/live`, {
          headers,
          signal: abort.signal,
          onMessage: (message) => {
            let body: unknown;
            try {
              body = JSON.parse(message.data);
            } catch {
              return;
            }
            if (message.event === 'live.snapshot') {
              setConnected(true);
              delay = RETRY_MIN_MS;
              setState(stateFromSnapshot(body as LiveSnapshotWire));
            } else if (message.event === 'live.event') {
              setState((current) => applyLiveEvent(current, body as LiveEventWire));
            }
          },
        });
      } catch {
        // Fall through to the retry below. An abort is not an error.
      }
      if (abort.signal.aborted) return;
      setConnected(false);
      retry = setTimeout(() => void connect(), delay);
      delay = Math.min(delay * 2, RETRY_MAX_MS);
    };

    void connect();
    return () => {
      abort.abort();
      if (retry) clearTimeout(retry);
      setConnected(false);
    };
  }, [projectId]);

  return { state, connected };
}
