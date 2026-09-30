import { useQuery } from '@tanstack/react-query';

import { getDesignFlows } from '../api/endpoints/design-flows';

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
