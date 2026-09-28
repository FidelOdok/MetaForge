import { useQuery } from '@tanstack/react-query';
import { getBom, getHierarchicalBom } from '../api/endpoints/bom';

export const bomKeys = {
  all: ['bom'] as const,
  project: (projectId: string) => [...bomKeys.all, projectId] as const,
  hierarchicalAll: ['bom', 'hierarchical'] as const,
  hierarchicalProject: (projectId: string) => [...bomKeys.hierarchicalAll, projectId] as const,
};

export function useBom(projectId?: string) {
  return useQuery({
    queryKey: projectId ? bomKeys.project(projectId) : bomKeys.all,
    queryFn: () => getBom(projectId),
    staleTime: 60_000,
  });
}

/** FORGE-267: the EBOM derived from the product hierarchy. */
export function useHierarchicalBom(projectId?: string) {
  return useQuery({
    queryKey: projectId ? bomKeys.hierarchicalProject(projectId) : bomKeys.hierarchicalAll,
    queryFn: () => getHierarchicalBom(projectId),
    staleTime: 60_000,
  });
}
