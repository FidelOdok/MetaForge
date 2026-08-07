import { useMutation, useQuery } from '@tanstack/react-query';
import { getProposals, decideProposal } from '../api/endpoints/assistant';

export const assistantKeys = {
  all: ['assistant'] as const,
  proposals: (projectId?: string) =>
    [...assistantKeys.all, 'proposals', projectId ?? 'all'] as const,
};

export function useProposals(projectId?: string) {
  return useQuery({
    queryKey: assistantKeys.proposals(projectId),
    queryFn: () => getProposals(projectId),
    staleTime: 10_000,
  });
}

export function useDecideProposal() {
  return useMutation({
    mutationFn: (args: { changeId: string; decision: 'approve' | 'reject'; reason: string; reviewer: string }) =>
      decideProposal(args.changeId, args.decision, args.reason, args.reviewer),
  });
}
