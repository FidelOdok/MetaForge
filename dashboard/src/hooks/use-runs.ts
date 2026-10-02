import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  createRun,
  getRun,
  listRuns,
  submitRunApproval,
} from '../api/endpoints/runs';
import type { ApprovalDecision } from '../types/run';

export const runKeys = {
  all: ['runs'] as const,
  // The project is part of the key, not just the request. Without it two
  // projects share one cache entry and switching project shows the previous
  // one's runs until the poll lands.
  list: (projectId?: string) => [...runKeys.all, 'list', projectId ?? 'all'] as const,
  detail: (id: string) => [...runKeys.all, id] as const,
};

/** Poll the runs list, scoped to a project when one is active. */
export function useRuns(projectId?: string) {
  return useQuery({
    queryKey: runKeys.list(projectId),
    queryFn: () => listRuns(projectId),
    staleTime: 5_000,
    refetchInterval: 4_000,
  });
}

/** Poll a single run. */
export function useRun(id: string | undefined) {
  return useQuery({
    queryKey: runKeys.detail(id ?? ''),
    queryFn: () => getRun(id!),
    enabled: !!id,
    staleTime: 3_000,
    refetchInterval: 3_000,
  });
}

/** Create a run and refresh the list. */
export function useCreateRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { request?: Record<string, unknown>; start?: boolean }) =>
      createRun(vars.request ?? {}, vars.start ?? true),
    onSuccess: () => qc.invalidateQueries({ queryKey: runKeys.all }),
  });
}

/** Approve or reject a paused run and refresh it. */
export function useSubmitApproval() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; decision: ApprovalDecision }) =>
      submitRunApproval(vars.id, vars.decision),
    onSuccess: (run) => {
      qc.invalidateQueries({ queryKey: runKeys.all });
      qc.invalidateQueries({ queryKey: runKeys.detail(run.id) });
    },
  });
}
