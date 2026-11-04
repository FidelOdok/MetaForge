import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { conflictOf, getCreation, type CreationView } from '../api/endpoints/create';

export const creationKeys = {
  all: ['creation'] as const,
  detail: (projectId: string) => [...creationKeys.all, projectId] as const,
};

// Polled, so a change saved in the Engineering view (or by an agent) and the
// readiness it implies reach the Create view without a reload.
const POLL_INTERVAL_MS = 5_000;

export function useCreation(projectId: string | null | undefined) {
  return useQuery({
    queryKey: creationKeys.detail(projectId ?? ''),
    queryFn: () => getCreation(projectId as string),
    enabled: !!projectId,
    refetchInterval: POLL_INTERVAL_MS,
  });
}

/**
 * A write to the creation. The result replaces the cached view; a conflict
 * (someone saved first) also refreshes it, so the person sees the newer
 * record before deciding to apply their change again.
 */
export function useCreationWrite<V>(projectId: string, write: (vars: V) => Promise<CreationView>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: write,
    onSuccess: (view) => {
      qc.setQueryData(creationKeys.detail(projectId), view);
      void qc.invalidateQueries({ queryKey: ['projects'] });
    },
    onError: (error) => {
      if (conflictOf(error)) void qc.invalidateQueries({ queryKey: creationKeys.detail(projectId) });
    },
  });
}
