import { useQuery } from '@tanstack/react-query';
import { getTwinNodes, getTwinNode, getTwinRelationships, getNodeVersionHistory } from '../api/endpoints/twin';

export const twinKeys = {
  all: ['twin'] as const,
  node: (id: string) => [...twinKeys.all, id] as const,
  relationships: ['twin', 'relationships'] as const,
};

// MET-539: keep the Twin view live by polling. React Query only polls while the
// tab is focused (refetchIntervalInBackground defaults to false), so this picks
// up imports/edits/deletes within a few seconds without burning requests when
// the dashboard isn't on screen. staleTime is kept below the interval so focus
// refetches stay fresh too.
const TWIN_NODES_POLL_MS = 10_000;
const TWIN_NODE_POLL_MS = 15_000;
const TWIN_RELATIONSHIPS_POLL_MS = 15_000;

export function useTwinNodes(projectId?: string) {
  return useQuery({
    // MET-491: project scope is part of the cache key so switching
    // projects refetches the scoped node list.
    queryKey: [...twinKeys.all, 'project', projectId ?? ''] as const,
    queryFn: () => getTwinNodes(projectId || undefined),
    staleTime: TWIN_NODES_POLL_MS,
    refetchInterval: TWIN_NODES_POLL_MS,
  });
}

export function useTwinNode(id: string | undefined) {
  return useQuery({
    queryKey: twinKeys.node(id ?? ''),
    queryFn: () => getTwinNode(id!),
    enabled: !!id,
    staleTime: TWIN_NODE_POLL_MS,
    refetchInterval: TWIN_NODE_POLL_MS,
  });
}

export function useTwinRelationships() {
  return useQuery({
    queryKey: twinKeys.relationships,
    queryFn: getTwinRelationships,
    staleTime: TWIN_RELATIONSHIPS_POLL_MS,
    refetchInterval: TWIN_RELATIONSHIPS_POLL_MS,
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
