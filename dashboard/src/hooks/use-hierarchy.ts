import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getHierarchyTree, realizeHierarchyNode } from '../api/endpoints/hierarchy';

export const hierarchyKeys = {
  all: ['hierarchy'] as const,
  project: (projectId: string) => [...hierarchyKeys.all, projectId] as const,
};

export function useHierarchyTree(projectId?: string) {
  return useQuery({
    queryKey: projectId ? hierarchyKeys.project(projectId) : hierarchyKeys.all,
    queryFn: () => getHierarchyTree(projectId),
    staleTime: 30_000,
  });
}

/** FORGE-266 (gap G-C2): "Replace placeholder with part". */
export function useRealizeHierarchyNode() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      nodeId,
      ...payload
    }: {
      nodeId: string;
      workProductId?: string;
      bomItemId?: string;
    }) => realizeHierarchyNode(nodeId, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: hierarchyKeys.all });
      queryClient.invalidateQueries({ queryKey: ['bom'] });
    },
  });
}
