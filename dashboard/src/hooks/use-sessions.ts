import { useQuery } from '@tanstack/react-query';
import { getSessions, getSession } from '../api/endpoints/sessions';

export const sessionKeys = {
  all: ['sessions'] as const,
  project: (projectId: string) => [...sessionKeys.all, 'project', projectId] as const,
  detail: (id: string) => [...sessionKeys.all, id] as const,
};

export function useSessions(projectId?: string) {
  return useQuery({
    queryKey: projectId ? sessionKeys.project(projectId) : sessionKeys.all,
    queryFn: () => getSessions(projectId),
    staleTime: 10_000,
    refetchInterval: 5_000,
  });
}

export function useSession(id: string | undefined) {
  return useQuery({
    queryKey: sessionKeys.detail(id ?? ''),
    queryFn: () => getSession(id!),
    enabled: !!id,
    staleTime: 5_000,
    refetchInterval: 3_000,
  });
}
