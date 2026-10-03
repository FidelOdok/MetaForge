import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { decideApproval, getApproval, listApprovals } from '../api/endpoints/approvals';
import type { ApprovalDecisionRequest, ApprovalListFilters } from '../types/approvals';

export const approvalKeys = {
  all: ['approvals'] as const,
  list: (f: ApprovalListFilters) =>
    [...approvalKeys.all, 'list', f.status ?? 'pending', f.projectId ?? 'all', f.kind ?? 'any'] as const,
  detail: (id: string) => [...approvalKeys.all, 'detail', id] as const,
};

// A held item can expire on its own with nothing prompting a human to look,
// so the pending queue polls rather than waiting for a manual reload.
const POLL_INTERVAL_MS = 5_000;

export function useApprovals(filters: ApprovalListFilters = {}) {
  return useQuery({
    queryKey: approvalKeys.list(filters),
    queryFn: () => listApprovals(filters),
    refetchInterval: filters.status === 'decided' ? false : POLL_INTERVAL_MS,
  });
}

export function useApproval(id: string | undefined, enabled = true) {
  return useQuery({
    queryKey: approvalKeys.detail(id ?? ''),
    queryFn: () => getApproval(id!),
    enabled: !!id && enabled,
    refetchInterval: POLL_INTERVAL_MS,
  });
}

export function useDecideApproval() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; body: ApprovalDecisionRequest }) =>
      decideApproval(vars.id, vars.body),
    onSuccess: () => {
      // Prefix invalidation: every list, every detail, and the runs the
      // decision may have resumed or ended.
      void qc.invalidateQueries({ queryKey: approvalKeys.all });
      void qc.invalidateQueries({ queryKey: ['runs'] });
      void qc.invalidateQueries({ queryKey: ['run-flow-state'] });
    },
  });
}
