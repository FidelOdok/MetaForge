import { useMutation, useQuery } from '@tanstack/react-query';
import {
  submitRequest,
  getRunStatus,
  getProposals,
  decideProposal,
  type SubmitRequestPayload,
} from '../api/endpoints/assistant';

export const assistantKeys = {
  all: ['assistant'] as const,
  proposals: () => [...assistantKeys.all, 'proposals'] as const,
  runStatus: (runId: string) => [...assistantKeys.all, 'run', runId] as const,
};

export function useProposals(sessionId?: string) {
  return useQuery({
    queryKey: assistantKeys.proposals(),
    queryFn: () => getProposals(sessionId),
    staleTime: 10_000,
  });
}

export function useRunStatus(runId: string | undefined) {
  return useQuery({
    queryKey: assistantKeys.runStatus(runId ?? ''),
    queryFn: () => getRunStatus(runId!),
    enabled: !!runId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === 'completed' || status === 'failed') return false;
      return 2_000;
    },
  });
}

export function useSubmitRequest() {
  return useMutation({
    mutationFn: (payload: SubmitRequestPayload) => submitRequest(payload),
  });
}

export function useDecideProposal() {
  return useMutation({
    mutationFn: (args: { changeId: string; decision: 'approve' | 'reject'; reason: string; reviewer: string }) =>
      decideProposal(args.changeId, args.decision, args.reason, args.reviewer),
  });
}
