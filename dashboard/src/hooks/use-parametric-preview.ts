import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { commitParameters, getPreviewDefinition } from '../api/endpoints/parametricPreview';
import { retryOperation } from '../api/endpoints/operations';
import { twinKeys } from './use-twin';
import { amountOf, isDirty, withOverrides, type PreviewDefinition, type PreviewOverrides } from '../lib/preview/definition';
import { buildPreviewTimed, type PreviewBuild } from '../lib/preview/buildPreview';
import { parametricDisplay, type ParametricDisplay } from '../lib/preview/display';
import type { OperationView } from '../lib/operations-reducer';
import { useProjectOperations } from '../store/operations-store';

export const parametricPreviewKeys = {
  definition: (projectId: string, sketchKey: string, featureId: string | null, jobId: string | null) =>
    ['parametric-preview', projectId, sketchKey, featureId, jobId] as const,
  all: ['parametric-preview'] as const,
};

const RUNNING = new Set(['queued', 'running']);
/** How often the twin is polled while the operations stream is offline. */
export const FALLBACK_POLL_MS = 1_500;
/**
 * Exact build events that change what the stage shows (the gateway's
 * `exact_cad` event table): the exact B-Rep was built, a validated revision
 * was recorded; a terminal state is the build's verdict (`validation_completed`
 * arrives as `completed`) or its failure.
 */
const SWAP_STAGES = new Set(['exact_geometry_ready', 'validation_completed', 'artifact_ready']);
const VERDICT_STATES = new Set(['completed', 'failed', 'stale', 'superseded', 'cancelled']);

/**
 * What the stream has said about the exact builds this preview cares about:
 * its own job's operation and any other build of the same CAD item. Changes
 * only when one of the events above arrives.
 */
export function buildSignal(
  ops: Record<string, OperationView>,
  operationId: string | null,
  targetKey: string | null,
): string {
  const parts: string[] = [];
  for (const op of Object.values(ops)) {
    const mine = operationId !== null && op.operation_id === operationId;
    const sameTarget = !!targetKey && op.target_key === targetKey && op.kind === 'job';
    if (!mine && !sameTarget) continue;
    const stages = op.stages.map((s) => s.stage_id).filter((s) => SWAP_STAGES.has(s)).sort();
    const verdict = VERDICT_STATES.has(op.state) ? op.state : '';
    if (!stages.length && !verdict) continue;
    parts.push(`${op.operation_id}:${verdict}:${stages.join(',')}`);
  }
  return parts.sort().join('|');
}

function refusalText(err: unknown): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  return 'The change could not be saved.';
}

export interface ParametricPreviewState {
  definition: PreviewDefinition | undefined;
  loading: boolean;
  /** The definition could not be read (no parametric sketch, or it cannot be previewed). */
  unavailable: string | null;
  overrides: PreviewOverrides;
  /** The definition with the browser's uncommitted edits (what is drawn). */
  effective: PreviewDefinition | undefined;
  build: PreviewBuild | null;
  buildError: string | null;
  /** The last preview rebuild, milliseconds (3D-RC-004: target 100 ms for simple parts). */
  buildMs: number | null;
  display: ParametricDisplay | null;
  setAmount: (value: number) => void;
  setRadius: (constraintId: string, value: number) => void;
  /** Commit the browser's edits: a new parametric revision, then one exact build. */
  commit: (source: 'drag' | 'numeric', extra?: { amount?: number; radius?: Record<string, number>; material?: string }) => Promise<void>;
  committing: boolean;
  /** Re-run the failed exact build (the existing operation retry). */
  retry: () => Promise<void>;
  retrying: boolean;
  jobId: string | null;
  /**
   * How the stage learns that the exact build ended: from the operations
   * stream, or by polling the twin while the stream is offline.
   */
  updates: 'stream' | 'polling' | 'idle';
}

/**
 * The L2 parametric preview of one parametric sketch (C3D slice 5).
 *
 * Every parameter change rebuilds the mesh here, in the browser, at once
 * (no network call). A commit (drag release, Enter, Apply) writes the new
 * parameter as the sketch's next revision with its `base_revision`, and the
 * gateway schedules the one exact build. The stage then follows that build
 * on the operations stream (the store `use-operations` keeps): when it
 * reports the exact geometry built, the build's verdict or its failure, the
 * definition is read again, so the stage swaps to the validated L3 model or
 * shows the failure. Only while the stream is not live is the twin polled
 * instead, every {@link FALLBACK_POLL_MS} ms.
 */
