import { useQuery } from '@tanstack/react-query';
import { getRelatedDecisions } from '../api/endpoints/decisions';

export const decisionKeys = {
  all: ['decisions'] as const,
  relatedTo: (nodeId: string) => [...decisionKeys.all, nodeId] as const,
};

export function useRelatedDecisions(nodeId?: string) {
  return useQuery({
    queryKey: nodeId ? decisionKeys.relatedTo(nodeId) : decisionKeys.all,
    queryFn: () => getRelatedDecisions(nodeId),
    enabled: Boolean(nodeId),
    staleTime: 15_000,
  });
}
