import { useEffect, useState } from 'react';

import { getAccessToken } from '../auth/accessToken';
import { apiBase } from '../lib/gatewayConfig';
import { streamSse } from '../lib/sse';

/** One tool call a run's phases made, folded from its started and finished events. */
export interface ToolCall {
  callId: string;
  tool: string;
  phase: string;
  status: 'running' | 'finished' | 'failed';
  durationMs: number | null;
  /** The stream sequence of the call's latest event. */
  seq: number;
}

/** The `tool.progress` payload the gateway sends (FORGE-643). */
export interface ToolProgressEvent {
  run_id: string;
  seq: number;
  call_id: string;
  tool: string;
  phase: string;
  status: 'started' | 'finished' | 'failed';
  duration_ms: number | null;
}

/** Most calls kept; a long run must not grow the page without bound. */
export const MAX_TOOL_CALLS = 200;

/**
 * Fold one event into the list of calls. An event at or before a call's latest
 * sequence is ignored, so a replay after a reconnect cannot move a finished
 * call back to running.
 */
export function applyToolEvent(calls: ToolCall[], event: ToolProgressEvent): ToolCall[] {
  const status: ToolCall['status'] = event.status === 'started' ? 'running' : event.status;
  const index = calls.findIndex((c) => c.callId === event.call_id);
  const existing = calls[index];
  if (!existing) {
    const next = [
      ...calls,
      {
        callId: event.call_id,
        tool: event.tool,
        phase: event.phase,
        status,
        durationMs: event.duration_ms,
        seq: event.seq,
      },
    ];
    return next.length > MAX_TOOL_CALLS ? next.slice(next.length - MAX_TOOL_CALLS) : next;
  }
  if (event.seq <= existing.seq) return calls;
  const next = calls.slice();
  next[index] = { ...existing, status, durationMs: event.duration_ms, seq: event.seq };
  return next;
}

const RETRY_MIN_MS = 1_000;
const RETRY_MAX_MS = 15_000;

/**
 * The tool calls of a run, streamed from `GET /runs/{id}/events` (FORGE-645).
 *
 * The 2s `useFlowState` poll stays the source of truth for phase status; this
 * adds the tool calls between its ticks. A dropped connection reconnects with
 * `Last-Event-ID` so the gateway replays what was missed, and a viewer that
 * opens a run already underway asks for everything the gateway still holds.
 * `connected` is false until the stream is open, so a caller can say "not
 * live" rather than show an empty list that reads as "no tool calls".
 */
export function useRunToolEvents(runId: string | undefined, active: boolean) {
  const [calls, setCalls] = useState<ToolCall[]>([]);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    setCalls([]);
    if (!runId || !active) {
      setConnected(false);
      return undefined;
    }
    const abort = new AbortController();
    let lastSeq = 0;
    let delay = RETRY_MIN_MS;
    let retry: ReturnType<typeof setTimeout> | undefined;

    const connect = async (): Promise<void> => {
      const headers: Record<string, string> = { 'Last-Event-ID': String(lastSeq) };
      const token = getAccessToken();
      if (token) headers.Authorization = `Bearer ${token}`;
      try {
        await streamSse(`${apiBase()}/runs/${runId}/events`, {
          headers,
          signal: abort.signal,
          onMessage: (message) => {
            setConnected(true);
            delay = RETRY_MIN_MS;
            if (message.event !== 'tool.progress') return;
            let event: ToolProgressEvent;
            try {
              event = JSON.parse(message.data) as ToolProgressEvent;
            } catch {
              return;
            }
            lastSeq = Math.max(lastSeq, event.seq);
            setCalls((current) => applyToolEvent(current, event));
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
  }, [runId, active]);

  return { calls, connected };
}
