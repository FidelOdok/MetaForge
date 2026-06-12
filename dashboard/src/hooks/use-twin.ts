import { useQuery } from '@tanstack/react-query';
import { getTwinNodes, getTwinNode, getTwinRelationships, getNodeVersionHistory } from '../api/endpoints/twin';

export const twinKeys = {
  all: ['twin'] as const,
  node: (id: string) => [...twinKeys.all, id] as const,
  relationships: ['twin', 'relationships'] as const,
};

export function useTwinNodes(projectId?: string) {
  return useQuery({
    // MET-491: project scope is part of the cache key so switching
    // projects refetches the scoped node list.
    queryKey: [...twinKeys.all, 'project', projectId ?? ''] as const,
    queryFn: () => getTwinNodes(projectId || undefined),
    staleTime: 30_000,
  });
}

export function useTwinNode(id: string | undefined) {
  return useQuery({
    queryKey: twinKeys.node(id ?? ''),
    queryFn: () => getTwinNode(id!),
    enabled: !!id,
    staleTime: 15_000,
  });
}

export function useTwinRelationships() {
  return useQuery({
    queryKey: twinKeys.relationships,
    queryFn: getTwinRelationships,
    staleTime: 30_000,
  });
}

export function useNodeVersionHistory(nodeId: string | undefined) {
  return useQuery({
    queryKey: [...twinKeys.all, nodeId, 'versions'] as const,
    queryFn: () => getNodeVersionHistory(nodeId!),
    enabled: !!nodeId,
    staleTime: 15_000,
  });
}
