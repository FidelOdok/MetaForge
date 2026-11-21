import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { adoptComponent, componentKeys, getComponentThreads } from '../api/endpoints/components';
import { twinKeys } from './use-twin';

// Polled like the studio, so a part Develop records shows in every stage without a reload.
const POLL_MS = 10_000;

/** Every component followed through the four stages (FORGE-903). */
export function useComponentThreads(projectId: string) {
  return useQuery({
    queryKey: componentKeys.threads(projectId),
    queryFn: ({ signal }) => getComponentThreads(projectId, signal),
    refetchInterval: POLL_MS,
    retry: false,
  });
}

/** Take Develop's part for one thread into the Imagine studio. */
export function useAdoptComponent(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ revision, key }: { revision: number; key: string }) => adoptComponent(projectId, revision, key),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: componentKeys.threads(projectId) });
      void qc.invalidateQueries({ queryKey: ['imagine', projectId] });
      void qc.invalidateQueries({ queryKey: twinKeys.all });
    },
  });
}
