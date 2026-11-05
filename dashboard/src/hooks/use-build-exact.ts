import { useCallback, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { getPreviewDefinition, commitParameters } from '../api/endpoints/parametricPreview';
import { requestExactBuild } from '../api/endpoints/cad-artifacts';
import { getManufacturingContext, materialChoices } from '../api/endpoints/manufacturingContext';
import { exactBuildPlan, type ExactQuestion, type MaterialHint } from '../lib/preview/buildExact';
import type { PreviewDefinition } from '../lib/preview/definition';
import { useOperationsStore } from '../store/operations-store';
import { parametricPreviewKeys } from './use-parametric-preview';
import { twinKeys } from './use-twin';

export type BuildExactState =
  | { phase: 'idle' }
  | { phase: 'checking' }
  | { phase: 'asking'; question: ExactQuestion }
  | { phase: 'blocked'; message: string; fix: 'editable' | 'plane' | null }
  | { phase: 'starting' }
  | { phase: 'started'; message: string }
  | { phase: 'error'; message: string };

export const BUILD_STARTED = 'Building the exact model. Its progress is in the activity panel.';
export const BUILD_UP_TO_DATE = 'The exact model is already built from this sketch; nothing needed rebuilding.';

function refusal(err: unknown): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === 'string' ? detail : 'The exact build could not be started.';
}

/**
 * Build exact model from Create, for the project's parametric sketch
 * (FORGE-808). It reads the sketch's preview definition, asks one question
 * at a time for any input only a person can give, then starts the existing
 * exact build: the answers are committed as the sketch's next revision
 * through the parameters route (which schedules the one build), or, with
 * nothing to ask, the exact build route is called directly. Approvals and
 * refusals are the routes' own; progress shows in the activity overlay.
 */
export function useBuildExact(projectId: string | null | undefined, sketchKey: string | null | undefined) {
  const qc = useQueryClient();
  const [state, setState] = useState<BuildExactState>({ phase: 'idle' });
  const ctx = useRef<{ def: PreviewDefinition; answers: Record<string, number | string>; materials: MaterialHint[] } | null>(null);

  const started = useCallback(
    async (message: string) => {
      if (projectId) useOperationsStore.getState().setSize(projectId, 'expanded');
      setState({ phase: 'started', message });
      await qc.invalidateQueries({ queryKey: parametricPreviewKeys.all });
      await qc.invalidateQueries({ queryKey: twinKeys.all });
    },
    [projectId, qc],
  );

  const submit = useCallback(
    async (def: PreviewDefinition, values: Record<string, number | string>) => {
      if (!projectId || !sketchKey) return;
      setState({ phase: 'starting' });
      try {
        if (Object.keys(values).length) {
          // The answers become the sketch's next revision; the route schedules the one build.
          const reply = await commitParameters(projectId, sketchKey, {
            base_revision: def.base_revision,
            feature_id: def.feature_id,
            values,
            source: 'numeric',
          });
          const build = reply.build;
          if (build?.job_id) return void (await started(BUILD_STARTED));
          if (build?.status === 'succeeded') return void (await started(BUILD_UP_TO_DATE));
          setState({ phase: 'error', message: build?.detail ?? 'The answers were saved, but the exact build did not start.' });
          return;
        }
        const reply = await requestExactBuild(sketchKey, projectId, null, { featureId: def.feature_id });
        if (reply.job_id) return void (await started(BUILD_STARTED));
        if (reply.status === 'succeeded') return void (await started(BUILD_UP_TO_DATE));
        setState({ phase: 'error', message: reply.error ?? 'The exact build did not start.' });
      } catch (err) {
        setState({ phase: 'error', message: refusal(err) });
      }
    },
    [projectId, sketchKey, started],
  );

  const proceed = useCallback(async () => {
    const c = ctx.current;
    if (!c) return;
    const plan = exactBuildPlan(c.def, c.answers, c.materials);
    if (plan.kind === 'blocked') setState({ phase: 'blocked', message: plan.message, fix: plan.fix });
    else if (plan.kind === 'ask') setState({ phase: 'asking', question: plan.question });
    else await submit(c.def, plan.values);
  }, [submit]);

  const start = useCallback(async () => {
    if (!projectId || !sketchKey) return;
    setState({ phase: 'checking' });
    try {
      // Fresh: the decision is made on the sketch as it is now.
      const def = await qc.fetchQuery({
        queryKey: parametricPreviewKeys.definition(projectId, sketchKey, null, null),
        queryFn: ({ signal }) => getPreviewDefinition(projectId, sketchKey, {}, signal),
        staleTime: 0,
      });
      let materials: MaterialHint[] = [];
      if (def.missing.includes('material')) {
        materials = await getManufacturingContext(projectId)
          .then(materialChoices)
          .catch(() => []);
      }
      ctx.current = { def, answers: {}, materials };
      await proceed();
    } catch (err) {
      setState({ phase: 'error', message: refusal(err) });
    }
  }, [projectId, sketchKey, qc, proceed]);

  const answer = useCallback(
    async (value: number | string) => {
      const c = ctx.current;
      if (!c || state.phase !== 'asking') return;
      const v = typeof value === 'string' ? value.trim() : value;
      if (v === '' || (typeof v === 'number' && !(v > 0))) return;
      c.answers = { ...c.answers, [state.question.parameter]: v };
      await proceed();
    },
    [state, proceed],
  );

  const dismiss = useCallback(() => {
    ctx.current = null;
    setState({ phase: 'idle' });
  }, []);

  return { state, start, answer, dismiss };
}
