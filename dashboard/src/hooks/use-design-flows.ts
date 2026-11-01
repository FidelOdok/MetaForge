import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  getDesignFlows,
  saveFlowVersion,
  validateFlowEdit,
} from '../api/endpoints/design-flows';

export const designFlowKeys = {
  all: ['design-flows'] as const,
};

/**
 * The flow catalogue from the gateway (FORGE-395).
 *
 * `staleTime` is generous because flows change when someone edits a template
 * file, not per interaction — but it is not `Infinity`, because FORGE-399 will
 * let a flow be edited from the canvas and a wizard showing yesterday's phases
 * is the same drift this ticket removed, just with a shorter half-life.
 */
export function useDesignFlows() {
  return useQuery({
    queryKey: designFlowKeys.all,
    queryFn: getDesignFlows,
    staleTime: 60_000,
  });
}

/**
 * Live invariant validation for the flow editor (FORGE-399).
 *
 * A mutation rather than a query: it is driven by edits, not by a cache key,
 * and the answer is about the flow in front of the person right now.
 */
export function useValidateFlowEdit() {
  return useMutation({ mutationFn: validateFlowEdit });
}

/** Save an edit as a new version, held for approval. */
export function useSaveFlowVersion() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: saveFlowVersion,
    onSuccess: () => {
      // The approvals queue just gained an entry.
      queryClient.invalidateQueries({ queryKey: ['approvals'] });
    },
  });
}
