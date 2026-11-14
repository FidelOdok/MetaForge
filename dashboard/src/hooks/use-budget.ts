import { useQuery } from '@tanstack/react-query';
import { getBudget } from '../api/endpoints/budget';

const BUDGET_POLL_MS = 60_000;

/** The active project's AI budget; idle without a project. */
export function useBudget(projectId: string | null | undefined) {
  return useQuery({
    queryKey: ['budget', projectId] as const,
    queryFn: () => getBudget(projectId as string),
    enabled: Boolean(projectId),
    staleTime: BUDGET_POLL_MS,
    refetchInterval: BUDGET_POLL_MS,
    retry: 1,
  });
}
