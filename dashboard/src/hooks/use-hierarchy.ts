import { useQuery } from '@tanstack/react-query';
import { getHierarchyTree } from '../api/endpoints/hierarchy';

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
