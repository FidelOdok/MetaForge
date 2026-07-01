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
  detail: (id: string) => [...runKeys.all, id] as const,
};

/** Poll the runs list. */
export function useRuns() {
  return useQuery({
    queryKey: runKeys.all,
    queryFn: listRuns,
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
