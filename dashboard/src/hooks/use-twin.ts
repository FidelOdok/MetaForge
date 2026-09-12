import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getTwinNodes, getTwinNode, getTwinRelationships, getNodeVersionHistory, approveSketch } from '../api/endpoints/twin';

export const twinKeys = {
  all: ['twin'] as const,
  node: (id: string) => [...twinKeys.all, id] as const,
  relationships: (projectId?: string) => ['twin', 'relationships', projectId ?? ''] as const,
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

export function useTwinRelationships(projectId?: string) {
  return useQuery({
    // MET-491/MET-653: project scope is part of the cache key so switching
    // projects refetches the scoped relationship list (mirrors useTwinNodes).
    queryKey: twinKeys.relationships(projectId),
    queryFn: () => getTwinRelationships(projectId),
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

/** Follow-up to MET-740/747: human sign-off on a design_sketch node.
 * Invalidates the node + its version history so the approval badge and the
 * new revision it creates show up immediately rather than on the next poll. */
export function useApproveSketch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ nodeId, approvedBy }: { nodeId: string; approvedBy?: string }) =>
      approveSketch(nodeId, approvedBy),
    onSuccess: (_data, { nodeId }) => {
      queryClient.invalidateQueries({ queryKey: twinKeys.node(nodeId) });
      queryClient.invalidateQueries({ queryKey: [...twinKeys.all, nodeId, 'versions'] });
      queryClient.invalidateQueries({ queryKey: twinKeys.all });
    },
  });
}
