import { useQuery } from '@tanstack/react-query';
import { getImpact, getInterfaces } from '../api/endpoints/impact';

/** Query keys for interface edges and recorded impacts (FORGE-793). */
export const impactKeys = {
  interfaces: (projectId: string) => ['interfaces', projectId] as const,
  impact: (projectId: string, ref: string) => ['impact', projectId, ref] as const,
};

/** The project's dependency graph. Refetched when the activity asks, not on a timer. */
export function useInterfaces(projectId: string | null | undefined, enabled = true) {
  return useQuery({
    queryKey: impactKeys.interfaces(projectId ?? ''),
    queryFn: ({ signal }) => getInterfaces(projectId!, signal),
    enabled: !!projectId && enabled,
    staleTime: 30_000,
    retry: false,
  });
}

/**
 * One recorded impact. Rerun decisions change it, so it is refetched while
 * a rerun still waits for a person.
 */
export function useImpact(projectId: string | null | undefined, ref: string | null | undefined) {
  return useQuery({
    queryKey: impactKeys.impact(projectId ?? '', ref ?? ''),
    queryFn: ({ signal }) => getImpact(projectId!, ref!, signal),
    enabled: !!projectId && !!ref,
    staleTime: 5_000,
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.proposed_reruns.some((r) => r.status === 'proposed' && r.disposition === 'requires_approval')
        ? 10_000
        : false,
  });
}
