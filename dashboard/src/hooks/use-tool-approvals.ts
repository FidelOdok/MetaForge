import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getPendingToolApprovals, decideToolApproval } from '../api/endpoints/toolApprovals';

export const toolApprovalKeys = {
  all: ['tool-approvals'] as const,
  pending: () => [...toolApprovalKeys.all, 'pending'] as const,
};

// A paused tool call can auto-deny in as little as 10s (design-flow) or as
// long as 30min (live chat, METAFORGE_CHAT_APPROVAL_TIMEOUT_SECONDS) with
// nothing else prompting a human to look — unlike proposals (use-assistant.ts,
// no interval), this needs to actually notice a new one without a manual
// page reload.
const POLL_INTERVAL_MS = 5_000;

export function usePendingToolApprovals() {
  return useQuery({
    queryKey: toolApprovalKeys.pending(),
    queryFn: getPendingToolApprovals,
    refetchInterval: POLL_INTERVAL_MS,
  });
}

export function useDecideToolApproval() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (args: { runId: string; decision: 'approve' | 'reject' }) =>
      decideToolApproval(args.runId, args.decision),
    // Decided runs drop out of the pending list server-side; refetch now
    // instead of waiting up to POLL_INTERVAL_MS for it to disappear.
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: toolApprovalKeys.pending() });
    },
  });
}
