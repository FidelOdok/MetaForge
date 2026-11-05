import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { commitParameters, getPreviewDefinition } from '../api/endpoints/parametricPreview';
import { retryOperation } from '../api/endpoints/operations';
import { twinKeys } from './use-twin';
import { amountOf, isDirty, withOverrides, type PreviewDefinition, type PreviewOverrides } from '../lib/preview/definition';
import { buildPreviewTimed, type PreviewBuild } from '../lib/preview/buildPreview';
import { parametricDisplay, type ParametricDisplay } from '../lib/preview/display';
import { NO_LENGTH_EDITS, applyLength, geometryPayload, type LengthEdits } from '../lib/preview/lineLength';

export const parametricPreviewKeys = {
  definition: (projectId: string, sketchKey: string, featureId: string | null, jobId: string | null) =>
    ['parametric-preview', projectId, sketchKey, featureId, jobId] as const,
  all: ['parametric-preview'] as const,
};

const RUNNING = new Set(['queued', 'running']);

function refusalText(err: unknown): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  return 'The change could not be saved.';
}

export interface CommitExtra {
  amount?: number;
  radius?: Record<string, number>;
  /** A line's length by its dimension's constraint id: committed with the solved geometry. */
  length?: Record<string, number>;
  material?: string;
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
  /**
   * A line's length: re-solves the sketch's accepted rules in the browser and
   * redraws the preview from the solved geometry. A length the rules cannot
   * all hold leaves the last good geometry and sets `conflict`.
   */
  setLength: (constraintId: string, value: number) => void;
  /** Why the last length cannot hold, in plain words; nothing is committed while it is set. */
  conflict: string | null;
  /** Commit the browser's edits: a new parametric revision, then one exact build. */
  commit: (source: 'drag' | 'numeric', extra?: CommitExtra) => Promise<void>;
  committing: boolean;
  /** Re-run the failed exact build (the existing operation retry). */
  retry: () => Promise<void>;
  retrying: boolean;
  jobId: string | null;
}

/**
 * The L2 parametric preview of one parametric sketch (C3D slice 5).
 *
 * Every parameter change rebuilds the mesh here, in the browser, at once
 * (no network call). A commit (drag release, Enter, Apply) writes the new
 * parameter as the sketch's next revision with its `base_revision`, and the
 * gateway schedules the one exact build; the definition is then polled
 * until the job ends, so the stage can swap to the validated L3 model.
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

  const query = useQuery({
    queryKey: parametricPreviewKeys.definition(projectId ?? '', sketchKey ?? '', featureId, jobId),
    queryFn: ({ signal }) => getPreviewDefinition(projectId!, sketchKey!, { featureId, jobId }, signal),
    enabled,
    // While the exact build runs, look again until it ends (polling the twin).
    refetchInterval: (q) => (q.state.data?.build && RUNNING.has(q.state.data.build.status) ? 1_500 : false),
    retry: false,
  });
  const definition = query.data;

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

  // Line lengths (FORGE-808): the last good solve, read at release too (a
  // drag's callbacks are captured when the drag starts, so state is stale there).
  const [conflict, setConflict] = useState<string | null>(null);
  const solveRef = useRef<LengthEdits>(NO_LENGTH_EDITS);
  const baseRevision = definition?.base_revision;
  useEffect(() => {
    solveRef.current = NO_LENGTH_EDITS;
    setConflict(null);
  }, [baseRevision]);
  const setLength = useCallback(
    (constraintId: string, value: number) => {
      if (!definition) return;
      setCommitError(null);
      const next = applyLength(definition, solveRef.current, constraintId, value);
      solveRef.current = next;
      setConflict(next.conflict);
      if (!next.conflict) setOverrides((o) => ({ ...o, length: next.length, solved: next.solved }));
    },
    [definition],
  );

  const commit = useCallback(
    async (source: 'drag' | 'numeric', extra: CommitExtra = {}) => {
      if (!definition || !projectId || !sketchKey) return;
      let edits = solveRef.current;
      for (const [cid, v] of Object.entries(extra.length ?? {})) {
        if (edits.conflict || edits.length[cid] !== v) edits = applyLength(definition, edits, cid, v);
      }
      if (edits.conflict) {
        // Refused: the accepted rules cannot all hold. Back to the last saved part.
        setCommitError(edits.conflict);
        solveRef.current = NO_LENGTH_EDITS;
        setConflict(null);
        setOverrides((o) => ({ ...o, length: undefined, solved: undefined }));
        return;
      }
      const amount = extra.amount ?? overrides.amount;
      const radius = { ...(overrides.radius ?? {}), ...(extra.radius ?? {}) };
      const values: Record<string, number | string> = {};
      const name = definition.op === 'extrude' ? 'depth' : 'angle';
      if (amount !== undefined && (Math.abs(amount - amountOf(definition)) > 1e-9 || definition.parameters[name]?.assumed)) {
        values[name] = amount;
      }
      if (extra.material) values.material = extra.material;
      const dimensions: Record<string, number> = {};
      let lengthEdited = false;
      for (const d of definition.dimensions) {
        const r = d.type === 'length' ? edits.length[d.constraint_id] : radius[d.constraint_id];
        if (r !== undefined && Math.abs(r - d.value) > 1e-9) {
          dimensions[d.constraint_id] = r;
          if (d.type === 'length') lengthEdited = true;
        }
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
          ...(lengthEdited ? { geometry: geometryPayload(edits.solved) } : {}),
          source,
          preview_ms: buildMs,
        });
        solveRef.current = NO_LENGTH_EDITS;
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
    setLength,
    conflict,
    commit,
    committing,
    retry,
    retrying,
    jobId,
  };
}
