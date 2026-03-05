import { useQuery } from '@tanstack/react-query';
import { getProjects, getProject } from '../api/endpoints/projects';

export const projectKeys = {
  all: ['projects'] as const,
  detail: (id: string) => [...projectKeys.all, id] as const,
};

export function useProjects() {
  return useQuery({
    queryKey: projectKeys.all,
    queryFn: getProjects,
    staleTime: 60_000,
  });
}

export function useProject(id: string | undefined) {
  return useQuery({
    queryKey: projectKeys.detail(id ?? ''),
    queryFn: () => getProject(id!),
    enabled: !!id,
    staleTime: 30_000,
  });
}
