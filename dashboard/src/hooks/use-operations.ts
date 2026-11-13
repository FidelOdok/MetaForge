import { useEffect, useMemo } from 'react';
import axios from 'axios';

import { getAccessToken } from '../auth/accessToken';
import { getOperations, operationsStreamUrl } from '../api/endpoints/operations';
import { activeOperations, isUrgent } from '../lib/operations-reducer';
import { isSampleMode } from '../lib/sample-workspace';
import { streamSse, type SseMessage } from '../lib/sse';
import { useOperationsStore, useProjectOperations } from '../store/operations-store';
import type { OperationEvent, OperationResync } from '../types/operations';

const RETRY_MIN_MS = 1_000;
const RETRY_MAX_MS = 15_000;
/** Consecutive failures after which the connection is reported as offline. */
export const OFFLINE_AFTER = 3;

export interface UseOperationsTiming {
  retryMinMs?: number;
  retryMaxMs?: number;
}

type FrameHandle = { cancel: () => void };

/** One render per animation frame; a timer where there is no rAF (tests, background tabs). */
function scheduleFrame(fn: () => void): FrameHandle {
  if (typeof requestAnimationFrame === 'function' && typeof document !== 'undefined' && !document.hidden) {
    const id = requestAnimationFrame(fn);
    return { cancel: () => cancelAnimationFrame(id) };
  }
  const id = setTimeout(fn, 16);
  return { cancel: () => clearTimeout(id) };
}

/**
 * Keep a project's operations live (FORGE-786, RT-015..017, RT-034).
 *
 * Fetches the snapshot first, then streams `/operations/stream` with
 * `Last-Event-ID` set to the snapshot cursor, through the fetch-based
 * `streamSse` (so a bearer token reaches a gateway behind an authentication
 * provider). A `resync` frame, or any reconnect after an error or a closed
 * stream, refetches the snapshot and resumes from its cursor.
 *
 * Routine events are batched into one store update per animation frame so a
 * busy run cannot steal frame time from the canvas; a decision or a failure is
 * applied at once. The fold itself is `lib/operations-reducer.ts`.
 *
 * Call it once per mounted view; both Create and Engineering read the same
 * store, so the state carries across a view switch.
 */
export function useOperations(projectId: string | null | undefined, timing: UseOperationsTiming = {}) {
  const retryMinMs = timing.retryMinMs ?? RETRY_MIN_MS;
  const retryMaxMs = timing.retryMaxMs ?? RETRY_MAX_MS;

  useEffect(() => {
    const store = useOperationsStore.getState();
    if (!projectId) return undefined;
    if (isSampleMode()) {
      store.setHealth(projectId, 'offline');
      return undefined;
    }
    const root = new AbortController();
    let attempt: AbortController | undefined;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let frame: FrameHandle | undefined;
    let pending: OperationEvent[] = [];
    let cursor = store.projects[projectId]?.ops.cursor ?? 0;
    let failures = 0;
    let delay = retryMinMs;
    // A hidden tab holds no stream. Chrome allows six connections per host across all tabs, and
    // a stream per open tab starved the tab on screen (health and every request timed out).
    const hidden = () => typeof document !== 'undefined' && document.visibilityState === 'hidden';
    let paused = hidden();

    const flush = () => {
      frame?.cancel();
      frame = undefined;
      if (pending.length === 0) return;
      const batch = pending;
      pending = [];
      useOperationsStore.getState().applyEvents(projectId, batch);
    };

    const headers = (): Record<string, string> => {
      const token = getAccessToken();
      const out: Record<string, string> = { 'Last-Event-ID': String(cursor) };
      if (token) out.Authorization = `Bearer ${token}`;
      return out;
    };

    const setHealth = (health: Parameters<typeof store.setHealth>[1]) => {
      if (!root.signal.aborted) useOperationsStore.getState().setHealth(projectId, health);
    };

    const connect = async (): Promise<void> => {
      if (root.signal.aborted || paused) return;
      const current = new AbortController();
      attempt = current;
      const onRootAbort = () => current.abort();
      root.signal.addEventListener('abort', onRootAbort);
      let resync = false;
      let unavailable = false;
      try {
        const snapshot = await getOperations(projectId, { signal: current.signal });
        if (root.signal.aborted) return;
        flush();
        useOperationsStore.getState().mergeSnapshot(projectId, snapshot);
        cursor = Math.max(cursor, snapshot.cursor);
        failures = 0;
        delay = retryMinMs;
        setHealth('live');

        const onMessage = (message: SseMessage) => {
          if (message.event === 'heartbeat') {
            setHealth('live');
            return;
          }
          let body: unknown;
          try {
            body = JSON.parse(message.data);
          } catch {
            return;
          }
          if (message.event === 'resync') {
            const data = body as Partial<OperationResync>;
            if (typeof data.cursor === 'number' && data.cursor < cursor) cursor = data.cursor;
            resync = true;
            current.abort();
            return;
          }
          if (message.event !== 'operation') return;
          const event = body as OperationEvent;
          if (!event || typeof event.operation_id !== 'string') return;
          const seq = Number(message.id ?? event.project_seq);
          if (Number.isFinite(seq)) cursor = Math.max(cursor, seq);
          setHealth('live');
          pending.push(event);
          if (isUrgent(event)) flush();
          else if (!frame) frame = scheduleFrame(flush);
        };

        await streamSse(operationsStreamUrl(projectId), {
          headers: headers(),
          signal: current.signal,
          onMessage,
        });
      } catch (error) {
        // An abort ends the loop and anything else reconnects, except a
        // gateway without the operations routes, which is not retried.
        unavailable = axios.isAxiosError(error) && error.response?.status === 404;
      } finally {
        root.signal.removeEventListener('abort', onRootAbort);
      }
      if (root.signal.aborted) return;
      flush();
      if (paused) return;
      if (unavailable) {
        setHealth('offline');
        return;
      }
      if (resync) {
        // The cursor is too old for replay: refetch the snapshot right away.
        void connect();
        return;
      }
      failures += 1;
      setHealth(failures >= OFFLINE_AFTER ? 'offline' : 'reconnecting');
      retryTimer = setTimeout(() => void connect(), delay);
      delay = Math.min(delay * 2, retryMaxMs);
    };

    // A view switch remounts this hook; the shared state it left keeps its
    // health until the new snapshot answers, so the card does not flash.
    const onVisibility = () => {
      if (hidden()) {
        if (paused) return;
        paused = true;
        if (retryTimer) clearTimeout(retryTimer);
        attempt?.abort();
      } else if (paused) {
        // Back on screen: reconnect now; the snapshot and the cursor catch up what was missed.
        paused = false;
        failures = 0;
        delay = retryMinMs;
        void connect();
      }
    };
    document.addEventListener('visibilitychange', onVisibility);

    if (!store.projects[projectId]) setHealth('connecting');
    void connect();
    return () => {
      document.removeEventListener('visibilitychange', onVisibility);
      root.abort();
      attempt?.abort();
      frame?.cancel();
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, [projectId, retryMinMs, retryMaxMs]);

  const project = useProjectOperations(projectId);
  const active = useMemo(() => activeOperations(project.ops), [project.ops]);
  return {
    operations: project.ops,
    health: project.health,
    active,
    /** True when the project has at least one operation the client knows of. */
    known: Object.keys(project.ops.byId).length > 0,
  };
}