export function useParametricPreview(
  projectId: string | null | undefined,
  sketchKey: string | null | undefined,
  featureId: string | null = null,
): ParametricPreviewState {
  const qc = useQueryClient();
  const [overrides, setOverrides] = useState<PreviewOverrides>({});
  const [jobId, setJobId] = useState<string | null>(null);
  const [committing, setCommitting] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [commitError, setCommitError] = useState<string | null>(null);
  const [buildMs, setBuildMs] = useState<number | null>(null);
  const enabled = !!projectId && !!sketchKey;
  const operations = useProjectOperations(projectId);
  const streamLive = operations.health === 'live';

  const query = useQuery({
    queryKey: parametricPreviewKeys.definition(projectId ?? '', sketchKey ?? '', featureId, jobId),
    queryFn: ({ signal }) => getPreviewDefinition(projectId!, sketchKey!, { featureId, jobId }, signal),
    enabled,
    // While the exact build runs and the stream is offline, poll the twin.
    refetchInterval: (q) =>
      !streamLive && q.state.data?.build && RUNNING.has(q.state.data.build.status) ? FALLBACK_POLL_MS : false,
    retry: false,
  });
  const definition = query.data;
  const building = !!definition?.build && RUNNING.has(definition.build.status);

  // The stream's word on this build: read the definition again when it changes.
  const operationId = definition?.build?.operation_id ?? (jobId ? `job:${jobId}` : null);
  const targetKey = definition?.cad?.item_key ?? null;
  const watched = `${operationId ?? ''}|${targetKey ?? ''}`;
  const signal = useMemo(
    () => buildSignal(operations.ops.byId, operationId, targetKey),
    [operations.ops.byId, operationId, targetKey],
  );
  const lastSignal = useRef<{ watched: string; signal: string } | null>(null);
  const refetch = query.refetch;
  useEffect(() => {
    const last = lastSignal.current;
    lastSignal.current = { watched, signal };
    // The first reading of what is watched is the history already known
    // (the definition just read covers it); only a change after it is news.
    if (!last || last.watched !== watched || last.signal === signal || !enabled) return;
    const previous = last.signal;
    void refetch();
    // A new verdict may have added a revision: the twin's lists are stale too.
    const before = new Set(previous.split('|'));
    if (signal.split('|').some((part) => !before.has(part) && /:(completed|failed|stale|superseded|cancelled):/.test(part))) {
      void qc.invalidateQueries({ queryKey: twinKeys.all });
    }
  }, [signal, watched, enabled, refetch, qc]);

  const effective = useMemo(() => (definition ? withOverrides(definition, overrides) : undefined), [definition, overrides]);
  const built = useMemo(() => {
    if (!effective) return { build: null, ms: null, error: null };
    try {
      const { build, ms } = buildPreviewTimed(effective);
      return { build, ms, error: null };
    } catch (err) {
      return { build: null, ms: null, error: err instanceof Error ? err.message : 'The preview could not be drawn.' };
    }
  }, [effective]);
  useEffect(() => {
    if (built.ms !== null) setBuildMs(built.ms);
  }, [built.ms]);
  // The previous mesh's GPU buffers are released when a new one replaces it.
  const lastGeometry = useRef(built.build?.geometry);
  useEffect(() => {
    const prev = lastGeometry.current;
    lastGeometry.current = built.build?.geometry;
    if (prev && prev !== built.build?.geometry) prev.dispose();
  }, [built.build]);

  const dirty = !!definition && isDirty(definition, overrides);
  const display = useMemo(
    () => (definition ? parametricDisplay(definition, { dirty, committing, commitError }) : null),
    [definition, dirty, committing, commitError],
  );

  const setAmount = useCallback((value: number) => {
    setCommitError(null);
    setOverrides((o) => ({ ...o, amount: value }));
  }, []);
  const setRadius = useCallback((constraintId: string, value: number) => {
    setCommitError(null);
    setOverrides((o) => ({ ...o, radius: { ...(o.radius ?? {}), [constraintId]: value } }));
  }, []);

  const commit = useCallback(
    async (source: 'drag' | 'numeric', extra: { amount?: number; radius?: Record<string, number>; material?: string } = {}) => {
      if (!definition || !projectId || !sketchKey) return;
      const amount = extra.amount ?? overrides.amount;
      const radius = { ...(overrides.radius ?? {}), ...(extra.radius ?? {}) };
      const values: Record<string, number | string> = {};
      const name = definition.op === 'extrude' ? 'depth' : 'angle';
      if (amount !== undefined && (Math.abs(amount - amountOf(definition)) > 1e-9 || definition.parameters[name]?.assumed)) {
        values[name] = amount;
      }
      if (extra.material) values.material = extra.material;
      const dimensions: Record<string, number> = {};
      for (const d of definition.dimensions) {
        const r = radius[d.constraint_id];
        if (r !== undefined && Math.abs(r - d.value) > 1e-9) dimensions[d.constraint_id] = r;
      }
      if (!Object.keys(values).length && !Object.keys(dimensions).length) return;
      setCommitting(true);
      setCommitError(null);
      try {
        const reply = await commitParameters(projectId, sketchKey, {
          base_revision: definition.base_revision,
          feature_id: definition.feature_id,
          values,
          dimensions,
          source,
          preview_ms: buildMs,
        });
        const next = reply.build?.job_id ?? null;
        if (reply.build && !reply.build.job_id && reply.build.status !== 'succeeded' && reply.build.detail) {
          setCommitError(reply.build.detail);
        }
        setJobId(next);
        await qc.invalidateQueries({ queryKey: parametricPreviewKeys.all });
        await qc.invalidateQueries({ queryKey: twinKeys.all });
        setOverrides({});
      } catch (err) {
        setCommitError(refusalText(err));
      } finally {
        setCommitting(false);
      }
    },
    [definition, projectId, sketchKey, overrides, buildMs, qc],
  );

  const retry = useCallback(async () => {
    const op = display?.retryOperation;
    if (!op || !projectId) return;
    setRetrying(true);
    try {
      const reply = await retryOperation(projectId, op);
      setJobId(reply.operation_id.startsWith('job:') ? reply.operation_id.slice(4) : reply.operation_id);
      await qc.invalidateQueries({ queryKey: parametricPreviewKeys.all });
    } catch (err) {
      setCommitError(refusalText(err));
    } finally {
      setRetrying(false);
    }
  }, [display?.retryOperation, projectId, qc]);

  const status = (query.error as { response?: { status?: number } } | null)?.response?.status;
  const unavailable = query.error ? (status === 404 ? 'No parametric sketch to preview.' : refusalText(query.error)) : null;

  return {
    definition,
    loading: enabled && query.isLoading,
    unavailable,
    overrides,
    effective,
    build: built.build,
    buildError: built.error,
    buildMs,
    display,
    setAmount,
    setRadius,
    commit,
    committing,
    retry,
    retrying,
    jobId,
    updates: building ? (streamLive ? 'stream' : 'polling') : 'idle',
  };
}
