import { useQuery } from '@tanstack/react-query';
import { getBom } from '../api/endpoints/bom';

export const bomKeys = {
  all: ['bom'] as const,
  project: (projectId: string) => [...bomKeys.all, projectId] as const,
};

export function useBom(projectId?: string) {
  return useQuery({
    queryKey: projectId ? bomKeys.project(projectId) : bomKeys.all,
    queryFn: () => getBom(projectId),
    staleTime: 60_000,
  });
}
