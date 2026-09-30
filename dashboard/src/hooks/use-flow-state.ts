import { useQuery } from '@tanstack/react-query';

import { getFlowState } from '../api/endpoints/run-flow';

export const flowStateKeys = {
  detail: (runId: string) => ['run-flow-state', runId] as const,
};

/**
 * Live phase state for a design-flow run (FORGE-396).
 *
 * Polls at 2s while the run is active. Polling rather than the run SSE
 * stream, deliberately and for now: that stream carries run *status*
 * transitions only — it is fired by the run store's `on_transition`, which a
 * phase starting inside the workflow does not touch. Phase-level events would
 * need the workflow to publish into it, which is its own change. A 2s poll is
 * honest about being a poll; an SSE subscription that silently only updated on
 * status changes would look live and not be.
 */
export function useFlowState(runId: string | undefined, active: boolean) {
  return useQuery({
    queryKey: flowStateKeys.detail(runId ?? ''),
    queryFn: () => getFlowState(runId!),
    enabled: !!runId,
    refetchInterval: active ? 2_000 : false,
    staleTime: 1_000,
  });
}
